"""Check sentiment from today's new pipeline run."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')

# Check the new S3 output
resp = s3.get_object(
    Bucket='stock-screener-raw-data-116488731375',
    Key='pipeline/2026-07-19/step6_sentiment_041942.json'
)
data = json.loads(resp['Body'].read())
stocks = data.get('stocks_with_sentiment', [])
print(f"=== Step 6 sentiment (today's run) ===")
print(f"Stocks: {len(stocks)}")
for s in stocks:
    sent = s.get('sentiment', {})
    print(f"  {s.get('symbol')}: score={sent.get('sentiment_score')}, "
          f"confidence={sent.get('confidence')}, "
          f"relevant={sent.get('relevant_count')}/{sent.get('article_count')}, "
          f"pos={sent.get('positive_count')} neg={sent.get('negative_count')} "
          f"neutral={sent.get('neutral_count')}")

# Check DynamoDB (should be updated by step 7)
print(f"\n=== DynamoDB (post-run) ===")
for ticker in ['LRN', 'TRS', 'PRIM', 'EXLS', 'TILE', 'PTC']:
    item = table.get_item(Key={'PK': f'STOCK#{ticker}', 'SK': 'LATEST'}).get('Item', {})
    print(f"  {ticker}: sentiment={item.get('sentiment_score')}, "
          f"confidence={item.get('sentiment_confidence')}, "
          f"investability={item.get('investability_score')}, "
          f"icr={item.get('interest_coverage_ratio', 'N/A')}")
