"""
Check coverage of BROAD financial tags that are near-universal.

Instead of chasing specific debt variants, use higher-level aggregates
that every company MUST report regardless of how they break down the details.
"""
import requests
import time

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap"

# Broad tags that should be universal (every balance sheet has these)
BROAD_TAGS = [
    # Total level — every company reports these
    ("Liabilities", "USD", "CY2026Q1I", "Total Liabilities"),
    ("LiabilitiesNoncurrent", "USD", "CY2026Q1I", "Non-current Liabilities (≈ long-term debt + other)"),
    ("LiabilitiesCurrent", "USD", "CY2026Q1I", "Current Liabilities"),
    ("Assets", "USD", "CY2026Q1I", "Total Assets"),
    ("StockholdersEquity", "USD", "CY2026Q1I", "Total Equity"),
    ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "USD", "CY2026Q1I", "Total Equity (incl NCI)"),
    # Revenue at broadest level
    ("Revenues", "USD", "CY2026Q1", "Revenues (broad)"),
    ("RevenueFromContractWithCustomerExcludingAssessedTax", "USD", "CY2026Q1", "Revenue from contracts"),
    # Income
    ("NetIncomeLoss", "USD", "CY2026Q1", "Net Income"),
    ("OperatingIncomeLoss", "USD", "CY2026Q1", "Operating Income"),
    ("GrossProfit", "USD", "CY2026Q1", "Gross Profit"),
]

print(f"{'Description':<55} {'Tag':<60} {'Coverage':>8}")
print("=" * 125)

for tag, unit, frame, desc in BROAD_TAGS:
    url = f"{BASE}/{tag}/{unit}/{frame}.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code == 200:
        count = len(resp.json().get("data", []))
        print(f"{desc:<55} {tag:<60} {count:>8}")
    else:
        print(f"{desc:<55} {tag:<60} {'N/A':>8}")
    time.sleep(0.15)
