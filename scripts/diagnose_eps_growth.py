"""
Compare our EDGAR EPS growth calculation vs what Finviz shows.
Sample: INTU, HRMY, UPWK, UHS, BMRN (all fail eps_growth_yoy in our pipeline but pass on Finviz).

Our formula: TTM = CY2025_annual + CY2026Q1 - CY2025Q1
Prior TTM = CY2024_annual + CY2025Q1 - CY2024Q1
Growth = (TTM - Prior_TTM) / Prior_TTM (using net income, then divided by shares for EPS)
"""
import requests
import time

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap/NetIncomeLoss/USD"

# Load ticker → CIK mapping
print("Loading ticker → CIK mapping...")
resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
ticker_to_cik = {entry["ticker"]: int(entry["cik_str"]) for entry in resp.json().values()}

# Fetch all frames needed for our TTM formula
frames_to_fetch = {
    "CY2025": None,     # Full year 2025
    "CY2026Q1": None,   # Q1 2026
    "CY2025Q1": None,   # Q1 2025 (for TTM derivation + prior TTM)
    "CY2024": None,     # Full year 2024 (for prior TTM)
    "CY2024Q1": None,   # Q1 2024 (for prior TTM)
}

print("Fetching EDGAR frames...")
for frame_name in frames_to_fetch:
    url = f"{BASE}/{frame_name}.json"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code == 200:
        data = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
        frames_to_fetch[frame_name] = data
        print(f"  {frame_name}: {len(data)} companies")
    else:
        frames_to_fetch[frame_name] = {}
        print(f"  {frame_name}: FAILED ({resp.status_code})")
    time.sleep(0.2)

# Also fetch shares for EPS
print("Fetching shares (CY2025Q4I + CY2026Q1I fallback)...")
resp = requests.get(f"https://data.sec.gov/api/xbrl/frames/us-gaap/CommonStockSharesOutstanding/shares/CY2026Q1I.json", headers=HEADERS, timeout=30)
shares_q1 = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
time.sleep(0.2)
resp = requests.get(f"https://data.sec.gov/api/xbrl/frames/us-gaap/CommonStockSharesOutstanding/shares/CY2025Q4I.json", headers=HEADERS, timeout=30)
shares_q4 = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
# Merge
shares = {**shares_q4, **shares_q1}  # Q1 takes precedence
print(f"  Shares: {len(shares)} companies")

# Sample stocks
test_tickers = ['INTU', 'HRMY', 'UPWK', 'UHS', 'BMRN', 'EPAM', 'EZPW']

print(f"\n{'='*100}")
print(f"{'Ticker':<7} | {'CY2025 Ann':>12} | {'CY2026Q1':>10} | {'CY2025Q1':>10} | {'Our TTM':>12} | {'CY2024 Ann':>12} | {'CY2024Q1':>10} | {'Prior TTM':>12} | {'Growth':>8}")
print(f"{'='*100}")

for ticker in test_tickers:
    cik = ticker_to_cik.get(ticker)
    if not cik:
        print(f"{ticker:<7} | NOT FOUND IN SEC TICKER LIST")
        continue

    cy2025 = frames_to_fetch["CY2025"].get(cik)
    cy2026q1 = frames_to_fetch["CY2026Q1"].get(cik)
    cy2025q1 = frames_to_fetch["CY2025Q1"].get(cik)
    cy2024 = frames_to_fetch["CY2024"].get(cik)
    cy2024q1 = frames_to_fetch["CY2024Q1"].get(cik)
    sh = shares.get(cik)

    # Our TTM formula
    if cy2025 is not None and cy2026q1 is not None and cy2025q1 is not None:
        ttm_current = cy2025 + cy2026q1 - cy2025q1
    else:
        ttm_current = None

    # Prior TTM
    if cy2024 is not None and cy2025q1 is not None and cy2024q1 is not None:
        ttm_prior = cy2024 + cy2025q1 - cy2024q1
    else:
        ttm_prior = None

    # Growth
    if ttm_current is not None and ttm_prior is not None and ttm_prior != 0:
        if ttm_prior > 0:
            growth = (ttm_current - ttm_prior) / ttm_prior
        else:
            growth = None  # Can't compute growth from negative base
    else:
        growth = None

    def fmt_m(v):
        if v is None: return "N/A"
        return f"${v/1e6:.0f}M"

    def fmt_g(v):
        if v is None: return "N/A"
        return f"{v*100:.1f}%"

    print(f"{ticker:<7} | {fmt_m(cy2025):>12} | {fmt_m(cy2026q1):>10} | {fmt_m(cy2025q1):>10} | {fmt_m(ttm_current):>12} | {fmt_m(cy2024):>12} | {fmt_m(cy2024q1):>10} | {fmt_m(ttm_prior):>12} | {fmt_g(growth):>8}")

    # Additional detail
    if growth is not None and growth <= 0:
        print(f"        → FAILS our screen (growth={growth*100:.1f}%)")
        print(f"        → TTM NI: ${ttm_current/1e6:.1f}M vs Prior TTM NI: ${ttm_prior/1e6:.1f}M")
        if sh:
            ttm_eps = ttm_current / sh
            prior_eps = ttm_prior / sh
            print(f"        → TTM EPS: ${ttm_eps:.2f} vs Prior EPS: ${prior_eps:.2f}")
    elif growth is not None:
        print(f"        → PASSES (growth={growth*100:.1f}%) — why did our pipeline fail this?")
    print()
