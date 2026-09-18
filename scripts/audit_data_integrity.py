"""
Data-integrity audit for the live DynamoDB table.

Checks every tracked stock against the invariants the pipeline documents:
hard filters (with overrides), sanity ranges, PEG<->forward-PE consistency,
market-cap floor, data recency, first_tracked presence — plus orphan items and
industry P/E coverage.

Usage:
    .venv/bin/python3 scripts/audit_data_integrity.py            # human-readable report
    .venv/bin/python3 scripts/audit_data_integrity.py --json     # machine-readable

Exit code: 0 if no HIGH/MED issues, 1 otherwise (so it can gate CI / hooks).

The core logic lives in audit_tracked_stocks() so tests can call it directly
against a mocked table.
"""
import sys
import json
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr


def _f(v):
    return float(v) if v is not None else None


def audit_tracked_stocks(latest_items, today=None, price_bars_by_symbol=None):
    """
    Pure function: given the list of LATEST items, return a list of issues.
    Each issue is a dict: {severity, symbol, message}. No AWS calls here so it's
    unit-testable.

    Severities:
      HIGH/MED/LOW — invariant violations (gate the audit, exit 1).
      ODD          — advisory plausibility warnings (internally-consistent but
                     suspicious values, e.g. divergent P/E, price outside its
                     30-day range). These NEVER gate; they just surface oddities
                     that may indicate bad source data.

    price_bars_by_symbol: optional {symbol: [bar,...]} for range checks.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()
    price_bars_by_symbol = price_bars_by_symbol or {}
    issues = []

    def flag(sym, sev, msg):
        issues.append({"severity": sev, "symbol": sym, "message": msg})

    for s in latest_items:
        sym = s.get("symbol")
        peg = _f(s.get("peg_ratio")); pe = _f(s.get("pe_ratio")); fpe = _f(s.get("forward_pe"))
        de = _f(s.get("debt_to_equity")); icr = _f(s.get("interest_coverage_ratio")); qr = _f(s.get("quick_ratio"))
        om = _f(s.get("operating_margin")); revg = _f(s.get("revenue_growth_yoy")); fwd_revg = _f(s.get("est_lt_revenue_growth"))
        lt = _f(s.get("est_lt_growth")); price = _f(s.get("price")); mc = _f(s.get("market_cap")); pfcf = _f(s.get("price_to_fcf"))
        inv = _f(s.get("investability_score")); fund = _f(s.get("fundamental_score"))
        sent = _f(s.get("sentiment_score")); comp = _f(s.get("competition_score"))
        ft = s.get("first_tracked"); lu = s.get("last_updated")

        # --- HARD FILTERS ---
        if peg is None:
            flag(sym, "HIGH", "PEG blank but tracked (should fail: missing=FAIL)")
        elif peg > 1.0:
            flag(sym, "HIGH", f"PEG={peg} > 1.0 but tracked")
        if fwd_revg is None:
            flag(sym, "HIGH", "forward revenue growth blank but tracked")
        elif fwd_revg <= 0:
            flag(sym, "HIGH", f"forward revenue growth={fwd_revg} <= 0 but tracked")
        if de is not None and de > 1.0 and not (icr is not None and icr > 3.0):
            flag(sym, "HIGH", f"D/E={de} > 1.0 and ICR override not met (ICR={icr})")
        if qr is None:
            flag(sym, "MED", "quick_ratio blank")
        elif qr < 1.0:
            flag(sym, "HIGH", f"quick_ratio={qr} < 1.0 but tracked")
        if om is not None and om <= 0 and not (revg is not None and revg > 0.20):
            flag(sym, "HIGH", f"operating_margin={om} <= 0 and rev-growth override not met (revg={revg})")
        if pfcf is None:
            flag(sym, "MED", "price_to_fcf blank")
        elif pfcf > 20 or pfcf < 0:
            flag(sym, "HIGH", f"price_to_fcf={pfcf} fails <20 but tracked")

        # --- SANITY RANGES ---
        if price is None or price <= 0:
            flag(sym, "HIGH", f"price invalid: {price}")
        if mc is not None and mc < 150_000_000:
            flag(sym, "MED", f"market_cap {mc:.0f} below 150M floor")
        if pe is not None and (pe < 0 or pe > 500):
            flag(sym, "MED", f"pe_ratio out of range: {pe}")
        if inv is None or not (0 <= inv <= 100):
            flag(sym, "MED", f"investability_score out of range: {inv}")
        if fund is None or not (0 <= fund <= 100):
            flag(sym, "MED", f"fundamental_score out of range: {fund}")
        if sent is not None and not (-1 <= sent <= 1):
            flag(sym, "LOW", f"sentiment_score (raw) out of [-1,1]: {sent}")
        if comp is not None and not (1 <= comp <= 5):
            flag(sym, "MED", f"competition_score out of [1,5]: {comp}")

        # --- CONSISTENCY ---
        if peg is not None and fpe is not None and lt is not None and lt > 0:
            exp = round(fpe / (lt * 100), 2)
            if abs(exp - peg) > 0.02:
                flag(sym, "MED", f"PEG={peg} inconsistent with forward_pe/(lt*100)={exp}")

        # --- RECENCY / METADATA ---
        if lu:
            try:
                lud = datetime.fromisoformat(str(lu).replace("Z", "+00:00")).date()
                if (today - lud).days > 4:
                    flag(sym, "MED", f"last_updated stale: {lud}")
            except (ValueError, TypeError):
                flag(sym, "LOW", f"unparseable last_updated: {lu}")
        if not ft:
            flag(sym, "MED", "first_tracked missing")

        # --- ODD: advisory plausibility (never gates) ---
        # Trailing vs forward P/E wildly divergent — usually a data error on one.
        if pe is not None and fpe is not None and pe > 0 and fpe > 0:
            ratio = max(pe, fpe) / min(pe, fpe)
            if ratio >= 3.0:
                flag(sym, "ODD", f"trailing P/E ({pe}) and forward P/E ({fpe}) differ {ratio:.1f}x")
        # Market cap should be roughly price * shares; if price is tiny but mc huge
        # (or vice versa) it hints at a stale/mismatched field. Loose bounds only.
        if price is not None and mc is not None and price > 0:
            if price < 1 and mc > 10_000_000_000:
                flag(sym, "ODD", f"sub-$1 price ({price}) with >$10B market cap ({mc:.0f})")
            if price > 1000 and mc < 300_000_000:
                flag(sym, "ODD", f">$1000 price ({price}) with <$300M market cap ({mc:.0f})")
        # Current price outside its own 30-day OHLCV range — price vs history mismatch.
        bars = price_bars_by_symbol.get(sym) or []
        closes = [_f(b.get("c")) for b in bars if b.get("c") is not None]
        closes = [c for c in closes if c is not None]
        if price is not None and price > 0 and len(closes) >= 5:
            lo, hi = min(closes), max(closes)
            # Allow 15% slack beyond the observed range (a fresh close can exceed it).
            if price < lo * 0.85 or price > hi * 1.15:
                flag(sym, "ODD", f"price {price} outside 30d range [{lo:.2f}, {hi:.2f}] (+/-15% slack)")
        # PEG present but its inputs (forward P/E or growth) missing — shouldn't happen.
        if peg is not None and (fpe is None or lt is None):
            flag(sym, "ODD", f"PEG={peg} present but forward_pe={fpe} / est_lt_growth={lt} missing")

    return issues


def _scan(table, **kw):
    r = table.scan(**kw); items = r.get("Items", [])
    while r.get("LastEvaluatedKey"):
        r = table.scan(ExclusiveStartKey=r["LastEvaluatedKey"], **kw); items += r.get("Items", [])
    return items


def find_orphans(table):
    """Items (SCORE#/ARTICLES/PRICE_HISTORY) for stocks that have no LATEST."""
    latest = _scan(table, FilterExpression=Attr("SK").eq("LATEST"), ProjectionExpression="symbol")
    live = {s["symbol"] for s in latest}
    allitems = _scan(table, ProjectionExpression="PK, SK")
    orphans = {}
    for it in allitems:
        pk = it["PK"]
        if pk.startswith("STOCK#") and it["SK"] != "TRACKING":
            sym = pk.split("#", 1)[1]
            if sym not in live:
                orphans[sym] = orphans.get(sym, 0) + 1
        elif pk.startswith("PRICE_HISTORY#"):
            sym = pk.split("#", 1)[1]
            if sym not in live:
                orphans[sym] = orphans.get(sym, 0) + 1
    return orphans


def main():
    as_json = "--json" in sys.argv
    table = boto3.Session(profile_name="stock-screener", region_name="us-east-2") \
        .resource("dynamodb").Table("stock-screener-data")
    latest = _scan(table, FilterExpression=Attr("SK").eq("LATEST"))
    # Pull price-history bars for the range plausibility check.
    price_bars = {}
    for s in latest:
        sym = s.get("symbol")
        ph = table.get_item(Key={"PK": f"PRICE_HISTORY#{sym}", "SK": "DAILY"}).get("Item")
        if ph and ph.get("bars"):
            price_bars[sym] = ph["bars"]
    issues = audit_tracked_stocks(latest, price_bars_by_symbol=price_bars)
    orphans = find_orphans(table)

    if as_json:
        print(json.dumps({"audited": len(latest), "issues": issues, "orphans": orphans}, default=str))
    else:
        print(f"Audited {len(latest)} tracked stocks.\n")
        for sev in ["HIGH", "MED", "LOW", "ODD"]:
            rows = [i for i in issues if i["severity"] == sev]
            label = f"{sev} (advisory)" if sev == "ODD" else sev
            print(f"=== {label} ({len(rows)}) ===")
            for i in sorted(rows, key=lambda x: x["symbol"]):
                print(f"  {i['symbol']}: {i['message']}")
            print()
        print("=== ORPHANS ===")
        print("  none" if not orphans else "\n".join(f"  {k}: {v} leftover items" for k, v in sorted(orphans.items())))

    # ODD is advisory and never gates — only real violations + orphans set exit 1.
    gating = [i for i in issues if i["severity"] in ("HIGH", "MED")]
    return 1 if (gating or orphans) else 0


if __name__ == "__main__":
    sys.exit(main())
