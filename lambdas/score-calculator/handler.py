"""
Score Calculator Lambda
========================
Step 7 in the pipeline.

Combines the fundamental score (from Step 2: stock-screener) with the
sentiment score (from Step 6: sentiment-analyzer) into a single
**Investability Score** per stock.

Also:
- Fetches company descriptions from FMP (fallback for stocks missing from enrichment)
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
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3

# US-Eastern business-date helpers (dependency-free). Business dates
# (first_tracked, SCORE#{date}, last_passed) are keyed on the US market day.
from et_date import eastern_date, eastern_today, days_tracked

# Real New York market-clock helpers: decide whether a daily bar is final.
from estimates import build_estimates_snapshot, has_estimate_data
from market_time import (
    SKIP_SETTLED_AFTER, is_bar_final, latest_final_session_date,
    new_york_date, new_york_dt, parse_utc_iso,
)

# DynamoDB client
dynamodb = boto3.resource("dynamodb")

# SSM client for API keys
ssm_client = boto3.client("ssm")

# Cache
_fmp_key = None


def get_fmp_key() -> str:
    """Get FMP API key from SSM (cached)."""
    global _fmp_key
    if not _fmp_key:
        param = os.environ.get("FMP_API_KEY_PARAM", "/stock-screener/fmp-api-key")
        _fmp_key = ssm_client.get_parameter(Name=param, WithDecryption=True)["Parameter"]["Value"]
    return _fmp_key


def fetch_company_description(symbol: str) -> str:
    """
    Fetch company description from FMP /stable/profile.
    Only called for final passing stocks that don't already have a description
    (enrichment step should have set this, so this is a fallback).
    """
    try:
        import urllib.request
        key = get_fmp_key()
        url = f"https://financialmodelingprep.com/stable/profile?symbol={symbol}&apikey={key}"
        req = urllib.request.Request(url, headers={"User-Agent": "StockScreener/2.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            if data and isinstance(data, list) and data[0]:
                return data[0].get("description", "")
    except Exception as e:
        print(f"  Warning: FMP profile error for {symbol}: {e}")
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


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _fetch_fmp_bars(symbol: str, from_date: str, to_date: str, key: str):
    """One FMP historical-price-eod/full call. Returns the parsed JSON (newest first)."""
    import urllib.request
    url = (f"https://financialmodelingprep.com/stable/historical-price-eod/full"
           f"?symbol={symbol}&from={from_date}&to={to_date}&apikey={key}")
    req = urllib.request.Request(url, headers={"User-Agent": "StockScreener/2.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def normalize_bars(raw) -> tuple:
    """
    Validate FMP rows. Returns (bars_ascending, skipped_count).
    A row needs a YYYY-MM-DD date, numeric open/high/low/close and close > 0.
    """
    if not isinstance(raw, list):
        return [], 0
    bars, skipped = [], 0
    for row in raw:
        try:
            if (not isinstance(row, dict) or not _DATE_RE.match(str(row.get("date", "")))
                    or not all(_is_num(row.get(k)) for k in ("open", "high", "low", "close"))
                    or row["close"] <= 0):
                skipped += 1
                continue
            vol = row.get("volume")
            bars.append({
                "d": row["date"],
                "o": round(row["open"], 2),
                "h": round(row["high"], 2),
                "l": round(row["low"], 2),
                "c": round(row["close"], 2),
                "v": vol if _is_num(vol) else 0,
            })
        except Exception:
            skipped += 1
    bars.sort(key=lambda b: b["d"])
    return bars, skipped


def select_final_bars(bars: list, now_utc) -> list:
    """Keep only bars whose session is complete as of now_utc (ascending by date)."""
    final = [b for b in bars if isinstance(b, dict) and is_bar_final(b.get("d"), now_utc)]
    final.sort(key=lambda b: b["d"])
    return final


def should_skip_backfill(existing: dict, now_utc) -> bool:
    """
    True only when the stored series is final AND was fetched after the vendor
    settle time on the session date, so a re-run needs no FMP call. Legacy items
    (no last_final_date / fetched_at) are always refetched.
    """
    lfd = existing.get("last_final_date")
    fetched = parse_utc_iso(existing.get("fetched_at"))
    if not lfd or fetched is None:
        return False
    if lfd < latest_final_session_date(now_utc):
        return False
    fd = new_york_date(fetched)
    return fd > lfd or (fd == lfd and new_york_dt(fetched).time() >= SKIP_SETTLED_AFTER)


def _bar_close(bar):
    """(date, close) for a stored bar, or None if unusable."""
    try:
        c = float(bar.get("c"))
        d = bar.get("d")
        return (d, c) if d and c > 0 else None
    except (TypeError, ValueError, AttributeError):
        return None


def _fallback_bar(existing: dict, now_utc):
    """
    Newest trustworthy stored bar. Items with last_final_date were written by the
    final-bars-only code and are trusted. Legacy items may hold a partial bar on
    their last run date, so bars on/after that date are never re-adopted.
    """
    bars = [b for b in (existing.get("bars") or []) if isinstance(b, dict)]
    if not existing.get("last_final_date"):
        cutoff = existing.get("last_backfill") or new_york_date(now_utc)
        bars = [b for b in bars if str(b.get("d", "")) < cutoff]
    final = select_final_bars(bars, now_utc)
    return _bar_close(final[-1]) if final else None


def backfill_price_history(scored_stocks: list, today: str, now_utc=None):
    """
    Maintain PRICE_HISTORY#{ticker} (30-day OHLCV) and sync each stock's canonical
    price from it.

    Only FINAL bars are stored (see market_time.is_bar_final), so every consumer
    can treat the stored series as complete. price = close of the newest final
    bar, price_as_of = that bar's date, and market_cap = shares_implied * price
    when shares_implied is known.

    FMP /stable/historical-price-eod/full: 300 calls/min (1s pacing is very safe).
    Returns {symbol: close} for symbols that have a canonical price.
    """
    table_name = os.environ.get("DATA_TABLE_NAME", "")
    if not table_name:
        return {}

    from datetime import timedelta

    def clock():
        return now_utc if now_utc is not None else datetime.now(timezone.utc)

    table = dynamodb.Table(table_name)
    ny_today = new_york_date(clock())
    from_date = (datetime.strptime(ny_today, "%Y-%m-%d") - timedelta(days=45)).strftime("%Y-%m-%d")

    symbols = [s.get("symbol", "") for s in scored_stocks if s.get("symbol")]
    print(f"  Backfilling 30-day price history for {len(symbols)} stocks...")

    fmp_key = get_fmp_key()

    # symbol -> (bar_date, close) of the newest final bar. Recorded only once the
    # bar is durably stored (or already stored), so price never runs ahead of the series.
    canonical = {}

    for i, symbol in enumerate(symbols):
        existing = {}
        try:
            existing = table.get_item(
                Key={"PK": f"PRICE_HISTORY#{symbol}", "SK": "DAILY"},
                ProjectionExpression="last_final_date, fetched_at, last_backfill, bars",
            ).get("Item", {})
        except Exception as e:
            print(f"    {symbol}: WARN could not read existing history ({type(e).__name__})")

        if should_skip_backfill(existing, clock()):
            print(f"    {symbol}: final bars already stored, skipping")
            bars_existing = [b for b in (existing.get("bars") or []) if isinstance(b, dict)]
            bc = _bar_close(bars_existing[-1]) if bars_existing else None
            if bc:
                canonical[symbol] = bc
            continue

        stored = False
        try:
            raw = _fetch_fmp_bars(symbol, from_date, ny_today, fmp_key)
            fetched_at = clock().strftime("%Y-%m-%dT%H:%M:%SZ")
            bars, skipped = normalize_bars(raw)
            final = select_final_bars(bars, clock())
            if skipped:
                print(f"    {symbol}: skipped {skipped} invalid bar(s)")
            if final:
                newest = final[-1]["d"]
                table.put_item(Item=_to_decimal({
                    "PK": f"PRICE_HISTORY#{symbol}",
                    "SK": "DAILY",
                    "symbol": symbol,
                    "bars": final,
                    "bar_count": len(final),
                    "from_date": from_date,
                    "to_date": newest,
                    "last_final_date": newest,
                    "fetched_at": fetched_at,
                }))
                canonical[symbol] = (newest, final[-1]["c"])
                stored = True
                print(f"    {symbol}: {len(final)} final daily bars stored")
            else:
                print(f"    {symbol}: no final bars returned")
        except Exception as e:
            # Never log the message: urllib/requests errors can embed the URL (API key).
            print(f"    {symbol}: error — {type(e).__name__}")

        if not stored:
            fb = _fallback_bar(existing, clock())
            if fb:
                canonical[symbol] = fb

        # FMP rate limit: 300 calls/min → 1s pacing is very safe
        if i < len(symbols) - 1:
            time.sleep(1)

    # Sync price / price_as_of / market_cap so Price, "Since Mark" and "Daily"
    # all derive from the SAME final-bar series.
    for s in scored_stocks:
        bc = canonical.get(s.get("symbol"))
        if bc is None:
            continue
        bar_date, close = bc
        s["price"] = close
        s["price_as_of"] = bar_date
        shares = s.get("shares_implied")
        if _is_num(shares) and shares > 0:
            s["market_cap"] = round(shares * close)
    return {sym: c for sym, (_, c) in canonical.items()}


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

    Formula: (0.6 × fundamental) + (0.25 × sentiment_normalized) + (0.15 × competition_normalized) + risk_penalties

    All components on 0-100 scale:
    - Fundamental: 0 = barely passed, 100 = crushed every filter
    - Sentiment normalized: 0 = worst possible, 50 = neutral, 100 = best possible
      Blended toward neutral (50) when confidence is low:
      sentiment_normalized = 50 + (raw_sentiment × 50 × confidence)
    - Competition normalized: 1 (high competition) → 0, 5 (no competition) → 100
      Inverted: lower competition = higher score contribution

    This ensures:
    - Max possible (before penalties): (0.6 × 100) + (0.25 × 100) + (0.15 × 100) = 100
    - Neutral sentiment, perfect fundamentals, avg competition: (0.6 × 100) + (0.25 × 50) + (0.15 × 50) = 80
    - Min possible: 0
    """
    fundamental_score = stock.get("fundamental_score", 0.0)
    sentiment_data = stock.get("sentiment", {})
    sentiment_score = sentiment_data.get("sentiment_score", 0.0)
    sentiment_confidence = sentiment_data.get("confidence", 0.0)
    competition_data = stock.get("competition", {})
    competition_score = competition_data.get("competition_score", 3)  # Default 3 (middle)

    w_fundamental = float(os.environ.get("FUNDAMENTAL_WEIGHT", "0.6"))
    w_sentiment = float(os.environ.get("SENTIMENT_WEIGHT", "0.25"))
    w_competition = float(os.environ.get("COMPETITION_WEIGHT", "0.15"))

    # Normalize sentiment to 0-100 scale, blended toward neutral by confidence
    # raw=-1,conf=1 → 0 | raw=0 or conf=0 → 50 | raw=+1,conf=1 → 100
    sentiment_normalized = 50 + (sentiment_score * 50 * sentiment_confidence)
    sentiment_normalized = max(0.0, min(100.0, sentiment_normalized))

    # Normalize competition to 0-100 (inverted: 1=dominant→100, 5=fragmented→0)
    competition_normalized = (5 - competition_score) * 25.0  # 1→100, 2→75, 3→50, 4→25, 5→0

    base_score = (w_fundamental * fundamental_score) + (w_sentiment * sentiment_normalized) + (w_competition * competition_normalized)

    # Build/update risk flag ledger — use risk_flags_with_dates (has article publication dates)
    # Falls back to plain risk_flags list for backward compat
    new_flags_input = sentiment_data.get("risk_flags_with_dates") or sentiment_data.get("risk_flags", [])
    risk_ledger = build_risk_flag_ledger(new_flags_input, existing_ledger, today)

    # Calculate penalty from ledger (with time-decay)
    total_penalty, applied_penalties = calculate_penalty_from_ledger(risk_ledger, today)

    # Revenue-acceleration-underpriced bonus: forward rev growth > trailing AND
    # cheap vs industry on P/S. A flat +5 reward (mirrors how risk flags apply a
    # flat penalty).
    rev_accel_bonus = 5.0 if stock.get("revenue_accel_underpriced") else 0.0

    final_score = max(0.0, min(100.0, base_score + total_penalty + rev_accel_bonus))

    return {
        **stock,
        "investability_score": round(final_score, 1),
        "risk_ledger": risk_ledger,
        "score_breakdown": {
            "fundamental_score": fundamental_score,
            "fundamental_weighted": round(w_fundamental * fundamental_score, 1),
            "sentiment_score": sentiment_score,
            "sentiment_confidence": sentiment_confidence,
            "sentiment_normalized": round(sentiment_normalized, 1),
            "sentiment_weighted": round(w_sentiment * sentiment_normalized, 1),
            "competition_score": competition_score,
            "competition_normalized": round(competition_normalized, 1),
            "competition_weighted": round(w_competition * competition_normalized, 1),
            "risk_penalties": applied_penalties,
            "total_penalty": total_penalty,
            "rev_accel_bonus": rev_accel_bonus,
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


def _earliest_score_date(table, symbol: str):
    """
    Return the earliest SCORE#{date} for a stock, or None if it has no history.

    SCORE# items are immutable (one per run, never overwritten), so the earliest
    one is the true date the stock first entered tracking. Used as a self-healing
    fallback for first_tracked when the TRACKING item's value is missing — so a
    transient read gap can never reset a continuously-tracked stock to "today".
    """
    from boto3.dynamodb.conditions import Key
    try:
        resp = table.query(
            KeyConditionExpression=Key("PK").eq(f"STOCK#{symbol}") & Key("SK").begins_with("SCORE#"),
            ProjectionExpression="SK",
            ScanIndexForward=True,   # ascending -> earliest first
            Limit=1,
        )
        items = resp.get("Items", [])
        if items:
            return items[0]["SK"].replace("SCORE#", "")
    except Exception:
        pass
    return None


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

    # Load existing first_tracked dates + manual-mark snapshots from TRACKING items.
    # These must be preserved across pipeline runs, otherwise the daily overwrite
    # would wipe the user's manual mark or reset the "Days" column.
    #
    # first_tracked resolution is HARDENED so a transient/missing TRACKING read
    # can never reset the date to today: if the TRACKING item lacks first_tracked,
    # we fall back to the stock's EARLIEST immutable SCORE#{date} snapshot (the
    # true first-tracked date, written every run and never overwritten). Only if
    # there is genuinely no history do we treat the stock as new (today).
    first_tracked_dates = {}
    mark_snapshots = {}  # symbol -> {"mark_price", "mark_date"}
    prev_tracking = {}   # symbol -> {"last_passed", "grace_start", "tracking_status"}
    for stock in scored_stocks:
        symbol = stock.get("symbol", "")
        if not symbol:
            continue
        try:
            resp = table.get_item(
                Key={"PK": f"STOCK#{symbol}", "SK": "TRACKING"},
                ProjectionExpression="first_tracked, mark_price, mark_date, last_passed, grace_start, tracking_status",
            )
            existing_tracking = resp.get("Item", {})
            ft = existing_tracking.get("first_tracked")
            if not ft:
                # Fallback: earliest immutable SCORE# snapshot = true first-tracked.
                ft = _earliest_score_date(table, symbol)
            if ft:
                first_tracked_dates[symbol] = ft
            if existing_tracking.get("mark_price") is not None or existing_tracking.get("mark_date"):
                mark_snapshots[symbol] = {
                    "mark_price": existing_tracking.get("mark_price"),
                    "mark_date": existing_tracking.get("mark_date"),
                }
            prev_tracking[symbol] = {
                "last_passed": existing_tracking.get("last_passed"),
                "grace_start": existing_tracking.get("grace_start"),
                "tracking_status": existing_tracking.get("tracking_status"),
            }
        except Exception:
            pass

    with table.batch_writer() as batch:
        for stock in scored_stocks:
            symbol = stock.get("symbol", "")
            if not symbol:
                continue

            now_iso = datetime.now(timezone.utc).isoformat()
            first_tracked = first_tracked_dates.get(symbol, today)

            # Estimate snapshot: always a full map (None fields when never enriched).
            # estimates_as_of is the New York date of the FMP fetch, same calendar
            # as price_as_of; a carried-over snapshot therefore shows as stale.
            snapshot = stock.get("estimates") or build_estimates_snapshot(None, None, None)
            fetched_dt = parse_utc_iso(snapshot.get("fetched_at"))
            estimates_as_of = new_york_date(fetched_dt) if fetched_dt else None

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
                "price_as_of": stock.get("price_as_of"),
                "market_cap": stock.get("market_cap"),
                "shares_implied": stock.get("shares_implied"),
                "estimates": snapshot,
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
                "operating_income_growth_yoy": stock.get("operating_income_growth_yoy"),
                "revenue_growth_yoy": stock.get("revenue_growth_yoy"),
                "est_lt_growth": stock.get("est_lt_growth"),
                "est_lt_revenue_growth": stock.get("est_lt_revenue_growth"),
                "price_to_sales": stock.get("price_to_sales"),
                "revenue_accel_underpriced": stock.get("revenue_accel_underpriced", False),
                "analyst_count": stock.get("analyst_count"),
                "analyst_recommendation": stock.get("analyst_recommendation"),
                "target_price_upside": stock.get("target_price_upside"),
                "analyst_target_price": stock.get("analyst_target_price"),
                "institutional_transactions": stock.get("institutional_transactions"),
                "interest_coverage_ratio": stock.get("interest_coverage_ratio"),
                "sic_industry": stock.get("sic_industry", ""),
                "hhi_score": stock.get("competition", {}).get("hhi_score"),
                "competition_score": stock.get("competition", {}).get("competition_score"),
                "competition_reasoning": stock.get("competition", {}).get("competition_reasoning", ""),
                "ai_threat": stock.get("competition", {}).get("ai_threat", ""),
                "first_tracked": first_tracked,
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
                "price_as_of": stock.get("price_as_of"),
                "estimates": snapshot,
                "estimates_as_of": estimates_as_of,
                "pe_ratio": stock.get("pe_ratio"),
                "debt_to_equity": stock.get("debt_to_equity"),
                "risk_flags": stock.get("sentiment", {}).get("risk_flags", []),
                "last_updated": now_iso,
            }
            batch.put_item(Item=_to_decimal(
                {k: v for k, v in score_item.items() if v is not None}
            ))

            # Item 3: Tracking status
            passes = stock.get("passes_screen")
            prev = prev_tracking.get(symbol, {})

            # Durable grace anchor. The 30-day removal clock counts from grace_start.
            #  - Passing: clears the anchor and stamps last_passed = today.
            #  - Failing (GRACE): preserve the existing grace_start if present; if
            #    this is the first failing run, stamp grace_start = today. NEVER
            #    overwrite with None (the old bug that made GRACE stocks immortal).
            if passes:
                last_passed = today
                grace_start = None
            else:
                last_passed = prev.get("last_passed")  # keep the real last-pass date
                grace_start = prev.get("grace_start") or today

            tracking_item = {
                "PK": f"STOCK#{symbol}",
                "SK": "TRACKING",
                "symbol": symbol,
                "tracking_status": "ACTIVE" if passes else "GRACE",
                "first_tracked": first_tracked,
                "last_passed": last_passed,
                "grace_start": grace_start,
                "last_updated": now_iso,
                # GSI attributes
            }
            # Preserve the user's manual-mark snapshot (mark_price/mark_date) across
            # the daily overwrite. Without this, the pipeline would wipe the mark and
            # "Since Mark" would reset every run.
            snap = mark_snapshots.get(symbol)
            if snap:
                if snap.get("mark_price") is not None:
                    tracking_item["mark_price"] = snap["mark_price"]
                if snap.get("mark_date"):
                    tracking_item["mark_date"] = snap["mark_date"]
            batch.put_item(Item=_to_decimal(
                {k: v for k, v in tracking_item.items() if v is not None}
            ))

            # Item 4: Analyzed articles (with per-article sentiment + risk flags)
            articles = stock.get("articles", [])
            if articles:
                # Store compact version: title, url, source, published_at, sentiment, risk_flags
                analyzed_articles = []
                for article in articles[:10]:  # Cap at 10
                    analysis = article.get("analysis", {})
                    analyzed_articles.append({
                        "title": article.get("title", ""),
                        "url": article.get("url", ""),
                        "source": article.get("source", ""),
                        "published_at": article.get("published_at", 0),
                        "sentiment": analysis.get("sentiment", 0),
                        "confidence": analysis.get("confidence", 0),
                        "relevant": analysis.get("relevant", True),
                        "risk_flags": analysis.get("risk_flags", []),
                        "summary": analysis.get("summary", ""),
                    })
                articles_item = {
                    "PK": f"STOCK#{symbol}",
                    "SK": "ARTICLES",
                    "symbol": symbol,
                    "articles": analyzed_articles,
                    "article_count": len(analyzed_articles),
                    "last_updated": now_iso,
                }
                batch.put_item(Item=_to_decimal(articles_item))

            written += 1

    print(f"  Persisted {written} stocks to DynamoDB ({written * 3} items)")


def compute_portfolio_signals(scored_stocks: list, today: str):
    """
    Compute hold/sell signals for stocks the user owns (has PORTFOLIO# items).

    Signal logic:
    - GREEN (hold): no negative signals, stock is ACTIVE, all clear
    - YELLOW (watch): stock in GRACE, or sentiment trending negative, or minor risk flag
    - RED (consider sell): serious risk flag, sentiment < -0.3, trailing stop hit,
                           or in GRACE with deteriorating fundamentals

    Updates the PORTFOLIO#{ticker} SUMMARY item with signal + reasons.
    """
    table_name = os.environ.get("DATA_TABLE_NAME", "")
    if not table_name:
        return

    table = dynamodb.Table(table_name)

    # Find which scored stocks have portfolio positions
    scored_by_symbol = {s.get("symbol", ""): s for s in scored_stocks}

    # Scan for all PORTFOLIO SUMMARY items
    from boto3.dynamodb.conditions import Attr
    result = table.scan(
        FilterExpression=Attr("PK").begins_with("PORTFOLIO#") & Attr("SK").eq("SUMMARY"),
    )

    portfolio_items = result.get("Items", [])
    if not portfolio_items:
        return

    print(f"  Computing signals for {len(portfolio_items)} portfolio positions...")

    from decimal import Decimal

    SERIOUS_FLAGS = {"fraud_allegation", "SEC_investigation", "accounting_irregularity"}
    MINOR_FLAGS = {"revenue_risk", "product_recall", "management_departure", "regulatory_risk", "lawsuit"}

    for position in portfolio_items:
        symbol = position.get("symbol", "")
        stock = scored_by_symbol.get(symbol)
        if not stock:
            continue

        avg_cost = float(position.get("avg_cost_basis", 0))
        current_price = stock.get("price")
        tracking_status = stock.get("tracking_status", "ACTIVE")
        sentiment = stock.get("sentiment", {})
        sentiment_score = sentiment.get("sentiment_score", 0) if isinstance(sentiment, dict) else 0
        risk_ledger = stock.get("risk_ledger", [])
        risk_flag_names = {f.get("flag", f) if isinstance(f, dict) else f for f in risk_ledger}

        # Track peak price since purchase for trailing stop
        peak_price = float(position.get("peak_price", 0))
        if current_price and current_price > peak_price:
            peak_price = current_price

        # Determine signal
        signal = "green"
        reasons = []

        # RED conditions
        if risk_flag_names & SERIOUS_FLAGS:
            signal = "red"
            reasons.append(f"Serious risk: {risk_flag_names & SERIOUS_FLAGS}")

        if sentiment_score < -0.3:
            signal = "red"
            reasons.append(f"Sentiment crashed: {sentiment_score:.2f}")

        if current_price and peak_price > 0 and avg_cost > 0:
            drop_from_peak = (peak_price - current_price) / peak_price
            if drop_from_peak >= 0.15:
                signal = "red"
                reasons.append(f"Trailing stop: -{drop_from_peak*100:.0f}% from peak ${peak_price:.2f}")

        if tracking_status == "GRACE" and sentiment_score < 0:
            signal = "red"
            reasons.append("In GRACE with negative sentiment")

        # YELLOW conditions (only if not already red)
        if signal == "green":
            if tracking_status == "GRACE":
                signal = "yellow"
                reasons.append("Stock in GRACE (no longer passing filters)")

            if sentiment_score < 0 and sentiment_score >= -0.3:
                signal = "yellow"
                reasons.append(f"Sentiment negative: {sentiment_score:.2f}")

            if risk_flag_names & MINOR_FLAGS:
                signal = "yellow"
                reasons.append(f"Minor risk flag: {risk_flag_names & MINOR_FLAGS}")

        if not reasons:
            reasons.append("All clear")

        # Update the SUMMARY item with signal + peak price
        table.update_item(
            Key={"PK": f"PORTFOLIO#{symbol}", "SK": "SUMMARY"},
            UpdateExpression="SET signal = :sig, signal_reasons = :reasons, peak_price = :peak, last_signal_update = :d",
            ExpressionAttributeValues={
                ":sig": signal,
                ":reasons": reasons,
                ":peak": Decimal(str(round(peak_price, 2))),
                ":d": today,
            },
        )

    print(f"  Portfolio signals updated")


def backfill_from_latest(stocks: list):
    """
    For tracked stocks that bypassed Steps 2-4 (came directly from news→sentiment),
    backfill their fundamental_score, metrics, estimate snapshot and shares_implied
    from their existing DynamoDB LATEST item.
    """
    table_name = os.environ.get("DATA_TABLE_NAME", "")
    if table_name:
        _table = dynamodb.Table(table_name)
        for stock in stocks:
            if stock.get("fundamental_score") is None:
                symbol = stock.get("symbol", "")
                try:
                    resp = _table.get_item(
                        Key={"PK": f"STOCK#{symbol}", "SK": "LATEST"},
                        ProjectionExpression="fundamental_score, pe_ratio, peg_ratio, price_to_fcf, "
                                            "debt_to_equity, quick_ratio, operating_margin, "
                                            "revenue_growth_yoy, eps_growth_yoy, est_lt_growth, "
                                            "est_lt_revenue_growth, "
                                            "forward_pe, interest_coverage_ratio, analyst_recommendation, "
                                            "target_price_upside, analyst_target_price, market_cap, "
                                            "company_description, logo, weburl, sector, industry, "
                                            "estimates, shares_implied",
                    )
                    existing = resp.get("Item", {})
                    if existing:
                        for key, val in existing.items():
                            if key not in ("PK", "SK") and stock.get(key) is None:
                                if isinstance(val, (dict, list)):
                                    stock[key] = _from_decimal(val)  # nested Decimals break the Lambda JSON response
                                else:
                                    stock[key] = float(val) if isinstance(val, (int, float)) or (hasattr(val, 'is_finite') and val.is_finite()) else val
                        print(f"    Backfilled {symbol} from DynamoDB (fundamental_score={existing.get('fundamental_score')})")
                except Exception as e:
                    print(f"    Warning: Could not backfill {symbol}: {e}")
        _carry_forward_enrichment(_table, stocks)


def _carry_forward_enrichment(table, stocks: list):
    """
    LATEST is rewritten whole each run, so a stock whose FMP profile/estimates
    fetch failed today (no shares_implied / empty snapshot) would lose the values
    stored yesterday. Carry them forward instead; estimates_as_of keeps showing
    the snapshot's real age.
    """
    for stock in stocks:
        symbol = stock.get("symbol", "")
        if not symbol or (stock.get("shares_implied") is not None
                          and has_estimate_data(stock.get("estimates"))):
            continue
        try:
            prev = table.get_item(
                Key={"PK": f"STOCK#{symbol}", "SK": "LATEST"},
                ProjectionExpression="shares_implied, estimates",
            ).get("Item", {})
        except Exception as e:
            print(f"    Warning: Could not carry forward {symbol}: {type(e).__name__}")
            continue
        if stock.get("shares_implied") is None and prev.get("shares_implied") is not None:
            stock["shares_implied"] = float(prev["shares_implied"])
        prev_est = _from_decimal(prev.get("estimates"))
        if not has_estimate_data(stock.get("estimates")) and has_estimate_data(prev_est):
            stock["estimates"] = prev_est


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
    today = eastern_today()  # US market calendar date (not UTC)
    print(f"Starting score calculation at {start_time.isoformat()} (ET date {today})")

    # Read input from S3 if needed (Step Functions payload limit workaround)
    data = read_pipeline_input(event)

    stocks = data.get("stocks_with_sentiment", [])
    if not stocks:
        return {
            "scored_stocks": [],
            "metadata": {"error": "No stocks provided"},
        }

    print(f"Calculating investability scores for {len(stocks)} stocks...")

    backfill_from_latest(stocks)

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

    # Fetch company descriptions (FMP profile — only for stocks missing description)
    # Enrichment step should have set this, but fallback if not
    missing_desc = [s for s in scored if not s.get("company_description")]
    if missing_desc:
        print(f"Fetching company descriptions from FMP for {len(missing_desc)} stocks...")
        for i, stock in enumerate(missing_desc):
            symbol = stock.get("symbol", "")
            if symbol:
                desc = fetch_company_description(symbol)
                if desc:
                    stock["company_description"] = desc
                    print(f"  {symbol}: got description ({len(desc)} chars)")
                # FMP: 300 calls/min → 1s pacing
                if i < len(missing_desc) - 1:
                    time.sleep(1)
    else:
        print("All stocks have descriptions from enrichment step")

    # Enrich with SEC SIC industry labels (for industry comparison matching)
    enrich_with_sic_industry(scored)

    # Backfill 30-day price history from FMP (for trend detection)
    backfill_price_history(scored, today)

    # Persist to DynamoDB
    persist_to_dynamodb(scored, today)

    # Compute portfolio signals for owned stocks
    compute_portfolio_signals(scored, today)

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
