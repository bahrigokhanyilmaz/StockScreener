"""
Check INTU's revenue across quarters and tags to find the mismatch.
"""
import requests
import time

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap"

resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
t2c = {e["ticker"]: int(e["cik_str"]) for e in resp.json().values()}
intu_cik = t2c.get('INTU')

TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
]

quarters = ["CY2026Q1", "CY2025Q4", "CY2025Q3", "CY2025Q2", "CY2025", "CY2024", "CY2025Q1", "CY2024Q1"]

print(f"INTU (CIK {intu_cik}) revenue across tags and quarters:\n")
print(f"{'Quarter':<12} {'RevenueFromContract...':>25} {'Revenues':>15}")
print("-" * 55)

for q in quarters:
    vals = []
    for tag in TAGS:
        url = f"{BASE}/{tag}/USD/{q}.json"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
            val = data.get(intu_cik)
            vals.append(f"${val/1e6:.0f}M" if val else "—")
        else:
            vals.append("N/A")
        time.sleep(0.15)
    print(f"{q:<12} {vals[0]:>25} {vals[1]:>15}")
