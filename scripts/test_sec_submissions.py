"""Test SEC submissions API for SIC codes (single company)."""
import requests

# Check what fields submissions has for one company (Apple)
url = "https://data.sec.gov/submissions/CIK0000320193.json"
headers = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}

resp = requests.get(url, headers=headers, timeout=30)
print(f"Status: {resp.status_code}")
data = resp.json()

# Print company-level info (not the filings list)
keys_to_check = ['cik', 'entityType', 'sic', 'sicDescription', 'name', 'ticker', 'category',
                 'fiscalYearEnd', 'stateOfIncorporation', 'exchanges', 'tickers']
for k in keys_to_check:
    if k in data:
        print(f"  {k}: {data[k]}")
