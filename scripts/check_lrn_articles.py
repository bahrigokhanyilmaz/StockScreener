"""Show per-article sentiment analysis for LRN."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

# Get the latest sentiment step output
resp = s3.get_object(
    Bucket='stock-screener-raw-data-116488731375',
    Key='pipeline/2026-07-19/step6_sentiment_055615.json'
)
data = json.loads(resp['Body'].read())

for stock in data.get('stocks_with_sentiment', []):
    if stock.get('symbol') != 'LRN':
        continue
    
    articles = stock.get('analyzed_articles', [])
    print(f"LRN: {len(articles)} analyzed articles\n")
    
    for i, article in enumerate(articles):
        analysis = article.get('analysis', {})
        title = article.get('title', '?')[:80]
        sentiment = analysis.get('sentiment', 0)
        confidence = analysis.get('confidence', 0)
        flags = analysis.get('risk_flags', [])
        summary = analysis.get('summary', '')
        
        flag_str = f" *** FLAGS: {flags}" if flags else ""
        print(f"  [{i+1}] sentiment={sentiment:+.2f} conf={confidence:.2f}{flag_str}")
        print(f"      Title: {title}")
        print(f"      Summary: {summary}")
        print()
    break
