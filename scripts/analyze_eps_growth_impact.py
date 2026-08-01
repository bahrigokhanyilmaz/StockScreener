"""
Impact analysis: what happens if we loosen eps_growth_yoy in pre-screen?

Currently 223 pass pre-screen. How many additional stocks would pass if we
allow negative trailing EPS growth through (to be evaluated later by Finnhub forward estimates)?

Key concern: how many more stocks reach the Finnhub enrichment stage (3 calls each)?
"""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
bucket = 'stock-screener-raw-data-116488731375'

# Load Step 1
resp = s3.get_object(Bucket=bucket, Key='pipeline/2026-08-01/step1_fundamentals_160609.json')
stocks = json.loads(resp['Body'].read()).get('stocks', [])

print(f"Total stocks: {len(stocks)}\n")

# Count how many pass pre-screen under different eps_growth rules
pass_current = 0  # Current: eps_growth > 0 required
pass_allow_negative = 0  # New: eps_growth can be negative (or None)
pass_only_if_none = 0  # Only skip if None (still fail if explicitly negative)

for stock in stocks:
    de = stock.get('debt_to_equity')
    qr = stock.get('quick_ratio')
    om = stock.get('operating_margin')
    eg = stock.get('eps_growth_yoy')
    rg = stock.get('revenue_growth_yoy')

    # Base filters (non-EPS)
    base_passes = (
        (de is None or de < 1) and
        (qr is not None and qr > 1) and
        (om is not None and om > 0) and
        (rg is not None and rg > 0)
    )

    if not base_passes:
        continue

    # Current: eps_growth must be > 0
    if eg is not None and eg > 0:
        pass_current += 1

    # Option A: allow negative eps_growth through (evaluate later with Finnhub)
    if eg is None or eg > 0 or eg <= 0:  # always passes (removes eps_growth from prescreen)
        pass_allow_negative += 1

    # Option B: only allow if eps_growth is negative (not None)
    if eg is not None and (eg > 0 or eg <= 0):
        pass_only_if_none += 1

print(f"Pre-screen results with different eps_growth handling:")
print(f"  Current (eg > 0 required):         {pass_current}")
print(f"  Remove eg from prescreen entirely:  {pass_allow_negative}")
print(f"  Allow negative eg through:          {pass_only_if_none}")
print()

# The real question: how many reach Finnhub (which happens AFTER Polygon prices + P/E prefilter)?
# Currently: 223 prescreen → 40 prices → 5 pass P/E prefilter → 5 Finnhub calls
# If we allow negative eg: ~X prescreen → Y prices → Z pass P/E prefilter → Z Finnhub calls

# Estimate: how many of the "new" stocks would also pass the P/E + PEG + P/FCF prefilter?
# We can't compute P/E without prices (happens in Step 3), but we can count how many
# have the other metrics that matter.

additional = pass_allow_negative - pass_current
print(f"Additional stocks reaching Step 3: +{additional}")
print(f"Of those, ~{additional * 40 // pass_allow_negative} would get Polygon prices (based on 40/223 ratio)")
print(f"Of those, ~{additional * 5 // pass_allow_negative} would pass P/E prefilter (based on 5/223 ratio)")
print()
print(f"Estimated additional Finnhub calls: {additional * 5 // pass_allow_negative * 3} (3 per stock)")
print(f"At 60/min rate limit: adds ~{additional * 5 // pass_allow_negative * 3 // 20}s to pipeline")
