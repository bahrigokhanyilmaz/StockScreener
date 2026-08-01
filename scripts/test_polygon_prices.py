"""Test Polygon grouped daily directly."""
import requests
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
ssm = session.client('ssm')
polygon_key = ssm.get_parameter(Name='/stock-screener/polygon-api-key', WithDecryption=True)['Parameter']['Value']

# Try different dates
dates = ['2026-07-18', '2026-07-17', '2026-07-20']
for date in dates:
    url = f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date}"
    resp = requests.get(url, params={"apiKey": polygon_key}, timeout=30)
    data = resp.json()
    count = len(data.get("results", []))
    status = data.get("status")
    print(f"  {date}: {count} results, status={status}, queryCount={data.get('queryCount', '?')}")
    if count == 0:
        print(f"    Full response: {str(data)[:200]}")
