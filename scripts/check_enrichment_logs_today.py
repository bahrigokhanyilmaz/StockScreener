"""Check enrichment Lambda logs from today's scheduled run."""
import json
import datetime
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
logs = session.client('logs')

# Scheduled run started at 13:00 PT = 20:00 UTC on July 21
start = int(datetime.datetime(2026, 7, 21, 20, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
end = int(datetime.datetime(2026, 7, 21, 20, 15, tzinfo=datetime.timezone.utc).timestamp() * 1000)

resp = logs.filter_log_events(
    logGroupName='/aws/lambda/stock-screener-price-enrichment',
    startTime=start,
    endTime=end,
    limit=20,
)

for e in resp.get('events', []):
    msg = e['message'].strip()
    if msg and not msg.startswith(('INIT_', 'END ', 'REPORT ', 'START ')):
        print(f"  {msg[:200]}")
