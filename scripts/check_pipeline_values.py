"""Check what our pipeline actually computed for INTU, UHS, EZPW."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
bucket = 'stock-screener-raw-data-116488731375'

# Load Step 1 output
resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-22/step1_')
if not resp.get('Contents'):
    resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-21/step1_')
data = json.loads(s3.get_object(Bucket=bucket, Key=resp['Contents'][-1]['Key'])['Body'].read())

for ticker in ['INTU', 'UHS', 'EZPW', 'HRMY', 'EPAM']:
    stock = None
    for s in data['stocks']:
        if s['symbol'] == ticker:
            stock = s
            break
    if not stock:
        print(f"{ticker}: NOT IN STEP 1 OUTPUT")
        continue
    
    print(f"{ticker}:")
    print(f"  eps: {stock.get('eps')}")
    print(f"  eps_growth_yoy: {stock.get('eps_growth_yoy')}")
    print(f"  revenue_growth_yoy: {stock.get('revenue_growth_yoy')}")
    print(f"  debt_to_equity: {stock.get('debt_to_equity')}")
    print(f"  operating_margin: {stock.get('operating_margin')}")
    print(f"  quick_ratio: {stock.get('quick_ratio')}")
    print()
