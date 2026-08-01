"""
Test quarterly YoY growth calculation for stocks that Finviz passes but we fail.
Fetches CY2026Q1 and CY2025Q1 net income, computes growth.
"""
import requests
import time

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames"

# Load ticker → CIK mapping
print("Loading ticker mapping...")
resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
tickers_data = resp.json()
ticker_to_cik = {entry["ticker"]: entry["cik_str"] for entry in tickers_data.values()}
cik_to_ticker = {v: k for k, v in ticker_to_cik.items()}

# Fetch CY2026Q1 net income
print("Fetching CY2026Q1 NetIncomeLoss...")
resp = requests.get(f"{BASE}/us-gaap/NetIncomeLoss/USD/CY2026Q1.json", headers=HEADERS, timeout=30)
current = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
print(f"  {len(current)} companies")
time.sleep(0.2)

# Fetch CY2025Q1 net income
print("Fetching CY2025Q1 NetIncomeLoss...")
resp = requests.get(f"{BASE}/us-gaap/NetIncomeLoss/USD/CY2025Q1.json", headers=HEADERS, timeout=30)
prior = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
print(f"  {len(prior)} companies")
time.sleep(0.2)

# Fetch shares for EPS calculation (CY2026Q1I)
print("Fetching CY2026Q1I shares...")
resp = requests.get(f"{BASE}/us-gaap/CommonStockSharesOutstanding/shares/CY2026Q1I.json", headers=HEADERS, timeout=30)
shares = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
print(f"  {len(shares)} companies")

# Test specific stocks
test_tickers = ['CELH', 'SAIA', 'DECK', 'ENPH', 'LRN', 'CROX', 'HIMS', 'DOCS']

print(f"\n{'Ticker':<8} {'Q1 2026 NI':>12} {'Q1 2025 NI':>12} {'EPS Growth':>12} {'Pass?':>6}")
print("-" * 55)

for ticker in test_tickers:
    cik = ticker_to_cik.get(ticker)
    if not cik:
        print(f"{ticker:<8} {'NOT FOUND':>12}")
        continue
    
    cik = int(cik)
    curr_ni = current.get(cik)
    prev_ni = prior.get(cik)
    sh = shares.get(cik)
    
    if curr_ni is None or prev_ni is None or sh is None or sh == 0 or prev_ni == 0:
        print(f"{ticker:<8} {'MISSING DATA':>12}")
        continue
    
    curr_eps = curr_ni / sh
    prev_eps = prev_ni / sh
    
    if prev_eps > 0:
        growth = (curr_eps - prev_eps) / prev_eps
    else:
        growth = None
    
    growth_str = f"{growth*100:.1f}%" if growth is not None else "N/A"
    passes = "✓" if growth is not None and growth > 0 else "✗"
    
    print(f"{ticker:<8} {curr_ni/1e6:>10.1f}M {prev_ni/1e6:>10.1f}M {growth_str:>12} {passes:>6}")
