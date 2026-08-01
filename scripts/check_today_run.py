"""Check today's (Aug 1) pipeline results."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
bucket = 'stock-screener-raw-data-116488731375'

# Step 2
resp = s3.get_object(Bucket=bucket, Key='pipeline/2026-08-01/step2_prescreen_160615.json')
step2 = json.loads(resp['Body'].read())
print(f"Pre-screen: {len(step2.get('passing_stocks', []))} pass / {step2['metadata']['total_screened']} screened")

# Step 4
resp = s3.get_object(Bucket=bucket, Key='pipeline/2026-08-01/step4_fullscreen_160946.json')
step4 = json.loads(resp['Body'].read())
passing = step4.get('passing_stocks', [])
near = step4.get('near_misses', [])
print(f"Full screen: {len(passing)} pass, {len(near)} near misses")
print(f"Passing: {[s['symbol'] for s in passing]}")
print()
print("Near misses (fail 1-2 filters):")
for s in near[:10]:
    fr = s.get('filter_results', {})
    failed = [(k, f"val={v.get('value'):.3f}" if v.get('value') else "None") 
              for k, v in fr.items() if v.get('passes') == False]
    print(f"  {s['symbol']}: {failed}")
