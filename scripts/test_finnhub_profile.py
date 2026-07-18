"""Test Finnhub profile2 endpoint directly."""
import json
import boto3
import requests

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
ssm_client = session.client('ssm')
finnhub_key = ssm_client.get_parameter(
    Name='/stock-screener/finnhub-api-key', WithDecryption=True
)['Parameter']['Value']

symbols = ['LRN', 'TRS', 'PRIM']
for symbol in symbols:
    resp = requests.get(
        f"https://finnhub.io/api/v1/stock/profile2",
        params={"symbol": symbol, "token": finnhub_key},
        timeout=10
    )
    data = resp.json()
    print(f"\n{symbol}:")
    print(f"  Name: {data.get('name')}")
    print(f"  Industry: {data.get('finnhubIndustry')}")
    print(f"  Logo: {data.get('logo')}")
    print(f"  Web: {data.get('weburl')}")
    desc = data.get('description', '')
    print(f"  Description length: {len(desc)}")
    print(f"  Description: {desc[:200]}")
    print(f"  All keys: {list(data.keys())}")
