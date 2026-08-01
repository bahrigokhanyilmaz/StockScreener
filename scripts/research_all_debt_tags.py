"""
Research: How many companies does each debt-related XBRL frame cover?

The US-GAAP taxonomy has a FINITE set of tags. This script checks coverage
of ALL known debt-related tags in the Frames API to find the combination
that gives complete coverage. Same for revenue and operating income.

This is not sampling — it's checking the actual EDGAR database.
"""
import requests
import time

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap"

# Known US-GAAP debt tags (from the taxonomy, not guessing)
DEBT_TAGS = [
    "LongTermDebt",
    "LongTermDebtNoncurrent",
    "LongTermDebtCurrent",
    "DebtInstrumentCarryingAmount",
    "LongTermLineOfCredit",
    "LongTermNotesPayable",
    "SecuredLongTermDebt",
    "UnsecuredLongTermDebt",
    "ConvertibleLongTermNotesPayable",
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebtFairValue",
]

REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueGoodsNet",
    "SalesRevenueServicesNet",
    "NetRevenue",
    "TotalRevenue",
]

OPERATING_INCOME_TAGS = [
    "OperatingIncomeLoss",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesForeign",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
]

def check_tag_coverage(tag, unit, frame):
    url = f"{BASE}/{tag}/{unit}/{frame}.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code == 200:
        return len(resp.json().get("data", []))
    return 0

print("DEBT TAGS — checking CY2026Q1I (instant) coverage:")
print(f"{'Tag':<55} {'Companies':>10}")
print("-" * 67)
total_debt_ciks = set()
for tag in DEBT_TAGS:
    count = check_tag_coverage(tag, "USD", "CY2026Q1I")
    # Also check CY2025Q4I if primary is low
    if count == 0:
        count = check_tag_coverage(tag, "USD", "CY2025Q4I")
        if count > 0:
            print(f"{tag:<55} {count:>10} (Q4I)")
        else:
            print(f"{tag:<55} {'0/NOT FOUND':>10}")
    else:
        print(f"{tag:<55} {count:>10}")
    time.sleep(0.15)

print(f"\n\nREVENUE TAGS — checking CY2026Q1 (duration) coverage:")
print(f"{'Tag':<60} {'Companies':>10}")
print("-" * 72)
for tag in REVENUE_TAGS:
    count = check_tag_coverage(tag, "USD", "CY2026Q1")
    if count > 0:
        print(f"{tag:<60} {count:>10}")
    else:
        print(f"{tag:<60} {'0/NOT FOUND':>10}")
    time.sleep(0.15)

print(f"\n\nOPERATING INCOME TAGS — checking CY2026Q1 (duration) coverage:")
print(f"{'Tag':<80} {'Companies':>10}")
print("-" * 92)
for tag in OPERATING_INCOME_TAGS:
    count = check_tag_coverage(tag, "USD", "CY2026Q1")
    if count > 0:
        print(f"{tag:<80} {count:>10}")
    else:
        print(f"{tag:<80} {'0/NOT FOUND':>10}")
    time.sleep(0.15)
