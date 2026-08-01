"""Check shares outstanding frame availability."""
import requests

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap/CommonStockSharesOutstanding/shares"

frames = ["CY2025Q4I", "CY2026Q1I", "CY2025Q3I", "CY2025Q2I"]
for f in frames:
    resp = requests.get(f"{BASE}/{f}.json", headers=HEADERS, timeout=30)
    if resp.status_code == 200:
        count = len(resp.json().get("data", []))
        print(f"  {f}: {count} companies")
    else:
        print(f"  {f}: HTTP {resp.status_code}")
