"""Check latest pipeline execution."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
sfn = session.client('stepfunctions')
s3 = session.client('s3')

arn = "arn:aws:states:us-east-2:116488731375:execution:stock-screener-pipeline:022bed80-8305-4954-ad6c-361a6b3bc94a"
resp = sfn.describe_execution(executionArn=arn)
print(f"Status: {resp['status']}")

if resp['status'] == 'FAILED':
    print(f"Error: {resp.get('error', '?')}")
    print(f"Cause: {resp.get('cause', '?')[:500]}")
elif resp['status'] == 'SUCCEEDED':
    # Check how many stocks passed
    resp2 = s3.list_objects_v2(
        Bucket='stock-screener-raw-data-116488731375',
        Prefix='pipeline/2026-07-21/step2_'
    )
    if resp2.get('Contents'):
        key = resp2['Contents'][-1]['Key']
        data = json.loads(s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=key)['Body'].read())
        print(f"\nStep 2 (prescreen): {len(data.get('passing_stocks', []))} pass out of {data.get('metadata', {}).get('total_screened', '?')}")

    resp3 = s3.list_objects_v2(
        Bucket='stock-screener-raw-data-116488731375',
        Prefix='pipeline/2026-07-21/step4_'
    )
    if resp3.get('Contents'):
        key = resp3['Contents'][-1]['Key']
        data = json.loads(s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=key)['Body'].read())
        passing = data.get('passing_stocks', [])
        print(f"Step 4 (full screen): {len(passing)} pass")
        print(f"Final stocks: {[s['symbol'] for s in passing]}")
