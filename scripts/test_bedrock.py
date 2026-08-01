"""Test Bedrock Claude invocation."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
client = session.client('bedrock-runtime')

try:
    resp = client.invoke_model(
        modelId='us.anthropic.claude-haiku-4-5-20251001-v1:0',
        contentType='application/json',
        accept='application/json',
        body=json.dumps({
            'anthropic_version': 'bedrock-2023-05-31',
            'max_tokens': 50,
            'messages': [{'role': 'user', 'content': 'Say hello in one word'}]
        })
    )
    result = json.loads(resp['body'].read())
    print(f"SUCCESS: {result['content'][0]['text']}")
except Exception as e:
    print(f"ERROR: {e}")
