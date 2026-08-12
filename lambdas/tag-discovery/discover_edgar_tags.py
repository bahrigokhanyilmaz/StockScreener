#!/usr/bin/env python3
"""
EDGAR Tag Discovery Script
===========================
Queries the SEC EDGAR companyfacts API for companies with missing metrics
and identifies which XBRL tags they actually use.

Purpose: Find alternative XBRL tags our pipeline should be fetching to
improve coverage. The output is a frequency report + recommended tag list
that gets persisted to S3 as reference/discovered_tags.json.

Usage:
    # One-time local run (uses latest Step 1 output from S3):
    python3 scripts/discover_edgar_tags.py

    # Also runs as a Lambda (monthly scheduled via EventBridge)

Rate Limits:
    - SEC EDGAR: 10 requests/second with User-Agent header
    - We pace at 0.12s between requests (safe under limit)
    - Samples up to 50 companies per metric gap (not all 500+)

Output:
    - Prints frequency report to stdout
    - Uploads discovered_tags.json to S3: reference/discovered_tags.json
"""

import json
import time
import os
import sys
from collections import defaultdict, Counter

try:
    import requests as http_requests
except ImportError:
    import urllib.request
    import urllib.error

    class _SimpleRequests:
        """Minimal requests-like wrapper for environments without requests."""
        def get(self, url, headers=None, timeout=30):
            req = urllib.request.Request(url, headers=headers or {})
            try:
                resp = urllib.request.urlopen(req, timeout=timeout)
                return _SimpleResponse(resp)
            except urllib.error.HTTPError as e:
                return _SimpleResponse(e, error=True)

        class _Resp:
            pass

    class _SimpleResponse:
        def __init__(self, resp, error=False):
            self._resp = resp
            self.status_code = resp.status if not error else resp.code
            self._body = resp.read()

        def json(self):
            return json.loads(self._body)

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

    http_requests = _SimpleRequests()


# SEC requires contact info in User-Agent
HEADERS = {
    "User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# Metrics we want to discover tags for, mapped to keyword patterns
# that identify relevant tags in companyfacts output.
METRIC_SEARCH_PATTERNS = {
    "operating_cash_flow": {
        "keywords": ["NetCashProvided", "CashFlowsFromOperating", "OperatingActivities"],
        "unit": "USD",
        "concept": "Cash from operations",
    },
    "capex": {
        "keywords": ["PaymentsToAcquire", "CapitalExpenditure", "PurchaseOfProperty"],
        "unit": "USD",
        "concept": "Capital expenditures",
    },
    "operating_income": {
        "keywords": ["OperatingIncome", "IncomeLossFromContinuingOperations", "OperatingProfit"],
        "unit": "USD",
        "concept": "Operating income / profit",
    },
    "stockholders_equity": {
        "keywords": ["StockholdersEquity", "Equity"],
        "unit": "USD",
        "concept": "Shareholders equity",
    },
    "revenue": {
        "keywords": ["Revenue", "SalesRevenue", "NetSales"],
        "unit": "USD",
        "concept": "Revenue / Sales",
    },
}

# Tags we already fetch (to exclude from "new" discoveries)
EXISTING_TAGS = {
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "stockholders_equity": ["StockholdersEquity"],
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
    ],
}


def fetch_company_facts(cik: int) -> dict:
    """Fetch all XBRL facts for a single company."""
    url = COMPANYFACTS_URL.format(cik=cik)
    try:
        resp = http_requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"    Warning: Failed to fetch CIK {cik}: {e}")
        return {}


def find_matching_tags(facts: dict, metric_name: str) -> list[str]:
    """
    Search a company's facts for tags matching our metric patterns.
    Returns list of matching XBRL tag names.
    """
    patterns = METRIC_SEARCH_PATTERNS[metric_name]
    keywords = patterns["keywords"]
    unit_type = patterns["unit"]

    matching = []
    us_gaap = facts.get("facts", {}).get("us-gaap", {})

    for tag_name, tag_data in us_gaap.items():
        # Check if tag name contains any of our keywords (case-insensitive)
        tag_lower = tag_name.lower()
        if any(kw.lower() in tag_lower for kw in keywords):
            # Verify it has USD data (not just shares or pure)
            units = tag_data.get("units", {})
            if unit_type in units:
                # Has relevant data — this is a candidate
                matching.append(tag_name)

    return matching


def run_discovery(step1_stocks: list[dict], ticker_to_cik: dict[str, int],
                  sample_size: int = 50) -> dict:
    """
    Run tag discovery for all metrics with coverage gaps.

    Args:
        step1_stocks: List of stock dicts from Step 1 output
        ticker_to_cik: Mapping of ticker → CIK
        sample_size: Max companies to query per metric gap

    Returns:
        Dict with discovered tags per metric + frequency counts
    """
    # Identify which stocks are missing each metric
    missing_by_metric = {
        "operating_cash_flow": [],  # Stocks with EPS but no fcf_per_share
        "capex": [],                # Same set — FCF needs both OCF and capex
        "operating_income": [],     # Stocks with revenue but no operating_margin
        "stockholders_equity": [],  # Stocks with no debt_to_equity
        "revenue": [],              # Stocks with no revenue_per_share
    }

    for stock in step1_stocks:
        symbol = stock.get("symbol", "")
        cik = ticker_to_cik.get(symbol)
        if not cik:
            continue

        # FCF gap: has net_income (so has shares) but no fcf_per_share
        if stock.get("eps") is not None and stock.get("fcf_per_share") is None:
            missing_by_metric["operating_cash_flow"].append((symbol, cik))
            missing_by_metric["capex"].append((symbol, cik))

        # Operating income gap: has revenue but no operating_margin
        if stock.get("revenue_per_share") is not None and stock.get("operating_margin") is None:
            missing_by_metric["operating_income"].append((symbol, cik))

        # Equity gap: no debt_to_equity despite having liabilities data
        if stock.get("debt_to_equity") is None and stock.get("quick_ratio") is not None:
            missing_by_metric["stockholders_equity"].append((symbol, cik))

        # Revenue gap: has EPS (so has shares + net_income) but no revenue
        if stock.get("eps") is not None and stock.get("revenue_per_share") is None:
            missing_by_metric["revenue"].append((symbol, cik))

    # Discover tags for each metric
    results = {}
    all_queried_ciks = set()  # Avoid querying same CIK twice

    for metric_name, missing_stocks in missing_by_metric.items():
        if not missing_stocks:
            print(f"\n  {metric_name}: No gaps detected — skipping")
            results[metric_name] = {"gap_count": 0, "tag_frequencies": {}, "new_tags": []}
            continue

        print(f"\n  {metric_name}: {len(missing_stocks)} stocks with gaps, sampling {min(sample_size, len(missing_stocks))}")

        # Sample diversely (take first N — they're already in EDGAR order)
        sample = missing_stocks[:sample_size]
        tag_counter = Counter()
        sampled_count = 0

        for symbol, cik in sample:
            # Check if we already fetched this CIK for another metric
            if cik in all_queried_ciks:
                # Re-use from cache would be ideal, but for simplicity just skip
                # (the Lambda version will cache)
                pass

            facts = fetch_company_facts(cik)
            all_queried_ciks.add(cik)
            time.sleep(0.12)  # Rate limit: ~8 req/sec (under 10/sec limit)

            if not facts:
                continue

            sampled_count += 1
            matching_tags = find_matching_tags(facts, metric_name)
            for tag in matching_tags:
                tag_counter[tag] += 1

        # Filter out tags we already fetch
        existing = set(EXISTING_TAGS.get(metric_name, []))
        new_tags = {tag: count for tag, count in tag_counter.most_common()
                    if tag not in existing and count >= 3}  # At least 3 companies use it

        results[metric_name] = {
            "gap_count": len(missing_stocks),
            "sampled": sampled_count,
            "tag_frequencies": dict(tag_counter.most_common(20)),
            "new_tags": list(new_tags.keys()),
            "existing_tags": list(existing),
        }

        print(f"    Sampled {sampled_count} companies")
        print(f"    Top tags found:")
        for tag, count in tag_counter.most_common(10):
            marker = " ← ALREADY FETCHING" if tag in existing else " ← NEW" if count >= 3 else ""
            print(f"      {tag}: {count}/{sampled_count}{marker}")

    return results


def load_step1_from_s3():
    """Load latest Step 1 output from S3."""
    import boto3

    bucket = os.environ.get("RAW_DATA_BUCKET", "stock-screener-raw-data-116488731375")
    profile = os.environ.get("AWS_PROFILE", "stock-screener")

    # If running locally, use profile; in Lambda, use IAM role
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        s3 = boto3.client("s3")
    else:
        session = boto3.Session(profile_name=profile)
        s3 = session.client("s3")

    # Find latest step1 file
    from datetime import date
    today = date.today().isoformat()

    # Try today, then yesterday, then day before
    for days_back in range(3):
        d = date.today()
        from datetime import timedelta
        d = d - timedelta(days=days_back)
        prefix = f"pipeline/{d.isoformat()}/step1_fundamentals_"
        result = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        contents = result.get("Contents", [])
        if contents:
            # Take the latest one
            key = sorted(contents, key=lambda x: x["Key"])[-1]["Key"]
            print(f"  Loading Step 1 from: s3://{bucket}/{key}")
            resp = s3.get_object(Bucket=bucket, Key=key)
            data = json.loads(resp["Body"].read())
            return data.get("stocks", data) if isinstance(data, dict) else data

    raise RuntimeError("No Step 1 output found in last 3 days")


def load_ticker_to_cik() -> dict[str, int]:
    """Load SEC ticker → CIK mapping."""
    print("  Loading SEC ticker → CIK mapping...")
    url = "https://www.sec.gov/files/company_tickers.json"
    resp = http_requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    mapping = {}
    for entry in data.values():
        mapping[entry["ticker"]] = entry["cik_str"]
    print(f"  Loaded {len(mapping)} tickers")
    return mapping


def persist_results(results: dict):
    """Save discovered tags to S3 for the pipeline to reference."""
    import boto3
    from datetime import datetime, timezone

    bucket = os.environ.get("RAW_DATA_BUCKET", "stock-screener-raw-data-116488731375")

    output = {
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "metrics": results,
        # Build the recommended tag map (what the pipeline should fetch)
        "recommended_tags": {},
    }

    for metric_name, metric_data in results.items():
        existing = metric_data.get("existing_tags", [])
        new = metric_data.get("new_tags", [])
        output["recommended_tags"][metric_name] = existing + new

    # If running locally, use profile
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        s3 = boto3.client("s3")
    else:
        session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "stock-screener"))
        s3 = session.client("s3")

    key = "reference/discovered_tags.json"
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(output, indent=2),
        ContentType="application/json",
    )
    print(f"\n  Persisted to s3://{bucket}/{key}")

    return output


def handler(event=None, context=None):
    """Lambda handler for monthly scheduled execution."""
    print("=== EDGAR Tag Discovery ===")

    stocks = load_step1_from_s3()
    print(f"  Loaded {len(stocks)} stocks from Step 1")

    ticker_to_cik = load_ticker_to_cik()

    sample_size = int(os.environ.get("DISCOVERY_SAMPLE_SIZE", "50"))
    results = run_discovery(stocks, ticker_to_cik, sample_size=sample_size)

    output = persist_results(results)

    # Summary
    print("\n=== DISCOVERY SUMMARY ===")
    total_new = 0
    for metric, data in results.items():
        new_count = len(data.get("new_tags", []))
        total_new += new_count
        if new_count > 0:
            print(f"  {metric}: +{new_count} new tags → {data['new_tags']}")
        else:
            print(f"  {metric}: no new tags needed (gap: {data['gap_count']} stocks)")

    print(f"\n  Total new tags discovered: {total_new}")
    print(f"  Recommended tag map saved to S3")

    return output


if __name__ == "__main__":
    handler()
