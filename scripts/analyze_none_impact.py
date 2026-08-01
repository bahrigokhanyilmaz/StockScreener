"""
Analyze: how many stocks would pass pre-screen if None = skip (not fail)?

This determines whether CompanyFacts gap-fill is viable at the resulting scale.
"""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
bucket = 'stock-screener-raw-data-116488731375'

# Load latest Step 1 output (all ~4,571 stocks)
resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-22/step1_')
if not resp.get('Contents'):
    resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-21/step1_')
data = json.loads(s3.get_object(Bucket=bucket, Key=resp['Contents'][-1]['Key'])['Body'].read())
stocks = data.get('stocks', [])

print(f"Total stocks from Step 1: {len(stocks)}")

# Pre-screen filters: D/E<1, QR>1, OpMgn>0, EPSGr>0, RevGr>0
# Count how many pass under different None handling strategies

# Strategy A: None = FAIL (current behavior)
pass_current = 0
# Strategy B: None = SKIP (pass if all AVAILABLE metrics pass)
pass_skip_none = 0
# Strategy C: Count None occurrences per metric
none_counts = {
    'debt_to_equity': 0,
    'quick_ratio': 0,
    'operating_margin': 0,
    'eps_growth_yoy': 0,
    'revenue_growth_yoy': 0,
}
# Strategy D: count stocks with at least 1 None but all available metrics pass
pass_with_gaps = 0

for stock in stocks:
    de = stock.get('debt_to_equity')
    qr = stock.get('quick_ratio')
    om = stock.get('operating_margin')
    eg = stock.get('eps_growth_yoy')
    rg = stock.get('revenue_growth_yoy')

    # Count Nones
    for key, val in [('debt_to_equity', de), ('quick_ratio', qr), 
                     ('operating_margin', om), ('eps_growth_yoy', eg),
                     ('revenue_growth_yoy', rg)]:
        if val is None:
            none_counts[key] += 1

    # Strategy A: all must be present AND pass
    if (de is not None and de < 1 and
        qr is not None and qr > 1 and
        om is not None and om > 0 and
        eg is not None and eg > 0 and
        rg is not None and rg > 0):
        pass_current += 1

    # Strategy B: skip None, pass if all available metrics pass
    fails = False
    has_any = False
    for val, check in [(de, lambda v: v < 1), (qr, lambda v: v > 1),
                       (om, lambda v: v > 0), (eg, lambda v: v > 0),
                       (rg, lambda v: v > 0)]:
        if val is not None:
            has_any = True
            if not check(val):
                fails = True
                break

    if has_any and not fails:
        pass_skip_none += 1
        # Does this stock have any None?
        if any(v is None for v in [de, qr, om, eg, rg]):
            pass_with_gaps += 1

print(f"\n--- Pre-screen results under different strategies ---")
print(f"Strategy A (None=FAIL, current):     {pass_current} pass")
print(f"Strategy B (None=SKIP):              {pass_skip_none} pass")
print(f"  Of which, have gaps (need fill):   {pass_with_gaps}")
print(f"  Already complete (no gaps):        {pass_skip_none - pass_with_gaps}")

print(f"\n--- None counts per metric (out of {len(stocks)} stocks) ---")
for key, count in none_counts.items():
    pct = count / len(stocks) * 100
    print(f"  {key:<25}: {count:>5} None ({pct:.1f}%)")

print(f"\n--- Viability assessment ---")
if pass_skip_none < 200:
    print(f"  CompanyFacts gap-fill: VIABLE ({pass_skip_none} stocks, ~{pass_skip_none//10}s)")
elif pass_skip_none < 500:
    print(f"  CompanyFacts gap-fill: MARGINAL ({pass_skip_none} stocks, ~{pass_skip_none//10}s)")
else:
    print(f"  CompanyFacts gap-fill: TOO MANY ({pass_skip_none} stocks)")
    print(f"  Need: multi-tag Frames approach instead")
