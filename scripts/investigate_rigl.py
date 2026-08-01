"""Investigate RIGL — P/E of 2.0 is suspiciously low."""
import json
import requests
import boto3

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
bucket = 'stock-screener-raw-data-116488731375'

# Step 1 raw data
resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-22/step1_')
data = json.loads(s3.get_object(Bucket=bucket, Key=resp['Contents'][-1]['Key'])['Body'].read())
for stock in data['stocks']:
    if stock['symbol'] == 'RIGL':
        print("=== RIGL from Step 1 ===")
        print(f"  Company: {stock.get('company_name')}")
        print(f"  EPS (TTM): ${stock.get('eps'):.4f}" if stock.get('eps') else "  EPS: None")
        print(f"  eps_growth_yoy: {stock.get('eps_growth_yoy')}")
        print(f"  revenue_growth_yoy: {stock.get('revenue_growth_yoy')}")
        print(f"  revenue_per_share: {stock.get('revenue_per_share')}")
        print(f"  operating_margin: {stock.get('operating_margin')}")
        print(f"  debt_to_equity: {stock.get('debt_to_equity')}")
        print(f"  quick_ratio: {stock.get('quick_ratio')}")
        print(f"  fcf_per_share: {stock.get('fcf_per_share')}")
        break

# Check SEC for context
resp2 = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
t2c = {e["ticker"]: int(e["cik_str"]) for e in resp2.json().values()}
cik = t2c.get('RIGL')
if cik:
    cik_padded = str(cik).zfill(10)
    resp3 = requests.get(f"https://data.sec.gov/submissions/CIK{cik_padded}.json", headers=HEADERS, timeout=30)
    sub = resp3.json()
    print(f"\n=== SEC Filing Info ===")
    print(f"  Name: {sub.get('name')}")
    print(f"  SIC: {sub.get('sic')} - {sub.get('sicDescription')}")
    print(f"  Fiscal Year End: {sub.get('fiscalYearEnd')}")

# DynamoDB
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')
item = table.get_item(Key={'PK': 'STOCK#RIGL', 'SK': 'LATEST'}).get('Item', {})
print(f"\n=== DynamoDB ===")
print(f"  Price: ${float(item.get('price', 0)):.2f}")
print(f"  P/E: {float(item.get('pe_ratio', 0)):.2f}")
print(f"  Implied EPS: ${float(item.get('price', 0)) / float(item.get('pe_ratio', 1)):.2f}")
print(f"  Market Cap: ${float(item.get('market_cap', 0))/1e6:.0f}M")
