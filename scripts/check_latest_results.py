"""Check latest pipeline results."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

# Latest step7 (scores)
resp = s3.get_object(
    Bucket='stock-screener-raw-data-116488731375',
    Key='pipeline/2026-07-19/step7_scores_045721.json'
)
data = json.loads(resp['Body'].read())
stocks = data.get('scored_stocks', [])
print(f"Scored stocks: {len(stocks)}")
for s in stocks:
    sent = s.get('sentiment', {})
    print(f"  {s.get('symbol')}: investability={s.get('investability_score')}, "
          f"fundamental={s.get('fundamental_score')}, "
          f"sentiment={sent.get('sentiment_score')}, "
          f"passes={s.get('passes_screen')}")
