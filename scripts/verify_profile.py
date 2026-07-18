"""Verify company profile is returned by API."""
import json
import urllib.request

url = 'https://kw8mlahpj2.execute-api.us-east-2.amazonaws.com/prod/stocks/LRN'
resp = urllib.request.urlopen(url)
data = json.loads(resp.read())
stock = data.get('stock', {})
print(f"Symbol: {stock.get('symbol')}")
print(f"Company: {stock.get('company_name')}")
print(f"Industry: {stock.get('industry')}")
print(f"Logo: {stock.get('logo', 'MISSING')}")
print(f"Web: {stock.get('weburl', 'MISSING')}")
desc = stock.get('company_description', 'MISSING')
print(f"Description: {desc[:200]}...")
