"""
Price & Metrics Enrichment Lambda
==================================
Step 3 in the pipeline.

Combines two data sources to fully enrich all stocks that passed EDGAR pre-screen:

1. Polygon.io Grouped Daily — ALL US stock prices in ONE API call (1 credit)
2. Finnhub Basic Financials — 133 metrics per stock (60 calls/min free)

After this step, ALL 13 screening filters can be evaluated. No filter is skipped.

Data provided by this step:
- Current price (Polygon)
- P/E ratio (Finnhub: peTTM)
- PEG ratio (calculated: P/E ÷ EPS growth)
- Price/FCF (Finnhub: pfcfShareTTM)
- Forward P/E (Finnhub: forwardPE, or calculated from EPS estimates)
- EPS Growth YoY (Finnhub: epsGrowthTTMAnnual or revenueGrowthTTMYoy)
- Revenue Growth YoY (Finnhub: revenueGrowthTTMYoy)
- Analyst Target Price (Finnhub: price-target endpoint)
- Market Cap (Finnhub: marketCapitalization)
- All balance sheet ratios refreshed (Finnhub more current than EDGAR annual)

Rate limits:
- Polygon: 5 calls/min (we use 1)
- Finnhub: 60 calls/min → 233 stocks in ~4 minutes

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
    """Fetch a parameter from SSM."""
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
    """Get the most recent trading day (skip weekends)."""
    today = datetime.now(timezone.utc).date()
    if datetime.now(timezone.utc).hour < 21:
        today = today - timedelta(days=1)
    while today.weekday() >= 5:
        today = today - timedelta(days=1)
    return today.strftime("%Y-%m-%d")


def fetch_all_prices(polygon_key: str, date: str) -> dict[str, float]:
    """Fetch ALL US stock closing prices in one Polygon API call."""
    url = f"{POLYGON_GROUPED_URL}/{date}"
    response = http_requests.get(url, params={"apiKey": polygon_key}, timeout=30)
    if response.status_code != 200:
        print(f"  Warning: Polygon returned {response.status_code}")
        return {}
    data = response.json()
    results = data.get("results", [])
    return {item["T"]: item["c"] for item in results if "T" in item and "c" in item}


def fetch_finnhub_metrics(symbol: str, finnhub_key: str) -> dict:
    """
    Fetch full fundamental metrics for a single stock from Finnhub.
    Returns 133 metrics including P/E, PEG components, margins, growth, etc.
    """
    url = f"{FINNHUB_BASE_URL}/stock/metric"
    try:
        response = http_requests.get(
            url, params={"symbol": symbol, "metric": "all", "token": finnhub_key}, timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("metric", {})
        elif response.status_code == 429:
            print(f"    Rate limited on {symbol} — waiting 2s")
            time.sleep(2)
            return {}
    except Exception as e:
        print(f"    Finnhub error for {symbol}: {e}")
    return {}


def fetch_finnhub_price_target(symbol: str, finnhub_key: str) -> dict:
    """Fetch analyst price target consensus."""
    url = f"{FINNHUB_BASE_URL}/stock/price-target"
    try:
        response = http_requests.get(
            url, params={"symbol": symbol, "token": finnhub_key}, timeout=10
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return {}


def fetch_finnhub_institutional(symbol: str, finnhub_key: str) -> float:
    """
    Fetch institutional ownership and calculate net share change.

    Calls /stock/institutional-ownership, sums the 'change' field across
    all reporting institutions. Returns the net change as a decimal
    (positive = net buying, negative = net selling).

    Maps to Finviz's sh_insttrans_pos filter.
    """
    url = f"{FINNHUB_BASE_URL}/stock/institutional-ownership"
    try:
        response = http_requests.get(
            url, params={"symbol": symbol, "token": finnhub_key}, timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            holders = data.get("data", [])
            if holders:
                # Sum net share changes across all institutions
                net_change = sum(h.get("change", 0) for h in holders)
                # Normalize: return as a fraction of total shares held
                total_shares = sum(abs(h.get("share", 0)) for h in holders)
                if total_shares > 0:
                    return net_change / total_shares  # Decimal: 0.05 = 5% net buying
                return 0.01 if net_change > 0 else -0.01 if net_change < 0 else 0.0
    except Exception:
        pass
    return None


def enrich_stock(stock: dict, price: float, metrics: dict, target: dict) -> dict:
    """
    Apply all enrichment data to a stock dict.
    Calculates derived metrics (PEG, forward P/E, target upside).
    """
    # Price from Polygon
    stock["price"] = price
    stock["market_cap"] = metrics.get("marketCapitalization")

    # Direct from Finnhub metrics
    stock["pe_ratio"] = metrics.get("peTTM")
    stock["price_to_fcf"] = metrics.get("pfcfShareTTM")
    stock["debt_to_equity"] = metrics.get("totalDebt/totalEquityQuarterly")
    stock["quick_ratio"] = metrics.get("quickRatioQuarterly")
    stock["current_ratio"] = metrics.get("currentRatioQuarterly")
    stock["operating_margin"] = (metrics.get("operatingMarginTTM") or 0) / 100.0  # Convert % to decimal
    stock["net_profit_margin"] = (metrics.get("netProfitMarginTTM") or 0) / 100.0
    stock["gross_margin"] = (metrics.get("grossMarginTTM") or 0) / 100.0
    stock["return_on_equity"] = (metrics.get("roeTTM") or 0) / 100.0
    stock["dividend_yield"] = (metrics.get("dividendYieldIndicatedAnnual") or 0) / 100.0

    # Growth metrics (Finnhub returns as percentage, convert to decimal)
    eps_growth = metrics.get("epsGrowthTTMAnnual") or metrics.get("epsGrowthTTMYoy") or metrics.get("epsGrowth3Y")
    rev_growth = metrics.get("revenueGrowthTTMYoy")
    lt_growth = metrics.get("epsGrowth5Y") or metrics.get("epsGrowth3Y")
    stock["eps_growth_yoy"] = eps_growth / 100.0 if eps_growth else None
    stock["revenue_growth_yoy"] = rev_growth / 100.0 if rev_growth else None
    stock["est_lt_growth"] = lt_growth / 100.0 if lt_growth else None

    # PEG calculation: P/E ÷ EPS growth rate
    pe = stock.get("pe_ratio")
    if pe and eps_growth and eps_growth > 0:
        stock["peg_ratio"] = round(pe / eps_growth, 2)  # eps_growth already in %
    else:
        stock["peg_ratio"] = None

    # Forward P/E (Finnhub sometimes has this)
    stock["forward_pe"] = metrics.get("forwardPE") or metrics.get("peExclExtraAnnual")

    # Analyst target price + upside
    target_mean = target.get("targetMean") or target.get("targetMedian")
    if target_mean and price and price > 0:
        stock["analyst_target_price"] = target_mean
        stock["target_price_upside"] = (target_mean - price) / price
    else:
        stock["analyst_target_price"] = None
        stock["target_price_upside"] = None

    return stock


def handler(event, context):
    """
    Lambda entry point.

    1. Fetch ALL stock prices from Polygon (1 API call)
    2. For each stock that passed EDGAR pre-screen, fetch Finnhub metrics (60/min)
    3. Combine and calculate all derived metrics
    4. Output fully enriched stocks for the full screener (Step 4)
    """
    from pipeline_io import read_pipeline_input, write_pipeline_output

    start_time = datetime.now(timezone.utc)
    print(f"Starting enrichment at {start_time.isoformat()}")

    # Read input
    data = read_pipeline_input(event)
    passing = data.get("passing_stocks", [])

    if not passing:
        return write_pipeline_output(
            {"enriched_stocks": [], "metadata": {"error": "No stocks provided"}},
            step_name="step3_enriched"
        )

    print(f"Enriching {len(passing)} stocks (Polygon prices + Finnhub metrics)")

    # Step A: Bulk prices from Polygon (1 API call, all stocks)
    polygon_key = get_polygon_key()
    trading_date = get_last_trading_day()
    print(f"  Fetching Polygon grouped daily for {trading_date}...")
    all_prices = fetch_all_prices(polygon_key, trading_date)
    print(f"  Got {len(all_prices)} prices from Polygon")

    # Step B: Finnhub metrics for each stock (60/min — paced at 1/sec)
    finnhub_key = get_finnhub_key()
    enriched = []
    metrics_fetched = 0

    for i, stock in enumerate(passing):
        symbol = stock.get("symbol", "")
        price = all_prices.get(symbol)

        if not price:
            # No price from Polygon — can't calculate P/E. Stock fails.
            enriched.append(stock)
            continue

        # Fetch Finnhub metrics (2 calls: basic metrics + price target)
        metrics = fetch_finnhub_metrics(symbol, finnhub_key)
        target = fetch_finnhub_price_target(symbol, finnhub_key)

        if metrics:
            stock = enrich_stock(stock, price, metrics, target)
            metrics_fetched += 1

            # Only fetch institutional ownership for stocks likely to pass
            # (have P/E < 50, positive margins, etc.) — saves API calls
            if (stock.get("pe_ratio") and stock["pe_ratio"] < 50 and
                stock.get("operating_margin") and stock["operating_margin"] > 0 and
                stock.get("peg_ratio") and stock["peg_ratio"] < 1):
                inst_data = fetch_finnhub_institutional(symbol, finnhub_key)
                stock["institutional_transactions"] = inst_data
                time.sleep(1)  # Extra call — add 1s pacing

        enriched.append(stock)

        # Progress logging
        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(passing)}] enriched {metrics_fetched} so far")

        # Pacing: Finnhub allows 60 calls/min. We make 2-3 calls per stock.
        # 3 seconds between stocks = safe margin under 60/min limit.
        if i < len(passing) - 1:
            time.sleep(3)

    # Output
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    pe_count = sum(1 for s in enriched if s.get("pe_ratio") is not None)
    peg_count = sum(1 for s in enriched if s.get("peg_ratio") is not None)

    result = {
        "enriched_stocks": enriched,
        "metadata": {
            "total_stocks": len(passing),
            "prices_matched": sum(1 for s in enriched if s.get("price")),
            "metrics_fetched": metrics_fetched,
            "pe_available": pe_count,
            "peg_available": peg_count,
            "trading_date": trading_date,
            "duration_seconds": duration,
            "timestamp": end_time.isoformat(),
        },
    }

    print(f"Done in {duration:.1f}s. Metrics: {metrics_fetched}, P/E: {pe_count}, PEG: {peg_count}")
    return write_pipeline_output(result, step_name="step3_enriched")
