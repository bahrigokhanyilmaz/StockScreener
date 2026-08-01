"""
Check why CTSH, LCII, SEIC have None for D/E, OpMargin, RevGrowth.
Lookup their CIK and check which frames they appear in.
"""
import requests
import time

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap"

# Get CIKs
resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
t2c = {e["ticker"]: int(e["cik_str"]) for e in resp.json().values()}

test_tickers = ['CTSH', 'LCII', 'SEIC', 'CDE']

# Check each relevant metric in both instant and duration frames
metrics_to_check = [
    ("StockholdersEquity", "USD", "CY2026Q1I"),
    ("StockholdersEquity", "USD", "CY2025Q4I"),
    ("LongTermDebt", "USD", "CY2026Q1I"),
    ("LongTermDebt", "USD", "CY2025Q4I"),
    ("OperatingIncomeLoss", "USD", "CY2026Q1"),
    ("RevenueFromContractWithCustomerExcludingAssessedTax", "USD", "CY2026Q1"),
    ("Revenues", "USD", "CY2026Q1"),
]

print(f"{'Tag':<55} {'Frame':<12} ", end="")
for t in test_tickers:
    print(f"{t:<8}", end="")
print()
print("-" * 100)

for tag, unit, frame in metrics_to_check:
    url = f"{BASE}/{tag}/{unit}/{frame}.json"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        print(f"{tag:<55} {frame:<12} HTTP {resp.status_code}")
        time.sleep(0.1)
        continue
    data = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
    
    print(f"{tag:<55} {frame:<12} ", end="")
    for t in test_tickers:
        cik = t2c.get(t)
        val = data.get(cik)
        if val is not None:
            if abs(val) >= 1e9:
                print(f"${val/1e9:.1f}B  ", end="")
            elif abs(val) >= 1e6:
                print(f"${val/1e6:.0f}M   ", end="")
            else:
                print(f"{val:<8.0f}", end="")
        else:
            print(f"{'MISSING':<8}", end="")
    print()
    time.sleep(0.2)
