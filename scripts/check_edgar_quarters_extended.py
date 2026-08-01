"""Check EDGAR frames availability past Q2 2025."""
import requests

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames"

quarters = ["CY2025Q3", "CY2025Q4", "CY2026Q1", "CY2026Q2"]

print("Checking EDGAR frames past Q2 2025 (NetIncomeLoss):\n")
for q in quarters:
    url = f"{BASE}/us-gaap/NetIncomeLoss/USD/{q}.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        count = len(data.get("data", []))
        print(f"  {q}: ✓ {count} companies")
    else:
        print(f"  {q}: ✗ (HTTP {resp.status_code} — not available)")
