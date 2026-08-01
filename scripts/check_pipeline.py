"""Check recent pipeline executions."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
sfn = session.client('stepfunctions')

resp = sfn.list_executions(
    stateMachineArn='arn:aws:states:us-east-2:116488731375:stateMachine:stock-screener-pipeline',
    maxResults=5
)

for ex in resp.get('executions', []):
    print(f"  {ex['status']:10} | started: {ex['startDate']} | {ex['name'][:50]}")
