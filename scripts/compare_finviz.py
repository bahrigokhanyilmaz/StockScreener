"""
Compare Finviz screener results to our pipeline.
Check why none of Finviz's 29 stocks appear in our final 6.

We'll check if they exist in our Step 1 data and where they got filtered out.
"""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')

# Finviz stocks from the URL (we'll need to manually list them since we can't scrape Finviz)
# These are the typical small/mid-cap value stocks that pass Finviz's strict filters
# Let me check our step2 (all 5097 stocks) and step4 (full screen results) to see what happened

# Load today's step1 output (all stocks)
print("Loading Step 1 (all 5097 stocks)...")
resp = s3.list_objects_v2(
    Bucket='stock-screener-raw-data-116488731375',
    Prefix='pipeline/2026-07-20/step1_'
)
step1_key = resp['Contents'][0]['Key']
resp = s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=step1_key)
step1_data = json.loads(resp['Body'].read())
all_stocks = step1_data.get('stocks', [])
print(f"  Total stocks in Step 1: {len(all_stocks)}")

# Load step2 (prescreen results)
print("\nLoading Step 2 (prescreen)...")
resp = s3.list_objects_v2(
    Bucket='stock-screener-raw-data-116488731375',
    Prefix='pipeline/2026-07-20/step2_'
)
step2_key = resp['Contents'][0]['Key']
resp = s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=step2_key)
step2_data = json.loads(resp['Body'].read())
passing_prescreen = step2_data.get('passing_stocks', [])
all_screened = step2_data.get('all_screened', [])
print(f"  Passing prescreen: {len(passing_prescreen)}")

# Load step4 (full screen results)
print("\nLoading Step 4 (full screen)...")
resp = s3.list_objects_v2(
    Bucket='stock-screener-raw-data-116488731375',
    Prefix='pipeline/2026-07-20/step4_'
)
step4_key = resp['Contents'][0]['Key']
resp = s3.get_object(Bucket='stock-screener-raw-data-116488731375', Key=step4_key)
step4_data = json.loads(resp['Body'].read())
passing_fullscreen = step4_data.get('passing_stocks', [])
print(f"  Passing full screen: {len(passing_fullscreen)}")
print(f"  Final stocks: {[s['symbol'] for s in passing_fullscreen]}")

# Build lookup by symbol
step1_by_symbol = {s['symbol']: s for s in all_stocks}
step2_by_symbol = {s['symbol']: s for s in all_screened}

# Let's check some well-known stocks that typically pass Finviz value screens
# Common Finviz value picks in 2026 mid-cap space:
test_tickers = [
    'CARG', 'ROAD', 'FTAI', 'CVNA', 'DUOL', 'CELH', 'HIMS', 'RELY',
    'DOCS', 'PAYC', 'ENPH', 'LNTH', 'COOP', 'TMDX', 'APPS', 'CROX',
    'FROG', 'SAIA', 'DECK', 'AXON', 'WFRD', 'TPX', 'COKE', 'FN', 'SKY'
]

print(f"\n\n=== Checking sample tickers (common Finviz value picks) ===\n")
print(f"{'Ticker':<8} {'In EDGAR?':>10} {'PreScreen?':>11} {'FullScreen?':>12} {'Why Failed':>30}")
print("-" * 75)

for ticker in test_tickers:
    in_edgar = ticker in step1_by_symbol
    in_prescreen = ticker in step2_by_symbol
    in_fullscreen = ticker in [s['symbol'] for s in passing_fullscreen]

    if not in_edgar:
        reason = "NOT IN EDGAR DATA"
    elif in_prescreen:
        stock = step2_by_symbol[ticker]
        if stock.get('passes_screen'):
            reason = "PASSED prescreen"
        else:
            # Find which filter failed
            fr = stock.get('filter_results', {})
            failed = [k for k, v in fr.items() if v.get('passes') == False]
            reason = f"Failed: {', '.join(failed[:3])}"
    else:
        reason = "Unknown"

    pre = "✓" if (in_prescreen and step2_by_symbol.get(ticker, {}).get('passes_screen')) else "✗"
    full = "✓" if in_fullscreen else "✗"
    edgar = "✓" if in_edgar else "✗"
    print(f"{ticker:<8} {edgar:>10} {pre:>11} {full:>12} {reason:>30}")
