"""Test the /industries API endpoint."""
import json
import urllib.request

url = "https://kw8mlahpj2.execute-api.us-east-2.amazonaws.com/prod/industries"
resp = urllib.request.urlopen(url)
data = json.loads(resp.read())

print(f"Industries returned: {data.get('count', 0)}")
industries = data.get('industries', {})

# Show data for our stocks' industries
for ind in ['Services-Educational Services', 'Services-Prepackaged Software',
            'Services-Business Services, NEC', 'Carpets & Rugs',
            'Metal Forgings & Stampings',
            'Water, Sewer, Pipeline, Comm & Power Line Construction']:
    entry = industries.get(ind)
    if entry:
        print(f"\n  {ind} (n={entry.get('sample_size', '?')}):")
        for k in ['pe_ratio', 'debt_to_equity', 'quick_ratio', 'operating_margin',
                  'eps_growth_yoy', 'revenue_growth_yoy']:
            v = entry.get(k, '—')
            print(f"    {k}: {v}")
