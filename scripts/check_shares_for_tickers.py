"""Check shares availability for INTU, UHS, EZPW."""
import requests

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap"

resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
t2c = {e["ticker"]: int(e["cik_str"]) for e in resp.json().values()}

tickers = ['INTU', 'UHS', 'EZPW']

# Check multiple share tag variants and frames
tags = [
    ("CommonStockSharesOutstanding", "shares", True),
    ("WeightedAverageNumberOfShareOutstandingBasicAndDiluted", "shares", False),
    ("WeightedAverageNumberOfDilutedSharesOutstanding", "shares", False),
    ("EntityCommonStockSharesOutstanding", "shares", True),
]
frames_instant = ["CY2026Q1I", "CY2025Q4I", "CY2025Q3I"]
frames_duration = ["CY2026Q1", "CY2025Q4", "CY2025"]

for tag, unit, is_instant in tags:
    print(f"\nTag: {tag} ({'instant' if is_instant else 'duration'})")
    test_frames = frames_instant if is_instant else frames_duration
    for frame in test_frames:
        url = f"{BASE}/{tag}/{unit}/{frame}.json"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            continue
        shares = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
        found = []
        for ticker in tickers:
            cik = t2c.get(ticker)
            val = shares.get(cik)
            if val:
                found.append(f"{ticker}={val:,.0f}")
        if found:
            print(f"  {frame} ({len(shares)} cos): {', '.join(found)}")
