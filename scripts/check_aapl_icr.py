"""Check AAPL, AMZN, CRM D/E and ICR in Step 1 data."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
resp = s3.list_objects_v2(Bucket='stock-screener-raw-data-116488731375', Prefix='pipeline/2026-08-01/step1_')
data = json.loads(s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=resp['Contents'][-1]['Key'])['Body'].read())

for s in data['stocks']:
    if s['symbol'] in ('AAPL', 'AMZN', 'CRM', 'SNOW', 'AMD'):
        print(f"{s['symbol']}: D/E={s.get('debt_to_equity')}, ICR={s.get('interest_coverage_ratio')}, "
              f"OpMargin={s.get('operating_margin')}, QR={s.get('quick_ratio')}")
