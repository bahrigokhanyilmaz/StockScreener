"""Debug why a specific stock failed our filters."""
import json
import sys
import boto3

ticker = sys.argv[1] if len(sys.argv) > 1 else 'CELH'

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

# Load step1
resp = s3.list_objects_v2(
    Bucket='stock-screener-raw-data-116488731375',
    Prefix='pipeline/2026-07-20/step1_'
)
step1_key = resp['Contents'][0]['Key']
resp = s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=step1_key)
data = json.loads(resp['Body'].read())

stock = None
for s in data['stocks']:
    if s['symbol'] == ticker:
        stock = s
        break

if not stock:
    print(f"{ticker} NOT FOUND in Step 1 EDGAR data")
    sys.exit(1)

print(f"=== {ticker} — EDGAR Data ===\n")
print(f"  company_name: {stock.get('company_name')}")
print(f"  eps: {stock.get('eps')}")
print(f"  debt_to_equity: {stock.get('debt_to_equity')}")
print(f"  quick_ratio: {stock.get('quick_ratio')}")
print(f"  operating_margin: {stock.get('operating_margin')}")
print(f"  eps_growth_yoy: {stock.get('eps_growth_yoy')}")
print(f"  revenue_growth_yoy: {stock.get('revenue_growth_yoy')}")
print(f"  net_profit_margin: {stock.get('net_profit_margin')}")
print(f"  fcf_per_share: {stock.get('fcf_per_share')}")
print()

# Show the raw underlying data
# eps_growth = (current_eps - prev_eps) / prev_eps
# We store both in the dict under different keys
for key in sorted(stock.keys()):
    val = stock[key]
    if val is not None and key not in ['symbol', 'company_name', 'sector', 'industry', 'exchange']:
        if isinstance(val, float):
            print(f"  {key}: {val:.4f}")
        elif isinstance(val, (int, bool)):
            print(f"  {key}: {val}")
