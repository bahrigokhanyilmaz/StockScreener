"""
Seed industry averages into DynamoDB using today's pipeline data.

Reads the Step 1 output (5,097 stocks with EDGAR fundamentals),
joins with the industry map, computes medians, and writes to DynamoDB.

This is exactly what the pre-screen Lambda will do on each pipeline run.
Running it manually now so the UI has data immediately.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')

BUCKET = 'stock-screener-raw-data-116488731375'

# Load industry map
print("Loading industry map from S3...")
resp = s3.get_object(Bucket=BUCKET, Key='reference/ticker_industry_map.json')
industry_map = json.loads(resp['Body'].read())
print(f"  {len(industry_map)} tickers mapped")

# Load today's Step 1 output (all 5,097 stocks with EDGAR fundamentals)
print("Loading Step 1 output from S3...")
resp = s3.list_objects_v2(Bucket=BUCKET, Prefix='pipeline/2026-07-18/step1_fundamentals_')
keys = [obj['Key'] for obj in resp.get('Contents', [])]
if not keys:
    # Try most recent date
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix='pipeline/', Delimiter='/')
    prefixes = [p['Prefix'] for p in resp.get('CommonPrefixes', [])]
    latest_date = sorted(prefixes)[-1]
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=f'{latest_date}step1_fundamentals_')
    keys = [obj['Key'] for obj in resp.get('Contents', [])]

if not keys:
    print("ERROR: No Step 1 output found in S3")
    exit(1)

key = keys[0]
print(f"  Reading {key}...")
resp = s3.get_object(Bucket=BUCKET, Key=key)
data = json.loads(resp['Body'].read())
stocks = data.get('stocks', [])
print(f"  {len(stocks)} stocks loaded")

# Metrics to compute medians for
METRICS = ['pe_ratio', 'debt_to_equity', 'quick_ratio', 'operating_margin',
           'eps_growth_yoy', 'revenue_growth_yoy']

# Join industry and group metrics
print("Computing industry medians...")
industry_data = defaultdict(lambda: defaultdict(list))
mapped = 0

for stock in stocks:
    symbol = stock.get('symbol', '')
    entry = industry_map.get(symbol)
    if not entry:
        continue
    industry = entry.get('industry', '')
    if not industry:
        continue
    mapped += 1
    for metric in METRICS:
        val = stock.get(metric)
        if val is not None and isinstance(val, (int, float)):
            industry_data[industry][metric].append(val)

print(f"  Mapped {mapped}/{len(stocks)} stocks to industries")
print(f"  {len(industry_data)} unique industries with data")


def median(values):
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


# Compute medians (min 5 stocks per industry)
industry_medians = {}
for industry, metrics in industry_data.items():
    avgs = {}
    sample_size = 0
    for metric, values in metrics.items():
        if len(values) >= 5:
            avgs[metric] = round(median(values), 4)
            sample_size = max(sample_size, len(values))
    if avgs:
        avgs['sample_size'] = sample_size
        industry_medians[industry] = avgs

print(f"  Computed medians for {len(industry_medians)} industries (min 5 stocks each)")

# Show examples for our stocks
for ticker in ['LRN', 'PRIM', 'EXLS', 'TILE', 'PTC', 'TRS']:
    entry = industry_map.get(ticker, {})
    ind = entry.get('industry', '')
    if ind in industry_medians:
        m = industry_medians[ind]
        print(f"    {ticker} ({ind}): PE={m.get('pe_ratio','?')}, D/E={m.get('debt_to_equity','?')}, "
              f"QR={m.get('quick_ratio','?')}, OpMgn={m.get('operating_margin','?')}, "
              f"n={m.get('sample_size','?')}")

# Persist to DynamoDB
print("\nPersisting to DynamoDB...")
today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
now_iso = datetime.now(timezone.utc).isoformat()

written = 0
with table.batch_writer() as batch:
    for industry, metrics in industry_medians.items():
        item = {
            'PK': f'INDUSTRY_AVG#{industry}',
            'SK': 'METRICS',
            'industry': industry,
            'updated_date': today,
            'last_updated': now_iso,
        }
        for k, v in metrics.items():
            if isinstance(v, float):
                item[k] = Decimal(str(round(v, 6)))
            else:
                item[k] = v
        batch.put_item(Item=item)
        written += 1

print(f"  Wrote {written} industry average records to DynamoDB")
print("\nDone!")
