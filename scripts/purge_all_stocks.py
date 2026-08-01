"""
Clean slate: remove ALL stock items from DynamoDB.
Tomorrow's pipeline run will repopulate with only stocks that pass
the current corrected filters.
"""
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')

# Scan for all STOCK# and PRICE_HISTORY# items
resp = table.scan(ProjectionExpression='PK, SK')
items = resp.get('Items', [])

# Handle pagination
while resp.get('LastEvaluatedKey'):
    resp = table.scan(ProjectionExpression='PK, SK', ExclusiveStartKey=resp['LastEvaluatedKey'])
    items.extend(resp.get('Items', []))

# Filter to stock-related items (keep INDUSTRY_AVG# items)
to_delete = [i for i in items if i['PK'].startswith('STOCK#') or i['PK'].startswith('PRICE_HISTORY#')]

print(f"Total items in table: {len(items)}")
print(f"Stock-related items to delete: {len(to_delete)}")
print(f"Keeping: {len(items) - len(to_delete)} (industry averages, etc.)")

with table.batch_writer() as batch:
    for item in to_delete:
        batch.delete_item(Key={'PK': item['PK'], 'SK': item['SK']})

print(f"\nDeleted {len(to_delete)} items. Clean slate for next pipeline run.")
