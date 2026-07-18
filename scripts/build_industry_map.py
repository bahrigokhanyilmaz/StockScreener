"""
Build Ticker → Industry Map from SEC Bulk Submissions
======================================================
One-time script (re-run weekly if needed) that:
1. Downloads SEC bulk submissions ZIP (~1.5 GB)
2. Extracts SIC code + SIC description for every company
3. Outputs a lightweight JSON: { "AAPL": {"sic": "3571", "industry": "Electronic Computers"}, ... }
4. Uploads to S3 as reference/ticker_industry_map.json

A company's industry classification almost NEVER changes.
This file is ~200 KB and eliminates the need for per-stock API calls.

SEC source: https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip
Alternative (faster): Parse individual submission files from data.sec.gov/submissions/
"""
import json
import os
import time
import zipfile
import tempfile
from io import BytesIO

import boto3
import requests

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}
S3_BUCKET = "stock-screener-raw-data-116488731375"
S3_KEY = "reference/ticker_industry_map.json"

session = boto3.Session(profile_name='stock-screener', region_name='us-east-2')
s3_client = session.client('s3')


def build_from_submissions_api():
    """
    Build the industry map using SEC's per-company submissions endpoint.
    
    Faster than downloading the 1.5GB ZIP: we already have CIK list from
    the company_tickers.json file (~10K entries). We fetch the first page
    of each company's submissions which includes SIC in the header.
    
    BUT: 10K individual requests is too many. Instead, use the bulk ZIP.
    """
    pass


def build_from_bulk_zip():
    """
    Download the SEC bulk submissions ZIP and extract SIC codes.
    
    The ZIP contains one JSON per company (by CIK). Each JSON has:
    - cik, entityType, sic, sicDescription, name, tickers[], exchanges[]
    
    This is ~1.5 GB compressed. We stream and extract only what we need.
    """
    print("Downloading SEC bulk submissions ZIP (this may take a few minutes)...")
    url = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
    
    resp = requests.get(url, headers=HEADERS, stream=True, timeout=300)
    if resp.status_code != 200:
        print(f"Failed to download: {resp.status_code}")
        return None
    
    # Write to temp file (too large for memory)
    total_size = int(resp.headers.get('content-length', 0))
    print(f"  Download size: {total_size / 1e9:.1f} GB")
    
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
        downloaded = 0
        for chunk in resp.iter_content(chunk_size=8192 * 1024):  # 8MB chunks
            tmp.write(chunk)
            downloaded += len(chunk)
            if downloaded % (100 * 1024 * 1024) == 0:  # Every 100MB
                print(f"  Downloaded {downloaded / 1e6:.0f} MB / {total_size / 1e6:.0f} MB")
        tmp_path = tmp.name
    
    print(f"  Download complete. Extracting SIC codes...")
    
    industry_map = {}
    processed = 0
    
    with zipfile.ZipFile(tmp_path, 'r') as zf:
        for name in zf.namelist():
            if not name.startswith('CIK') or not name.endswith('.json'):
                continue
            try:
                with zf.open(name) as f:
                    data = json.loads(f.read())
                    sic = data.get('sic', '')
                    sic_desc = data.get('sicDescription', '')
                    tickers = data.get('tickers', [])
                    
                    if sic and tickers:
                        for ticker in tickers:
                            industry_map[ticker] = {
                                "sic": str(sic),
                                "industry": sic_desc,
                            }
                processed += 1
                if processed % 2000 == 0:
                    print(f"  Processed {processed} companies, {len(industry_map)} tickers mapped")
            except Exception:
                continue
    
    # Clean up temp file
    os.unlink(tmp_path)
    
    print(f"  Done. {len(industry_map)} tickers mapped to {len(set(v['industry'] for v in industry_map.values()))} industries")
    return industry_map


def build_from_individual_submissions():
    """
    Alternative: Use SEC company_tickers.json to get all CIKs,
    then fetch submissions for each to get SIC.
    
    This is 10K+ API calls but SEC has no rate limit (just be polite with 0.1s delay).
    Faster than downloading 1.5GB ZIP for initial build.
    """
    print("Loading SEC ticker list...")
    resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
    tickers_data = resp.json()
    
    # Build CIK → ticker mapping
    cik_to_tickers = {}
    for entry in tickers_data.values():
        cik = entry["cik_str"]
        ticker = entry["ticker"]
        cik_to_tickers[cik] = ticker
    
    print(f"  {len(cik_to_tickers)} companies to process")
    
    industry_map = {}
    errors = 0
    
    for i, (cik, ticker) in enumerate(cik_to_tickers.items()):
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
            else:
                errors += 1
        except Exception:
            errors += 1
        
        if (i + 1) % 500 == 0:
            print(f"  [{i+1}/{len(cik_to_tickers)}] mapped {len(industry_map)} tickers ({errors} errors)")
        
        # Polite: 10 requests/sec
        time.sleep(0.1)
    
    print(f"  Done. {len(industry_map)} tickers, {errors} errors")
    return industry_map


def upload_to_s3(industry_map: dict):
    """Upload the map to S3."""
    body = json.dumps(industry_map, separators=(',', ':'))
    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=S3_KEY,
        Body=body,
        ContentType="application/json",
    )
    print(f"  Uploaded to s3://{S3_BUCKET}/{S3_KEY} ({len(body) / 1024:.0f} KB)")


if __name__ == "__main__":
    print("Building ticker → industry map from SEC submissions API...")
    print("(Using individual submissions endpoint — ~17 minutes at 10 req/sec)")
    print()
    
    industry_map = build_from_individual_submissions()
    
    if industry_map:
        # Save locally
        local_path = os.path.join(os.path.dirname(__file__), "ticker_industry_map.json")
        with open(local_path, 'w') as f:
            json.dump(industry_map, f, separators=(',', ':'))
        print(f"\n  Saved locally: {local_path} ({os.path.getsize(local_path) / 1024:.0f} KB)")
        
        # Upload to S3
        upload_to_s3(industry_map)
        print("\nDone!")
    else:
        print("Failed to build industry map")
