"""Check LRN from latest pipeline run."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')

# Latest step7
resp = s3.get_object(
    Bucket='stock-screener-raw-data-116488731375',
    Key='pipeline/2026-07-19/step7_scores_055721.json'
)
data = json.loads(resp['Body'].read())
for s in data['scored_stocks']:
    if s['symbol'] == 'LRN':
        breakdown = s.get('score_breakdown', {})
        sent = s.get('sentiment', {})
        print(f"=== LRN (latest run) ===")
        print(f"Investability: {s.get('investability_score')}")
        print(f"Fundamental: {breakdown.get('fundamental_score')}")
        print(f"Sentiment: {sent.get('sentiment_score')}")
        print(f"Risk Flags: {sent.get('risk_flags', [])}")
        print(f"Risk Penalties: {breakdown.get('risk_penalties')}")
        print(f"Total Penalty: {breakdown.get('total_penalty')}")
        break

# Also check DynamoDB
print(f"\n=== All stocks (DynamoDB) ===")
for ticker in ['LRN', 'TRS', 'PRIM', 'EXLS', 'TILE', 'PTC']:
    item = table.get_item(Key={'PK': f'STOCK#{ticker}', 'SK': 'LATEST'}).get('Item', {})
    print(f"  {ticker}: score={item.get('investability_score')}, "
          f"sentiment={item.get('sentiment_score')}, "
          f"flags={item.get('risk_flags', [])}")
