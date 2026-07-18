"""Compare Finnhub vs SEC SIC industry labels for our stocks."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')
s3 = session.client('s3')

# Load industry map once
resp = s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key='reference/ticker_industry_map.json')
ind_map = json.loads(resp['Body'].read())

for ticker in ['LRN', 'PRIM', 'EXLS', 'TILE', 'PTC', 'TRS']:
    item = table.get_item(Key={'PK': f'STOCK#{ticker}', 'SK': 'LATEST'}).get('Item', {})
    finnhub_ind = item.get('industry', '?')
    sic_entry = ind_map.get(ticker, {})
    sic_ind = sic_entry.get('industry', '?')
    print(f'{ticker}: Finnhub="{finnhub_ind}" | SEC SIC="{sic_ind}"')
