"""Check risk flag ledger in DynamoDB after pipeline run."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')

for ticker in ['LRN', 'TRS', 'PRIM', 'EXLS', 'TILE', 'PTC']:
    item = table.get_item(Key={'PK': f'STOCK#{ticker}', 'SK': 'LATEST'}).get('Item', {})
    score = item.get('investability_score', '?')
    flags = item.get('risk_flags', [])
    print(f"\n{ticker} (score: {score}):")
    if not flags:
        print("  No risk flags")
    else:
        for f in flags:
            if isinstance(f, dict):
                print(f"  {f.get('flag')}: first_seen={f.get('first_seen')}, "
                      f"last_seen={f.get('last_seen')}, days_active={f.get('days_active')}")
            else:
                print(f"  {f} (old format)")
