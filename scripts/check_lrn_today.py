"""Check LRN's sentiment breakdown from today's run."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

resp = s3.get_object(
    Bucket='stock-screener-raw-data-116488731375',
    Key='pipeline/2026-07-20/step6_sentiment_200812.json'
)
data = json.loads(resp['Body'].read())

for stock in data.get('stocks_with_sentiment', []):
    if stock.get('symbol') != 'LRN':
        continue

    sent = stock.get('sentiment', {})
    print(f"LRN Sentiment Aggregate:")
    print(f"  Raw score: {sent.get('sentiment_score')}")
    print(f"  Confidence: {sent.get('confidence')}")
    print(f"  Articles: {sent.get('article_count')}")
    print(f"  Relevant: {sent.get('relevant_count')}")
    print(f"  Positive: {sent.get('positive_count')}")
    print(f"  Negative: {sent.get('negative_count')}")
    print(f"  Neutral: {sent.get('neutral_count')}")
    print(f"  Risk flags: {sent.get('risk_flags')}")
    print()

    articles = stock.get('articles', [])
    print(f"Per-article breakdown ({len(articles)} articles):")
    for i, a in enumerate(articles):
        analysis = a.get('analysis', {})
        if not analysis.get('relevant'):
            continue
        title = a.get('title', '?')[:70]
        s = analysis.get('sentiment', 0)
        c = analysis.get('confidence', 0)
        flags = analysis.get('risk_flags', [])
        flag_str = f" *** {flags}" if flags else ""
        print(f"  [{i+1}] sent={s:+.2f} conf={c:.2f} | {title}{flag_str}")
    break
