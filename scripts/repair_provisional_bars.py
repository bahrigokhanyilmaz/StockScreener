"""
Repair stored daily bars that were written before the session was final.

Background: before the DST-aware 5 PM ET schedule, Step 7 could store FMP's
partial same-day bar and freeze it (HLIO, 2026-11-09). This script compares the
newest stored bar for a date against FMP's now-final bar and, with --apply,
replaces it.

Usage:
    .venv/bin/python3 scripts/repair_provisional_bars.py [--date YYYY-MM-DD]          # dry run (default)
    .venv/bin/python3 scripts/repair_provisional_bars.py [--date YYYY-MM-DD] --apply  # write

Safety:
  - Dry run performs reads only (one FMP call per candidate) and prints a diff.
  - --apply is refused (exit 2) before 17:00 ET on the target date.
  - Every write is conditional on the value we read, so a concurrent pipeline
    write is never clobbered. A failed condition is reported as UNVERIFIED (exit 1).
  - The FMP API key is read from SSM and never printed: all error text passes
    through redact().
  - Manual marks are never edited; a mark equal to the stored provisional close
    is reported as a warning for manual review.

Exit codes: 0 ok, 1 something unverified, 2 usage error / refused.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lambdas", "shared"))
from market_time import (  # noqa: E402
    SKIP_SETTLED_AFTER, latest_final_session_date, new_york_dt,
)

TOLERANCE = 0.005
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def redact(msg, key):
    """Remove the API key (and any apikey= query value) from text before printing."""
    text = str(msg)
    if key:
        text = text.replace(key, "***")
    return re.sub(r"(apikey=)[^&\s'\"]+", r"\1***", text, flags=re.IGNORECASE)


def _f(v):
    try:
        return None if v is None or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return None


def find_candidates(price_history_items, target_date):
    """Items whose newest stored bar is dated target_date. Pure."""
    out = []
    for it in price_history_items:
        bars = it.get("bars") or []
        if bars and isinstance(bars[-1], dict) and bars[-1].get("d") == target_date:
            out.append(it)
    return out


def plan_repairs(stored_bars, final_bars, tol=TOLERANCE):
    """
    stored_bars / final_bars: {symbol: bar}. OHLC compared within tol, volume exact.
    Returns change rows {symbol, date, stored, final, fields_changed}. Pure.
    """
    rows = []
    for sym, sb in stored_bars.items():
        fb = final_bars.get(sym)
        if not fb:
            continue
        changed = []
        for k in ("o", "h", "l", "c"):
            a, b = _f(sb.get(k)), _f(fb.get(k))
            if a is None or b is None:
                if a != b:
                    changed.append(k)
            elif abs(a - b) > tol:
                changed.append(k)
        if _f(sb.get("v")) != _f(fb.get("v")):
            changed.append("v")
        if changed:
            rows.append({"symbol": sym, "date": fb.get("d"), "stored": sb, "final": fb,
                         "fields_changed": changed})
    return rows


def _normalize(row):
    ok = (isinstance(row, dict) and _DATE_RE.match(str(row.get("date", "")))
          and all(isinstance(row.get(k), (int, float)) and not isinstance(row.get(k), bool)
                  for k in ("open", "high", "low", "close")) and row["close"] > 0)
    if not ok:
        return None
    vol = row.get("volume")
    return {"d": row["date"], "o": round(row["open"], 2), "h": round(row["high"], 2),
            "l": round(row["low"], 2), "c": round(row["close"], 2),
            "v": vol if isinstance(vol, (int, float)) and not isinstance(vol, bool) else 0}


def fetch_final_bar(symbol, date, key):
    """One FMP call for a single session. Returns the bar or raises (message may contain the URL)."""
    url = (f"https://financialmodelingprep.com/stable/historical-price-eod/full"
           f"?symbol={symbol}&from={date}&to={date}&apikey={key}")
    req = urllib.request.Request(url, headers={"User-Agent": "StockScreener/2.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    for row in data if isinstance(data, list) else []:
        bar = _normalize(row)
        if bar and bar["d"] == date:
            return bar
    return None


def _dec(obj):
    if isinstance(obj, float):
        return Decimal(str(round(obj, 6)))
    if isinstance(obj, dict):
        return {k: _dec(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dec(v) for v in obj]
    return obj


def _cond_failed(exc):
    return getattr(exc, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _apply_one(table, row, target, now_iso):
    """Conditional writes for one symbol. Returns (messages, unverified_count)."""
    sym, final = row["symbol"], row["final"]
    msgs, unverified = [], 0

    def run(label, fn):
        nonlocal unverified
        try:
            fn()
            msgs.append(f"{sym}: {label} updated")
        except Exception as e:
            if _cond_failed(e):
                unverified += 1
                msgs.append(f"{sym}: {label} UNVERIFIED (changed since read, not written)")
            else:
                unverified += 1
                msgs.append(f"{sym}: {label} UNVERIFIED ({type(e).__name__})")

    # (a) bar replacement
    ph_key = {"PK": f"PRICE_HISTORY#{sym}", "SK": "DAILY"}
    ph = table.get_item(Key=ph_key).get("Item") or {}
    bars = ph.get("bars") or []
    idx = next((i for i, b in enumerate(bars) if b.get("d") == target), None)
    if idx is None:
        msgs.append(f"{sym}: bar {target} not found in series, skipped")
        unverified += 1
    else:
        names = {"#b": "bars", "#d": "d", "#c": "c"}
        vals = {":bar": _dec(final), ":d": target, ":old_c": bars[idx].get("c")}
        update = f"SET #b[{idx}] = :bar"
        if idx == len(bars) - 1:
            update += ", last_final_date = :d, fetched_at = :now, to_date = :d"
            vals[":now"] = now_iso
        run("price-history bar", lambda: table.update_item(
            Key=ph_key, UpdateExpression=update,
            ConditionExpression=f"#b[{idx}].#d = :d AND #b[{idx}].#c = :old_c",
            ExpressionAttributeNames=names, ExpressionAttributeValues=vals))

    # (b) LATEST (never rolled backwards)
    latest_key = {"PK": f"STOCK#{sym}", "SK": "LATEST"}
    latest = table.get_item(Key=latest_key).get("Item")
    if latest and latest.get("price") is not None:
        sets = ["#p = :p", "price_as_of = :d"]
        vals = {":p": _dec(final["c"]), ":d": target, ":old": latest["price"]}
        shares = _f(latest.get("shares_implied"))
        if shares and shares > 0:
            sets.append("market_cap = :mc")
            vals[":mc"] = _dec(round(shares * final["c"]))
        run("LATEST", lambda: table.update_item(
            Key=latest_key, UpdateExpression="SET " + ", ".join(sets),
            ConditionExpression="#p = :old AND (attribute_not_exists(price_as_of) OR price_as_of = :d)",
            ExpressionAttributeNames={"#p": "price"}, ExpressionAttributeValues=vals))
    else:
        msgs.append(f"{sym}: no LATEST price, skipped")

    # (c) SCORE# snapshot
    score_key = {"PK": f"STOCK#{sym}", "SK": f"SCORE#{target}"}
    score = table.get_item(Key=score_key).get("Item")
    if score and score.get("price") is not None:
        run("SCORE# row", lambda: table.update_item(
            Key=score_key, UpdateExpression="SET #p = :p, price_as_of = :d",
            ConditionExpression="attribute_exists(PK) AND #p = :old",
            ExpressionAttributeNames={"#p": "price"},
            ExpressionAttributeValues={":p": _dec(final["c"]), ":d": target, ":old": score["price"]}))
    else:
        msgs.append(f"{sym}: no SCORE#{target} row, skipped")
    return msgs, unverified


def _scan_price_history(table):
    from boto3.dynamodb.conditions import Attr
    kw = {"FilterExpression": Attr("PK").begins_with("PRICE_HISTORY#")}
    r = table.scan(**kw)
    items = r.get("Items", [])
    while r.get("LastEvaluatedKey"):
        r = table.scan(ExclusiveStartKey=r["LastEvaluatedKey"], **kw)
        items += r.get("Items", [])
    return items


def main(argv=None, *, now_utc=None, table=None, key=None, fetch=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--date")
    ap.add_argument("--apply", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2
    now = now_utc or datetime.now(timezone.utc)

    if args.date is not None:
        try:
            if not _DATE_RE.match(args.date):
                raise ValueError
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print("error: --date must be YYYY-MM-DD")
            return 2
    target = args.date or latest_final_session_date(now)

    if args.apply:
        settled = datetime.strptime(target, "%Y-%m-%d").replace(
            hour=SKIP_SETTLED_AFTER.hour, minute=SKIP_SETTLED_AFTER.minute)
        if new_york_dt(now) < settled:
            print(f"error: refusing --apply before 17:00 ET on {target} (FMP bar may still be settling)")
            return 2

    if table is None or key is None:
        import boto3
        sess = boto3.Session(profile_name="stock-screener", region_name="us-east-2")
        table = table or sess.resource("dynamodb").Table("stock-screener-data")
        key = key or sess.client("ssm").get_parameter(
            Name="/stock-screener/fmp-api-key", WithDecryption=True)["Parameter"]["Value"]
    fetch = fetch or fetch_final_bar

    print(f"{'APPLY' if args.apply else 'DRY RUN'}: target session {target}")
    candidates = find_candidates(_scan_price_history(table), target)
    print(f"{len(candidates)} stock(s) with a stored bar dated {target}")

    stored, final, unverified = {}, {}, 0
    for it in candidates:
        sym = it["PK"].split("#", 1)[1]
        stored[sym] = it["bars"][-1]
        try:
            bar = fetch(sym, target, key)
        except urllib.error.HTTPError as e:
            print(f"  {sym}: HTTP {e.code}")
            unverified += 1
            continue
        except Exception as e:
            print(f"  {sym}: {type(e).__name__}: {redact(e, key)}")
            unverified += 1
            continue
        if bar is None:
            print(f"  {sym}: no final bar from FMP, unverified")
            unverified += 1
            continue
        final[sym] = bar

    rows = plan_repairs(stored, final)
    if rows:
        print(f"\n{'ticker':8} {'date':11} {'stored c/h/v':>26} {'final c/h/v':>26}  changed")
        for r in rows:
            s, f = r["stored"], r["final"]
            print(f"{r['symbol']:8} {r['date']:11} "
                  f"{_f(s.get('c'))}/{_f(s.get('h'))}/{_f(s.get('v')):>12} "
                  f"{f['c']}/{f['h']}/{f['v']:>12}  {','.join(r['fields_changed'])}")
    else:
        print("No differences found.")

    for r in rows:
        try:
            t = table.get_item(Key={"PK": f"STOCK#{r['symbol']}", "SK": "TRACKING"}).get("Item") or {}
        except Exception:
            continue
        if t.get("mark_date") == target and _f(t.get("mark_price")) == _f(r["stored"].get("c")):
            print(f"  WARNING {r['symbol']}: mark_price equals the stored provisional close; "
                  f"review the mark manually (never edited here)")

    if args.apply and rows:
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        for r in rows:
            msgs, bad = _apply_one(table, r, target, now_iso)
            unverified += bad
            for m in msgs:
                print("  " + m)
    elif rows:
        print("\nDry run: nothing written. Re-run with --apply after 17:00 ET to write.")

    if unverified:
        print(f"\n{unverified} item(s) unverified")
    return 1 if unverified else 0


if __name__ == "__main__":
    sys.exit(main())
