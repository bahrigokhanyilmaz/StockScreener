"""Check PRIM's score breakdown."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

resp = s3.get_object(
    Bucket='stock-screener-raw-data-116488731375',
    Key='pipeline/2026-07-19/step7_scores_045721.json'
)
data = json.loads(resp['Body'].read())
for s in data['scored_stocks']:
    if s['symbol'] == 'PRIM':
        print(f"Score breakdown: {json.dumps(s.get('score_breakdown'), indent=2)}")
        print(f"Risk flags: {s.get('sentiment', {}).get('risk_flags', [])}")
        break
