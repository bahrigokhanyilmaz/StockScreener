"""Check what's deployed on the fundamentals-fetcher Lambda."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
client = session.client('lambda')

resp = client.get_function(FunctionName='stock-screener-fundamentals-fetcher')
print(f"Last modified: {resp['Configuration']['LastModified']}")
print(f"Code size: {resp['Configuration']['CodeSize']}")
