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
  - Apply hard filters: P/E < industry quartile, D/E < 1 (or ICR>3), QR > 1, OpMargin > 0
  - ~69 → ~30-50 survivors

Stage 3 — FMP enrichment (only for survivors):
  - /stable/ratios-ttm → Normalized P/E, PEG, P/FCF, ICR (validated override)
  - /stable/financial-growth → EPS growth, revenue growth
  - /stable/analyst-estimates → Forward EPS → Forward P/E
  - /stable/price-target-summary → Analyst target price consensus
  - /stable/grades → Analyst recommendation (buy/hold/sell derived)
  - /stable/profile → Description, logo, website, sector, industry
  - 6 calls per survivor × ~30-50 stocks = ~180-300 FMP calls
  - At 300 req/min = ~1-2 minutes

Total API calls per run:
  - Polygon: 1 (grouped daily prices)
  - FMP: ~180-300 (only for pre-filtered candidates)

Environment Variables:
    POLYGON_API_KEY_PARAM  - SSM path for Polygon.io key
    FMP_API_KEY_PARAM      - SSM path for FMP key
    RAW_DATA_BUCKET        - S3 bucket for pipeline I/O
    DATA_TABLE_NAME        - DynamoDB table for industry averages
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta, date

import boto3
import requests as http_requests

# AWS
ssm_client = boto3.client("ssm")

# API URLs
POLYGON_GROUPED_URL = "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks"
FMP_BASE = "https://financialmodelingprep.com/stable"

# Cache
_polygon_key = None
_fmp_key = None


def get_ssm_param(param_name: str) -> str:
    response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
    return response["Parameter"]["Value"]


def get_polygon_key() -> str:
    global _polygon_key
    if not _polygon_key:
        _polygon_key = get_ssm_param(os.environ.get("POLYGON_API_KEY_PARAM", "/stock-screener/polygon-api-key"))
    return _polygon_key


def get_fmp_key() -> str:
    global _fmp_key
    if not _fmp_key:
        _fmp_key = get_ssm_param(os.environ.get("FMP_API_KEY_PARAM", "/stock-screener/fmp-api-key"))
    return _fmp_key


def get_last_trading_day(polygon_key: str = None) -> str:
    """
    Get the most recent COMPLETED trading day for Polygon data.

    Strategy: Try T-1 first (yesterday, skipping weekends). If Polygon
    returns an error (data not yet published), fall back to T-2.

    Polygon free tier publishes previous day's data by ~5 AM UTC.
    Since our pipeline runs at 8 PM UTC (well past that), T-1 should
    always work. The fallback exists as a safety net.
    """
    # Try T-1 first (skip weekends)
    target = datetime.now(timezone.utc).date() - timedelta(days=1)
    while target.weekday() >= 5:
        target = target - timedelta(days=1)

    # If we have the API key, verify T-1 is available
    if polygon_key:
        test_url = f"{POLYGON_GROUPED_URL}/{target.strftime('%Y-%m-%d')}"
        try:
            resp = http_requests.get(test_url, params={"apiKey": polygon_key}, timeout=10)
            if resp.status_code == 200:
                return target.strftime("%Y-%m-%d")
            print(f"  T-1 ({target}) not available (HTTP {resp.status_code}), falling back to T-2")
        except Exception as e:
            print(f"  T-1 check failed ({e}), falling back to T-2")

        # Fallback: T-2 (guaranteed available)
        target = datetime.now(timezone.utc).date() - timedelta(days=2)
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

def local_prefilter(stocks: list, prices: dict) -> tuple[list, list, dict, dict]:
    """
    Calculate P/E locally, compute industry P/E quartiles, and apply filters.
    
    P/E filter is industry-relative: a stock passes if its P/E is below the
    lower quartile (25th percentile) of its SEC SIC industry group.
    This means "cheaper than 75% of peers in the same industry."
    
    Returns (candidates_for_fmp, all_stocks_with_price, industry_pe_quartiles).
    """
    import json
    import boto3
    from collections import defaultdict

    all_enriched = []
    candidates = []

    # Step 1: Assign prices and compute P/E, PEG, Price/FCF for ALL stocks
    for stock in stocks:
        symbol = stock.get("symbol", "")
        price = prices.get(symbol)

        if price:
            stock["price"] = price

            # Calculate P/E locally: Price ÷ TTM EPS (from EDGAR)
            eps = stock.get("eps")
            if eps and eps > 0:
                stock["pe_ratio"] = round(price / eps, 2)

            # Calculate PEG locally: P/E ÷ EPS Growth (from EDGAR TTM)
            pe = stock.get("pe_ratio")
            eps_growth = stock.get("eps_growth_yoy")
            if pe and eps_growth and eps_growth > 0:
                stock["peg_ratio"] = round(pe / (eps_growth * 100), 2)

            # Calculate Price/FCF locally: Price ÷ FCF per share (from EDGAR)
            fcf_ps = stock.get("fcf_per_share")
            if fcf_ps and fcf_ps > 0:
                stock["price_to_fcf"] = round(price / fcf_ps, 2)

        all_enriched.append(stock)

    # Step 2: Load industry map and compute P/E lower quartile per industry
    # Uses ALL stocks from Step 1 (full universe ~4,500) for meaningful industry samples,
    # not just the 70 pre-screen passers.
    industry_pe_quartiles = {}
    industry_pe_q1 = {}
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
                revenue_ps = stock.get("revenue_per_share")
                price = prices.get(symbol)
                if price and eps and eps > 0:
                    pe = price / eps
                    # Sanity checks: exclude data artifacts from quartile computation
                    # P/E < 1 means EPS > price (impossible without one-time gains)
                    # EPS > revenue/share means net income > revenue (impossible for normal ops)
                    if pe < 1:
                        continue
                    if revenue_ps and revenue_ps > 0 and eps > revenue_ps:
                        continue
                    if pe > 500:  # Nonsensically high
                        continue
                    entry = industry_map.get(symbol)
                    if entry:
                        industry_pe_values[entry["industry"]].append(pe)

            # Compute P/E percentile threshold per industry
            # Tech industries (SEC SIC 35xx, 36xx, 737x) use 50th percentile (median)
            # because they structurally trade at higher valuations.
            # Non-tech uses 25th percentile (lower quartile).
            # SIC code ranges are the SEC's own standardized classification:
            #   35xx = Industrial Machinery & Equipment (includes computers)
            #   36xx = Electronic & Electrical Equipment
            #   737x = Computer & Data Processing Services (includes software)
            TECH_SIC_PREFIXES = ("35", "36", "737")

            for industry, values in industry_pe_values.items():
                if len(values) >= 5:
                    sorted_vals = sorted(values)
                    # Pipeline threshold: 50th percentile (median) for ALL industries
                    # Stock must be cheaper than half its industry peers to pass
                    median_idx = len(sorted_vals) // 2
                    industry_pe_quartiles[industry] = round(sorted_vals[median_idx], 2)
                    # Also compute 25th percentile for frontend "strict" toggle
                    q1_idx = len(sorted_vals) // 4
                    industry_pe_q1[industry] = round(sorted_vals[q1_idx], 2)

            print(f"  Computed P/E median (50th pctile) for {len(industry_pe_quartiles)} industries "
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

    # Price-dependent pre-filter: only checks conditions Step 2 cannot evaluate
    # (Step 2 has no prices, so it can't compute P/E, PEG, or P/FCF)
    for stock in all_enriched:
        price = stock.get("price")
        pe = stock.get("pe_ratio")
        peg = stock.get("peg_ratio")
        pfcf = stock.get("price_to_fcf")

        if price is None:
            continue

        pe_threshold = stock.get("_pe_industry_q1") or 50
        pe_passes = pe is not None and pe > 0 and pe < pe_threshold
        peg_passes = peg is not None and peg > 0 and peg < 1.0
        pfcf_passes = pfcf is not None and pfcf > 0 and pfcf < 20

        # Valuation gate: must pass at least one of P/E or PEG
        # PEG < 1.0 exempts from P/E check (growth justifies the multiple)
        # P/E < industry median exempts from PEG check (cheap regardless of growth)
        valuation_passes = pe_passes or peg_passes

        # Must have at least one computable valuation metric
        has_valuation = (pe is not None and pe > 0) or (peg is not None and peg > 0)

        passes = valuation_passes and has_valuation and pfcf_passes

        if passes:
            candidates.append(stock)

    return candidates, all_enriched, industry_pe_quartiles, industry_pe_q1


# ==========================================
# STAGE 3: FMP enrichment (only candidates)
# ==========================================

def fmp_get(path: str, api_key: str, params: dict = None, max_retries: int = 3):
    """
    Make a GET request to FMP stable API with retry on transient failures.

    Retries up to max_retries times with exponential backoff (2s, 4s, 8s)
    for timeouts and server errors (5xx).

    Returns:
      - Parsed JSON (list or dict) on success
      - [] on HTTP 200 with empty response OR HTTP 404 (stock doesn't exist)
      - None only after all retries exhausted (genuine persistent failure)
    """
    url = f"{FMP_BASE}/{path}"
    query_parts = [f"apikey={api_key}"]
    if params:
        for k, v in params.items():
            query_parts.append(f"{k}={v}")
    url += "?" + "&".join(query_parts)

    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers={"User-Agent": "StockScreener/2.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []  # Stock genuinely doesn't exist
            if e.code == 429:
                # Rate limited — wait longer and retry
                wait = (attempt + 1) * 5
                print(f"    FMP {path}: rate limited (429), waiting {wait}s...")
                time.sleep(wait)
                continue
            if e.code >= 500:
                # Server error — retry with backoff
                wait = 2 ** (attempt + 1)
                print(f"    FMP {path}: server error ({e.code}), retry {attempt+1}/{max_retries} in {wait}s...")
                time.sleep(wait)
                continue
            # Client error (4xx other than 404/429) — don't retry
            print(f"    FMP {path} error: HTTP {e.code}")
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # Network timeout or connection failure — retry with backoff
            wait = 2 ** (attempt + 1)
            print(f"    FMP {path}: timeout/network error, retry {attempt+1}/{max_retries} in {wait}s...")
            time.sleep(wait)
            continue
        except Exception as e:
            print(f"    FMP {path} unexpected error: {e}")
            return None

    # All retries exhausted
    print(f"    FMP {path}: FAILED after {max_retries} retries")
    return None


def fetch_fmp_ratios(symbol: str, api_key: str) -> dict:
    """
    Fetch TTM ratios from FMP. 1 API call, 62 fields.
    Returns dict of ratios on success, {} if not found or error.
    """
    data = fmp_get("ratios-ttm", api_key, {"symbol": symbol})
    if data and isinstance(data, list) and data[0]:
        return data[0]
    return {}


def fetch_fmp_profile(symbol: str, api_key: str) -> dict | None:
    """
    Fetch company profile. Retries if FMP returns empty (FMP sometimes
    returns [] for valid stocks on transient data issues).

    Returns:
      - dict with profile data on success
      - {} if consistently empty after retries (stock may not exist)
      - None if API errors persist after retries
    """
    data = fmp_get("profile", api_key, {"symbol": symbol})
    if data is None:
        return None  # All retries failed with errors

    if isinstance(data, list) and data:
        return data[0]  # Success

    # Got empty [] — FMP sometimes does this transiently. Retry once more.
    time.sleep(2)
    data = fmp_get("profile", api_key, {"symbol": symbol})
    if data and isinstance(data, list) and data:
        return data[0]  # Succeeded on retry

    return {}  # Consistently empty — stock likely doesn't exist


def fetch_fmp_growth(symbol: str, api_key: str) -> dict:
    """
    Fetch annual financial growth. 1 API call.
    Key fields: epsgrowth, revenueGrowth, fiveYNetIncomeGrowthPerShare
    """
    data = fmp_get("financial-growth", api_key, {"symbol": symbol, "period": "annual", "limit": "1"})
    if data and isinstance(data, list) and data[0]:
        return data[0]
    return {}


def fetch_fmp_estimates(symbol: str, api_key: str) -> list:
    """
    Fetch analyst EPS estimates. 1 API call.
    Returns ALL future annual estimates sorted by date (nearest first).
    Each entry has: epsAvg, epsHigh, epsLow, numAnalystsEps, date
    Used for:
      - Forward P/E (nearest year epsAvg)
      - Forward LT Growth (3-year-out epsAvg vs current EPS → CAGR)
    """
    data = fmp_get("analyst-estimates", api_key, {"symbol": symbol, "period": "annual", "limit": "10"})
    if data and isinstance(data, list):
        today_str = str(date.today())
        future = [e for e in sorted(data, key=lambda x: x.get("date", ""))
                  if e.get("date", "") > today_str]
        return future[:5]  # Cap at 5 future years
    return []


def fetch_fmp_targets(symbol: str, api_key: str) -> dict:
    """
    Fetch analyst price target consensus. 1 API call.
    Fields: lastQuarterAvgPriceTarget, lastQuarterCount,
    lastYearAvgPriceTarget, lastYearCount
    """
    data = fmp_get("price-target-summary", api_key, {"symbol": symbol})
    if data and isinstance(data, list) and data[0]:
        return data[0]
    return {}


def fetch_fmp_grades(symbol: str, api_key: str) -> float:
    """
    Fetch analyst grades and compute recommendation score. 1 API call.

    FMP /stable/grades returns individual analyst actions:
    {gradingCompany, previousGrade, newGrade, action, date}

    We map grades to a 1-5 numeric scale and average recent grades
    to get a consensus score (1=Strong Buy ... 5=Strong Sell).
    Our filter: score <= 3.0 (Hold or better).
    """
    data = fmp_get("grades", api_key, {"symbol": symbol, "limit": "20"})
    if not data or not isinstance(data, list):
        return None

    # Grade mapping: various firm-specific terms → 1-5 scale
    GRADE_MAP = {
        # Strong Buy = 1
        "strong buy": 1, "strong-buy": 1, "outperform": 1.5,
        "overweight": 1.5, "top pick": 1,
        # Buy = 2
        "buy": 2, "positive": 2, "accumulate": 2, "sector outperform": 2,
        "market outperform": 2, "add": 2,
        # Hold = 3
        "hold": 3, "neutral": 3, "equal-weight": 3, "equal weight": 3,
        "market perform": 3, "sector perform": 3, "in-line": 3,
        "sector weight": 3, "peer perform": 3, "market weight": 3,
        # Sell = 4
        "sell": 4, "underperform": 4, "underweight": 4, "reduce": 4,
        "negative": 4, "sector underperform": 4, "market underperform": 4,
        # Strong Sell = 5
        "strong sell": 5,
    }

    scores = []
    for grade in data:
        new_grade = (grade.get("newGrade") or "").lower().strip()
        mapped = GRADE_MAP.get(new_grade)
        if mapped:
            scores.append(mapped)

    if scores:
        return round(sum(scores) / len(scores), 2)
    return None


def enrich_with_fmp(stock: dict, ratios: dict, growth: dict,
                    estimates: dict, targets: dict, profile: dict,
                    grades_score: float) -> dict:
    """
    Apply FMP data to a stock dict. Overrides/supplements EDGAR data
    where FMP provides more accurate or additional information.
    """
    price = stock.get("price", 0)

    # --- P/E from FMP ratios (normalized, strips one-time items) ---
    pe_fmp = ratios.get("priceToEarningsRatioTTM")
    if pe_fmp and pe_fmp > 0:
        stock["pe_ratio"] = round(pe_fmp, 2)

    # --- PEG from FMP ratios ---
    peg_fmp = ratios.get("priceToEarningsGrowthRatioTTM")
    if peg_fmp is not None:
        stock["peg_ratio"] = round(peg_fmp, 3)

    # --- P/FCF from FMP ratios ---
    pfcf_fmp = ratios.get("priceToFreeCashFlowRatioTTM")
    if pfcf_fmp and pfcf_fmp > 0:
        stock["price_to_fcf"] = round(pfcf_fmp, 2)

    # --- D/E from FMP ratios ---
    de_fmp = ratios.get("debtToEquityRatioTTM")
    if de_fmp is not None:
        stock["debt_to_equity"] = round(de_fmp, 3)

    # --- ICR from FMP ratios ---
    icr_fmp = ratios.get("interestCoverageRatioTTM")
    if icr_fmp is not None and icr_fmp != 0:
        stock["interest_coverage_ratio"] = round(icr_fmp, 2)

    # --- Quick Ratio from FMP ratios ---
    qr_fmp = ratios.get("quickRatioTTM")
    if qr_fmp is not None:
        stock["quick_ratio"] = round(qr_fmp, 3)

    # --- Operating Margin from FMP ratios ---
    om_fmp = ratios.get("operatingProfitMarginTTM")
    if om_fmp is not None:
        stock["operating_margin"] = round(om_fmp, 4)

    # --- Forward P/E + LT Growth from analyst estimates ---
    # estimates is now a list of future annual estimates (nearest first)
    if estimates and isinstance(estimates, list):
        # Forward P/E: use nearest year's EPS
        nearest_eps = estimates[0].get("epsAvg") if estimates else None
        if nearest_eps and nearest_eps > 0 and price:
            stock["forward_pe"] = round(price / nearest_eps, 2)

        # Forward LT Growth: CAGR from current EPS to furthest available estimate (up to 3 years)
        # Use the furthest year available (prefer 3rd, fall back to 2nd, then 1st)
        # Get current EPS: from FMP ratios (netIncomePerShareTTM), EDGAR, or price/PE
        current_eps = ratios.get("netIncomePerShareTTM") or stock.get("eps")
        if not current_eps and price and stock.get("pe_ratio") and stock["pe_ratio"] > 0:
            current_eps = price / stock["pe_ratio"]

        if current_eps and current_eps > 0:
            # Pick the furthest future estimate (up to index 2 = year 3)
            furthest_idx = min(2, len(estimates) - 1)  # 0-indexed: 0=yr1, 1=yr2, 2=yr3
            furthest_eps = estimates[furthest_idx].get("epsAvg")
            years_out = furthest_idx + 1

            if furthest_eps and furthest_eps > 0 and years_out > 0:
                try:
                    forward_cagr = (furthest_eps / current_eps) ** (1 / years_out) - 1
                    if isinstance(forward_cagr, complex):
                        pass
                    else:
                        stock["est_lt_growth"] = round(forward_cagr, 4)
                except (ValueError, ZeroDivisionError, OverflowError, TypeError):
                    pass
    elif isinstance(estimates, dict) and estimates:
        # Backward compatibility: single dict format
        forward_eps = estimates.get("epsAvg")
        if forward_eps and forward_eps > 0 and price:
            stock["forward_pe"] = round(price / forward_eps, 2)

    # --- Forward LT Revenue Growth: CAGR from current revenue to furthest analyst estimate ---
    if isinstance(estimates, list) and len(estimates) >= 1:
        # Get current TTM revenue from FMP ratios or EDGAR
        current_revenue = ratios.get("revenuePerShareTTM")
        if current_revenue and stock.get("revenue_per_share"):
            # ratios gives per-share, estimates gives total — use total
            # Approximate total from per-share × shares (from market cap / price)
            pass
        # Better: use the total revenue directly. estimates[i]["revenueAvg"] is total revenue.
        # We need current total revenue. Get from financial-growth or income statement.
        # FMP ratios has revenuePerShareTTM; multiply by shares approximation.
        # Simpler: compare estimate revenue to the nearest prior estimate or just use CAGR from year 1 to furthest.
        # Most reliable: use year 0 (closest estimate) as proxy for current, compute CAGR to furthest.
        # Actually, estimates are sorted newest-first. The first entry is the furthest year.
        # Let's find the nearest and furthest estimates.
        sorted_estimates = sorted(
            [e for e in estimates if e.get("revenueAvg") and e.get("revenueAvg") > 0],
            key=lambda e: e.get("date", ""),
        )
        if len(sorted_estimates) >= 2:
            nearest = sorted_estimates[0]
            furthest = sorted_estimates[-1]
            nearest_rev = nearest.get("revenueAvg")
            furthest_rev = furthest.get("revenueAvg")
            try:
                from datetime import datetime as _dt
                nearest_date = _dt.strptime(nearest["date"][:10], "%Y-%m-%d")
                furthest_date = _dt.strptime(furthest["date"][:10], "%Y-%m-%d")
                years_between = (furthest_date - nearest_date).days / 365.25
                if years_between >= 1 and nearest_rev > 0 and furthest_rev > 0:
                    rev_cagr = (furthest_rev / nearest_rev) ** (1 / years_between) - 1
                    if not isinstance(rev_cagr, complex):
                        stock["est_lt_revenue_growth"] = round(rev_cagr, 4)
            except (ValueError, TypeError, ZeroDivisionError, OverflowError):
                pass

    # --- EPS Growth from financial-growth ---
    eps_g = growth.get("epsgrowth")
    if eps_g is not None:
        stock["eps_growth_yoy"] = round(eps_g, 4)

    # --- Revenue Growth from financial-growth ---
    rev_g = growth.get("revenueGrowth")
    if rev_g is not None:
        stock["revenue_growth_yoy"] = round(rev_g, 4)

    # --- Long-term Growth estimate (fallback) ---
    # Only use historical 5Y growth if forward analyst estimates didn't provide est_lt_growth.
    # Forward estimates (computed above from analyst EPS forecasts) are preferred.
    if stock.get("est_lt_growth") is None:
        lt_g = growth.get("fiveYNetIncomeGrowthPerShare")
        if lt_g is not None:
            try:
                cagr = (1 + lt_g) ** (1 / 5) - 1
                if not isinstance(cagr, complex):
                    stock["est_lt_growth"] = round(cagr, 4)
            except (ValueError, ZeroDivisionError, TypeError):
                pass

    # --- Analyst Target Price ---
    target_price = targets.get("lastQuarterAvgPriceTarget")
    if not target_price:
        target_price = targets.get("lastYearAvgPriceTarget")
    if target_price and price and price > 0:
        stock["analyst_target_price"] = round(target_price, 2)
        stock["target_price_upside"] = round((target_price - price) / price, 4)

    # --- Analyst Recommendation (from grades) ---
    if grades_score is not None:
        stock["analyst_recommendation"] = grades_score

    # --- Profile data ---
    if profile:
        stock["company_name"] = profile.get("companyName", stock.get("company_name", ""))
        stock["company_description"] = profile.get("description", "")
        stock["logo"] = profile.get("image", "")
        stock["weburl"] = profile.get("website", "")
        if profile.get("sector"):
            stock["sector"] = profile["sector"]
        if profile.get("industry"):
            stock["industry"] = profile["industry"]
        mc = profile.get("marketCap")
        if mc:
            stock["market_cap"] = mc
        avg_vol = profile.get("averageVolume")
        if avg_vol:
            stock["average_volume"] = avg_vol

    # Recompute PEG for consistency: if eps_growth_yoy was updated by FMP
    # but PEG wasn't (FMP returned None for PEG), recalculate from current P/E + growth
    pe = stock.get("pe_ratio")
    growth_rate = stock.get("eps_growth_yoy")
    if pe and pe > 0 and growth_rate and growth_rate > 0:
        stock["peg_ratio"] = round(pe / (growth_rate * 100), 2)
    elif growth_rate is not None and growth_rate <= 0:
        # Negative or zero growth makes PEG meaningless — clear it
        stock["peg_ratio"] = None

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
    trading_date = get_last_trading_day(polygon_key)
    print(f"  Stage 1: Polygon grouped daily for {trading_date}...")
    all_prices = fetch_all_prices(polygon_key, trading_date)
    print(f"  Got {len(all_prices)} prices")

    # Load foreign filer fundamentals from monthly reference file
    # These are 20-F/6-K filers not covered by the Frames API
    bucket = os.environ.get("RAW_DATA_BUCKET", "")
    if bucket:
        try:
            s3_ref = boto3.client("s3")
            ref_resp = s3_ref.get_object(Bucket=bucket, Key="reference/foreign_filer_fundamentals.json")
            foreign_data = json.loads(ref_resp["Body"].read())
            foreign_stocks = foreign_data.get("stocks", [])
            # Only include foreign filers that have Polygon prices (actively traded)
            step1_symbols = {s.get("symbol") for s in passing}
            added = 0
            for stock in foreign_stocks:
                sym = stock.get("symbol", "")
                if sym in all_prices and sym not in step1_symbols:
                    stock["price"] = all_prices[sym]
                    passing.append(stock)
                    added += 1
            if added:
                print(f"  Loaded {added} foreign filers from reference file")
        except Exception as e:
            print(f"  Note: No foreign filer reference file yet ({e})")

    # STAGE 2: Local P/E calculation + pre-filter (zero API calls)
    print(f"  Stage 2: Local P/E + industry-relative pre-filter...")
    candidates, all_enriched, industry_pe_quartiles, industry_pe_q1 = local_prefilter(passing, all_prices)
    print(f"  Pre-filter: {len(candidates)} candidates for FMP (from {len(passing)})")

    # STAGE 3: FMP enrichment for candidates (6 calls per stock)
    # FMP rate limit: 300 req/min. At 6 calls/stock, we can safely do ~40 stocks/min.
    # Pace at ~1s between stocks (6 calls + 1s pause ≈ 7s/stock).
    fmp_key = get_fmp_key()
    fmp_enriched = 0
    print(f"  Stage 3: FMP enrichment for {len(candidates)} stocks...")

    for i, stock in enumerate(candidates):
        symbol = stock.get("symbol", "")

        # Fetch all FMP data for this stock
        ratios = fetch_fmp_ratios(symbol, fmp_key)
        profile = fetch_fmp_profile(symbol, fmp_key)

        # Distinguish: profile={} means stock doesn't exist in FMP (GOOGN-type ghost)
        #              profile=None means transient API failure (proceed without profile)
        #              profile={...} means success
        if profile == {}:
            # Stock genuinely doesn't exist on major exchanges — exclude entirely
            print(f"    {symbol}: not found in FMP (OTC/historical?), skipping")
            stock["_fmp_excluded"] = True
            continue
        elif profile is None:
            # Transient failure — proceed with enrichment using whatever data we got
            print(f"    {symbol}: FMP profile call failed (transient), proceeding without")
            profile = {}
        else:
            # Profile exists — enforce market cap floor ($300M minimum)
            mcap = profile.get("marketCap", 0) or 0
            if mcap < 150_000_000:
                print(f"    {symbol}: market cap ${mcap/1e6:.0f}M < $150M floor, skipping")
                stock["_fmp_excluded"] = True
                continue

        growth = fetch_fmp_growth(symbol, fmp_key)
        estimates = fetch_fmp_estimates(symbol, fmp_key)
        targets = fetch_fmp_targets(symbol, fmp_key)
        grades_score = fetch_fmp_grades(symbol, fmp_key)

        # Apply FMP data to stock
        enrich_with_fmp(stock, ratios, growth, estimates, targets, profile, grades_score)
        fmp_enriched += 1

        if (i + 1) % 10 == 0:
            print(f"    [{i+1}/{len(candidates)}] enriched {fmp_enriched}")

        # Pacing: 6 calls per stock at 300/min = 50 stocks/min max.
        # Add 1s between stocks for safety margin.
        if i < len(candidates) - 1:
            time.sleep(1)

    # Output: return ALL stocks EXCEPT those excluded by FMP (not on major exchanges)
    # Remove excluded stocks from candidates (not found in FMP, or below market cap)
    final_candidates = [s for s in candidates if not s.get("_fmp_excluded")]
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    pe_count = sum(1 for s in final_candidates if s.get("pe_ratio") is not None)
    peg_count = sum(1 for s in final_candidates if s.get("peg_ratio") is not None)

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
                    for industry, median_pe in industry_pe_quartiles.items():
                        q1_pe = industry_pe_q1.get(industry, median_pe)
                        # Update existing INDUSTRY_AVG item with both pe thresholds
                        _table.update_item(
                            Key={"PK": f"INDUSTRY_AVG#{industry}", "SK": "METRICS"},
                            UpdateExpression="SET pe_median = :med, pe_lower_quartile = :q1, pe_updated = :d",
                            ExpressionAttributeValues={
                                ":med": Decimal(str(median_pe)),
                                ":q1": Decimal(str(q1_pe)),
                                ":d": today,
                            },
                        )
                print(f"  Persisted P/E median + Q1 for {len(industry_pe_quartiles)} industries")
        except Exception as e:
            print(f"  Warning: Could not persist P/E quartiles: {e}")

    result = {
        "enriched_stocks": final_candidates,
        "metadata": {
            "total_stocks": len(passing),
            "prices_matched": sum(1 for s in final_candidates if s.get("price")),
            "local_prefilter_pass": len(candidates),
            "fmp_enriched": fmp_enriched,
            "pe_available": pe_count,
            "peg_available": peg_count,
            "trading_date": trading_date,
            "fmp_calls": fmp_enriched * 6,
            "industries_with_pe_quartile": len(industry_pe_quartiles),
            "duration_seconds": duration,
            "timestamp": end_time.isoformat(),
        },
    }

    print(f"Done in {duration:.1f}s. FMP calls: {fmp_enriched * 6}")
    return write_pipeline_output(result, step_name="step3_enriched")
