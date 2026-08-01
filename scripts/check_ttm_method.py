"""
Verify TTM method: CY2025 + CY2026Q1 - CY2025Q1
This gives last 4 quarters without needing the sparse CY2025Q4 frame.
"""
import requests

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames/us-gaap/NetIncomeLoss/USD"

# Fetch all three
print("Fetching frames for TTM calculation...")
resp = requests.get(f"{BASE}/CY2025.json", headers=HEADERS, timeout=30)
cy2025 = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
print(f"  CY2025 (full year): {len(cy2025)} companies")

resp = requests.get(f"{BASE}/CY2026Q1.json", headers=HEADERS, timeout=30)
cy2026q1 = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
print(f"  CY2026Q1: {len(cy2026q1)} companies")

resp = requests.get(f"{BASE}/CY2025Q1.json", headers=HEADERS, timeout=30)
cy2025q1 = {c["cik"]: c["val"] for c in resp.json().get("data", [])}
print(f"  CY2025Q1: {len(cy2025q1)} companies")

# Companies where we can compute TTM (need all 3)
ttm_possible = set(cy2025.keys()) & set(cy2026q1.keys()) & set(cy2025q1.keys())
print(f"\n  Companies with all 3 frames (TTM possible): {len(ttm_possible)}")

# Verify with a known stock (AAPL CIK=320193)
aapl_cik = 320193
if aapl_cik in ttm_possible:
    ttm = cy2025[aapl_cik] + cy2026q1[aapl_cik] - cy2025q1[aapl_cik]
    print(f"\n  AAPL TTM Net Income: CY2025({cy2025[aapl_cik]/1e9:.1f}B) + "
          f"Q1-26({cy2026q1[aapl_cik]/1e9:.1f}B) - Q1-25({cy2025q1[aapl_cik]/1e9:.1f}B) "
          f"= {ttm/1e9:.1f}B")
