"""Check what sentiment data exists for our stocks."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')

# Check DynamoDB
print("=== DynamoDB sentiment values ===")
for ticker in ['LRN', 'TRS', 'PRIM', 'EXLS', 'TILE', 'PTC']:
    item = table.get_item(Key={'PK': f'STOCK#{ticker}', 'SK': 'LATEST'}).get('Item', {})
    print(f"  {ticker}: sentiment_score={item.get('sentiment_score')}, "
          f"sentiment_confidence={item.get('sentiment_confidence')}")

# Check S3 pipeline output
print("\n=== Step 6 (sentiment) S3 output ===")
resp = s3.get_object(
    Bucket='stock-screener-raw-data-116488731375',
    Key='pipeline/2026-07-18/step6_sentiment_055419.json'
)
data = json.loads(resp['Body'].read())
stocks = data.get('stocks_with_sentiment', [])
print(f"Stocks with sentiment: {len(stocks)}")
for s in stocks:
    sent = s.get('sentiment', {})
    print(f"  {s.get('symbol')}: score={sent.get('sentiment_score')}, "
          f"confidence={sent.get('confidence')}, "
          f"articles={sent.get('article_count')}, "
          f"relevant={sent.get('relevant_count')}")
