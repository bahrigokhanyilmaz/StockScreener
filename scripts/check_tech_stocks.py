"""Check why major tech/AI stocks fail our filters."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
bucket = 'stock-screener-raw-data-116488731375'

# Load step 4 (all_screened has filter results)
resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-08-01/step4_')
key = resp['Contents'][-1]['Key']
step4 = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
step4_by_sym = {s['symbol']: s for s in step4.get('all_screened', [])}

# Load step 2
resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-08-01/step2_')
key = resp['Contents'][-1]['Key']
step2 = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
step2_by_sym = {s['symbol']: s for s in step2.get('all_screened', [])}
step2_pass = {s['symbol'] for s in step2.get('passing_stocks', [])}

# Load step 1
resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-08-01/step1_')
key = resp['Contents'][-1]['Key']
step1 = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
step1_by_sym = {s['symbol']: s for s in step1.get('stocks', [])}

tech_stocks = ['NVDA', 'MSFT', 'GOOGL', 'META', 'AMZN', 'AAPL', 'PLTR', 'CRM', 'SNOW', 'AMD']

print(f"{'Ticker':<7} {'PreScr':>6} {'FullScr':>7} {'P/E':>6} {'PEG':>6} {'P/FCF':>6} {'D/E':>5} {'EPSGr':>7} {'RevGr':>7} {'Failure'}")
print("-" * 95)

for ticker in tech_stocks:
    in_pre = ticker in step2_pass
    in_step4 = ticker in step4_by_sym

    # Get metrics from step1
    s1 = step1_by_sym.get(ticker, {})
    pe = s1.get('pe_ratio')
    # Can't get PEG from step1 (needs price), check step4
    s4 = step4_by_sym.get(ticker, {})

    # Determine failure reason
    if not s1:
        reason = "NOT IN EDGAR"
    elif not in_pre:
        # Check pre-screen failure
        stock = step2_by_sym.get(ticker, s1)
        fr = stock.get('filter_results', {})
        if fr:
            failed = [k for k, v in fr.items() if v.get('passes') == False]
            reason = f"PreScr: {', '.join(failed[:3])}"
        else:
            failures = []
            if s1.get('debt_to_equity') is None: failures.append('D/E=None')
            elif s1.get('debt_to_equity', 0) >= 1: failures.append(f"D/E={s1['debt_to_equity']:.2f}")
            if s1.get('eps_growth_yoy') is None: failures.append('EPSGr=None')
            elif s1.get('eps_growth_yoy', 0) <= 0: failures.append(f"EPSGr={s1['eps_growth_yoy']*100:.0f}%")
            if s1.get('revenue_growth_yoy') is None: failures.append('RevGr=None')
            elif s1.get('revenue_growth_yoy', 0) <= 0: failures.append(f"RevGr={s1['revenue_growth_yoy']*100:.0f}%")
            if s1.get('quick_ratio') is not None and s1['quick_ratio'] < 1: failures.append(f"QR={s1['quick_ratio']:.2f}")
            reason = f"PreScr: {', '.join(failures)}"
    elif in_step4:
        fr = s4.get('filter_results', {})
        failed = [(k, f"{v.get('value'):.1f}vs{v.get('threshold'):.1f}") 
                  for k, v in fr.items() if v.get('passes') == False and v.get('value') is not None]
        if s4.get('passes_screen'):
            reason = "✓ PASSES"
        else:
            reason = f"FullScr: {', '.join([f[0]+'('+f[1]+')' for f in failed[:2]])}"
    else:
        reason = "FullScr: not reached (enrichment prefilter)"

    pe_str = f"{s1.get('pe_ratio', 0):.1f}" if s1.get('pe_ratio') else "—"
    de_str = f"{s1.get('debt_to_equity', 0):.2f}" if s1.get('debt_to_equity') is not None else "None"
    eg_str = f"{s1.get('eps_growth_yoy', 0)*100:.0f}%" if s1.get('eps_growth_yoy') is not None else "None"
    rg_str = f"{s1.get('revenue_growth_yoy', 0)*100:.0f}%" if s1.get('revenue_growth_yoy') is not None else "None"

    print(f"{ticker:<7} {'✓' if in_pre else '✗':>6} {'✓' if in_step4 and s4.get('passes_screen') else '✗':>7} {pe_str:>6} {'—':>6} {'—':>6} {de_str:>5} {eg_str:>7} {rg_str:>7} {reason}")
