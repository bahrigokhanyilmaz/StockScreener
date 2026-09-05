"""
Market Gate (Step 0)
====================
First step of the pipeline. Decides whether the US stock market is OPEN today.
If it's closed (weekend or market holiday), the state machine short-circuits and
skips the entire pipeline — saving a full run's worth of EDGAR/FMP/Polygon/Bedrock
cost on days that would only re-process stale (unchanged) prices.

Why this exists:
    The EventBridge schedule fires Mon-Fri but has NO awareness of market holidays
    (e.g. Labor Day, Thanksgiving). On those days prices don't advance, so a run
    adds no new market data yet still incurs Bedrock/API cost. This gate stops that.

Signal (data-driven, no hardcoded holiday list):
    1. Weekend check — Saturday/Sunday are always closed.
    2. Polygon `/v1/marketstatus/upcoming` — the authoritative, self-maintaining
       holiday calendar. If today's date appears with status "closed" for a US
       equity exchange (NYSE/NASDAQ), the market is closed.

    A hardcoded holiday list was rejected: it goes stale and violates the
    "no shortcuts / data-driven" principle. Polygon maintains the calendar for us.

Output (consumed by the Step Functions Choice state):
    {"market_open": true|false, "reason": "...", "today": "YYYY-MM-DD"}

Environment Variables:
    POLYGON_API_KEY_PARAM - SSM path for Polygon.io key (default: /stock-screener/polygon-api-key)
"""

import os
from datetime import datetime, timezone

import boto3
import requests

ssm_client = boto3.client("ssm")

POLYGON_UPCOMING_URL = "https://api.polygon.io/v1/marketstatus/upcoming"
US_EQUITY_EXCHANGES = {"NYSE", "NASDAQ"}

_polygon_key = None


def get_polygon_key() -> str:
    global _polygon_key
    if not _polygon_key:
        param = os.environ.get("POLYGON_API_KEY_PARAM", "/stock-screener/polygon-api-key")
        resp = ssm_client.get_parameter(Name=param, WithDecryption=True)
        _polygon_key = resp["Parameter"]["Value"]
    return _polygon_key


def is_market_holiday(today: str, polygon_key: str) -> tuple[bool, str]:
    """
    Return (is_holiday, name) by checking Polygon's upcoming-holidays calendar
    for a US equity exchange closure on `today`.

    Fail-open: if the Polygon call fails, we return (False, ...) so a transient
    API error never silently skips a real trading day. The weekend check still
    runs regardless, so the worst case on API failure is one unnecessary run.
    """
    try:
        resp = requests.get(POLYGON_UPCOMING_URL, params={"apiKey": polygon_key}, timeout=15)
        if resp.status_code != 200:
            return False, f"holiday check unavailable (HTTP {resp.status_code}) — proceeding"
        for entry in resp.json():
            if (
                entry.get("date") == today
                and entry.get("status") == "closed"
                and entry.get("exchange") in US_EQUITY_EXCHANGES
            ):
                return True, entry.get("name", "market holiday")
        return False, "not a holiday"
    except Exception as e:  # noqa: BLE001 — fail open, never block a real trading day on error
        return False, f"holiday check error ({e}) — proceeding"


def handler(event, context):
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    weekday = now.weekday()  # Mon=0 ... Sun=6

    # 1) Weekend — market always closed.
    if weekday >= 5:
        reason = f"weekend ({'Saturday' if weekday == 5 else 'Sunday'})"
        print(f"Market CLOSED: {today} — {reason}. Skipping pipeline.")
        return {"market_open": False, "reason": reason, "today": today}

    # 2) Market holiday — data-driven via Polygon calendar.
    try:
        polygon_key = get_polygon_key()
        holiday, name = is_market_holiday(today, polygon_key)
        if holiday:
            reason = f"market holiday ({name})"
            print(f"Market CLOSED: {today} — {reason}. Skipping pipeline.")
            return {"market_open": False, "reason": reason, "today": today}
        note = name
    except Exception as e:  # noqa: BLE001
        # If we cannot even read the key, fail open — run rather than skip a real day.
        note = f"holiday check skipped ({e})"

    print(f"Market OPEN: {today} — proceeding with pipeline ({note}).")
    return {"market_open": True, "reason": "trading day", "today": today}
