"""
Diagnose why D/E, Operating Margin, and Revenue Growth fail for specific Finviz stocks.
Compare our EDGAR values to what Finviz likely uses.
"""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
bucket = 'stock-screener-raw-data-116488731375'

# Load Step 1 output
resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-22/step1_')
if not resp.get('Contents'):
    resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-21/step1_')
data = json.loads(s3.get_object(Bucket=bucket, Key=resp['Contents'][-1]['Key'])['Body'].read())
step1_by_sym = {s['symbol']: s for s in data.get('stocks', [])}

# D/E failures (Finviz says < 1, we say >= 1)
print("=" * 80)
print("DEBT/EQUITY FAILURES (our value >= 1, Finviz says < 1)")
print("=" * 80)
print(f"{'Ticker':<7} {'Our D/E':>10} {'LT Debt':>12} {'Equity':>12} {'Notes'}")
print("-" * 60)

for ticker in ['CDE', 'CTSH', 'EPAM', 'TCMD', 'TEL', 'TTD']:
    stock = step1_by_sym.get(ticker)
    if not stock:
        print(f"{ticker:<7} NOT IN EDGAR")
        continue
    de = stock.get('debt_to_equity')
    # We compute D/E as LongTermDebt / StockholdersEquity
    # But Finviz might use total_debt / equity or exclude certain items
    de_str = f"{de:.3f}" if de is not None else "None"
    print(f"{ticker:<7} {de_str:>10}")

# Operating margin failures
print("\n" + "=" * 80)
print("OPERATING MARGIN FAILURES (our value <= 0, Finviz says > 0)")
print("=" * 80)
print(f"{'Ticker':<7} {'Our OpMgn':>10} {'Notes'}")
print("-" * 40)

for ticker in ['LCII', 'SEIC', 'VC']:
    stock = step1_by_sym.get(ticker)
    if not stock:
        print(f"{ticker:<7} NOT IN EDGAR")
        continue
    om = stock.get('operating_margin')
    om_str = f"{om*100:.2f}%" if om is not None else "None"
    print(f"{ticker:<7} {om_str:>10}")

# Revenue growth failures
print("\n" + "=" * 80)
print("REVENUE GROWTH FAILURES (our value <= 0, Finviz says > 0)")
print("=" * 80)
print(f"{'Ticker':<7} {'Our RevGr':>10} {'Notes'}")
print("-" * 40)

for ticker in ['LCII', 'SEIC', 'VC']:
    stock = step1_by_sym.get(ticker)
    if not stock:
        print(f"{ticker:<7} NOT IN EDGAR")
        continue
    rg = stock.get('revenue_growth_yoy')
    rg_str = f"{rg*100:.2f}%" if rg is not None else "None"
    print(f"{ticker:<7} {rg_str:>10}")
