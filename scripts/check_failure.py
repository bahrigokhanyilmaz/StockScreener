"""Check why today's pipeline failed."""
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
sfn = session.client('stepfunctions')

# List recent executions
resp = sfn.list_executions(
    stateMachineArn='arn:aws:states:us-east-2:116488731375:stateMachine:stock-screener-pipeline',
    maxResults=3
)

for ex in resp['executions']:
    status = ex['status']
    arn = ex['executionArn']
    started = ex['startDate']
    print(f"\n{status} | {started}")
    
    if status == 'FAILED':
        detail = sfn.describe_execution(executionArn=arn)
        print(f"  Error: {detail.get('error', '?')}")
        cause = detail.get('cause', '?')
        print(f"  Cause: {cause[:300]}")
