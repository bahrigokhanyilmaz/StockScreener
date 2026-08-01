"""Show LRN sentiment data structure."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

resp = s3.get_object(
    Bucket='stock-screener-raw-data-116488731375',
    Key='pipeline/2026-07-19/step6_sentiment_055615.json'
)
data = json.loads(resp['Body'].read())

for stock in data.get('stocks_with_sentiment', []):
    if stock.get('symbol') != 'LRN':
        continue
    
    # Show all keys
    print(f"LRN keys: {[k for k in stock.keys() if k not in ['filter_results']]}")
    
    # Check articles field
    articles = stock.get('articles', stock.get('analyzed_articles', []))
    print(f"Articles field: {len(articles)} items")
    
    # Check sentiment field
    sent = stock.get('sentiment', {})
    print(f"Sentiment: {json.dumps(sent, indent=2)}")
    
    # Show first article structure
    if articles:
        print(f"\nFirst article keys: {list(articles[0].keys())}")
        a = articles[0]
        print(f"  Title: {a.get('title', '?')[:80]}")
        analysis = a.get('analysis', {})
        if analysis:
            print(f"  Analysis: {json.dumps(analysis, indent=2)}")
    break
