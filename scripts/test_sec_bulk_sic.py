"""Test if SEC provides bulk SIC data."""
import requests
import json

headers = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}

# The SEC publishes a company search JSON with SIC codes:
# https://efts.sec.gov/LATEST/search-index?q=*&dateRange=custom&startdt=2024-01-01&forms=10-K
# But a simpler approach: EDGAR's full-text search index includes SIC

# Try the SIC mapping file
url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=10-K&dateb=&owner=include&count=0&search_text=&SIC=3571&output=atom"
# That's the old approach. Let me try the bulk submissions

# Alternative: SEC publishes companyfacts (has all company metadata)
# But the real trick is: we can parse the CIK list file which the EDGAR provider
# already downloads, and cross-reference with the submissions API's bulk index

# The SEC doesn't have a single bulk file with all SIC codes.
# BUT: Polygon's /v3/reference/tickers endpoint returns SIC codes!
# And we already call Polygon for prices.

# Let's test Polygon ticker details which we know works:
import boto3
session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
ssm = session.client('ssm')
polygon_key = ssm.get_parameter(Name='/stock-screener/polygon-api-key', WithDecryption=True)['Parameter']['Value']

# Polygon's /v3/reference/tickers can return type + sic_code in bulk!
url = "https://api.polygon.io/v3/reference/tickers"
params = {
    "apiKey": polygon_key,
    "market": "stocks",
    "active": "true",
    "limit": 5,
}
resp = requests.get(url, params=params, timeout=30)
data = resp.json()
print(f"Status: {resp.status_code}")
print(f"Result count: {data.get('count', 0)}")
results = data.get('results', [])
for r in results[:5]:
    print(f"  {r.get('ticker')}: type={r.get('type')}, sic={r.get('sic_code')}, market={r.get('market')}")
    print(f"    Keys: {list(r.keys())}")
