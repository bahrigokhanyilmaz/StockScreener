"""
Remove all stocks from DynamoDB that didn't pass today's pipeline run.
Only keep the 4 legitimate passers: VISN, RIGL, TRS, NUTX.
"""
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')

# Today's legitimate passers
legitimate = {'VISN', 'RIGL', 'TRS', 'NUTX'}

# Find all ACTIVE/GRACE stocks in DynamoDB
for status in ['ACTIVE', 'GRACE']:
    resp = table.query(
        IndexName='tracking-status-index',
        KeyConditionExpression=boto3.dynamodb.conditions.Key('tracking_status').eq(status),
    )
    for item in resp.get('Items', []):
        symbol = item.get('symbol', '')
        pk = item.get('PK', '')
        sk = item.get('SK', '')
        if symbol and symbol not in legitimate:
            # Delete this item
            table.delete_item(Key={'PK': pk, 'SK': sk})
            print(f"  Deleted {pk}|{sk} ({symbol}, was {status})")

# Also delete LATEST and TRACKING items for these stocks
# (GSI only indexes items with tracking_status, but LATEST/SCORE items exist too)
resp = table.scan(
    FilterExpression=boto3.dynamodb.conditions.Attr('symbol').exists(),
    ProjectionExpression='PK, SK, symbol',
)
for item in resp.get('Items', []):
    symbol = item.get('symbol', '')
    if symbol and symbol not in legitimate:
        pk = item.get('PK', '')
        sk = item.get('SK', '')
        if pk.startswith('STOCK#') or pk.startswith('PRICE_HISTORY#'):
            table.delete_item(Key={'PK': pk, 'SK': sk})
            print(f"  Deleted {pk}|{sk}")

print(f"\nDone. Only {legitimate} remain.")
