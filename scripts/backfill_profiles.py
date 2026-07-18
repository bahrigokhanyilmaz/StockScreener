"""
Backfill company profiles for existing stocks in DynamoDB.
Fetches from Finnhub /stock/profile2 and updates LATEST items.
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

# Get Finnhub key from SSM
finnhub_key = ssm_client.get_parameter(
    Name='/stock-screener/finnhub-api-key', WithDecryption=True
)['Parameter']['Value']

FINNHUB_BASE = "https://finnhub.io/api/v1"

# Get all active stocks
response = table.query(
    IndexName='tracking-status-index',
    KeyConditionExpression=boto3.dynamodb.conditions.Key('tracking_status').eq('ACTIVE'),
)

stocks = [item for item in response['Items'] if item.get('SK') == 'LATEST']
print(f"Found {len(stocks)} active stocks to backfill profiles for")

for i, stock in enumerate(stocks):
    symbol = stock['symbol']
    print(f"\n[{i+1}/{len(stocks)}] Fetching profile for {symbol}...")

    try:
        resp = requests.get(
            f"{FINNHUB_BASE}/stock/profile2",
            params={"symbol": symbol, "token": finnhub_key},
            timeout=10
        )
        if resp.status_code == 200:
            profile = resp.json()
            description = profile.get("description", "")
            logo = profile.get("logo", "")
            weburl = profile.get("weburl", "")
            industry = profile.get("finnhubIndustry", "")

            # Update DynamoDB
            update_expr = "SET company_description = :desc, logo = :logo, weburl = :web"
            expr_values = {
                ':desc': description,
                ':logo': logo,
                ':web': weburl,
            }

            if industry and not stock.get('industry'):
                update_expr += ", industry = :ind"
                expr_values[':ind'] = industry

            table.update_item(
                Key={'PK': f'STOCK#{symbol}', 'SK': 'LATEST'},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values,
            )

            print(f"  Updated: {profile.get('name', '?')}")
            print(f"  Industry: {industry}")
            print(f"  Description: {description[:80]}...")
        else:
            print(f"  Warning: Finnhub returned {resp.status_code}")
    except Exception as e:
        print(f"  Error: {e}")

    # Rate limit: 60 calls/min
    if i < len(stocks) - 1:
        time.sleep(1.5)

print("\nDone!")
