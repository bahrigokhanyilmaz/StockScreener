"""Check EPS-related XBRL tags available in EDGAR Frames."""
import requests
import time

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap"

tags = [
    ("EarningsPerShareDiluted", "USD/shares", "CY2026Q1"),
    ("EarningsPerShareBasic", "USD/shares", "CY2026Q1"),
    ("EarningsPerShareDiluted", "USD/shares", "CY2025"),
    ("IncomeLossFromContinuingOperationsPerDilutedShare", "USD/shares", "CY2026Q1"),
    ("IncomeLossFromContinuingOperationsPerBasicShare", "USD/shares", "CY2026Q1"),
]

print("EPS tags in EDGAR Frames API:\n")
for tag, unit, frame in tags:
    url = f"{BASE}/{tag}/{unit}/{frame}.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code == 200:
        count = len(resp.json().get("data", []))
        print(f"  {tag:<60} {frame:<10} {count} companies")
    else:
        print(f"  {tag:<60} {frame:<10} HTTP {resp.status_code}")
    time.sleep(0.15)

# Check RIGL and VISN specifically
print("\n\nChecking RIGL and VISN in EarningsPerShareDiluted CY2026Q1:")
resp2 = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
t2c = {e["ticker"]: int(e["cik_str"]) for e in resp2.json().values()}

url = f"{BASE}/EarningsPerShareDiluted/USD%2Fshares/CY2026Q1.json"
resp = requests.get(url, headers=HEADERS, timeout=30)
if resp.status_code == 200:
    data = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
    for ticker in ['RIGL', 'VISN', 'INTU', 'TRS', 'TILE']:
        cik = t2c.get(ticker)
        val = data.get(cik)
        print(f"  {ticker}: EPS = ${val}" if val else f"  {ticker}: NOT FOUND")
