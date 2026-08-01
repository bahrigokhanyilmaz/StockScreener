"""Debug today's Step 4 — why did 9 enriched stocks all fail?"""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

# Get today's latest step4
resp = s3.list_objects_v2(
    Bucket='stock-screener-raw-data-116488731375',
    Prefix='pipeline/2026-07-22/'
)
files = sorted([obj['Key'] for obj in resp.get('Contents', []) if 'step4_' in obj['Key']])

if not files:
    # Try 2026-07-21
    resp = s3.list_objects_v2(
        Bucket='stock-screener-raw-data-116488731375',
        Prefix='pipeline/2026-07-21/'
    )
    files = sorted([obj['Key'] for obj in resp.get('Contents', []) if 'step4_' in obj['Key']])

key = files[-1]
print(f"Reading {key}\n")
data = json.loads(s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=key)['Body'].read())

all_screened = data.get('all_screened', [])
# Only show stocks that have price data (the 9 that were enriched)
enriched = [s for s in all_screened if s.get('price') is not None]
print(f"Stocks with price data: {len(enriched)}")

for s in enriched:
    fr = s.get('filter_results', {})
    failed = [(k, f"val={v.get('value')}", f"thresh={v.get('threshold')}")
              for k, v in fr.items() if v.get('passes') == False and not v.get('skipped')]
    passed = sum(1 for v in fr.values() if v.get('passes') == True)
    total = s.get('filters_evaluated', 0)
    print(f"\n  {s['symbol']} — passed {passed}/{total}")
    for f in failed:
        print(f"    FAIL: {f[0]} ({f[1]}, {f[2]})")
