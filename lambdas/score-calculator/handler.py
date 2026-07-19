"""
Score Calculator Lambda
========================
Step 7 in the pipeline.

Combines the fundamental score (from Step 2: stock-screener) with the
sentiment score (from Step 6: sentiment-analyzer) into a single
**Investability Score** per stock.

Also:
- Fetches company descriptions from Polygon (only for final ~6-10 stocks)
- Persists results to DynamoDB:
  - LATEST item: current scores (overwritten each run)
  - SCORE#date item: historical score (appended daily, never overwritten)
  - TRACKING item: tracking status (ACTIVE if passes screen)

This is what enables retroactive analysis — every day's scores are preserved
so you can look back and see how a stock's investability changed over time.

Environment Variables:
    FUNDAMENTAL_WEIGHT - Weight for fundamental score (default: 0.7)
    SENTIMENT_WEIGHT   - Weight for sentiment adjustment (default: 0.3)
    DATA_TABLE_NAME    - DynamoDB table for persistence
    RAW_DATA_BUCKET    - S3 bucket for pipeline I/O
"""

import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
import requests as http_requests

# DynamoDB client
dynamodb = boto3.resource("dynamodb")

# SSM client for API keys
ssm_client = boto3.client("ssm")

# Cache
_polygon_key = None


def get_polygon_key() -> str:
    """Get Polygon API key from SSM (cached)."""
    global _polygon_key
    if not _polygon_key:
        param = os.environ.get("POLYGON_API_KEY_PARAM", "/stock-screener/polygon-api-key")
        _polygon_key = ssm_client.get_parameter(Name=param, WithDecryption=True)["Parameter"]["Value"]
    return _polygon_key


def fetch_company_description(symbol: str) -> str:
    """
    Fetch company description from Polygon /v3/reference/tickers/{ticker}.
    Only called for final passing stocks (~6-10), so 5/min limit is fine.
    """
    try:
        key = get_polygon_key()
        url = f"https://api.polygon.io/v3/reference/tickers/{symbol}"
        response = http_requests.get(url, params={"apiKey": key}, timeout=10)
        if response.status_code == 200:
            results = response.json().get("results", {})
            return results.get("description", "")
    except Exception as e:
        print(f"  Warning: Polygon description error for {symbol}: {e}")
    return ""


def enrich_with_sic_industry(stocks: list):
    """
    Add SEC SIC industry label to each stock from the static reference map.
    This label matches the INDUSTRY_AVG# keys in DynamoDB for comparison.
    """
    import json
    bucket = os.environ.get("RAW_DATA_BUCKET", "")
    if not bucket:
        return

    try:
        s3 = boto3.client("s3")
        resp = s3.get_object(Bucket=bucket, Key="reference/ticker_industry_map.json")
        industry_map = json.loads(resp["Body"].read().decode("utf-8"))
    except Exception as e:
        print(f"  Warning: Could not load industry map: {e}")
        return

    mapped = 0
    for stock in stocks:
        symbol = stock.get("symbol", "")
        entry = industry_map.get(symbol)
        if entry:
            stock["sic_industry"] = entry.get("industry", "")
            mapped += 1

    print(f"  Mapped {mapped}/{len(stocks)} stocks to SIC industries")


def backfill_price_history(scored_stocks: list, today: str):
    """
    Fetch 30-day price history from Polygon for each passing stock.

    Stores as PRICE_HISTORY#{ticker} in DynamoDB with a list of daily bars.
    Only backfills if the stock doesn't already have recent price history
    (avoids redundant calls on subsequent runs).

    Polygon /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}: 5 calls/min.
    For ~6 stocks = ~72 seconds with 12s pacing.
    """
    table_name = os.environ.get("DATA_TABLE_NAME", "")
    if not table_name:
        return

    from datetime import timedelta

    table = dynamodb.Table(table_name)
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    from_date = (today_dt - timedelta(days=30)).strftime("%Y-%m-%d")

    symbols = [s.get("symbol", "") for s in scored_stocks if s.get("symbol")]
    print(f"  Backfilling 30-day price history for {len(symbols)} stocks...")

    for i, symbol in enumerate(symbols):
        # Check if we already have recent history (skip if last backfill was today)
        try:
            existing = table.get_item(
                Key={"PK": f"PRICE_HISTORY#{symbol}", "SK": "DAILY"},
                ProjectionExpression="last_backfill",
            ).get("Item", {})

            if existing.get("last_backfill") == today:
                print(f"    {symbol}: already backfilled today, skipping")
                continue
        except Exception:
            pass

        # Fetch from Polygon
        try:
            key = get_polygon_key()
            url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{from_date}/{today}"
            resp = http_requests.get(url, params={"apiKey": key, "adjusted": "true"}, timeout=15)

            if resp.status_code == 200:
                data = resp.json()
                bars = data.get("results", [])
                if bars:
                    # Store as compact list: [{d: "2026-07-01", c: 123.45, v: 1000000}, ...]
                    price_history = []
                    for bar in bars:
                        ts = bar.get("t", 0) / 1000  # ms → seconds
                        bar_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                        price_history.append({
                            "d": bar_date,
                            "o": round(bar.get("o", 0), 2),
                            "h": round(bar.get("h", 0), 2),
                            "l": round(bar.get("l", 0), 2),
                            "c": round(bar.get("c", 0), 2),
                            "v": bar.get("v", 0),
                        })

                    # Write to DynamoDB
                    table.put_item(Item=_to_decimal({
                        "PK": f"PRICE_HISTORY#{symbol}",
                        "SK": "DAILY",
                        "symbol": symbol,
                        "bars": price_history,
                        "bar_count": len(price_history),
                        "from_date": from_date,
                        "to_date": today,
                        "last_backfill": today,
                    }))
                    print(f"    {symbol}: {len(price_history)} daily bars stored")
                else:
                    print(f"    {symbol}: no bars returned")
            else:
                print(f"    {symbol}: Polygon returned {resp.status_code}")
        except Exception as e:
            print(f"    {symbol}: error — {e}")

        # Polygon rate limit: 5 calls/min → 12s pacing
        if i < len(symbols) - 1:
            time.sleep(12.5)


# Risk flag penalties — severe issues get hard score reductions
RISK_FLAG_PENALTIES = {
    "SEC_investigation": -30,
    "fraud_allegation": -35,
    "accounting_irregularity": -25,
    "lawsuit": -10,
    "regulatory_risk": -15,
    "management_departure": -10,
    "product_recall": -10,
    "revenue_risk": -15,
}

# Flags with uncertain/escalating outcomes — penalty persists until flag disappears
UNCERTAIN_FLAGS = {"SEC_investigation", "fraud_allegation", "accounting_irregularity", "lawsuit", "regulatory_risk"}

# Flags for one-time events — penalty decays over 5 days (market prices it in)
ONE_TIME_FLAGS = {"management_departure", "product_recall", "revenue_risk"}

# Days after last_seen before a flag is removed from the ledger entirely
FLAG_EXPIRY_DAYS = 14

# Days over which one-time event penalties decay to zero
DECAY_DAYS = 5


def build_risk_flag_ledger(new_flags: list, existing_ledger: list, today: str) -> list:
    """
    Merge today's detected flags with the existing ledger.

    Rules:
    1. New flag found today that's already in ledger → update last_seen, increment days_active
    2. New flag not in ledger → add with first_seen = article publication date (not today)
    3. Existing flag NOT found today → keep but don't update last_seen
    4. Remove flags where today - last_seen > 14 days (resolved)

    Args:
        new_flags: list of flag dicts with {"flag": str, "article_date": str} from sentiment
                   (or plain strings for backward compat)
        existing_ledger: list of flag dicts from DynamoDB (previous state)
        today: current date string YYYY-MM-DD

    Returns:
        Updated ledger (list of flag dicts)
    """
    from datetime import datetime, timedelta

    today_dt = datetime.strptime(today, "%Y-%m-%d")

    # Index existing ledger by flag name
    ledger_map = {}
    for entry in existing_ledger:
        if isinstance(entry, dict) and "flag" in entry:
            ledger_map[entry["flag"]] = entry
        elif isinstance(entry, str):
            # Migration: old-style string flags → convert to ledger entry
            ledger_map[entry] = {"flag": entry, "first_seen": today, "last_seen": today, "days_active": 1}

    # Process today's flags
    for flag_entry in new_flags:
        # Handle both formats: {"flag": "...", "article_date": "..."} or plain string
        if isinstance(flag_entry, dict):
            flag_name = flag_entry.get("flag", "")
            article_date = flag_entry.get("article_date", "") or today
        else:
            flag_name = flag_entry
            article_date = today

        if not flag_name:
            continue

        if flag_name in ledger_map:
            # Re-confirmed: update last_seen and increment days_active
            ledger_map[flag_name]["last_seen"] = today
            ledger_map[flag_name]["days_active"] = ledger_map[flag_name].get("days_active", 0) + 1
            # Update first_seen if article_date is earlier
            if article_date < ledger_map[flag_name].get("first_seen", today):
                ledger_map[flag_name]["first_seen"] = article_date
        else:
            # New flag — first_seen is the article publication date, not today
            ledger_map[flag_name] = {
                "flag": flag_name,
                "first_seen": article_date,
                "last_seen": today,
                "days_active": 1,
            }

    # Remove expired flags (not seen in 14+ days)
    result = []
    for entry in ledger_map.values():
        last_seen_dt = datetime.strptime(entry["last_seen"], "%Y-%m-%d")
        days_since_last_seen = (today_dt - last_seen_dt).days
        if days_since_last_seen <= FLAG_EXPIRY_DAYS:
            entry["days_since_last_seen"] = days_since_last_seen
            result.append(entry)

    return result


def calculate_penalty_from_ledger(ledger: list, today: str) -> tuple[float, list]:
    """
    Calculate total penalty from the risk flag ledger with time-decay.

    Uncertain/escalating risks (fraud, SEC, accounting): Full penalty persists
    as long as the flag is in the ledger. No decay.

    One-time events (revenue_risk, management, recall): Full penalty on day 0,
    linear decay to 0 over DECAY_DAYS. After that, flag remains informational
    (visible in UI) but contributes no penalty.

    Returns:
        (total_penalty, applied_penalties_list)
    """
    from datetime import datetime

    today_dt = datetime.strptime(today, "%Y-%m-%d")
    total_penalty = 0.0
    applied = []

    for entry in ledger:
        flag = entry["flag"]
        base_penalty = RISK_FLAG_PENALTIES.get(flag, -5)
        first_seen_dt = datetime.strptime(entry["first_seen"], "%Y-%m-%d")
        days_since_first = (today_dt - first_seen_dt).days

        if flag in UNCERTAIN_FLAGS:
            # Full penalty — persists until flag expires from ledger
            penalty = base_penalty
            status = "active"
        elif flag in ONE_TIME_FLAGS:
            # Time-decay: full on day 0, zero after DECAY_DAYS
            if days_since_first >= DECAY_DAYS:
                penalty = 0
                status = "decayed"
            else:
                decay_factor = 1.0 - (days_since_first / DECAY_DAYS)
                penalty = round(base_penalty * decay_factor, 1)
                status = "decaying"
        else:
            # Unknown flag type — treat as one-time with decay
            if days_since_first >= DECAY_DAYS:
                penalty = 0
                status = "decayed"
            else:
                decay_factor = 1.0 - (days_since_first / DECAY_DAYS)
                penalty = round(base_penalty * decay_factor, 1)
                status = "decaying"

        total_penalty += penalty
        applied.append({
            "flag": flag,
            "base_penalty": base_penalty,
            "effective_penalty": penalty,
            "status": status,
            "first_seen": entry["first_seen"],
            "last_seen": entry["last_seen"],
            "days_active": entry.get("days_active", 1),
            "days_since_first": days_since_first,
        })

    return total_penalty, applied


def calculate_investability_score(stock: dict, existing_ledger: list, today: str) -> dict:
    """
    Calculate the final Investability Score for a single stock.

    Combines:
    1. Fundamental score (0-100) — how well it passes value filters
    2. Sentiment score (-1 to +1) — news/market perception
    3. Risk flag penalties — tiered with time-decay for one-time events
    """
    fundamental_score = stock.get("fundamental_score", 0.0)
    sentiment_data = stock.get("sentiment", {})
    sentiment_score = sentiment_data.get("sentiment_score", 0.0)
    sentiment_confidence = sentiment_data.get("confidence", 0.0)

    w_fundamental = float(os.environ.get("FUNDAMENTAL_WEIGHT", "0.7"))
    w_sentiment = float(os.environ.get("SENTIMENT_WEIGHT", "0.3"))

    max_sentiment_bonus = 25.0
    sentiment_adjustment = sentiment_score * max_sentiment_bonus * sentiment_confidence
    base_score = (w_fundamental * fundamental_score) + (w_sentiment * sentiment_adjustment)

    # Build/update risk flag ledger — use risk_flags_with_dates (has article publication dates)
    # Falls back to plain risk_flags list for backward compat
    new_flags_input = sentiment_data.get("risk_flags_with_dates") or sentiment_data.get("risk_flags", [])
    risk_ledger = build_risk_flag_ledger(new_flags_input, existing_ledger, today)

    # Calculate penalty from ledger (with time-decay)
    total_penalty, applied_penalties = calculate_penalty_from_ledger(risk_ledger, today)

    final_score = max(0.0, min(100.0, base_score + total_penalty))

    return {
        **stock,
        "investability_score": round(final_score, 1),
        "risk_ledger": risk_ledger,
        "score_breakdown": {
            "fundamental_score": fundamental_score,
            "fundamental_weighted": round(w_fundamental * fundamental_score, 1),
            "sentiment_score": sentiment_score,
            "sentiment_confidence": sentiment_confidence,
            "sentiment_adjustment": round(sentiment_adjustment, 1),
            "sentiment_weighted": round(w_sentiment * sentiment_adjustment, 1),
            "risk_penalties": applied_penalties,
            "total_penalty": total_penalty,
            "base_score_before_penalty": round(base_score, 1),
        },
    }


def _to_decimal(obj):
    """
    Convert floats to Decimal for DynamoDB (DynamoDB doesn't accept float).
    Also handles None and nested structures.
    """
    if isinstance(obj, float):
        return Decimal(str(round(obj, 6)))
    elif isinstance(obj, dict):
        return {k: _to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_to_decimal(i) for i in obj]
    return obj


def load_existing_risk_ledgers(symbols: list) -> dict:
    """
    Load existing risk_flags (ledger) from DynamoDB for each stock.
    
    This allows the score calculator to merge new flags with existing ones
    rather than replacing them — enabling lifecycle tracking.
    
    Returns: { "LRN": [{"flag": "...", "first_seen": "...", ...}], ... }
    """
    table_name = os.environ.get("DATA_TABLE_NAME")
    if not table_name:
        return {}

    table = dynamodb.Table(table_name)
    ledgers = {}

    for symbol in symbols:
        if not symbol:
            continue
        try:
            resp = table.get_item(
                Key={"PK": f"STOCK#{symbol}", "SK": "LATEST"},
                ProjectionExpression="risk_flags",
            )
            item = resp.get("Item", {})
            flags = item.get("risk_flags", [])
            # Convert Decimal back to float/int for processing
            ledgers[symbol] = _from_decimal(flags) if flags else []
        except Exception:
            ledgers[symbol] = []

    return ledgers


def _from_decimal(obj):
    """Convert DynamoDB Decimals back to Python floats."""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: _from_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_from_decimal(i) for i in obj]
    return obj


def persist_to_dynamodb(scored_stocks: list, today: str):
    """
    Write scored stocks to DynamoDB.

    For each stock, writes:
    1. LATEST item — current state (overwritten each run)
       PK: STOCK#AAPL  SK: LATEST
       Contains: all current scores, fundamentals, sentiment summary

    2. SCORE#date item — historical record (one per day)
       PK: STOCK#AAPL  SK: SCORE#2026-07-12
       Contains: scores + key metrics for that day

    3. TRACKING item — tracking status
       PK: STOCK#AAPL  SK: TRACKING
       Contains: status (ACTIVE/GRACE), first_tracked, last_passed

    This is the data that powers:
    - The dashboard (reads LATEST items)
    - Trend charts (reads SCORE# items over a date range)
    - The tracked stocks list (queries GSI on tracking_status)
    """
    table_name = os.environ.get("DATA_TABLE_NAME")
    if not table_name:
        print("  Warning: DATA_TABLE_NAME not set — skipping persistence")
        return

    table = dynamodb.Table(table_name)
    written = 0

    with table.batch_writer() as batch:
        for stock in scored_stocks:
            symbol = stock.get("symbol", "")
            if not symbol:
                continue

            now_iso = datetime.now(timezone.utc).isoformat()

            # Item 1: LATEST (current state — overwritten each run)
            # Persist ALL screener metrics so the dashboard can display them
            latest_item = {
                "PK": f"STOCK#{symbol}",
                "SK": "LATEST",
                "symbol": symbol,
                "company_name": stock.get("company_name", ""),
                "company_description": stock.get("company_description", ""),
                "logo": stock.get("logo", ""),
                "weburl": stock.get("weburl", ""),
                "sector": stock.get("sector", ""),
                "industry": stock.get("industry", ""),
                "price": stock.get("price"),
                "market_cap": stock.get("market_cap"),
                "investability_score": stock.get("investability_score"),
                "fundamental_score": stock.get("fundamental_score"),
                "sentiment_score": stock.get("sentiment", {}).get("sentiment_score"),
                "sentiment_confidence": stock.get("sentiment", {}).get("confidence"),
                "risk_flags": stock.get("risk_ledger", []),
                "passes_screen": stock.get("passes_screen", False),
                # All screener filter metrics (must match screener-filters.json keys)
                "pe_ratio": stock.get("pe_ratio"),
                "forward_pe": stock.get("forward_pe"),
                "peg_ratio": stock.get("peg_ratio"),
                "price_to_fcf": stock.get("price_to_fcf"),
                "debt_to_equity": stock.get("debt_to_equity"),
                "quick_ratio": stock.get("quick_ratio"),
                "operating_margin": stock.get("operating_margin"),
                "eps_growth_yoy": stock.get("eps_growth_yoy"),
                "revenue_growth_yoy": stock.get("revenue_growth_yoy"),
                "est_lt_growth": stock.get("est_lt_growth"),
                "analyst_recommendation": stock.get("analyst_recommendation"),
                "target_price_upside": stock.get("target_price_upside"),
                "institutional_transactions": stock.get("institutional_transactions"),
                "interest_coverage_ratio": stock.get("interest_coverage_ratio"),
                "sic_industry": stock.get("sic_industry", ""),
                "last_updated": now_iso,
                # GSI attributes for querying by tracking status
                "tracking_status": "ACTIVE" if stock.get("passes_screen") else "GRACE",
            }
            batch.put_item(Item=_to_decimal(
                {k: v for k, v in latest_item.items() if v is not None}
            ))

            # Item 2: Historical score (one per day — never overwritten)
            score_item = {
                "PK": f"STOCK#{symbol}",
                "SK": f"SCORE#{today}",
                "symbol": symbol,
                "date": today,
                "investability_score": stock.get("investability_score"),
                "fundamental_score": stock.get("fundamental_score"),
                "sentiment_score": stock.get("sentiment", {}).get("sentiment_score"),
                "price": stock.get("price"),
                "pe_ratio": stock.get("pe_ratio"),
                "debt_to_equity": stock.get("debt_to_equity"),
                "risk_flags": stock.get("sentiment", {}).get("risk_flags", []),
                "last_updated": now_iso,
            }
            batch.put_item(Item=_to_decimal(
                {k: v for k, v in score_item.items() if v is not None}
            ))

            # Item 3: Tracking status
            tracking_item = {
                "PK": f"STOCK#{symbol}",
                "SK": "TRACKING",
                "symbol": symbol,
                "tracking_status": "ACTIVE" if stock.get("passes_screen") else "GRACE",
                "last_passed": today if stock.get("passes_screen") else None,
                "last_updated": now_iso,
                # GSI attributes
            }
            batch.put_item(Item=_to_decimal(
                {k: v for k, v in tracking_item.items() if v is not None}
            ))

            written += 1

    print(f"  Persisted {written} stocks to DynamoDB ({written * 3} items)")


def handler(event, context):
    """
    Lambda entry point. Called by Step Functions after sentiment-analyzer.

    Input event:
        event["stocks_with_sentiment"] — stocks with fundamental + sentiment data

    Output:
        Ranked list of stocks with investability scores.
    """
    from pipeline_io import read_pipeline_input, write_pipeline_output

    start_time = datetime.now(timezone.utc)
    today = start_time.strftime("%Y-%m-%d")
    print(f"Starting score calculation at {start_time.isoformat()}")

    # Read input from S3 if needed (Step Functions payload limit workaround)
    data = read_pipeline_input(event)

    stocks = data.get("stocks_with_sentiment", [])
    if not stocks:
        return {
            "scored_stocks": [],
            "metadata": {"error": "No stocks provided"},
        }

    print(f"Calculating investability scores for {len(stocks)} stocks...")

    # Load existing risk flag ledgers from DynamoDB (for lifecycle management)
    existing_ledgers = load_existing_risk_ledgers([s.get("symbol", "") for s in stocks])

    # Calculate scores (passing existing ledger for each stock)
    scored = []
    for stock in stocks:
        symbol = stock.get("symbol", "")
        existing_ledger = existing_ledgers.get(symbol, [])
        scored_stock = calculate_investability_score(stock, existing_ledger, today)
        scored.append(scored_stock)

    scored.sort(key=lambda s: s["investability_score"], reverse=True)

    # Categorize
    highly_investable = [s for s in scored if s["investability_score"] >= 70]
    moderately_investable = [s for s in scored if 40 <= s["investability_score"] < 70]
    low_investability = [s for s in scored if s["investability_score"] < 40]

    # Fetch company descriptions from Polygon (only ~6-10 stocks, 5/min is fine)
    print(f"Fetching company descriptions from Polygon for {len(scored)} stocks...")
    for i, stock in enumerate(scored):
        symbol = stock.get("symbol", "")
        if symbol and not stock.get("company_description"):
            desc = fetch_company_description(symbol)
            if desc:
                stock["company_description"] = desc
                print(f"  {symbol}: got description ({len(desc)} chars)")
            # Polygon free: 5 calls/min → 12s pacing
            if i < len(scored) - 1:
                time.sleep(12.5)

    # Enrich with SEC SIC industry labels (for industry comparison matching)
    enrich_with_sic_industry(scored)

    # Backfill 30-day price history from Polygon (for trend detection)
    backfill_price_history(scored, today)

    # Persist to DynamoDB
    persist_to_dynamodb(scored, today)

    # Log top results
    for s in scored[:5]:
        print(f"  {s['symbol']}: investability={s['investability_score']}, "
              f"fundamental={s.get('fundamental_score', 0)}, "
              f"sentiment={s.get('sentiment', {}).get('sentiment_score', 'N/A')}")

    end_time = datetime.now(timezone.utc)

    result = {
        "scored_stocks": scored,
        "summary": {
            "highly_investable": [s["symbol"] for s in highly_investable],
            "moderately_investable": [s["symbol"] for s in moderately_investable],
            "low_investability": [s["symbol"] for s in low_investability],
        },
        "metadata": {
            "total_scored": len(scored),
            "highly_investable_count": len(highly_investable),
            "moderately_investable_count": len(moderately_investable),
            "low_investability_count": len(low_investability),
            "persisted_to_dynamodb": True,
            "weights": {
                "fundamental": float(os.environ.get("FUNDAMENTAL_WEIGHT", "0.7")),
                "sentiment": float(os.environ.get("SENTIMENT_WEIGHT", "0.3")),
            },
            "duration_seconds": (end_time - start_time).total_seconds(),
            "timestamp": end_time.isoformat(),
        },
    }

    print(f"Done. {len(highly_investable)} highly investable, "
          f"{len(moderately_investable)} moderate, "
          f"{len(low_investability)} low.")

    return write_pipeline_output(result, step_name="step7_scores")
