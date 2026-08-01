"""
Test SEC bulk company data options for SIC codes.
The SEC publishes bulk JSON files at data.sec.gov
"""
import requests
import json
import io
import zipfile

headers = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}

# Option 1: SEC's bulk submissions ZIP file
# https://data.sec.gov/submissions/ has per-company JSON
# But we can parse the bulk company tickers file which has CIK
# Then use the EDGAR XBRL companyfacts which includes entityType
# Actually — the simplest: SEC full-text search API has SIC

# Option 2: Just get the Finnhub industry we already have for the ~6 stocks
# and compute averages from the full EDGAR dataset grouped by SIC
# Problem: we don't have SIC for the full 5,097 stocks

# Option 3: Use the SEC's company search to get SIC codes in bulk
# The SEC bulk submissions file:
url = "https://data.sec.gov/submissions/company_tickers_with_cik_and_sic.json"
resp = requests.get(url, headers=headers, timeout=30)
print(f"company_tickers_with_cik_and_sic.json: {resp.status_code}")

# If that doesn't exist, try another known file
if resp.status_code != 200:
    # The SEC doesn't have this exact file. Let's try the XBRL frames response
    # which includes company metadata
    url = "https://data.sec.gov/api/xbrl/frames/us-gaap/NetIncomeLoss/USD/CY2025.json"
    resp = requests.get(url, headers=headers, timeout=30)
    print(f"Frames response status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print(f"Top-level keys: {list(data.keys())}")
        entries = data.get("data", [])
        print(f"Data entries: {len(entries)}")
        if entries:
            print(f"First entry keys: {list(entries[0].keys()) if isinstance(entries[0], dict) else 'list'}")
            print(f"First 2 entries: {entries[:2]}")
