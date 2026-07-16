"""
API Lambda Handler
==================
Serves REST endpoints for the React frontend.

This is a single Lambda that handles all API routes. API Gateway passes
the HTTP method and path, and we route to the appropriate handler function.

Why one Lambda instead of one-per-endpoint?
- Fewer cold starts (one function stays warm, not five)
- Shared code (DynamoDB client, response helpers) isn't duplicated
- Simpler deployment
- For our scale (~100 req/day), this is the right trade-off
- When you need to scale to millions of requests, you'd split them

Endpoints:
    GET  /stocks              → List all tracked stocks with latest scores
    GET  /stocks/{ticker}     → Single stock detail
    GET  /stocks/{ticker}/history → Score history (for trend charts)
    POST /stocks/{ticker}/track  → Manually track a stock
    DELETE /stocks/{ticker}/track → Stop tracking a stock
    GET  /pipeline/status     → Latest pipeline run info

Environment Variables:
    DATA_TABLE_NAME - DynamoDB table name
"""

import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key, Attr

# DynamoDB
dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("DATA_TABLE_NAME", "stock-screener-data")


def get_table():
    return dynamodb.Table(TABLE_NAME)


# ==========================================
# RESPONSE HELPERS
# ==========================================

def response(status_code: int, body: dict) -> dict:
    """
    Build an API Gateway response.

    API Gateway expects this exact format:
    - statusCode: HTTP status
    - headers: must include CORS headers for browser access
    - body: JSON string (not a dict — API Gateway requires stringified JSON)
    """
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",  # CORS — allows any frontend to call this
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": json.dumps(body, default=str),
    }


def decimal_to_float(obj):
    """Convert DynamoDB Decimal types to float for JSON serialization."""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: decimal_to_float(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [decimal_to_float(i) for i in obj]
    return obj


# ==========================================
# ROUTE HANDLERS
# ==========================================

def get_stocks():
    """
    GET /stocks — List all tracked stocks with their latest scores.

    Queries the GSI (tracking-status-index) to find all ACTIVE and GRACE stocks,
    then deduplicates (GSI returns both LATEST and TRACKING items).

    Returns a list sorted by investability score (highest first).
    """
    table = get_table()

    all_stocks = []
    seen_symbols = set()

    for status in ["ACTIVE", "GRACE", "MANUAL"]:
        result = table.query(
            IndexName="tracking-status-index",
            KeyConditionExpression=Key("tracking_status").eq(status),
        )
        for item in result.get("Items", []):
            # Only take LATEST items (which have investability_score)
            # Skip TRACKING items to avoid duplicates
            symbol = item.get("symbol", "")
            if symbol in seen_symbols:
                continue
            if item.get("SK") != "LATEST":
                continue
            seen_symbols.add(symbol)
            item["_tracking_status"] = status
            all_stocks.append(decimal_to_float(item))

    # Sort by investability score (highest first)
    all_stocks.sort(key=lambda s: s.get("investability_score") or 0, reverse=True)

    return response(200, {
        "stocks": all_stocks,
        "count": len(all_stocks),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def get_stock_detail(ticker: str):
    """
    GET /stocks/{ticker} — Full detail for a single stock.

    Returns the LATEST item which contains all current data:
    fundamentals, sentiment, scores, tracking status.
    """
    table = get_table()

    result = table.get_item(
        Key={"PK": f"STOCK#{ticker.upper()}", "SK": "LATEST"}
    )

    item = result.get("Item")
    if not item:
        return response(404, {"error": f"Stock {ticker} not found or not tracked"})

    return response(200, {"stock": decimal_to_float(item)})


def get_stock_history(ticker: str):
    """
    GET /stocks/{ticker}/history — Score history over time.

    Queries all SCORE#date items for the given stock.
    Returns a time series that the frontend renders as a chart.

    The sort key (SK) starts with "SCORE#" followed by a date (YYYY-MM-DD),
    so querying with begins_with gives us all historical scores in order.
    """
    table = get_table()

    result = table.query(
        KeyConditionExpression=(
            Key("PK").eq(f"STOCK#{ticker.upper()}")
            & Key("SK").begins_with("SCORE#")
        ),
        ScanIndexForward=True,  # Oldest first (chronological for charts)
    )

    items = [decimal_to_float(item) for item in result.get("Items", [])]

    return response(200, {
        "ticker": ticker.upper(),
        "history": items,
        "data_points": len(items),
    })


def get_stock_news(ticker: str):
    """
    GET /stocks/{ticker}/news — Recent news articles for a stock.

    Fetches live from TickerTick API (free, no key needed).
    Falls back to S3 pipeline cache if TickerTick is unavailable.
    """
    import requests as http_requests

    # Fetch live from TickerTick
    try:
        url = "https://api.tickertick.com/feed"
        params = {"q": f"tt:{ticker.lower()}", "lang": "en", "n": 15}
        resp = http_requests.get(url, params=params, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            stories = data.get("stories", [])
            articles = [{
                "title": s.get("title", ""),
                "description": s.get("description", ""),
                "url": s.get("url", ""),
                "source": s.get("site", ""),
                "published_at": s.get("time", 0),
            } for s in stories]

            return response(200, {
                "ticker": ticker.upper(),
                "articles": articles,
                "count": len(articles),
                "source": "tickertick_live",
            })
    except Exception:
        pass

    # Fallback: no news available
    return response(200, {
        "ticker": ticker.upper(),
        "articles": [],
        "count": 0,
        "source": "none",
    })


def track_stock(ticker: str):
    """
    POST /stocks/{ticker}/track — Manually track a stock.

    Adds a TRACKING item with status=MANUAL.
    This stock will get news/sentiment analysis on future pipeline runs
    even if it doesn't pass the value screen.
    """
    table = get_table()
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    table.put_item(Item={
        "PK": f"STOCK#{ticker.upper()}",
        "SK": "TRACKING",
        "symbol": ticker.upper(),
        "tracking_status": "MANUAL",
        "first_tracked": today,
        "last_updated": now,
    })

    return response(200, {
        "message": f"{ticker.upper()} is now manually tracked",
        "status": "MANUAL",
    })


def untrack_stock(ticker: str):
    """
    DELETE /stocks/{ticker}/track — Stop tracking a stock.

    Removes the TRACKING item. The stock will no longer get
    news/sentiment analysis unless it passes the screen again.
    """
    table = get_table()

    table.delete_item(
        Key={"PK": f"STOCK#{ticker.upper()}", "SK": "TRACKING"}
    )

    return response(200, {
        "message": f"{ticker.upper()} removed from tracking",
    })


def get_pipeline_status():
    """
    GET /pipeline/status — Latest pipeline run information.

    Returns a summary: how many stocks tracked, when last updated.
    """
    table = get_table()

    active_stocks = []
    grace_stocks = []

    for status, target_list in [("ACTIVE", active_stocks), ("GRACE", grace_stocks)]:
        result = table.query(
            IndexName="tracking-status-index",
            KeyConditionExpression=Key("tracking_status").eq(status),
        )
        seen = set()
        for item in result.get("Items", []):
            symbol = item.get("symbol", "")
            if symbol and symbol not in seen and item.get("SK") == "LATEST":
                seen.add(symbol)
                target_list.append(symbol)

    all_items_count = len(active_stocks) + len(grace_stocks)

    return response(200, {
        "active_count": len(active_stocks),
        "grace_count": len(grace_stocks),
        "total_tracked": all_items_count,
        "active_stocks": active_stocks,
        "grace_stocks": grace_stocks,
    })


# ==========================================
# ROUTER
# ==========================================

def handler(event, context):
    """
    API Gateway Lambda entry point.

    API Gateway passes the HTTP request as an event with:
    - httpMethod: GET, POST, DELETE, OPTIONS
    - path: /stocks, /stocks/AAPL, /stocks/AAPL/history, etc.
    - pathParameters: extracted path variables (e.g., {ticker} = "AAPL")
    - body: request body for POST requests (JSON string)

    We route based on method + path to the appropriate handler.
    """
    method = event.get("httpMethod", "GET")
    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}

    print(f"API request: {method} {path}")

    # Handle CORS preflight
    if method == "OPTIONS":
        return response(200, {})

    try:
        # Route: GET /stocks
        if path == "/stocks" and method == "GET":
            return get_stocks()

        # Route: GET /pipeline/status
        elif path == "/pipeline/status" and method == "GET":
            return get_pipeline_status()

        # Route: GET /stocks/{ticker}/history
        elif "/history" in path and method == "GET":
            ticker = path_params.get("ticker") or path.split("/")[2]
            return get_stock_history(ticker)

        # Route: GET /stocks/{ticker}/news
        elif "/news" in path and method == "GET":
            ticker = path_params.get("ticker") or path.split("/")[2]
            return get_stock_news(ticker)

        # Route: POST /stocks/{ticker}/track
        elif "/track" in path and method == "POST":
            ticker = path_params.get("ticker") or path.split("/")[2]
            return track_stock(ticker)

        # Route: DELETE /stocks/{ticker}/track
        elif "/track" in path and method == "DELETE":
            ticker = path_params.get("ticker") or path.split("/")[2]
            return untrack_stock(ticker)

        # Route: GET /stocks/{ticker}
        elif path.startswith("/stocks/") and method == "GET":
            ticker = path_params.get("ticker") or path.split("/")[2]
            return get_stock_detail(ticker)

        else:
            return response(404, {"error": f"Route not found: {method} {path}"})

    except Exception as e:
        print(f"ERROR: {e}")
        return response(500, {"error": str(e)})
