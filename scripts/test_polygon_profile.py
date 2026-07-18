"""Test Polygon ticker details for company description."""
import json
import boto3
import requests

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
ssm_client = session.client('ssm')
polygon_key = ssm_client.get_parameter(
    Name='/stock-screener/polygon-api-key', WithDecryption=True
)['Parameter']['Value']

symbols = ['LRN', 'TRS', 'PRIM', 'PTC', 'EXLS', 'TILE']
for symbol in symbols:
    resp = requests.get(
        f"https://api.polygon.io/v3/reference/tickers/{symbol}",
        params={"apiKey": polygon_key},
        timeout=10
    )
    data = resp.json()
    results = data.get('results', {})
    print(f"\n{symbol}:")
    print(f"  Name: {results.get('name')}")
    print(f"  SIC desc: {results.get('sic_description')}")
    desc = results.get('description', '')
    print(f"  Description length: {len(desc)}")
    print(f"  Description: {desc[:200]}")
    import time
    time.sleep(12.5)  # Polygon free: 5 calls/min
