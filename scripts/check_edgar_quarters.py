"""Check which EDGAR quarterly frames are available."""
import requests

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
BASE = "https://data.sec.gov/api/xbrl/frames"

# Check which quarters have data for NetIncomeLoss
quarters_to_check = [
    "CY2025Q2", "CY2025Q1", "CY2024Q4", "CY2024Q3", "CY2024Q2", "CY2024Q1"
]

print("Checking EDGAR quarterly frame availability (NetIncomeLoss):\n")
for q in quarters_to_check:
    url = f"{BASE}/us-gaap/NetIncomeLoss/USD/{q}.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        count = len(data.get("data", []))
        print(f"  {q}: ✓ {count} companies")
    else:
        print(f"  {q}: ✗ (HTTP {resp.status_code})")
