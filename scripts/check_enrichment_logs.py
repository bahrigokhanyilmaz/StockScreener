"""Check enrichment Lambda logs."""
import json
import datetime
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
logs = session.client('logs')

# Pipeline ran at ~03:22 UTC July 21
start = int(datetime.datetime(2026, 7, 21, 3, 20, tzinfo=datetime.timezone.utc).timestamp() * 1000)
end = int(datetime.datetime(2026, 7, 21, 3, 40, tzinfo=datetime.timezone.utc).timestamp() * 1000)

resp = logs.filter_log_events(
    logGroupName='/aws/lambda/stock-screener-price-enrichment',
    startTime=start,
    endTime=end,
    limit=30,
)

for e in resp.get('events', []):
    msg = e['message'].strip()
    if msg and not msg.startswith(('INIT_', 'END ', 'REPORT ', 'START ')):
        print(f"  {msg[:200]}")
