"""Show all articles with analysis for LRN — find the revenue_risk flag."""
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

    articles = stock.get('articles', [])
    for i, a in enumerate(articles):
        analysis = a.get('analysis', {})
        flags = analysis.get('risk_flags', [])
        sentiment = analysis.get('sentiment', 0)
        confidence = analysis.get('confidence', 0)
        relevant = analysis.get('relevant', False)
        title = a.get('title', '?')[:90]
        summary = analysis.get('summary', '')

        flag_str = f"  *** FLAG: {flags}" if flags else ""
        print(f"[{i+1}] sent={sentiment:+.2f} conf={confidence:.2f} rel={relevant}{flag_str}")
        print(f"    Title: {title}")
        print(f"    Summary: {summary}")
        print()
    break
