"""Check availability of the 4 quarters needed for TTM EPS."""
import requests

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap/NetIncomeLoss/USD"

# TTM = sum of most recent 4 quarters
# As of July 2026: CY2026Q1, CY2025Q4, CY2025Q3, CY2025Q2
quarters = ["CY2026Q1", "CY2025Q4", "CY2025Q3", "CY2025Q2"]

print("TTM quarters needed for EPS:")
for q in quarters:
    resp = requests.get(f"{BASE}/{q}.json", headers=HEADERS, timeout=30)
    if resp.status_code == 200:
        count = len(resp.json().get("data", []))
        print(f"  {q}: {count} companies")
    else:
        print(f"  {q}: HTTP {resp.status_code}")
