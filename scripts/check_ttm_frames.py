"""
Check if EDGAR has trailing-twelve-month (TTM) frames.
The Frames API supports a 'T' suffix for trailing annual values as of a given quarter.
e.g., CY2025Q4 = just Q4, but CY2025 = full year ending in CY2025.
There may also be frames like CY2026Q1T or similar for trailing 1 year ending Q1 2026.
"""
import requests

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap/NetIncomeLoss/USD"

# Test various TTM-style frame identifiers
frames_to_try = [
    "CY2026Q1",    # Just Q1 2026 (quarterly)
    "CY2025",      # Full year 2025 (annual)
    "CY2025Q4",    # Just Q4 2025 (quarterly)
    # Possible trailing annual frames (12 months ending at that quarter):
    "CY2025Q4T",   # TTM as of Q4 2025?
    "CY2026Q1T",   # TTM as of Q1 2026?
    "CY2025Q3T",   # TTM as of Q3 2025?
]

print("Checking EDGAR frame availability for TTM-style identifiers:\n")
for f in frames_to_try:
    url = f"{BASE}/{f}.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code == 200:
        count = len(resp.json().get("data", []))
        print(f"  {f:12} → ✓ {count} companies")
    else:
        print(f"  {f:12} → ✗ HTTP {resp.status_code}")
