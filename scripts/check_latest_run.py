"""Check results from the latest pipeline run."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

# Find latest step2 and step4
bucket = 'stock-screener-raw-data-116488731375'

for prefix, label in [('step2_prescreen', 'Pre-screen'), ('step3_enriched', 'Enrichment'), ('step4_fullscreen', 'Full screen')]:
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=f'pipeline/2026-07-22/{prefix}')
    if not resp.get('Contents'):
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=f'pipeline/2026-07-21/{prefix}')
    if resp.get('Contents'):
        key = resp['Contents'][-1]['Key']
        data = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
        meta = data.get('metadata', {})
        passing = data.get('passing_stocks', [])
        if label == 'Pre-screen':
            print(f"{label}: {len(passing)} pass / {meta.get('total_screened', '?')} screened")
        elif label == 'Enrichment':
            print(f"{label}: {meta.get('prices_matched', '?')} prices, "
                  f"{meta.get('local_prefilter_pass', '?')} pass prefilter, "
                  f"{meta.get('finnhub_enriched', '?')} Finnhub enriched, "
                  f"PE quartiles: {meta.get('industries_with_pe_quartile', '?')}")
        elif label == 'Full screen':
            print(f"{label}: {len(passing)} pass")
            if passing:
                print(f"  Stocks: {[s['symbol'] for s in passing]}")
