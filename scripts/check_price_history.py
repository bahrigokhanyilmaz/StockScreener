"""Check price history in DynamoDB."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')

for ticker in ['LRN', 'TRS', 'PRIM', 'EXLS', 'TILE', 'PTC']:
    item = table.get_item(
        Key={'PK': f'PRICE_HISTORY#{ticker}', 'SK': 'DAILY'}
    ).get('Item', {})
    bar_count = item.get('bar_count', 0)
    bars = item.get('bars', [])
    if bars:
        first = bars[0]
        last = bars[-1]
        print(f"{ticker}: {bar_count} bars | {first.get('d')} to {last.get('d')} | "
              f"start=${float(first.get('c',0)):.2f} end=${float(last.get('c',0)):.2f}")
    else:
        print(f"{ticker}: NO PRICE HISTORY")
