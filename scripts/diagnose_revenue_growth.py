"""
Diagnose revenue_growth_yoy failures.
Check what our pipeline computes vs reality for the failing stocks.
"""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
bucket = 'stock-screener-raw-data-116488731375'

# Load Step 1
resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-23/step1_')
if not resp.get('Contents'):
    resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-22/step1_')
data = json.loads(s3.get_object(Bucket=bucket, Key=resp['Contents'][-1]['Key'])['Body'].read())
step1_by_sym = {s['symbol']: s for s in data.get('stocks', [])}

tickers = ['INTU', 'CTSH', 'EPAM', 'TTD', 'EZPW', 'HRMY', 'LCII', 'BMRN', 'APEI', 'WAY', 'TCMD', 'UHS']

print(f"{'Ticker':<7} {'RevGrowth':>10} {'Revenue':>12} {'EPS Growth':>10} {'EPS':>8} {'Notes'}")
print("-" * 65)

for ticker in tickers:
    stock = step1_by_sym.get(ticker)
    if not stock:
        print(f"{ticker:<7} NOT IN STEP 1")
        continue
    
    rg = stock.get('revenue_growth_yoy')
    eg = stock.get('eps_growth_yoy')
    rev = stock.get('revenue_per_share')
    eps = stock.get('eps')
    
    rg_str = f"{rg*100:.1f}%" if rg is not None else "None"
    eg_str = f"{eg*100:.1f}%" if eg is not None else "None"
    rev_str = f"${rev:.2f}" if rev else "None"
    eps_str = f"${eps:.2f}" if eps else "None"
    
    notes = ""
    if rg is None:
        notes = "NO REVENUE DATA"
    elif rg <= 0:
        notes = "NEGATIVE GROWTH"
    
    print(f"{ticker:<7} {rg_str:>10} {rev_str:>12} {eg_str:>10} {eps_str:>8} {notes}")
