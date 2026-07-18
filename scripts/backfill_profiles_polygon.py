"""
Backfill company descriptions from Polygon ticker details.
Finnhub profile2 gives logo/industry/weburl but NOT descriptions on free tier.
Polygon /v3/reference/tickers/{ticker} gives full descriptions.

This script combines both sources:
- Finnhub: logo, weburl, industry (already backfilled)
- Polygon: description, sic_description
"""
import json
import time
from decimal import Decimal

import boto3
import requests

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
dynamodb = session.resource('dynamodb')
ssm_client = session.client('ssm')
table = dynamodb.Table('stock-screener-data')

polygon_key = ssm_client.get_parameter(
    Name='/stock-screener/polygon-api-key', WithDecryption=True
)['Parameter']['Value']

# Get all active stocks
response = table.query(
    IndexName='tracking-status-index',
    KeyConditionExpression=boto3.dynamodb.conditions.Key('tracking_status').eq('ACTIVE'),
)

stocks = [item for item in response['Items'] if item.get('SK') == 'LATEST']
print(f"Found {len(stocks)} active stocks to backfill descriptions for")

for i, stock in enumerate(stocks):
    symbol = stock['symbol']
    print(f"\n[{i+1}/{len(stocks)}] Fetching Polygon profile for {symbol}...")

    try:
        resp = requests.get(
            f"https://api.polygon.io/v3/reference/tickers/{symbol}",
            params={"apiKey": polygon_key},
            timeout=10
        )
        if resp.status_code == 200:
            results = resp.json().get('results', {})
            description = results.get('description', '')
            sic_desc = results.get('sic_description', '')

            if description:
                table.update_item(
                    Key={'PK': f'STOCK#{symbol}', 'SK': 'LATEST'},
                    UpdateExpression='SET company_description = :desc',
                    ExpressionAttributeValues={':desc': description},
                )
                print(f"  Description ({len(description)} chars): {description[:100]}...")
            else:
                print(f"  No description available")
        else:
            print(f"  Warning: Polygon returned {resp.status_code}")
    except Exception as e:
        print(f"  Error: {e}")

    # Polygon free: 5 calls/min → 12s between calls
    if i < len(stocks) - 1:
        time.sleep(12.5)

print("\nDone!")
