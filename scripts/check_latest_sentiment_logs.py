"""Check sentiment analyzer logs from the latest run."""
import json
import datetime
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
logs = session.client('logs')

# Today's run: step6 output at 04:19 UTC (2026-07-19)
start = int(datetime.datetime(2026, 7, 19, 4, 17, tzinfo=datetime.timezone.utc).timestamp() * 1000)
end = int(datetime.datetime(2026, 7, 19, 4, 22, tzinfo=datetime.timezone.utc).timestamp() * 1000)

resp = logs.filter_log_events(
    logGroupName='/aws/lambda/stock-screener-sentiment-analyzer',
    startTime=start,
    endTime=end,
    limit=30,
)

events = resp.get('events', [])
print(f"{len(events)} log events")
for e in events:
    msg = e['message'].strip()
    if msg and not msg.startswith('INIT_') and not msg.startswith('END ') and not msg.startswith('REPORT ') and not msg.startswith('START '):
        print(f"  {msg[:300]}")
