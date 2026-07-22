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
    """
    Get the most recent COMPLETED trading day for Polygon data.
    
    Polygon free tier has a 1 full trading day delay. Data for Monday
    isn't available until Tuesday. So we need the PREVIOUS completed
    trading day — not yesterday (which might be today's market).
    
    Safe approach: go back 2 days from UTC, then skip weekends.
    This guarantees we hit a completed trading day even if running
    early Monday UTC (which is still Sunday/Monday evening US time).
    """
    # Go back 2 days to ensure we're past any Polygon delay
    target = datetime.now(timezone.utc).date() - timedelta(days=2)
    # Skip weekends
    while target.weekday() >= 5:
        target = target - timedelta(days=1)
    return target.strftime("%Y-%m-%d")


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

def local_prefilter(stocks: list, prices: dict) -> tuple[list, list, dict]:
    """
    Calculate P/E locally, compute industry P/E quartiles, and apply filters.
    
    P/E filter is industry-relative: a stock passes if its P/E is below the
    lower quartile (25th percentile) of its SEC SIC industry group.
    This means "cheaper than 75% of peers in the same industry."
    
    Returns (candidates_for_finnhub, all_stocks_with_price, industry_pe_quartiles).
    """
    import json
    import boto3
    from collections import defaultdict

    all_enriched = []
    candidates = []

    # Step 1: Assign prices and compute P/E for ALL stocks
    for stock in stocks:
        symbol = stock.get("symbol", "")
        price = prices.get(symbol)

        if price:
            stock["price"] = price

            # Calculate P/E locally: Price ÷ TTM EPS (from EDGAR)
            eps = stock.get("eps")
            if eps and eps > 0:
                stock["pe_ratio"] = round(price / eps, 2)

        all_enriched.append(stock)

    # Step 2: Load industry map and compute P/E lower quartile per industry
    # Uses ALL stocks from Step 1 (full universe ~4,500) for meaningful industry samples,
    # not just the 70 pre-screen passers.
    industry_pe_quartiles = {}
    try:
        bucket = os.environ.get("RAW_DATA_BUCKET", "")
        if bucket:
            s3 = boto3.client("s3")

            # Load industry map
            resp = s3.get_object(Bucket=bucket, Key="reference/ticker_industry_map.json")
            industry_map = json.loads(resp["Body"].read().decode("utf-8"))

            # Load full Step 1 output (all ~4,500 stocks with TTM EPS)
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=f"pipeline/{today_str}/step1_fundamentals_")
            step1_keys = [obj["Key"] for obj in resp.get("Contents", [])]
            all_universe_stocks = []
            if step1_keys:
                step1_resp = s3.get_object(Bucket=bucket, Key=step1_keys[-1])
                step1_data = json.loads(step1_resp["Body"].read().decode("utf-8"))
                all_universe_stocks = step1_data.get("stocks", [])

            # Compute P/E for the full universe using Polygon prices
            industry_pe_values: dict[str, list] = defaultdict(list)
            for stock in all_universe_stocks:
                symbol = stock.get("symbol", "")
                eps = stock.get("eps")
                price = prices.get(symbol)
                if price and eps and eps > 0:
                    pe = price / eps
                    if pe > 0 and pe < 500:  # Exclude nonsensical values
                        entry = industry_map.get(symbol)
                        if entry:
                            industry_pe_values[entry["industry"]].append(pe)

            # Compute 25th percentile (lower quartile) for each industry with enough data
            for industry, values in industry_pe_values.items():
                if len(values) >= 5:
                    sorted_vals = sorted(values)
                    q1_idx = len(sorted_vals) // 4
                    industry_pe_quartiles[industry] = round(sorted_vals[q1_idx], 2)

            print(f"  Computed P/E lower quartile for {len(industry_pe_quartiles)} industries "
                  f"(from {len(all_universe_stocks)} stocks)")

            # Tag each pre-screen passer with its industry P/E threshold
            for stock in all_enriched:
                symbol = stock.get("symbol", "")
                entry = industry_map.get(symbol)
                if entry:
                    stock["_sic_industry"] = entry["industry"]
                    stock["_pe_industry_q1"] = industry_pe_quartiles.get(entry["industry"])
    except Exception as e:
        print(f"  Warning: Could not compute industry P/E quartiles: {e}")

    # Step 3: Pre-filter using industry-relative P/E
    for stock in all_enriched:
        price = stock.get("price")
        pe = stock.get("pe_ratio")
        de = stock.get("debt_to_equity")
        qr = stock.get("quick_ratio")
        om = stock.get("operating_margin")
        eps_g = stock.get("eps_growth_yoy")
        rev_g = stock.get("revenue_growth_yoy")

        # P/E must be below industry lower quartile (cheaper than 75% of peers)
        # Fallback to P/E < 50 if no industry data available
        pe_threshold = stock.get("_pe_industry_q1") or 50
        pe_passes = pe is not None and pe > 0 and pe < pe_threshold

        passes_prefilter = (
            price is not None
            and pe_passes
            and de is not None and de < 1
            and qr is not None and qr > 1
            and om is not None and om > 0
            and eps_g is not None and eps_g > 0
            and rev_g is not None and rev_g > 0
        )

        if passes_prefilter:
            candidates.append(stock)

    return candidates, all_enriched, industry_pe_quartiles


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


def fetch_finnhub_profile(symbol: str, key: str) -> dict:
    """
    Fetch company profile from Finnhub /stock/profile2. 1 API call.

    Returns: name, finnhubIndustry, weburl, logo, country, exchange,
    marketCapitalization. Note: free tier does NOT return 'description'.
    """
    url = f"{FINNHUB_BASE_URL}/stock/profile2"
    try:
        response = http_requests.get(
            url, params={"symbol": symbol, "token": key}, timeout=10
        )
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 429:
            print(f"    Rate limited on profile for {symbol}")
            time.sleep(5)
    except Exception as e:
        print(f"    Finnhub profile error for {symbol}: {e}")
    return {}


def fetch_polygon_description(symbol: str, polygon_key: str) -> str:
    """
    Fetch company description from Polygon /v3/reference/tickers/{ticker}.
    Polygon free tier includes full company descriptions.
    Rate limit: 5 calls/min — only call for final candidates (~6-10 stocks).
    """
    url = f"https://api.polygon.io/v3/reference/tickers/{symbol}"
    try:
        response = http_requests.get(
            url, params={"apiKey": polygon_key}, timeout=10
        )
        if response.status_code == 200:
            results = response.json().get("results", {})
            return results.get("description", "")
    except Exception as e:
        print(f"    Polygon description error for {symbol}: {e}")
    return ""


def fetch_finnhub_recommendation(symbol: str, key: str) -> float:
    """
    Fetch analyst recommendation consensus. 1 API call.

    Returns a score: 1=Strong Buy, 2=Buy, 3=Hold, 4=Sell, 5=Strong Sell.
    Calculated from the most recent recommendation period.
    Our filter: "Hold or better" = score <= 3.0.
    """
    url = f"{FINNHUB_BASE_URL}/stock/recommendation"
    try:
        response = http_requests.get(
            url, params={"symbol": symbol, "token": key}, timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and data:
                latest = data[0]  # Most recent period
                strong_buy = latest.get("strongBuy", 0)
                buy = latest.get("buy", 0)
                hold = latest.get("hold", 0)
                sell = latest.get("sell", 0)
                strong_sell = latest.get("strongSell", 0)
                total = strong_buy + buy + hold + sell + strong_sell
                if total > 0:
                    # Weighted average: 1×SB + 2×B + 3×H + 4×S + 5×SS / total
                    score = (1*strong_buy + 2*buy + 3*hold + 4*sell + 5*strong_sell) / total
                    return round(score, 2)
    except Exception:
        pass
    return None


def enrich_with_finnhub(stock: dict, metrics: dict, target: dict) -> dict:
    """Apply Finnhub data — ONLY fields that can't be computed from EDGAR/Polygon."""
    price = stock.get("price", 0)

    # PEG: P/E ÷ EPS growth (EPS growth now comes from EDGAR, not Finnhub)
    pe = stock.get("pe_ratio")
    eps_growth = stock.get("eps_growth_yoy")  # Already computed from EDGAR
    if pe and eps_growth and eps_growth > 0:
        stock["peg_ratio"] = round(pe / (eps_growth * 100), 2)  # growth is decimal, PEG uses %

    # Price/FCF: Price ÷ FCF per share (FCF now comes from EDGAR)
    fcf_ps = stock.get("fcf_per_share")
    if price and fcf_ps and fcf_ps > 0:
        stock["price_to_fcf"] = round(price / fcf_ps, 2)

    # Forward P/E — ONLY from Finnhub (analyst estimate, can't compute from filings)
    stock["forward_pe"] = metrics.get("peNormalizedAnnual") or metrics.get("peExclExtraAnnual")

    # Est. LT Growth — ONLY from Finnhub (analyst consensus)
    lt_g = metrics.get("epsGrowth5Y") or metrics.get("epsGrowth3Y")
    stock["est_lt_growth"] = lt_g / 100.0 if lt_g else None

    # Analyst target price + upside — DEFERRED (endpoint returns empty on free tier)
    # Keeping the code path for when we find a working source
    target_mean = target.get("targetMean") or target.get("targetMedian")
    if target_mean and price and price > 0:
        stock["analyst_target_price"] = target_mean
        stock["target_price_upside"] = (target_mean - price) / price

    # Market cap from Finnhub (supplement)
    mc = metrics.get("marketCapitalization")
    if mc:
        stock["market_cap"] = mc * 1_000_000

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
    print(f"  Stage 2: Local P/E + industry-relative pre-filter...")
    candidates, all_enriched, industry_pe_quartiles = local_prefilter(passing, all_prices)
    print(f"  Pre-filter: {len(candidates)} candidates for Finnhub (from {len(passing)})")

    # STAGE 3: Finnhub for candidates only (2 calls per stock, 3s pacing)
    finnhub_key = get_finnhub_key()
    finnhub_enriched = 0
    print(f"  Stage 3: Finnhub enrichment for {len(candidates)} stocks...")

    for i, stock in enumerate(candidates):
        symbol = stock.get("symbol", "")

        metrics = fetch_finnhub_metrics(symbol, finnhub_key)
        time.sleep(1)

        if metrics:
            enrich_with_finnhub(stock, metrics, {})
            # Analyst recommendation (separate endpoint)
            time.sleep(1)
            rec_score = fetch_finnhub_recommendation(symbol, finnhub_key)
            if rec_score is not None:
                stock["analyst_recommendation"] = rec_score
            finnhub_enriched += 1

        # Company profile (logo, industry, website) from Finnhub
        time.sleep(1)
        profile = fetch_finnhub_profile(symbol, finnhub_key)
        if profile:
            stock["logo"] = profile.get("logo", "")
            stock["weburl"] = profile.get("weburl", "")
            # Supplement sector/industry if not already set
            if not stock.get("industry") and profile.get("finnhubIndustry"):
                stock["industry"] = profile.get("finnhubIndustry")

        if (i + 1) % 25 == 0:
            print(f"    [{i+1}/{len(candidates)}] enriched {finnhub_enriched}")

        # Pacing: Finnhub allows 60 calls/min. We make 3 calls per stock
        # (metrics + recommendation + profile). 3s between stocks = safe.
        if i < len(candidates) - 1:
            time.sleep(3)

    # STAGE 3b: Polygon descriptions (only for candidates, 5/min rate limit)
    # Polygon descriptions take 12s each. For ~50-80 candidates, too slow.
    # Instead, we'll fetch descriptions only in the score-calculator step
    # after full screen narrows to ~6-10 final stocks.
    # The enrichment step passes logo/industry/weburl through from Finnhub.

    # Output: return ALL stocks (enriched candidates + non-candidates with just price/P/E)
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    pe_count = sum(1 for s in all_enriched if s.get("pe_ratio") is not None)
    peg_count = sum(1 for s in all_enriched if s.get("peg_ratio") is not None)

    # Persist industry P/E quartiles to DynamoDB (for full screen + dashboard)
    if industry_pe_quartiles:
        try:
            import boto3 as _boto3
            from decimal import Decimal
            _dynamodb = _boto3.resource("dynamodb")
            _table_name = os.environ.get("DATA_TABLE_NAME", "")
            if _table_name:
                _table = _dynamodb.Table(_table_name)
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                with _table.batch_writer() as batch:
                    for industry, q1_pe in industry_pe_quartiles.items():
                        # Update existing INDUSTRY_AVG item with pe_q1
                        _table.update_item(
                            Key={"PK": f"INDUSTRY_AVG#{industry}", "SK": "METRICS"},
                            UpdateExpression="SET pe_lower_quartile = :q1, pe_updated = :d",
                            ExpressionAttributeValues={
                                ":q1": Decimal(str(q1_pe)),
                                ":d": today,
                            },
                        )
                print(f"  Persisted P/E lower quartiles for {len(industry_pe_quartiles)} industries")
        except Exception as e:
            print(f"  Warning: Could not persist P/E quartiles: {e}")

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
            "finnhub_calls": finnhub_enriched * 3,
            "industries_with_pe_quartile": len(industry_pe_quartiles),
            "duration_seconds": duration,
            "timestamp": end_time.isoformat(),
        },
    }

    print(f"Done in {duration:.1f}s. Finnhub calls: {finnhub_enriched * 3}")
    return write_pipeline_output(result, step_name="step3_enriched")
