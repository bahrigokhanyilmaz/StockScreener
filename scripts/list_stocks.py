#!/usr/bin/env python3
"""
List all currently tracked stocks with their scores and key metrics.

Usage:
    python3 scripts/list_stocks.py
    python3 scripts/list_stocks.py --active    # Only ACTIVE status
    python3 scripts/list_stocks.py --grace     # Only GRACE status
"""

import subprocess
import json
import sys

PROFILE = "stock-screener"
REGION = "us-east-2"
TABLE = "stock-screener-data"


def aws_cmd(args):
    result = subprocess.run(
        ["aws"] + args + ["--profile", PROFILE, "--region", REGION, "--output", "json"],
        capture_output=True, text=True
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


# Filter by status if specified
status_filter = None
if "--active" in sys.argv:
    status_filter = "ACTIVE"
elif "--grace" in sys.argv:
    status_filter = "GRACE"

# Scan for LATEST items
scan_args = [
    "dynamodb", "scan",
    "--table-name", TABLE,
    "--filter-expression", "SK = :sk",
    "--expression-attribute-values", '{":sk": {"S": "LATEST"}}',
]

data = aws_cmd(scan_args)
items = data.get("Items", [])

# Parse DynamoDB format
stocks = []
for item in items:
    stock = {}
    for key, val in item.items():
        v = list(val.values())[0]
        stock[key] = v
    if status_filter and stock.get("tracking_status") != status_filter:
        continue
    stocks.append(stock)

stocks.sort(key=lambda s: float(s.get("investability_score", "0")), reverse=True)

print(f"Tracked stocks: {len(stocks)}" + (f" (filter: {status_filter})" if status_filter else ""))
print()
print(f"{'Symbol':<8} {'Score':>5} {'P/E':>6} {'PEG':>6} {'D/E':>5} {'QR':>5} {'OpM%':>5} {'Status':<8} {'Company'}")
print("-" * 80)

for s in stocks:
    sym = s.get("symbol", "?")
    score = s.get("investability_score", "—")
    pe = f"{float(s['pe_ratio']):.1f}" if s.get("pe_ratio") else "—"
    peg = f"{float(s['peg_ratio']):.2f}" if s.get("peg_ratio") else "—"
    de = f"{float(s['debt_to_equity']):.2f}" if s.get("debt_to_equity") else "—"
    qr = f"{float(s['quick_ratio']):.2f}" if s.get("quick_ratio") else "—"
    om = f"{float(s['operating_margin'])*100:.0f}" if s.get("operating_margin") else "—"
    status = s.get("tracking_status", "?")
    name = s.get("company_name", "")[:22]
    print(f"{sym:<8} {score:>5} {pe:>6} {peg:>6} {de:>5} {qr:>5} {om:>5} {status:<8} {name}")
