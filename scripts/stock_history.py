#!/usr/bin/env python3
"""
Show score history for a specific stock.

Usage:
    python3 scripts/stock_history.py MSFT
"""

import subprocess
import json
import sys

PROFILE = "stock-screener"
REGION = "us-east-2"
TABLE = "stock-screener-data"

if len(sys.argv) < 2:
    print("Usage: python3 scripts/stock_history.py <TICKER>")
    exit(1)

ticker = sys.argv[1].upper()


def aws_cmd(args):
    result = subprocess.run(
        ["aws"] + args + ["--profile", PROFILE, "--region", REGION, "--output", "json"],
        capture_output=True, text=True
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


# Query all items for this stock
data = aws_cmd([
    "dynamodb", "query",
    "--table-name", TABLE,
    "--key-condition-expression", "PK = :pk",
    "--expression-attribute-values", json.dumps({":pk": {"S": f"STOCK#{ticker}"}}),
])

items = data.get("Items", [])
if not items:
    print(f"No data found for {ticker}")
    exit()

# Separate LATEST, TRACKING, and SCORE items
latest = None
tracking = None
scores = []

for item in items:
    parsed = {}
    for key, val in item.items():
        parsed[key] = list(val.values())[0]

    sk = parsed.get("SK", "")
    if sk == "LATEST":
        latest = parsed
    elif sk == "TRACKING":
        tracking = parsed
    elif sk.startswith("SCORE#"):
        scores.append(parsed)

# Display
print(f"=== {ticker} ===")
if latest:
    print(f"  Company: {latest.get('company_name', '?')}")
    print(f"  Sector: {latest.get('sector', '?')}")
    print(f"  Price: ${latest.get('price', '?')}")
    print(f"  Investability: {latest.get('investability_score', '?')}")
    print(f"  Fundamental: {latest.get('fundamental_score', '?')}")
    print(f"  Sentiment: {latest.get('sentiment_score', '?')}")
    print(f"  P/E: {latest.get('pe_ratio', '?')}, PEG: {latest.get('peg_ratio', '?')}")
    print(f"  D/E: {latest.get('debt_to_equity', '?')}, QR: {latest.get('quick_ratio', '?')}")
    print(f"  Last updated: {latest.get('last_updated', '?')}")

if tracking:
    print(f"\n  Tracking: {tracking.get('tracking_status', '?')}")
    print(f"  First tracked: {tracking.get('first_tracked', '?')}")
    print(f"  Last passed: {tracking.get('last_passed', '?')}")

if scores:
    scores.sort(key=lambda s: s.get("date", ""))
    print(f"\n  Score history ({len(scores)} days):")
    for s in scores:
        print(f"    {s.get('date', '?')}: investability={s.get('investability_score', '?')}, "
              f"fundamental={s.get('fundamental_score', '?')}, sentiment={s.get('sentiment_score', '?')}")
