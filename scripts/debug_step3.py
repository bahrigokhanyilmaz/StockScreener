"""Debug Step 3 enrichment output."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

resp = s3.list_objects_v2(
    Bucket='stock-screener-raw-data-116488731375',
    Prefix='pipeline/2026-07-21/step3_'
)
key = resp['Contents'][-1]['Key']
print(f"Reading {key}")
data = json.loads(s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=key)['Body'].read())

metadata = data.get('metadata', {})
print(f"\nMetadata:")
for k, v in metadata.items():
    print(f"  {k}: {v}")

stocks = data.get('enriched_stocks', [])
print(f"\nEnriched stocks: {len(stocks)}")

# Check first few for price data
print("\nFirst 5 stocks:")
for s in stocks[:5]:
    print(f"  {s['symbol']}: price={s.get('price')}, pe={s.get('pe_ratio')}, "
          f"forward_pe={s.get('forward_pe')}, peg={s.get('peg_ratio')}")
