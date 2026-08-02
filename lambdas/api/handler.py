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
    GET /stocks/{ticker}/news — Analyzed news articles for a stock.

    Primary: serves pipeline-analyzed articles from DynamoDB (with risk flags per article).
    Fallback: fetches live from TickerTick if no analyzed articles exist.
    """
    table = get_table()

    # Try DynamoDB first (has risk flags and sentiment per article)
    result = table.get_item(
        Key={"PK": f"STOCK#{ticker.upper()}", "SK": "ARTICLES"}
    )
    item = result.get("Item")
    if item and item.get("articles"):
        articles = decimal_to_float(item.get("articles", []))
        return response(200, {
            "ticker": ticker.upper(),
            "articles": articles,
            "count": len(articles),
            "source": "pipeline_analyzed",
        })

    # Fallback: live from TickerTick (no risk flags)
    import requests as http_requests
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
                "risk_flags": [],
            } for s in stories]

            return response(200, {
                "ticker": ticker.upper(),
                "articles": articles,
                "count": len(articles),
                "source": "tickertick_live",
            })
    except Exception:
        pass

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
# PORTFOLIO ENDPOINTS
# ==========================================

def buy_stock(ticker: str, body: dict):
    """
    POST /portfolio/{ticker}/buy — Record a stock purchase.

    Body: { "price": 45.50, "shares": 100, "date": "2026-08-01" }
    Creates a new lot item in DynamoDB and updates the summary.
    """
    table = get_table()
    now = datetime.now(timezone.utc)

    price = body.get("price")
    shares = body.get("shares")
    purchase_date = body.get("date", now.strftime("%Y-%m-%d"))

    if not price or not shares:
        return response(400, {"error": "price and shares are required"})

    symbol = ticker.upper()
    lot_id = now.isoformat()  # Unique per lot

    # Write the lot
    from decimal import Decimal
    lot_item = {
        "PK": f"PORTFOLIO#{symbol}",
        "SK": f"LOT#{lot_id}",
        "symbol": symbol,
        "purchase_price": Decimal(str(price)),
        "purchase_date": purchase_date,
        "shares": Decimal(str(shares)),
        "status": "OPEN",
        "created_at": lot_id,
    }
    table.put_item(Item=lot_item)

    # Update summary (recalculate from all open lots)
    _update_portfolio_summary(table, symbol)

    return response(200, {
        "message": f"Recorded purchase: {shares} shares of {symbol} at ${price}",
        "lot_id": lot_id,
    })


def get_portfolio():
    """
    GET /portfolio — All open positions with current P&L and signals.
    """
    table = get_table()

    # Scan for all PORTFOLIO# SUMMARY items
    result = table.scan(
        FilterExpression=Attr("PK").begins_with("PORTFOLIO#") & Attr("SK").eq("SUMMARY"),
    )

    positions = []
    for item in result.get("Items", []):
        symbol = item.get("symbol", "")
        # Get current price from LATEST
        latest = table.get_item(Key={"PK": f"STOCK#{symbol}", "SK": "LATEST"}).get("Item", {})
        current_price = float(latest.get("price", 0)) if latest.get("price") else None

        position = decimal_to_float(item)
        position["current_price"] = current_price
        if current_price and item.get("avg_cost_basis"):
            cost = float(item["avg_cost_basis"])
            position["unrealized_pnl_pct"] = round((current_price - cost) / cost * 100, 2)
            position["unrealized_pnl"] = round((current_price - cost) * float(item.get("total_shares", 0)), 2)
        positions.append(position)

    positions.sort(key=lambda p: p.get("unrealized_pnl_pct", 0), reverse=True)

    return response(200, {
        "positions": positions,
        "count": len(positions),
    })


def get_portfolio_detail(ticker: str):
    """
    GET /portfolio/{ticker} — Lot history and details for a single position.
    """
    table = get_table()
    symbol = ticker.upper()

    # Get all lots
    result = table.query(
        KeyConditionExpression=Key("PK").eq(f"PORTFOLIO#{symbol}") & Key("SK").begins_with("LOT#"),
        ScanIndexForward=True,
    )
    lots = [decimal_to_float(item) for item in result.get("Items", [])]

    # Get summary
    summary = table.get_item(Key={"PK": f"PORTFOLIO#{symbol}", "SK": "SUMMARY"}).get("Item", {})

    # Get current price
    latest = table.get_item(Key={"PK": f"STOCK#{symbol}", "SK": "LATEST"}).get("Item", {})
    current_price = float(latest.get("price", 0)) if latest.get("price") else None

    return response(200, {
        "ticker": symbol,
        "summary": decimal_to_float(summary),
        "lots": lots,
        "current_price": current_price,
    })


def sell_stock(ticker: str, body: dict):
    """
    POST /portfolio/{ticker}/sell — Close a position (all lots or specific lot).

    Body: { "lot_id": "2026-08-01T..." } (optional — if omitted, closes all open lots)
          { "price": 55.00 } (required — the sell price)
    """
    table = get_table()
    symbol = ticker.upper()
    sell_price = body.get("price")
    lot_id = body.get("lot_id")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not sell_price:
        return response(400, {"error": "price is required"})

    from decimal import Decimal

    if lot_id:
        # Close specific lot
        table.update_item(
            Key={"PK": f"PORTFOLIO#{symbol}", "SK": f"LOT#{lot_id}"},
            UpdateExpression="SET #s = :s, closed_price = :p, closed_date = :d",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "CLOSED",
                ":p": Decimal(str(sell_price)),
                ":d": today,
            },
        )
    else:
        # Close all open lots
        result = table.query(
            KeyConditionExpression=Key("PK").eq(f"PORTFOLIO#{symbol}") & Key("SK").begins_with("LOT#"),
        )
        for item in result.get("Items", []):
            if item.get("status") == "OPEN":
                table.update_item(
                    Key={"PK": item["PK"], "SK": item["SK"]},
                    UpdateExpression="SET #s = :s, closed_price = :p, closed_date = :d",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={
                        ":s": "CLOSED",
                        ":p": Decimal(str(sell_price)),
                        ":d": today,
                    },
                )

    _update_portfolio_summary(table, symbol)

    return response(200, {"message": f"Sold {symbol} at ${sell_price}"})


def _update_portfolio_summary(table, symbol: str):
    """Recalculate portfolio summary from all open lots."""
    from decimal import Decimal

    result = table.query(
        KeyConditionExpression=Key("PK").eq(f"PORTFOLIO#{symbol}") & Key("SK").begins_with("LOT#"),
    )

    total_shares = Decimal("0")
    total_cost = Decimal("0")

    for item in result.get("Items", []):
        if item.get("status") == "OPEN":
            shares = item.get("shares", Decimal("0"))
            price = item.get("purchase_price", Decimal("0"))
            total_shares += shares
            total_cost += shares * price

    if total_shares > 0:
        avg_cost = total_cost / total_shares
        table.put_item(Item={
            "PK": f"PORTFOLIO#{symbol}",
            "SK": "SUMMARY",
            "symbol": symbol,
            "total_shares": total_shares,
            "avg_cost_basis": avg_cost.quantize(Decimal("0.01")),
            "total_invested": total_cost.quantize(Decimal("0.01")),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })
    else:
        # No open lots — delete summary
        table.delete_item(Key={"PK": f"PORTFOLIO#{symbol}", "SK": "SUMMARY"})


def get_price_history(ticker: str):
    """
    GET /stocks/{ticker}/prices — 30-day daily price history.

    Returns OHLCV bars for the stock, used for trend detection and mini charts.
    Data comes from Polygon via the score calculator's daily backfill.
    """
    table = get_table()

    result = table.get_item(
        Key={"PK": f"PRICE_HISTORY#{ticker.upper()}", "SK": "DAILY"}
    )

    item = result.get("Item")
    if not item:
        return response(200, {"ticker": ticker.upper(), "bars": [], "bar_count": 0})

    return response(200, decimal_to_float({
        "ticker": ticker.upper(),
        "bars": item.get("bars", []),
        "bar_count": item.get("bar_count", 0),
        "from_date": item.get("from_date", ""),
        "to_date": item.get("to_date", ""),
    }))


def get_industry_averages():
    """
    GET /industries — Industry median benchmarks.

    Returns median values for key metrics computed from the full 5,097-stock universe,
    grouped by SEC SIC industry classification.

    These are recalculated on every pipeline run from real market data.
    The frontend uses them to compare individual stocks against their industry peers.
    """
    table = get_table()

    # Scan for all INDUSTRY_AVG# items
    result = table.scan(
        FilterExpression=Attr("PK").begins_with("INDUSTRY_AVG#"),
    )

    industries = {}
    for item in result.get("Items", []):
        industry_name = item.get("industry", "")
        if not industry_name:
            continue
        industries[industry_name] = decimal_to_float(item)

    return response(200, {
        "industries": industries,
        "count": len(industries),
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

        # Route: GET /industries
        elif path == "/industries" and method == "GET":
            return get_industry_averages()

        # Route: GET /portfolio
        elif path == "/portfolio" and method == "GET":
            return get_portfolio()

        # Route: GET /portfolio/{ticker}
        elif path.startswith("/portfolio/") and "/buy" not in path and "/sell" not in path and method == "GET":
            ticker = path.split("/")[2]
            return get_portfolio_detail(ticker)

        # Route: POST /portfolio/{ticker}/buy
        elif "/portfolio/" in path and "/buy" in path and method == "POST":
            ticker = path.split("/")[2]
            body = json.loads(event.get("body", "{}") or "{}")
            return buy_stock(ticker, body)

        # Route: POST /portfolio/{ticker}/sell
        elif "/portfolio/" in path and "/sell" in path and method == "POST":
            ticker = path.split("/")[2]
            body = json.loads(event.get("body", "{}") or "{}")
            return sell_stock(ticker, body)

        # Route: GET /stocks/{ticker}/history
        elif "/history" in path and method == "GET":
            ticker = path_params.get("ticker") or path.split("/")[2]
            return get_stock_history(ticker)

        # Route: GET /stocks/{ticker}/prices
        elif "/prices" in path and method == "GET":
            ticker = path_params.get("ticker") or path.split("/")[2]
            return get_price_history(ticker)

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
