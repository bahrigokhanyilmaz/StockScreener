"""Check what Finnhub returns for VISN and RIGL epsTTM."""
import requests
import boto3

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
ssm = session.client('ssm')
finnhub_key = ssm.get_parameter(Name='/stock-screener/finnhub-api-key', WithDecryption=True)['Parameter']['Value']

for ticker in ['VISN', 'RIGL', 'TRS', 'NUTX']:
    resp = requests.get(
        "https://finnhub.io/api/v1/stock/metric",
        params={"symbol": ticker, "metric": "all", "token": finnhub_key},
        timeout=10
    )
    if resp.status_code == 200:
        metrics = resp.json().get("metric", {})
        eps_ttm = metrics.get("epsTTM")
        eps_annual = metrics.get("epsAnnual")
        pe_annual = metrics.get("peBasicExclExtraTTM")
        pe_norm = metrics.get("peNormalizedAnnual")
        print(f"{ticker}: epsTTM={eps_ttm}, epsAnnual={eps_annual}, peBasicTTM={pe_annual}, peNormalized={pe_norm}")
    else:
        print(f"{ticker}: HTTP {resp.status_code}")
