"""
One-time script to remove stale seeded test data from DynamoDB.
Deletes all items with last_updated before 2026-07-18T00:00:00.
"""
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')

# Scan for all items with last_updated before 2026-07-18
response = table.scan(
    FilterExpression='last_updated < :cutoff',
    ExpressionAttributeValues={':cutoff': '2026-07-18T00:00:00'},
    ProjectionExpression='PK, SK'
)

items = response['Items']
print(f'Found {len(items)} stale items to delete')

# Delete them all
with table.batch_writer() as batch:
    for item in items:
        batch.delete_item(Key={'PK': item['PK'], 'SK': item['SK']})
        print(f'  Deleted {item["PK"]}|{item["SK"]}')

print(f'\nDone. Deleted {len(items)} stale items.')
