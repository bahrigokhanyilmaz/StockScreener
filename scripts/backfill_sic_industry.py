"""Backfill sic_industry on existing LATEST items in DynamoDB."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')
s3 = session.client('s3')

# Load industry map
resp = s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key='reference/ticker_industry_map.json')
ind_map = json.loads(resp['Body'].read())

for ticker in ['LRN', 'PRIM', 'EXLS', 'TILE', 'PTC', 'TRS']:
    sic_entry = ind_map.get(ticker, {})
    sic_ind = sic_entry.get('industry', '')
    if sic_ind:
        table.update_item(
            Key={'PK': f'STOCK#{ticker}', 'SK': 'LATEST'},
            UpdateExpression='SET sic_industry = :val',
            ExpressionAttributeValues={':val': sic_ind},
        )
        print(f"  {ticker}: sic_industry = {sic_ind}")

print("Done!")
