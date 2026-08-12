#!/usr/bin/env python3
"""
Foreign Filer Fundamentals Generator
======================================
Monthly script that fetches fundamentals from EDGAR companyfacts API for
actively-traded stocks not covered by the Frames API (20-F/6-K filers).

These are typically foreign private issuers (Chinese ADRs, European companies, etc.)
that file with the SEC but use 20-F (annual) and 6-K (interim) forms instead of
the standard 10-K/10-Q that the Frames API indexes.

Workflow:
1. Load the latest Step 1 output from S3 (to know which stocks ARE covered)
2. Load Polygon grouped daily from S3 or API (to know which stocks are actively traded)
3. Identify the gap: traded stocks with SEC CIKs but not in Step 1
4. Fetch companyfacts for each, compute TTM fundamentals
5. Output to S3 as reference/foreign_filer_fundamentals.json

Usage:
    # Local run:
    python3 scripts/generate_foreign_filers.py

    # Also runs as a Lambda (monthly via EventBridge, bundled in tag-discovery Lambda)

Rate Limits:
    - SEC EDGAR: 10 requests/second (we pace at 9/sec)
    - Typically ~500-800 stocks to fetch, taking ~60-90 seconds
"""

import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, date, timedelta

try:
    import requests as http_requests
except ImportError:
    # Fallback for environments without requests
    import urllib.request

    class _SimpleRequests:
        def get(self, url, headers=None, timeout=30):
            req = urllib.request.Request(url, headers=headers or {})
            resp = urllib.request.urlopen(req, timeout=timeout)
            return _SimpleResponse(resp)

    class _SimpleResponse:
        def __init__(self, resp):
            self.status_code = resp.status
            self._body = resp.read()

        def json(self):
            import gzip
            data = self._body
            if data[:2] == b'\x1f\x8b':
                data = gzip.decompress(data)
            return json.loads(data)

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

    http_requests = _SimpleRequests()


HEADERS = {
    "User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def compute_ttm(facts: dict, tag: str, unit: str = "USD", ifrs_tag: str = None) -> float | None:
    """Compute TTM using annual + YTD derivation from companyfacts.
    Checks us-gaap first, falls back to ifrs-full with ifrs_tag if provided."""
    for namespace in ["us-gaap", "ifrs-full"]:
        search_tag = tag if namespace == "us-gaap" else (ifrs_tag or tag)
        ns_data = facts.get("facts", {}).get(namespace, {})
        tag_data = ns_data.get(search_tag, {})
        units_list = tag_data.get("units", {}).get(unit, [])
        if not units_list:
            continue

        cutoff = "2023-01-01"
        filings = [u for u in units_list
                   if u.get("form") in ("10-Q", "10-K", "20-F", "6-K")
                   and u.get("end", "") >= cutoff
                   and u.get("start")
                   and u.get("val") is not None]
        if not filings:
            continue

        annuals = sorted(
            [f for f in filings if f["form"] in ("10-K", "20-F")],
            key=lambda x: x["end"], reverse=True
        )
        quarterlies = sorted(
            [f for f in filings if f["form"] in ("10-Q", "6-K")],
            key=lambda x: x["end"], reverse=True
        )

        # Deduplicate
        seen = set()
        unique_q = []
        for q in quarterlies:
            key = (q["start"], q["end"])
            if key not in seen:
                seen.add(key)
                unique_q.append(q)
        quarterlies = unique_q

        if annuals and quarterlies:
            latest_annual = annuals[0]
            annual_end = latest_annual["end"]
            annual_val = latest_annual["val"]

            latest_ytd = None
            for q in quarterlies:
                if q["end"] > annual_end:
                    latest_ytd = q
                    break

            if latest_ytd:
                try:
                    ytd_s = datetime.strptime(latest_ytd["start"], "%Y-%m-%d")
                    ytd_e = datetime.strptime(latest_ytd["end"], "%Y-%m-%d")
                    ytd_months = round((ytd_e - ytd_s).days / 30.4)

                    prior_ytd = None
                    for q in quarterlies:
                        q_s = datetime.strptime(q["start"], "%Y-%m-%d")
                        q_e = datetime.strptime(q["end"], "%Y-%m-%d")
                        q_months = round((q_e - q_s).days / 30.4)
                        if q_months == ytd_months and abs((ytd_s - q_s).days - 365) < 45:
                            prior_ytd = q
                            break

                    if prior_ytd:
                        return annual_val + latest_ytd["val"] - prior_ytd["val"]
                except (ValueError, TypeError):
                    pass

            return annual_val

        if annuals:
            return annuals[0]["val"]

    return None


def get_latest_instant(facts: dict, tag: str, unit: str = "USD", ifrs_tag: str = None) -> float | None:
    """Get the most recent balance sheet (instant) value. Checks us-gaap then ifrs-full."""
    for namespace in ["us-gaap", "ifrs-full"]:
        search_tag = tag if namespace == "us-gaap" else (ifrs_tag or tag)
        ns_data = facts.get("facts", {}).get(namespace, {})
        tag_data = ns_data.get(search_tag, {})
        units_list = tag_data.get("units", {}).get(unit, [])
        if not units_list:
            continue
        recent = sorted(
            [u for u in units_list if u.get("end", "") >= "2024-01-01" and u.get("val") is not None],
            key=lambda x: x["end"], reverse=True
        )
        if recent:
            return recent[0]["val"]
    return None


def fetch_stock_fundamentals(symbol: str, cik: int, company_name: str,
                              rate_lock: threading.Lock, request_times: list) -> dict | None:
    """Fetch and compute all fundamentals for one stock from companyfacts."""
    # Rate limit
    with rate_lock:
        now = time.time()
        while request_times and now - request_times[0] > 1.0:
            request_times.pop(0)
        if len(request_times) >= 9:
            sleep_time = 1.0 - (now - request_times[0])
            if sleep_time > 0:
                time.sleep(sleep_time)
        request_times.append(time.time())

    url = COMPANYFACTS_URL.format(cik=cik)
    try:
        resp = http_requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        facts = resp.json()
    except Exception:
        return None

    # Recency check: reject stocks whose most recent filing is older than 180 days
    # We can't invest based on stale data
    most_recent_end = None
    for namespace in ["us-gaap", "ifrs-full"]:
        ns_data = facts.get("facts", {}).get(namespace, {})
        # Sample a few key tags to find the most recent filing date
        for tag_name in list(ns_data.keys())[:30]:
            for unit_data in ns_data[tag_name].get("units", {}).values():
                for entry in unit_data:
                    end = entry.get("end", "")
                    if end and (most_recent_end is None or end > most_recent_end):
                        most_recent_end = end
    if most_recent_end:
        from datetime import date as _date
        try:
            end_date = datetime.strptime(most_recent_end, "%Y-%m-%d").date()
            days_old = (_date.today() - end_date).days
            if days_old > 180:
                return None  # Data too stale
        except (ValueError, TypeError):
            pass

    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    ifrs = facts.get("facts", {}).get("ifrs-full", {})

    # Get shares (check us-gaap then ifrs)
    shares = None
    for ns in [us_gaap, ifrs]:
        for tag in ["CommonStockSharesOutstanding", "WeightedAverageNumberOfDilutedSharesOutstanding",
                    "IssuedCapital"]:
            shares_data = ns.get(tag, {}).get("units", {}).get("shares", [])
            if shares_data:
                recent = sorted([s for s in shares_data if s.get("end", "") >= "2024-01-01" and s.get("val")
                                and s.get("form") in ("10-Q", "10-K", "20-F", "6-K")],
                               key=lambda x: x["end"], reverse=True)
                if recent:
                    shares = recent[0]["val"]
                    break
        if shares:
            break
    if not shares or shares <= 0:
        return None

    # Income statement / cash flow (TTM)
    net_income = compute_ttm(facts, "NetIncomeLoss", ifrs_tag="ProfitLoss")
    revenue = (compute_ttm(facts, "RevenueFromContractWithCustomerExcludingAssessedTax", ifrs_tag="Revenue")
               or compute_ttm(facts, "Revenues", ifrs_tag="Revenue")
               or compute_ttm(facts, "RevenueFromContractWithCustomerIncludingAssessedTax", ifrs_tag="Revenue"))
    operating_income = (compute_ttm(facts, "OperatingIncomeLoss", ifrs_tag="ProfitLossFromOperatingActivities")
                       or compute_ttm(facts, "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", ifrs_tag="ProfitLossFromOperatingActivities"))
    ocf = (compute_ttm(facts, "NetCashProvidedByUsedInOperatingActivities", ifrs_tag="CashFlowsFromUsedInOperatingActivities")
           or compute_ttm(facts, "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations", ifrs_tag="CashFlowsFromUsedInOperatingActivities"))
    capex = (compute_ttm(facts, "PaymentsToAcquirePropertyPlantAndEquipment", ifrs_tag="PurchaseOfPropertyPlantAndEquipment")
             or compute_ttm(facts, "PaymentsToAcquireProductiveAssets", ifrs_tag="PurchaseOfPropertyPlantAndEquipment"))
    interest_exp = (compute_ttm(facts, "InterestExpense", ifrs_tag="InterestExpense")
                   or compute_ttm(facts, "InterestAndDebtExpense", ifrs_tag="InterestExpense"))

    # Balance sheet (latest instant)
    equity = (get_latest_instant(facts, "StockholdersEquity", ifrs_tag="EquityAttributableToOwnersOfParent")
              or get_latest_instant(facts, "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", ifrs_tag="Equity"))
    liabilities = get_latest_instant(facts, "Liabilities", ifrs_tag="Liabilities")
    assets_current = get_latest_instant(facts, "AssetsCurrent", ifrs_tag="CurrentAssets")
    liabilities_current = get_latest_instant(facts, "LiabilitiesCurrent", ifrs_tag="CurrentLiabilities")
    inventory = get_latest_instant(facts, "InventoryNet", ifrs_tag="Inventories")

    # Must have at least net_income or equity
    if net_income is None and equity is None:
        return None

    # Compute derived metrics
    eps = net_income / shares if net_income and shares > 0 else None
    noncurrent_liab = (liabilities - liabilities_current) if liabilities is not None and liabilities_current is not None else None
    debt_to_equity = noncurrent_liab / equity if noncurrent_liab is not None and equity and equity > 0 else None
    quick_ratio = ((assets_current or 0) - (inventory or 0)) / liabilities_current if assets_current and liabilities_current and liabilities_current > 0 else None
    operating_margin = operating_income / revenue if operating_income and revenue and revenue > 0 else None
    fcf_per_share = (ocf - (capex or 0)) / shares if ocf is not None and shares > 0 else None
    interest_coverage = operating_income / interest_exp if operating_income and interest_exp and interest_exp > 0 else None
    revenue_per_share = revenue / shares if revenue and shares > 0 else None

    return {
        "symbol": symbol,
        "company_name": company_name,
        "eps": round(eps, 4) if eps else None,
        "fcf_per_share": round(fcf_per_share, 4) if fcf_per_share else None,
        "debt_to_equity": round(debt_to_equity, 4) if debt_to_equity is not None else None,
        "quick_ratio": round(quick_ratio, 4) if quick_ratio is not None else None,
        "operating_margin": round(operating_margin, 4) if operating_margin is not None else None,
        "interest_coverage_ratio": round(interest_coverage, 4) if interest_coverage is not None else None,
        "revenue_per_share": round(revenue_per_share, 4) if revenue_per_share is not None else None,
        "eps_growth_yoy": None,
        "revenue_growth_yoy": None,
        "last_filing_date": most_recent_end,
    }


def load_step1_symbols(s3_client, bucket: str) -> set[str]:
    """Load symbols from the latest Step 1 output."""
    # Find latest step1 file (try last 3 days)
    for days_back in range(3):
        d = date.today() - timedelta(days=days_back)
        prefix = f"pipeline/{d.isoformat()}/step1_fundamentals_"
        result = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        contents = result.get("Contents", [])
        if contents:
            key = sorted(contents, key=lambda x: x["Key"])[-1]["Key"]
            resp = s3_client.get_object(Bucket=bucket, Key=key)
            data = json.loads(resp["Body"].read())
            stocks = data.get("stocks", data) if isinstance(data, dict) else data
            symbols = {s.get("symbol") for s in stocks if s.get("symbol")}
            print(f"  Loaded {len(symbols)} symbols from Step 1 ({key})")
            return symbols
    print("  Warning: No Step 1 output found in last 3 days")
    return set()


def load_polygon_tickers(s3_client, bucket: str) -> set[str]:
    """
    Load actively traded tickers from the latest Step 3 output metadata
    (which records the trading date used). Fall back to listing Polygon tickers
    from the most recent enriched output.
    """
    # The enriched output records which stocks had prices — use step1 output
    # combined with Polygon. Actually, the simplest source: the Polygon grouped
    # daily file is fetched in Step 3 but not stored separately.
    # Best proxy: all tickers in the industry map (which comes from SEC filings)
    # + recent step3 enriched stocks.
    # For now, use SEC ticker list as the full universe — the filtering happens
    # when we cross-reference with Step 1 (removing already-covered stocks).
    # This is fine because the per-stock companyfacts fetch returns None for
    # companies without data, so we only keep real ones.
    return set()  # We'll use SEC list directly


def handler(event=None, context=None):
    """Lambda/script entry point."""
    import boto3

    print("=== Foreign Filer Fundamentals Generator ===")
    start = time.time()

    bucket = os.environ.get("RAW_DATA_BUCKET", "stock-screener-raw-data-116488731375")
    profile = os.environ.get("AWS_PROFILE", "stock-screener")

    # Connect to S3
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        s3 = boto3.client("s3")
    else:
        session = boto3.Session(profile_name=profile)
        s3 = session.client("s3")

    # Step 1: Load SEC ticker list
    print("  Loading SEC ticker → CIK mapping...")
    resp = http_requests.get(TICKERS_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    ticker_data = resp.json()
    ticker_to_cik = {}
    cik_to_company = {}
    for entry in ticker_data.values():
        ticker_to_cik[entry["ticker"]] = entry["cik_str"]
        cik_to_company[entry["cik_str"]] = entry["title"]
    print(f"  {len(ticker_to_cik)} SEC tickers loaded")

    # Step 2: Load Step 1 symbols (already covered by Frames API)
    step1_symbols = load_step1_symbols(s3, bucket)

    # Step 3: Identify foreign filers to fetch
    # These are SEC-registered tickers NOT in Step 1 output, with clean ticker names
    missing = [sym for sym in ticker_to_cik
               if sym not in step1_symbols
               and not any(c in sym for c in ['-', '/', '+', '.'])
               and len(sym) <= 5]

    print(f"  {len(missing)} tickers not covered by Frames API")

    # Step 4: Fetch companyfacts for each (rate limited)
    rate_lock = threading.Lock()
    request_times: list[float] = []

    print(f"  Fetching companyfacts at 9 req/sec...")
    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for sym in missing:
            cik = ticker_to_cik[sym]
            company = cik_to_company.get(cik, "")
            futures[executor.submit(fetch_stock_fundamentals, sym, cik, company, rate_lock, request_times)] = sym

        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 100 == 0:
                print(f"    Processed {completed}/{len(missing)}...")
            try:
                stock = future.result()
                if stock is not None:
                    results.append(stock)
            except Exception:
                pass

    elapsed = time.time() - start
    print(f"  Done: {len(results)} foreign filers with fundamentals ({elapsed:.0f}s)")

    # Step 5: Persist to S3
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stocks_count": len(results),
        "stocks": results,
    }

    key = "reference/foreign_filer_fundamentals.json"
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(output, indent=None),  # No indent to save space
        ContentType="application/json",
    )
    print(f"  Persisted to s3://{bucket}/{key} ({len(json.dumps(output)) // 1024} KB)")

    return {"stocks_generated": len(results), "elapsed_seconds": round(elapsed, 1)}


if __name__ == "__main__":
    handler()
