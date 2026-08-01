"""Debug why 0 stocks passed full screen."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

# Get latest step4 output
resp = s3.list_objects_v2(
    Bucket='stock-screener-raw-data-116488731375',
    Prefix='pipeline/2026-07-21/step4_'
)
key = resp['Contents'][-1]['Key']
print(f"Reading {key}")
data = json.loads(s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=key)['Body'].read())

all_screened = data.get('all_screened', [])
near_misses = data.get('near_misses', [])
print(f"Total screened: {len(all_screened)}")
print(f"Near misses (fail 1-2 filters): {len(near_misses)}")

# Show top near-misses and why they failed
print(f"\nTop near-misses:")
for s in near_misses[:10]:
    fr = s.get('filter_results', {})
    failed = [(k, v.get('value'), v.get('threshold')) for k, v in fr.items()
              if v.get('passes') == False and not v.get('skipped')]
    print(f"  {s['symbol']}: score={s.get('fundamental_score',0):.0f}, "
          f"failed={[(f[0], f'{f[1]:.3f}' if f[1] else 'None') for f in failed]}")
