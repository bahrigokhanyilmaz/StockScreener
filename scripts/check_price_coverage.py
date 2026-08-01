"""Check which dashboard stocks have price history."""
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')

# Get all active stocks
resp = table.query(
    IndexName='tracking-status-index',
    KeyConditionExpression=boto3.dynamodb.conditions.Key('tracking_status').eq('ACTIVE'),
)
active = [i for i in resp['Items'] if i.get('SK') == 'LATEST']

print(f"Active stocks: {len(active)}\n")
print(f"{'Ticker':<7} {'Has Price History':>18} {'Bars':>5}")
print("-" * 35)

for stock in sorted(active, key=lambda x: float(x.get('investability_score', 0)), reverse=True):
    symbol = stock.get('symbol', '?')
    # Check if PRICE_HISTORY exists
    ph = table.get_item(Key={'PK': f'PRICE_HISTORY#{symbol}', 'SK': 'DAILY'}).get('Item')
    has_ph = "✓" if ph else "✗"
    bars = ph.get('bar_count', 0) if ph else 0
    print(f"{symbol:<7} {has_ph:>18} {int(bars):>5}")
