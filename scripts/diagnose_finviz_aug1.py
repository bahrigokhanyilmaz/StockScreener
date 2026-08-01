"""Diagnose why Finviz's 22 stocks fail our pipeline (Aug 1 run)."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
bucket = 'stock-screener-raw-data-116488731375'

finviz_tickers = [
    'APEI', 'AU', 'BMRN', 'CAAP', 'CDE', 'CINT', 'CLMB', 'DLO', 'EPAM',
    'EZPW', 'HAL', 'HRMY', 'KGC', 'LCII', 'QNST', 'SSRM', 'TCMD', 'TEL',
    'TKC', 'TTD', 'UPWK', 'WAY'
]

# Load Step 1
resp = s3.get_object(Bucket=bucket, Key='pipeline/2026-08-01/step1_fundamentals_160609.json')
step1_data = json.loads(resp['Body'].read())
step1_by_sym = {s['symbol']: s for s in step1_data.get('stocks', [])}

# Load Step 2 (all_screened has filter_results)
resp = s3.get_object(Bucket=bucket, Key='pipeline/2026-08-01/step2_prescreen_160615.json')
step2_data = json.loads(resp['Body'].read())
step2_by_sym = {s['symbol']: s for s in step2_data.get('all_screened', [])}
step2_passing = {s['symbol'] for s in step2_data.get('passing_stocks', [])}

# Load Step 4
resp = s3.get_object(Bucket=bucket, Key='pipeline/2026-08-01/step4_fullscreen_160946.json')
step4_data = json.loads(resp['Body'].read())
step4_by_sym = {s['symbol']: s for s in step4_data.get('all_screened', [])}
step4_passing = {s['symbol'] for s in step4_data.get('passing_stocks', [])}

print(f"Our pipeline: {len(step2_passing)} prescreen → {len(step4_passing)} full screen")
print(f"Finviz: {len(finviz_tickers)} stocks")
print(f"Overlap: {set(finviz_tickers) & step4_passing}")
print()

print(f"{'Ticker':<7} {'EDGAR':>6} {'Pre':>4} {'Full':>5} {'Failure Reason'}")
print("=" * 95)

for ticker in finviz_tickers:
    in_edgar = ticker in step1_by_sym
    in_prescreen = ticker in step2_passing
    in_fullscreen = ticker in step4_passing

    if not in_edgar:
        reason = "NOT IN EDGAR (foreign/non-GAAP/fiscal year mismatch)"
    elif not in_prescreen:
        stock = step2_by_sym.get(ticker, step1_by_sym.get(ticker, {}))
        fr = stock.get('filter_results', {})
        if fr:
            failed = []
            for k, v in fr.items():
                if v.get('passes') == False:
                    val = v.get('value')
                    val_str = f"{val:.3f}" if isinstance(val, float) else str(val)
                    failed.append(f"{k}={val_str}")
            reason = f"PreScreen: {', '.join(failed)}"
        else:
            # Check raw values
            failures = []
            de = stock.get('debt_to_equity')
            qr = stock.get('quick_ratio')
            om = stock.get('operating_margin')
            eg = stock.get('eps_growth_yoy')
            rg = stock.get('revenue_growth_yoy')
            if de is None: failures.append('D/E=None')
            elif de >= 1: failures.append(f'D/E={de:.2f}')
            if qr is None: failures.append('QR=None')
            elif qr < 1: failures.append(f'QR={qr:.2f}')
            if om is None: failures.append('OpM=None')
            elif om <= 0: failures.append(f'OpM={om:.3f}')
            if eg is None: failures.append('EPSGr=None')
            elif eg <= 0: failures.append(f'EPSGr={eg:.3f}')
            if rg is None: failures.append('RevGr=None')
            elif rg <= 0: failures.append(f'RevGr={rg:.3f}')
            reason = f"PreScreen: {', '.join(failures)}"
    elif not in_fullscreen:
        stock = step4_by_sym.get(ticker, {})
        fr = stock.get('filter_results', {})
        if fr:
            failed = []
            for k, v in fr.items():
                if v.get('passes') == False:
                    val = v.get('value')
                    thr = v.get('threshold')
                    val_str = f"{val:.2f}" if isinstance(val, float) else str(val)
                    thr_str = f"{thr:.2f}" if isinstance(thr, float) else str(thr)
                    failed.append(f"{k}({val_str} vs {thr_str})")
            reason = f"FullScreen: {', '.join(failed)}"
        else:
            reason = "FullScreen: not in step4 data (failed enrichment prefilter)"
    else:
        reason = "✓ PASSES"

    e = "✓" if in_edgar else "✗"
    p = "✓" if in_prescreen else "✗"
    f = "✓" if in_fullscreen else "✗"
    print(f"{ticker:<7} {e:>6} {p:>4} {f:>5} {reason}")
