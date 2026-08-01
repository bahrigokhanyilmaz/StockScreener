"""Check what the dashboard currently shows (all ACTIVE stocks in DynamoDB)."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')

# Query tracking-status-index for ACTIVE stocks
resp = table.query(
    IndexName='tracking-status-index',
    KeyConditionExpression=boto3.dynamodb.conditions.Key('tracking_status').eq('ACTIVE'),
)

items = [i for i in resp['Items'] if i.get('SK') == 'LATEST']
items.sort(key=lambda x: float(x.get('investability_score', 0)), reverse=True)

print(f"ACTIVE stocks in dashboard: {len(items)}\n")
print(f"{'Ticker':<7} {'Score':>6} {'Fund':>5} {'Sent':>5} {'P/E':>6} {'PEG':>5} {'D/E':>5} {'Flags'}")
print("-" * 70)

for item in items:
    ticker = item.get('symbol', '?')
    score = float(item.get('investability_score', 0))
    fund = float(item.get('fundamental_score', 0))
    sent = float(item.get('sentiment_score', 0))
    pe = item.get('pe_ratio')
    peg = item.get('peg_ratio')
    de = item.get('debt_to_equity')
    flags = item.get('risk_flags', [])
    flag_names = [f.get('flag', f) if isinstance(f, dict) else f for f in flags]

    pe_str = f"{float(pe):.1f}" if pe else "—"
    peg_str = f"{float(peg):.2f}" if peg else "—"
    de_str = f"{float(de):.2f}" if de else "—"
    flags_str = ', '.join(flag_names) if flag_names else '—'

    print(f"{ticker:<7} {score:>6.1f} {fund:>5.1f} {sent:>+5.2f} {pe_str:>6} {peg_str:>5} {de_str:>5} {flags_str}")
