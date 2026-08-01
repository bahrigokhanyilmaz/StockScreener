"""Check sentiment analyzer CloudWatch logs for errors."""
import json
import datetime
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
logs = session.client('logs')

# July 18 run was around 05:54 UTC
start = int(datetime.datetime(2026, 7, 18, 5, 50, tzinfo=datetime.timezone.utc).timestamp() * 1000)
end = int(datetime.datetime(2026, 7, 18, 6, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)

resp = logs.filter_log_events(
    logGroupName='/aws/lambda/stock-screener-sentiment-analyzer',
    startTime=start,
    endTime=end,
    filterPattern='Warning',
)

events = resp.get('events', [])
print(f"{len(events)} warning events")
for e in events[:10]:
    print(f"  {e['message'].strip()[:300]}")

# Also check for any parse errors
print("\n--- Checking for parse errors ---")
resp2 = logs.filter_log_events(
    logGroupName='/aws/lambda/stock-screener-sentiment-analyzer',
    startTime=start,
    endTime=end,
    filterPattern='parse',
)
events2 = resp2.get('events', [])
print(f"{len(events2)} parse-related events")
for e in events2[:5]:
    print(f"  {e['message'].strip()[:300]}")
