"""Check pipeline execution status."""
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
sfn = session.client('stepfunctions')

arn = "arn:aws:states:us-east-2:116488731375:execution:stock-screener-pipeline:ac7101b9-d96a-40fe-82fd-c57b7782a988"
resp = sfn.describe_execution(executionArn=arn)
print(f"Status: {resp['status']}")
if resp['status'] == 'FAILED':
    print(f"Error: {resp.get('error', '?')}")
    print(f"Cause: {resp.get('cause', '?')[:500]}")
