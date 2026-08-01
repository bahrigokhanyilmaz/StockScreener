"""Explain LRN's investability score breakdown."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

resp = s3.get_object(
    Bucket='stock-screener-raw-data-116488731375',
    Key='pipeline/2026-07-19/step7_scores_045721.json'
)
data = json.loads(resp['Body'].read())
for s in data['scored_stocks']:
    if s['symbol'] == 'LRN':
        print("=== LRN (Stride) Score Breakdown ===")
        print(f"\nInvestability Score: {s.get('investability_score')}")
        print(f"\nScore Breakdown:")
        breakdown = s.get('score_breakdown', {})
        print(f"  Fundamental Score: {breakdown.get('fundamental_score')}")
        print(f"  Fundamental Weighted (×0.7): {breakdown.get('fundamental_weighted')}")
        print(f"  Sentiment Score (raw): {breakdown.get('sentiment_score')}")
        print(f"  Sentiment Confidence: {breakdown.get('sentiment_confidence')}")
        print(f"  Sentiment Adjustment: {breakdown.get('sentiment_adjustment')}")
        print(f"  Sentiment Weighted (×0.3): {breakdown.get('sentiment_weighted')}")
        print(f"  Base Score (before penalty): {breakdown.get('base_score_before_penalty')}")
        print(f"  Risk Penalties: {breakdown.get('risk_penalties')}")
        print(f"  Total Penalty: {breakdown.get('total_penalty')}")

        print(f"\nSentiment Detail:")
        sent = s.get('sentiment', {})
        print(f"  Score: {sent.get('sentiment_score')}")
        print(f"  Confidence: {sent.get('confidence')}")
        print(f"  Articles: {sent.get('article_count')}")
        print(f"  Relevant: {sent.get('relevant_count')}")
        print(f"  Positive: {sent.get('positive_count')}")
        print(f"  Negative: {sent.get('negative_count')}")
        print(f"  Neutral: {sent.get('neutral_count')}")
        print(f"  Risk Flags: {sent.get('risk_flags', [])}")

        print(f"\nKey Metrics:")
        for k in ['pe_ratio', 'forward_pe', 'peg_ratio', 'price_to_fcf',
                  'debt_to_equity', 'quick_ratio', 'operating_margin',
                  'eps_growth_yoy', 'revenue_growth_yoy', 'est_lt_growth',
                  'analyst_recommendation']:
            print(f"  {k}: {s.get(k)}")
        break
