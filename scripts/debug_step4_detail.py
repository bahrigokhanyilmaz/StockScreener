"""Show details of what's failing in Step 4."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

resp = s3.list_objects_v2(
    Bucket='stock-screener-raw-data-116488731375',
    Prefix='pipeline/2026-07-21/step4_'
)
key = resp['Contents'][-1]['Key']
data = json.loads(s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=key)['Body'].read())

all_screened = data.get('all_screened', [])

# Show first 3 stocks with their filter results
for s in all_screened[:3]:
    print(f"\n{s['symbol']} — filters evaluated: {s.get('filters_evaluated')}, passed: {s.get('filters_passed')}")
    fr = s.get('filter_results', {})
    for k, v in fr.items():
        status = '✓' if v.get('passes') else ('SKIP' if v.get('skipped') else '✗')
        val = v.get('value')
        val_str = f"{val:.4f}" if isinstance(val, float) else str(val)
        print(f"  {k:<25} {status:>4}  value={val_str:<12} threshold={v.get('threshold')}")
