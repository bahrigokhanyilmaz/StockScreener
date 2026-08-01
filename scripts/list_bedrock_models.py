"""List available Anthropic models in Bedrock."""
import json
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
bedrock = session.client('bedrock')

resp = bedrock.list_foundation_models(byProvider='Anthropic')
models = resp.get('modelSummaries', [])

print(f"Total Anthropic models: {len(models)}")
print("\nHaiku models:")
for m in models:
    mid = m.get('modelId', '')
    name = m.get('modelName', '')
    status = m.get('modelLifecycle', {}).get('status', '')
    if 'haiku' in mid.lower() or 'haiku' in name.lower():
        print(f"  {mid} | {name} | {status}")

print("\nAll active Claude models:")
for m in models:
    mid = m.get('modelId', '')
    name = m.get('modelName', '')
    status = m.get('modelLifecycle', {}).get('status', '')
    if status == 'ACTIVE':
        print(f"  {mid} | {name}")
