"""Check the latest pipeline execution."""
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
sfn = session.client('stepfunctions')

arn = "arn:aws:states:us-east-2:116488731375:execution:stock-screener-pipeline:06acb514-317f-46ce-b3af-6b5acb85d7d9"
resp = sfn.describe_execution(executionArn=arn)
print(f"Status: {resp['status']}")
if resp['status'] == 'FAILED':
    print(f"Error: {resp.get('error', '?')}")
    print(f"Cause: {resp.get('cause', '?')[:500]}")
