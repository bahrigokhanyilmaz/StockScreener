"""Debug why Polygon prices aren't matching our stocks."""
import json
import boto3
import requests

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
ssm = session.client('ssm')

# Get our stock symbols from step2
resp = s3.list_objects_v2(
    Bucket='stock-screener-raw-data-116488731375',
    Prefix='pipeline/2026-07-21/step2_'
)
key = resp['Contents'][-1]['Key']
data = json.loads(s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=key)['Body'].read())
our_symbols = [s['symbol'] for s in data.get('passing_stocks', [])]
print(f"Our 81 passing symbols (first 10): {our_symbols[:10]}")

# Get Polygon prices
polygon_key = ssm.get_parameter(Name='/stock-screener/polygon-api-key', WithDecryption=True)['Parameter']['Value']
url = f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/2026-07-18"
resp = requests.get(url, params={"apiKey": polygon_key}, timeout=30)
data = resp.json()
polygon_symbols = {item["T"] for item in data.get("results", [])}
print(f"\nPolygon symbols count: {len(polygon_symbols)}")
print(f"Polygon sample: {list(polygon_symbols)[:10]}")

# Check overlap
matched = [s for s in our_symbols if s in polygon_symbols]
unmatched = [s for s in our_symbols if s not in polygon_symbols]
print(f"\nMatched: {len(matched)}/{len(our_symbols)}")
print(f"Unmatched (first 20): {unmatched[:20]}")

# Check if it's a date issue — what date does the enrichment Lambda use?
from datetime import datetime, timezone, timedelta
today = datetime.now(timezone.utc).date()
if datetime.now(timezone.utc).hour < 21:
    check_date = today - timedelta(days=1)
else:
    check_date = today
# Skip weekends
while check_date.weekday() >= 5:
    check_date = check_date - timedelta(days=1)
print(f"\nExpected trading date: {check_date}")
print(f"Polygon URL date used: 2026-07-18 (from response)")
