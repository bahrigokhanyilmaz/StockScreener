"""Test SEC company tickers exchange file for SIC codes."""
import requests
import json

# SEC full company list with exchange info
url = "https://www.sec.gov/files/company_tickers_exchange.json"
headers = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}

resp = requests.get(url, headers=headers, timeout=30)
print(f"Status: {resp.status_code}")

if resp.status_code == 200:
    data = resp.json()
    print(f"Keys: {list(data.keys())}")
    fields = data.get("fields", [])
    print(f"Fields: {fields}")
    entries = data.get("data", [])
    print(f"Total entries: {len(entries)}")
    print(f"First 3 entries: {entries[:3]}")
