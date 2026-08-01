"""
Diagnose why each of Finviz's 28 stocks fails our pipeline.
Checks Step 1 (EDGAR data), Step 2 (prescreen), Step 3 (enrichment), Step 4 (full screen).
"""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
bucket = 'stock-screener-raw-data-116488731375'

finviz_tickers = [
    'APEI', 'ARIS', 'AU', 'BMRN', 'CAAP', 'CDE', 'CINT', 'CTSH', 'DLO', 'EPAM',
    'ETOR', 'EZPW', 'HRMY', 'INTU', 'JLL', 'KGC', 'LCII', 'SEIC', 'SSRM', 'STN',
    'TCMD', 'TEL', 'TKC', 'TTD', 'UHS', 'UPWK', 'VC', 'WAY'
]

# Load Step 1 (full universe with EDGAR data)
print("Loading Step 1...")
resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-22/step1_')
if not resp.get('Contents'):
    resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-21/step1_')
step1_data = json.loads(s3.get_object(Bucket=bucket, Key=resp['Contents'][-1]['Key'])['Body'].read())
step1_by_sym = {s['symbol']: s for s in step1_data.get('stocks', [])}

# Load Step 2 (prescreen results)
print("Loading Step 2...")
resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-22/step2_')
if not resp.get('Contents'):
    resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-21/step2_')
step2_data = json.loads(s3.get_object(Bucket=bucket, Key=resp['Contents'][-1]['Key'])['Body'].read())
step2_by_sym = {s['symbol']: s for s in step2_data.get('all_screened', [])}
step2_passing = {s['symbol'] for s in step2_data.get('passing_stocks', [])}

# Load Step 4 (full screen)
print("Loading Step 4...")
resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-22/step4_')
if not resp.get('Contents'):
    resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-21/step4_')
step4_data = json.loads(s3.get_object(Bucket=bucket, Key=resp['Contents'][-1]['Key'])['Body'].read())
step4_by_sym = {s['symbol']: s for s in step4_data.get('all_screened', [])}
step4_passing = {s['symbol'] for s in step4_data.get('passing_stocks', [])}

print(f"\nOur results: {len(step2_passing)} prescreen, {len(step4_passing)} full screen")
print(f"Finviz: {len(finviz_tickers)} stocks")
print(f"Overlap in full screen: {set(finviz_tickers) & step4_passing}")
print()

print(f"{'Ticker':<7} {'In EDGAR':>9} {'PreScreen':>10} {'FullScreen':>11} {'Failure Reason'}")
print("=" * 90)

for ticker in finviz_tickers:
    in_edgar = ticker in step1_by_sym
    in_prescreen = ticker in step2_passing
    in_fullscreen = ticker in step4_passing

    if not in_edgar:
        reason = "NOT IN EDGAR UNIVERSE (no CY2026Q1 filing)"
    elif not in_prescreen:
        # Check why prescreen failed
        stock = step2_by_sym.get(ticker, step1_by_sym.get(ticker, {}))
        fr = stock.get('filter_results', {})
        if fr:
            failed = [k for k, v in fr.items() if v.get('passes') == False]
            reason = f"PreScreen FAIL: {', '.join(failed)}"
        else:
            # No filter results — check raw values
            failures = []
            de = stock.get('debt_to_equity')
            qr = stock.get('quick_ratio')
            om = stock.get('operating_margin')
            eg = stock.get('eps_growth_yoy')
            rg = stock.get('revenue_growth_yoy')
            if de is None: failures.append('D/E=None')
            elif de >= 1: failures.append(f'D/E={de:.2f}>=1')
            if qr is None: failures.append('QR=None')
            elif qr < 1: failures.append(f'QR={qr:.2f}<1')
            if om is None: failures.append('OpMgn=None')
            elif om <= 0: failures.append(f'OpMgn={om:.3f}<=0')
            if eg is None: failures.append('EPSGr=None')
            elif eg <= 0: failures.append(f'EPSGr={eg:.3f}<=0')
            if rg is None: failures.append('RevGr=None')
            elif rg <= 0: failures.append(f'RevGr={rg:.3f}<=0')
            reason = f"PreScreen FAIL: {', '.join(failures)}" if failures else "PreScreen FAIL: unknown"
    elif not in_fullscreen:
        # Passed prescreen but failed full screen — check enrichment/step4
        stock = step4_by_sym.get(ticker, {})
        fr = stock.get('filter_results', {})
        if fr:
            failed = [(k, f"val={v.get('value')}, thr={v.get('threshold')}") 
                      for k, v in fr.items() if v.get('passes') == False]
            reason = f"FullScreen FAIL: {'; '.join([f[0]+'('+f[1]+')' for f in failed[:3]])}"
        else:
            reason = "FullScreen FAIL: not in step4 (no price or prefilter fail in enrichment)"
    else:
        reason = "✓ PASSES OUR SCREEN"

    e = "✓" if in_edgar else "✗"
    p = "✓" if in_prescreen else "✗"
    f = "✓" if in_fullscreen else "✗"
    print(f"{ticker:<7} {e:>9} {p:>10} {f:>11} {reason}")
