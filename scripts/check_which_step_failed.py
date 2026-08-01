"""Check which step in the pipeline failed."""
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
sfn = session.client('stepfunctions')

arn = "arn:aws:states:us-east-2:116488731375:execution:stock-screener-pipeline:c9b38523-6b51-412b-adac-42b950ea53b1"

resp = sfn.get_execution_history(executionArn=arn, reverseOrder=True, maxResults=20)
for event in resp['events']:
    etype = event['type']
    if 'Failed' in etype or 'Error' in etype or 'Entered' in etype:
        details = event.get('stateEnteredEventDetails', 
                  event.get('lambdaFunctionFailedEventDetails',
                  event.get('executionFailedEventDetails', {})))
        if isinstance(details, dict):
            name = details.get('name', '')
            error = details.get('error', '')
            cause = details.get('cause', '')[:200] if details.get('cause') else ''
            print(f"  {etype}: {name} {error} {cause}")
