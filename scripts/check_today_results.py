"""Check today's pipeline results."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')

# Find today's step files
resp = s3.list_objects_v2(
    Bucket='stock-screener-raw-data-116488731375',
    Prefix='pipeline/2026-07-21/'
)
files = sorted([obj['Key'] for obj in resp.get('Contents', [])])

# Get latest step2 and step4
step2_files = [f for f in files if 'step2_' in f]
step4_files = [f for f in files if 'step4_' in f]
step7_files = [f for f in files if 'step7_' in f]

if step2_files:
    data = json.loads(s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=step2_files[-1])['Body'].read())
    print(f"Step 2 (prescreen): {len(data.get('passing_stocks', []))} pass out of {data.get('metadata', {}).get('total_screened', '?')}")

if step4_files:
    data = json.loads(s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=step4_files[-1])['Body'].read())
    passing = data.get('passing_stocks', [])
    print(f"Step 4 (full screen): {len(passing)} pass")
    if passing:
        print(f"Stocks: {[s['symbol'] for s in passing]}")

if step7_files:
    data = json.loads(s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=step7_files[-1])['Body'].read())
    scored = data.get('scored_stocks', [])
    print(f"\nStep 7 (scored): {len(scored)} stocks")
    for s in scored[:15]:
        sent = s.get('sentiment', {})
        print(f"  {s['symbol']:<8} invest={s.get('investability_score'):<6} fund={s.get('fundamental_score'):<6} "
              f"sent={sent.get('sentiment_score', 0):+.2f} flags={[f.get('flag','?') if isinstance(f,dict) else f for f in s.get('risk_ledger', [])]}")
