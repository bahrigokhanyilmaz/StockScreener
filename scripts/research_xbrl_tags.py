"""
SYSTEMATIC XBRL tag research.

For each financial concept we need, look at what tags companies ACTUALLY use
by checking their SEC filings via the companyfacts endpoint.

This is the proper approach: check real filings, don't guess tag names.
"""
import requests
import time
import json

HEADERS = {"User-Agent": "stock-screener-app bahrigokhanyilmaz@gmail.com"}

# Get CIK mapping
resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
t2c = {e["ticker"]: int(e["cik_str"]) for e in resp.json().values()}

def get_company_facts(ticker):
    """Get all XBRL facts for a company — shows every tag they've ever reported."""
    cik = t2c.get(ticker)
    if not cik:
        return None
    cik_padded = str(cik).zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code == 200:
        return resp.json()
    return None

def find_tags_for_concept(facts, keywords):
    """Search a company's facts for tags matching keywords."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    matches = []
    for tag_name in us_gaap:
        tag_lower = tag_name.lower()
        if any(kw.lower() in tag_lower for kw in keywords):
            # Get most recent value
            units = us_gaap[tag_name].get("units", {})
            for unit, values in units.items():
                if values:
                    latest = values[-1]
                    matches.append({
                        "tag": tag_name,
                        "unit": unit,
                        "latest_val": latest.get("val"),
                        "form": latest.get("form"),
                        "end": latest.get("end"),
                    })
    return matches

# Companies that are MISSING key metrics in our pipeline
problem_companies = {
    "debt_missing": ["CTSH", "CDE", "TTD", "EPAM"],  # LongTermDebt returns None
    "revenue_missing": ["LCII", "SEIC"],  # Primary revenue tag missing
    "opmgn_missing": ["LCII", "SEIC", "VC"],  # OpIncomeLoss might use different tag
}

print("=" * 100)
print("RESEARCHING XBRL TAGS FOR DEBT")
print("=" * 100)
for ticker in problem_companies["debt_missing"][:2]:  # Check 2 to save time
    print(f"\n--- {ticker} ---")
    facts = get_company_facts(ticker)
    time.sleep(0.2)
    if not facts:
        print("  Could not fetch company facts")
        continue
    
    matches = find_tags_for_concept(facts, ["debt", "borrowing", "longterm"])
    # Filter to USD values (balance sheet items)
    debt_tags = [m for m in matches if m["unit"] == "USD" and m["latest_val"] and m["latest_val"] > 0]
    debt_tags.sort(key=lambda x: x["latest_val"] or 0, reverse=True)
    
    print(f"  Tags containing 'debt'/'borrowing'/'longterm' (USD, positive values):")
    for m in debt_tags[:10]:
        print(f"    {m['tag']:<50} = ${m['latest_val']/1e6:,.0f}M  ({m['form']}, {m['end']})")

print("\n\n" + "=" * 100)
print("RESEARCHING XBRL TAGS FOR REVENUE")
print("=" * 100)
for ticker in problem_companies["revenue_missing"]:
    print(f"\n--- {ticker} ---")
    facts = get_company_facts(ticker)
    time.sleep(0.2)
    if not facts:
        print("  Could not fetch company facts")
        continue
    
    matches = find_tags_for_concept(facts, ["revenue", "sales", "netrevenue"])
    rev_tags = [m for m in matches if m["unit"] == "USD" and m["latest_val"] and m["latest_val"] > 1e6]
    rev_tags.sort(key=lambda x: x["latest_val"] or 0, reverse=True)
    
    print(f"  Tags containing 'revenue'/'sales'/'netrevenue' (USD, > $1M):")
    for m in rev_tags[:10]:
        print(f"    {m['tag']:<60} = ${m['latest_val']/1e6:,.0f}M  ({m['form']}, {m['end']})")

print("\n\n" + "=" * 100)
print("RESEARCHING XBRL TAGS FOR OPERATING INCOME")
print("=" * 100)
for ticker in problem_companies["opmgn_missing"][:2]:
    print(f"\n--- {ticker} ---")
    facts = get_company_facts(ticker)
    time.sleep(0.2)
    if not facts:
        print("  Could not fetch company facts")
        continue
    
    matches = find_tags_for_concept(facts, ["operatingincome", "incomefromoperations", "operatingprofit"])
    oi_tags = [m for m in matches if m["unit"] == "USD" and m["latest_val"]]
    oi_tags.sort(key=lambda x: abs(x["latest_val"] or 0), reverse=True)
    
    print(f"  Tags containing 'operatingincome'/'incomefromoperations' (USD):")
    for m in oi_tags[:10]:
        print(f"    {m['tag']:<60} = ${m['latest_val']/1e6:,.0f}M  ({m['form']}, {m['end']})")
