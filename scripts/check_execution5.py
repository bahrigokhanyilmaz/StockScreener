"""Check pipeline execution."""
import boto3
session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
sfn = session.client('stepfunctions')
arn = "arn:aws:states:us-east-2:116488731375:execution:stock-screener-pipeline:1375474b-a55d-4c4e-8e23-73b80aa9fb9e"
resp = sfn.describe_execution(executionArn=arn)
print(f"Status: {resp['status']}")
if resp['status'] == 'FAILED':
    print(f"Error: {resp.get('error', '?')}")
    print(f"Cause: {resp.get('cause', '?')[:500]}")
