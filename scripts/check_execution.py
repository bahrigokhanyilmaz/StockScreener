"""Check the current pipeline execution status."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
sfn = session.client('stepfunctions')

arn = "arn:aws:states:us-east-2:116488731375:execution:stock-screener-pipeline:85ecada7-4f1b-4c53-9d5c-b3779a4f14be"

resp = sfn.describe_execution(executionArn=arn)
print(f"Status: {resp['status']}")
if resp['status'] == 'FAILED':
    print(f"Error: {resp.get('error', '?')}")
    print(f"Cause: {resp.get('cause', '?')[:500]}")
elif resp['status'] == 'RUNNING':
    # Get execution history to see which step we're on
    history = sfn.get_execution_history(executionArn=arn, reverseOrder=True, maxResults=5)
    for event in history['events'][:5]:
        etype = event['type']
        details = event.get('stateEnteredEventDetails', event.get('taskSucceededEventDetails', event.get('taskStartedEventDetails', {})))
        name = details.get('name', '') if isinstance(details, dict) else ''
        print(f"  {etype}: {name}")
