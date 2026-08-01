"""
Research ALL interest expense related tags in EDGAR Frames API.
The US-GAAP taxonomy is finite — check every known variant.
"""
import requests
import time

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap"

# All known US-GAAP interest expense tags from the taxonomy
INTEREST_TAGS = [
    "InterestExpense",
    "InterestExpenseDebt",
    "InterestExpenseOperating",  
    "InterestAndDebtExpense",
    "InterestExpenseBorrowings",
    "InterestExpenseOther",
    "InterestExpenseLongTermDebt",
    "InterestExpenseShortTermBorrowings",
    "InterestExpenseDeposits",
    "InterestExpenseFederalFundsPurchasedAndSecuritiesSoldUnderAgreementsToRepurchase",
    "InterestIncomeExpenseNet",
    "InterestPaid",
    "InterestPaidNet",
    "InterestCostsIncurred",
    "FinanceLeaseInterestExpense",
]

# Check CY2026Q1 (duration, latest quarter)
print(f"{'Tag':<70} {'Coverage':>8}")
print("=" * 80)

total_union = set()
tag_data = {}

for tag in INTEREST_TAGS:
    url = f"{BASE}/{tag}/USD/CY2026Q1.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code == 200:
        data = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
        count = len(data)
        tag_data[tag] = data
        total_union.update(data.keys())
        print(f"{tag:<70} {count:>8}")
    elif resp.status_code == 404:
        print(f"{tag:<70} {'N/A':>8}")
    else:
        print(f"{tag:<70} {'ERR':>8}")
    time.sleep(0.15)

print(f"\n{'TOTAL UNIQUE COMPANIES (union of all tags)':<70} {len(total_union):>8}")

# Check coverage for our problem companies
print("\n\nChecking AAPL, CRM, AMZN:")
resp2 = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
t2c = {e["ticker"]: int(e["cik_str"]) for e in resp2.json().values()}

for ticker in ['AAPL', 'CRM', 'AMZN', 'MSFT', 'META']:
    cik = t2c.get(ticker)
    found_in = []
    for tag, data in tag_data.items():
        if cik in data:
            found_in.append(f"{tag}=${data[cik]/1e6:.0f}M")
    if found_in:
        print(f"  {ticker}: {', '.join(found_in)}")
    else:
        print(f"  {ticker}: NOT IN ANY interest expense tag")
