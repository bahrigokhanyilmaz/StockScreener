"""Check if AAPL/CRM have cash that covers their non-current liabilities."""
import json
import requests
import time

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap"

resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
t2c = {e["ticker"]: int(e["cik_str"]) for e in resp.json().values()}

# Get Liabilities, LiabilitiesCurrent, Cash for AAPL and CRM
# Use CY2026Q1I (or CY2025Q4I fallback)
tags_to_check = {
    "Liabilities": "CY2026Q1I",
    "LiabilitiesCurrent": "CY2026Q1I",
    "CashAndCashEquivalentsAtCarryingValue": "CY2026Q1I",
}

results = {}
for tag, frame in tags_to_check.items():
    url = f"{BASE}/{tag}/USD/{frame}.json"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code == 200:
        data = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
        results[tag] = data
    time.sleep(0.15)

for ticker in ['AAPL', 'CRM', 'AMZN', 'SNOW']:
    cik = t2c.get(ticker)
    liab = results.get("Liabilities", {}).get(cik)
    liab_curr = results.get("LiabilitiesCurrent", {}).get(cik)
    cash = results.get("CashAndCashEquivalentsAtCarryingValue", {}).get(cik)

    ncl = (liab - liab_curr) if liab and liab_curr else None
    
    print(f"{ticker} (CIK {cik}):")
    print(f"  Total Liabilities: ${liab/1e9:.1f}B" if liab else "  Total Liabilities: None")
    print(f"  Current Liabilities: ${liab_curr/1e9:.1f}B" if liab_curr else "  Current Liabilities: None")
    print(f"  Non-current Liabilities: ${ncl/1e9:.1f}B" if ncl else "  Non-current Liabilities: None")
    print(f"  Cash: ${cash/1e9:.1f}B" if cash else "  Cash: None")
    if ncl and cash:
        print(f"  Cash covers NCL? {'YES' if cash >= ncl else 'NO'} ({cash/ncl*100:.0f}%)")
    print()
