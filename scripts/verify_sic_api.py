"""Verify sic_industry is returned by stock detail API."""
import json
import urllib.request

resp = urllib.request.urlopen('https://kw8mlahpj2.execute-api.us-east-2.amazonaws.com/prod/stocks/LRN')
data = json.loads(resp.read())
stock = data.get('stock', {})
print(f"symbol: {stock.get('symbol')}")
print(f"industry (Finnhub): {stock.get('industry')}")
print(f"sic_industry (SEC): {stock.get('sic_industry')}")
