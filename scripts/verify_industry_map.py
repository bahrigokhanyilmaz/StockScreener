"""Verify the industry map in S3."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

resp = s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key='reference/ticker_industry_map.json')
data = json.loads(resp['Body'].read())

print(f"Total tickers mapped: {len(data)}")
industries = set(v['industry'] for v in data.values())
print(f"Unique industries: {len(industries)}")
print()

# Check our current stocks
for ticker in ['AAPL', 'MSFT', 'LRN', 'PRIM', 'EXLS', 'TILE', 'PTC', 'TRS']:
    entry = data.get(ticker, {})
    print(f"  {ticker}: {entry.get('industry', 'NOT FOUND')} (SIC {entry.get('sic', '?')})")
