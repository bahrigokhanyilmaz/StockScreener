"""
One-time script to re-persist today's pipeline results to DynamoDB
with the fixed score calculator logic that includes ALL metrics.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal

import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
dynamodb = session.resource('dynamodb')
s3_client = session.client('s3')
table = dynamodb.Table('stock-screener-data')

BUCKET = 'stock-screener-raw-data-116488731375'
KEY = 'pipeline/2026-07-18/step7_scores_055421.json'


def to_decimal(obj):
    """Convert floats to Decimal for DynamoDB."""
    if isinstance(obj, float):
        return Decimal(str(round(obj, 6)))
    elif isinstance(obj, dict):
        return {k: to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [to_decimal(i) for i in obj]
    return obj


# Download step7 output
print(f"Reading {KEY} from S3...")
response = s3_client.get_object(Bucket=BUCKET, Key=KEY)
data = json.loads(response['Body'].read())

scored_stocks = data.get('scored_stocks', [])
print(f"Found {len(scored_stocks)} scored stocks")

today = '2026-07-18'
now_iso = datetime.now(timezone.utc).isoformat()

# First, delete ALL existing items for these stocks (clean slate)
for stock in scored_stocks:
    symbol = stock.get('symbol', '')
    if not symbol:
        continue
    # Delete LATEST, TRACKING, and SCORE items
    for sk in ['LATEST', 'TRACKING', f'SCORE#{today}']:
        try:
            table.delete_item(Key={'PK': f'STOCK#{symbol}', 'SK': sk})
        except Exception:
            pass

print("Cleared existing items")

# Re-persist with ALL metrics
with table.batch_writer() as batch:
    for stock in scored_stocks:
        symbol = stock.get('symbol', '')
        if not symbol:
            continue

        # LATEST item with ALL metrics
        latest_item = {
            'PK': f'STOCK#{symbol}',
            'SK': 'LATEST',
            'symbol': symbol,
            'company_name': stock.get('company_name', ''),
            'sector': stock.get('sector', ''),
            'industry': stock.get('industry', ''),
            'price': stock.get('price'),
            'market_cap': stock.get('market_cap'),
            'investability_score': stock.get('investability_score'),
            'fundamental_score': stock.get('fundamental_score'),
            'sentiment_score': stock.get('sentiment', {}).get('sentiment_score'),
            'sentiment_confidence': stock.get('sentiment', {}).get('confidence'),
            'risk_flags': stock.get('sentiment', {}).get('risk_flags', []),
            'passes_screen': stock.get('passes_screen', False),
            # ALL screener filter metrics
            'pe_ratio': stock.get('pe_ratio'),
            'forward_pe': stock.get('forward_pe'),
            'peg_ratio': stock.get('peg_ratio'),
            'price_to_fcf': stock.get('price_to_fcf'),
            'debt_to_equity': stock.get('debt_to_equity'),
            'quick_ratio': stock.get('quick_ratio'),
            'operating_margin': stock.get('operating_margin'),
            'eps_growth_yoy': stock.get('eps_growth_yoy'),
            'revenue_growth_yoy': stock.get('revenue_growth_yoy'),
            'est_lt_growth': stock.get('est_lt_growth'),
            'analyst_recommendation': stock.get('analyst_recommendation'),
            'target_price_upside': stock.get('target_price_upside'),
            'institutional_transactions': stock.get('institutional_transactions'),
            'last_updated': now_iso,
            'tracking_status': 'ACTIVE' if stock.get('passes_screen') else 'GRACE',
        }
        # Remove None values (DynamoDB doesn't accept None)
        latest_item = {k: v for k, v in latest_item.items() if v is not None}
        batch.put_item(Item=to_decimal(latest_item))

        # SCORE item (historical)
        score_item = {
            'PK': f'STOCK#{symbol}',
            'SK': f'SCORE#{today}',
            'symbol': symbol,
            'date': today,
            'investability_score': stock.get('investability_score'),
            'fundamental_score': stock.get('fundamental_score'),
            'sentiment_score': stock.get('sentiment', {}).get('sentiment_score'),
            'price': stock.get('price'),
            'pe_ratio': stock.get('pe_ratio'),
            'forward_pe': stock.get('forward_pe'),
            'debt_to_equity': stock.get('debt_to_equity'),
            'risk_flags': stock.get('sentiment', {}).get('risk_flags', []),
            'last_updated': now_iso,
        }
        score_item = {k: v for k, v in score_item.items() if v is not None}
        batch.put_item(Item=to_decimal(score_item))

        # TRACKING item
        tracking_item = {
            'PK': f'STOCK#{symbol}',
            'SK': 'TRACKING',
            'symbol': symbol,
            'tracking_status': 'ACTIVE' if stock.get('passes_screen') else 'GRACE',
            'last_passed': today if stock.get('passes_screen') else None,
            'last_updated': now_iso,
        }
        tracking_item = {k: v for k, v in tracking_item.items() if v is not None}
        batch.put_item(Item=to_decimal(tracking_item))

        print(f"  {symbol}: forward_pe={stock.get('forward_pe')}, "
              f"passes={stock.get('passes_screen')}, "
              f"score={stock.get('investability_score')}")

print(f"\nDone. Re-persisted {len(scored_stocks)} stocks with all metrics.")
