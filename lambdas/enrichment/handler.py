"""
Price & Metrics Enrichment Lambda
==================================
Step 3 in the pipeline.

Optimized 3-stage funnel that minimizes external API calls:

Stage 1 — Bulk data (0 per-symbol calls):
  - Polygon Grouped Daily: 1 API call → prices for ALL 12,000+ stocks
  - EDGAR data: already in the input from Step 1 (EPS, D/E, QR, OpMargin)

Stage 2 — Local compute & pre-filter (in-memory, milliseconds):
  - Calculate P/E locally: Price ÷ EPS (no API needed)
  - Apply hard filters: P/E < 50, D/E < 1, QR > 1, OpMargin > 0
  - ~232 → ~50-80 survivors

Stage 3 — External enrichment (only for survivors):
  - Finnhub /stock/metric → PEG, Forward P/E, LT Growth, EPS Growth, Revenue Growth
  - Finnhub /stock/price-target → Analyst target price
  - 2 calls per survivor × ~50-80 stocks = ~100-160 total Finnhub calls
  - At 3s pacing = ~5-8 minutes (safe under 60/min limit)

Total API calls per run:
  - Polygon: 1
  - Finnhub: ~100-160 (only for pre-filtered candidates)

Environment Variables:
    POLYGON_API_KEY_PARAM  - SSM path for Polygon.io key
    FINNHUB_API_KEY_PARAM  - SSM path for Finnhub key
    RAW_DATA_BUCKET        - S3 bucket for pipeline I/O
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta

import boto3
import requests as http_requests

# AWS
ssm_client = boto3.client("ssm")

# API URLs
POLYGON_GROUPED_URL = "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks"
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

# Cache
_polygon_key = None
_finnhub_key = None


def get_ssm_param(param_name: str) -> str:
    response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
    return response["Parameter"]["Value"]


def get_polygon_key() -> str:
    global _polygon_key
    if not _polygon_key:
        _polygon_key = get_ssm_param(os.environ["POLYGON_API_KEY_PARAM"])
    return _polygon_key


def get_finnhub_key() -> str:
    global _finnhub_key
    if not _finnhub_key:
        _finnhub_key = get_ssm_param(os.environ["FINNHUB_API_KEY_PARAM"])
    return _finnhub_key


def get_last_trading_day() -> str:
    today = datetime.now(timezone.utc).date()
    if datetime.now(timezone.utc).hour < 21:
        today = today - timedelta(days=1)
    while today.weekday() >= 5:
        today = today - timedelta(days=1)
    return today.strftime("%Y-%m-%d")


# ==========================================
# STAGE 1: Bulk price fetch (1 API call)
# ==========================================

def fetch_all_prices(polygon_key: str, date: str) -> dict[str, float]:
    """ONE Polygon call → prices for all 12,000+ US stocks."""
    url = f"{POLYGON_GROUPED_URL}/{date}"
    response = http_requests.get(url, params={"apiKey": polygon_key}, timeout=30)
    if response.status_code != 200:
        print(f"  Warning: Polygon returned {response.status_code}")
        return {}
    data = response.json()
    return {item["T"]: item["c"] for item in data.get("results", []) if "T" in item and "c" in item}


# ==========================================
# STAGE 2: Local compute & pre-filter
# ==========================================

def local_prefilter(stocks: list, prices: dict) -> tuple[list, list]:
    """
    Calculate P/E locally and apply hard filters.
    Returns (candidates_for_finnhub, all_stocks_with_price).
    """
    all_enriched = []
    candidates = []

    for stock in stocks:
        symbol = stock.get("symbol", "")
        price = prices.get(symbol)

        if price:
            stock["price"] = price

            # Calculate P/E locally: Price ÷ EPS (from EDGAR)
            eps = stock.get("eps")
            if eps and eps > 0:
                stock["pe_ratio"] = round(price / eps, 2)

        all_enriched.append(stock)

        # Pre-filter: only call Finnhub for stocks that could pass
        pe = stock.get("pe_ratio")
        de = stock.get("debt_to_equity")
        qr = stock.get("quick_ratio")
        om = stock.get("operating_margin")

        passes_prefilter = (
            price is not None
            and pe is not None and pe < 50
            and de is not None and de < 1
            and qr is not None and qr > 1
            and om is not None and om > 0
        )

        if passes_prefilter:
            candidates.append(stock)

    return candidates, all_enriched


# ==========================================
# STAGE 3: Finnhub enrichment (only candidates)
# ==========================================

def fetch_finnhub_metrics(symbol: str, key: str) -> dict:
    """Fetch 133 fundamental metrics from Finnhub. 1 API call."""
    url = f"{FINNHUB_BASE_URL}/stock/metric"
    try:
        response = http_requests.get(
            url, params={"symbol": symbol, "metric": "all", "token": key}, timeout=10
        )
        if response.status_code == 200:
            return response.json().get("metric", {})
        elif response.status_code == 429:
            print(f"    Rate limited on {symbol}")
            time.sleep(5)
    except Exception as e:
        print(f"    Finnhub error for {symbol}: {e}")
    return {}


def fetch_finnhub_price_target(symbol: str, key: str) -> dict:
    """Fetch analyst price target consensus. 1 API call."""
    url = f"{FINNHUB_BASE_URL}/stock/price-target"
    try:
        response = http_requests.get(
            url, params={"symbol": symbol, "token": key}, timeout=10
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return {}


def enrich_with_finnhub(stock: dict, metrics: dict, target: dict) -> dict:
    """Apply Finnhub data to a stock that passed the local pre-filter."""
    price = stock.get("price", 0)

    # PEG: P/E ÷ EPS growth rate
    pe = stock.get("pe_ratio")
    eps_growth = metrics.get("epsGrowthTTMYoy") or metrics.get("epsGrowth5Y") or metrics.get("epsGrowth3Y")
    if pe and eps_growth and eps_growth > 0:
        stock["peg_ratio"] = round(pe / eps_growth, 2)

    # Forward P/E
    stock["forward_pe"] = metrics.get("peNormalizedAnnual") or metrics.get("peExclExtraAnnual")

    # Price/FCF from Finnhub (more accurate than EDGAR-derived)
    stock["price_to_fcf"] = metrics.get("pfcfShareTTM")

    # Growth metrics (Finnhub returns as percentage, convert to decimal)
    eps_g = metrics.get("epsGrowthTTMYoy") or metrics.get("epsGrowth3Y")
    rev_g = metrics.get("revenueGrowthTTMYoy")
    lt_g = metrics.get("epsGrowth5Y") or metrics.get("epsGrowth3Y")
    stock["eps_growth_yoy"] = eps_g / 100.0 if eps_g else None
    stock["revenue_growth_yoy"] = rev_g / 100.0 if rev_g else None
    stock["est_lt_growth"] = lt_g / 100.0 if lt_g else None

    # Analyst target price + upside
    target_mean = target.get("targetMean") or target.get("targetMedian")
    if target_mean and price and price > 0:
        stock["analyst_target_price"] = target_mean
        stock["target_price_upside"] = (target_mean - price) / price

    # Market cap from Finnhub (more current than EDGAR)
    stock["market_cap"] = metrics.get("marketCapitalization")
    if stock["market_cap"]:
        stock["market_cap"] = stock["market_cap"] * 1_000_000  # Finnhub returns in millions

    # Sector/Industry
    # (Finnhub metric endpoint doesn't include these — keep from EDGAR if available)

    return stock


# ==========================================
# HANDLER
# ==========================================

def handler(event, context):
    from pipeline_io import read_pipeline_input, write_pipeline_output

    start_time = datetime.now(timezone.utc)
    print(f"Starting enrichment at {start_time.isoformat()}")

    # Read pre-screened stocks from S3
    data = read_pipeline_input(event)
    passing = data.get("passing_stocks", [])

    if not passing:
        return write_pipeline_output(
            {"enriched_stocks": [], "metadata": {"error": "No stocks provided"}},
            step_name="step3_enriched"
        )

    print(f"Input: {len(passing)} stocks from pre-screen")

    # STAGE 1: Bulk prices from Polygon (1 API call)
    polygon_key = get_polygon_key()
    trading_date = get_last_trading_day()
    print(f"  Stage 1: Polygon grouped daily for {trading_date}...")
    all_prices = fetch_all_prices(polygon_key, trading_date)
    print(f"  Got {len(all_prices)} prices")

    # STAGE 2: Local P/E calculation + pre-filter (zero API calls)
    print(f"  Stage 2: Local P/E + pre-filter...")
    candidates, all_enriched = local_prefilter(passing, all_prices)
    print(f"  Pre-filter: {len(candidates)} candidates for Finnhub (from {len(passing)})")

    # STAGE 3: Finnhub for candidates only (2 calls per stock, 3s pacing)
    finnhub_key = get_finnhub_key()
    finnhub_enriched = 0
    print(f"  Stage 3: Finnhub enrichment for {len(candidates)} stocks...")

    for i, stock in enumerate(candidates):
        symbol = stock.get("symbol", "")

        metrics = fetch_finnhub_metrics(symbol, finnhub_key)
        time.sleep(1)  # 1s between the 2 calls for same stock
        target = fetch_finnhub_price_target(symbol, finnhub_key)

        if metrics:
            enrich_with_finnhub(stock, metrics, target)
            finnhub_enriched += 1

        if (i + 1) % 25 == 0:
            print(f"    [{i+1}/{len(candidates)}] enriched {finnhub_enriched}")

        # Pacing: 2 calls done, wait before next stock.
        # 2 calls + 1s internal + 2s here = ~3s per stock = 40 calls/min
        if i < len(candidates) - 1:
            time.sleep(2)

    # Output: return ALL stocks (enriched candidates + non-candidates with just price/P/E)
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    pe_count = sum(1 for s in all_enriched if s.get("pe_ratio") is not None)
    peg_count = sum(1 for s in all_enriched if s.get("peg_ratio") is not None)

    result = {
        "enriched_stocks": all_enriched,
        "metadata": {
            "total_stocks": len(passing),
            "prices_matched": sum(1 for s in all_enriched if s.get("price")),
            "local_prefilter_pass": len(candidates),
            "finnhub_enriched": finnhub_enriched,
            "pe_available": pe_count,
            "peg_available": peg_count,
            "trading_date": trading_date,
            "finnhub_calls": finnhub_enriched * 2,
            "duration_seconds": duration,
            "timestamp": end_time.isoformat(),
        },
    }

    print(f"Done in {duration:.1f}s. Finnhub calls: {finnhub_enriched * 2}")
    return write_pipeline_output(result, step_name="step3_enriched")
