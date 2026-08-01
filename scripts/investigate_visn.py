"""
Investigate VISN — P/E of 0.4 is extremely low and likely a data issue.
Check the raw EDGAR data to see what's producing this number.
"""
import json
import requests
import boto3

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3 = session.client('s3')
ssm = session.client('ssm')
dynamodb = session.resource('dynamodb')
table = dynamodb.Table('stock-screener-data')
bucket = 'stock-screener-raw-data-116488731375'

# Get VISN's full data from DynamoDB
item = table.get_item(Key={'PK': 'STOCK#VISN', 'SK': 'LATEST'}).get('Item', {})
print("=== VISN from DynamoDB ===")
print(f"  Company: {item.get('company_name')}")
print(f"  Industry: {item.get('industry')} / SIC: {item.get('sic_industry')}")
print(f"  Price: ${float(item.get('price', 0)):.2f}")
print(f"  P/E: {float(item.get('pe_ratio', 0)):.2f}")
print(f"  EPS: — (not stored directly, derived from price/PE)")
print(f"  Implied EPS from P/E: ${float(item.get('price', 0)) / float(item.get('pe_ratio', 1)):.2f}")
print(f"  PEG: {item.get('peg_ratio')}")
print(f"  Price/FCF: {item.get('price_to_fcf')}")
print(f"  D/E: {float(item.get('debt_to_equity', 0)):.4f}")
print(f"  Quick Ratio: {item.get('quick_ratio')}")
print(f"  Op Margin: {item.get('operating_margin')}")
print(f"  EPS Growth: {item.get('eps_growth_yoy')}")
print(f"  Revenue Growth: {item.get('revenue_growth_yoy')}")
print(f"  Market Cap: {item.get('market_cap')}")
print(f"  Investability: {item.get('investability_score')}")
print(f"  Fundamental: {item.get('fundamental_score')}")

# Check Step 1 raw data
print("\n=== VISN from Step 1 (raw EDGAR) ===")
resp = s3.list_objects_v2(Bucket=bucket, Prefix='pipeline/2026-07-22/step1_')
if resp.get('Contents'):
    data = json.loads(s3.get_object(Bucket=bucket, Key=resp['Contents'][-1]['Key'])['Body'].read())
    for stock in data['stocks']:
        if stock['symbol'] == 'VISN':
            print(f"  eps: {stock.get('eps')}")
            print(f"  net_income (TTM): inferred from eps × shares")
            print(f"  eps_growth_yoy: {stock.get('eps_growth_yoy')}")
            print(f"  revenue_growth_yoy: {stock.get('revenue_growth_yoy')}")
            print(f"  operating_margin: {stock.get('operating_margin')}")
            print(f"  debt_to_equity: {stock.get('debt_to_equity')}")
            print(f"  quick_ratio: {stock.get('quick_ratio')}")
            print(f"  fcf_per_share: {stock.get('fcf_per_share')}")
            print(f"  revenue_per_share: {stock.get('revenue_per_share')}")
            break

# Now check the actual SEC filing to understand the numbers
print("\n=== VISN — SEC Company Facts ===")
resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
t2c = {e["ticker"]: int(e["cik_str"]) for e in resp.json().values()}
cik = t2c.get('VISN')
if cik:
    cik_padded = str(cik).zfill(10)
    print(f"  CIK: {cik}")
    resp = requests.get(f"https://data.sec.gov/submissions/CIK{cik_padded}.json", headers=HEADERS, timeout=30)
    if resp.status_code == 200:
        sub = resp.json()
        print(f"  Name: {sub.get('name')}")
        print(f"  SIC: {sub.get('sic')} - {sub.get('sicDescription')}")
        print(f"  Fiscal Year End: {sub.get('fiscalYearEnd')}")
        # Get recent filings
        recent = sub.get('filings', {}).get('recent', {})
        forms = recent.get('form', [])[:5]
        dates = recent.get('filingDate', [])[:5]
        print(f"  Recent filings: {list(zip(forms, dates))}")
else:
    print("  CIK NOT FOUND")

# Check what price Polygon has for VISN
polygon_key = ssm.get_parameter(Name='/stock-screener/polygon-api-key', WithDecryption=True)['Parameter']['Value']
resp = requests.get(
    f"https://api.polygon.io/v2/aggs/ticker/VISN/prev",
    params={"apiKey": polygon_key},
    timeout=10
)
if resp.status_code == 200:
    results = resp.json().get('results', [])
    if results:
        print(f"\n=== Polygon last price for VISN ===")
        print(f"  Close: ${results[0].get('c')}")
        print(f"  Volume: {results[0].get('v'):,}")
