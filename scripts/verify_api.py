"""Verify the API returns correct data after cleanup."""
import json
import urllib.request

url = "https://kw8mlahpj2.execute-api.us-east-2.amazonaws.com/prod/stocks"
response = urllib.request.urlopen(url)
data = json.loads(response.read())

stocks = data if isinstance(data, list) else data.get('stocks', data.get('body', []))
if isinstance(stocks, str):
    stocks = json.loads(stocks)

print(f"Total stocks returned by API: {len(stocks)}")
print()
for s in stocks:
    sym = s.get('symbol', '?')
    fpe = s.get('forward_pe', 'MISSING')
    pe = s.get('pe_ratio', 'MISSING')
    peg = s.get('peg_ratio', 'MISSING')
    score = s.get('investability_score', '?')
    print(f"  {sym}: forward_pe={fpe}, pe_ratio={pe}, peg={peg}, score={score}")
