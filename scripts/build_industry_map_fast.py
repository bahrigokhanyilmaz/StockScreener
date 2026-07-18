"""
Fast industry map builder — uses SEC submissions endpoint.
SEC has no rate limit but asks for polite access (10 req/sec is fine).
~10K companies at 10 req/sec = ~17 minutes.

Run this once. Re-run monthly (companies rarely change SIC codes).
"""
import json
import os
import time

import boto3
import requests

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
S3_BUCKET = "stock-screener-raw-data-116488731375"
S3_KEY = "reference/ticker_industry_map.json"

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3_client = session.client('s3')


def main():
    print("Loading SEC ticker list...")
    resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
    tickers_data = resp.json()

    # Build list of (cik, ticker) pairs
    companies = [(entry["cik_str"], entry["ticker"]) for entry in tickers_data.values()]
    print(f"  {len(companies)} companies to process")

    industry_map = {}
    errors = 0

    for i, (cik, ticker) in enumerate(companies):
        cik_padded = str(cik).zfill(10)
        url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"

        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                sic = data.get('sic', '')
                sic_desc = data.get('sicDescription', '')
                if sic:
                    industry_map[ticker] = {
                        "sic": str(sic),
                        "industry": sic_desc,
                    }
            elif resp.status_code == 429:
                # Rate limited — back off
                print(f"  Rate limited at {i}, sleeping 5s...")
                time.sleep(5)
                errors += 1
            else:
                errors += 1
        except Exception as e:
            errors += 1

        if (i + 1) % 500 == 0:
            print(f"  [{i+1}/{len(companies)}] mapped {len(industry_map)} tickers ({errors} errors)")

        # Polite: 10 requests/sec
        time.sleep(0.1)

    print(f"\nDone. {len(industry_map)} tickers mapped to "
          f"{len(set(v['industry'] for v in industry_map.values()))} unique industries")

    # Save locally
    local_path = os.path.join(os.path.dirname(__file__), "ticker_industry_map.json")
    with open(local_path, 'w') as f:
        json.dump(industry_map, f, separators=(',', ':'))
    print(f"Saved locally: {local_path} ({os.path.getsize(local_path) / 1024:.0f} KB)")

    # Upload to S3
    body = json.dumps(industry_map, separators=(',', ':'))
    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=S3_KEY,
        Body=body,
        ContentType="application/json",
    )
    print(f"Uploaded to s3://{S3_BUCKET}/{S3_KEY} ({len(body) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
