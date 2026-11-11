#!/usr/bin/env python3
"""
Event-study backtest of the proposed "oversold assessment" rules (research only).

Local files only: no deploy, no AWS writes. FMP responses and EDGAR companyfacts
are cached under .cache/backtest/, so `--offline` re-runs with zero network calls.

Rules under test are frozen in RULES below (written before outcomes were seen).
Block B (estimate-driven share) cannot be backtested: FMP has no historical
analyst estimates.

Reproduce (from cache, zero network):
    .venv/bin/python3 scripts/backtest_oversold.py --offline
Fetch (uses FMP budget, AWS_PROFILE for the SSM key):
    AWS_PROFILE=stock-screener AWS_DEFAULT_REGION=us-east-2 \
        .venv/bin/python3 scripts/backtest_oversold.py --fetch
"""
import argparse
import hashlib
import json
import math
import random
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "backtest"
FMP_BASE = "https://financialmodelingprep.com/stable/"
FMP_BUDGET = 350
FMP_PACING_S = 1.0
PRICE_FROM, PRICE_TO = "2006-01-01", "2026-11-10"
SEED = 20261110
SAMPLE_N = 150
SEC_UA = "stock-screener-backtest research script (local, non-commercial) research@example.com"

# ---- Frozen rules (not tuned after seeing results) --------------------------
RULES = {
    "trigger_ret3": -0.10,
    "trigger_z": -3.0,
    "z_window": 504,
    "rsi_max": 30.0,
    "range_pos_max": 0.10,
    "sma200_dist_max": -0.20,
    "vol_mult": 3.0,
    "a_min_flags": 3,
    "own_hist_pct": 20.0,
    "own_hist_min_months": 60,
    "own_hist_max_months": 120,
    "fcf_yield_min": 0.05,
    "nd_ebitda_max": 2.5,
    "icr_min": 4.0,
    "event_gap": 20,
    "horizons": (5, 20, 60, 120),
    "mae_days": 60,
}


# =============================== PURE FUNCTIONS ==============================
def rsi_wilder(close, n=14):
    """Wilder RSI. Seed = simple mean of first n changes, then (prev*(n-1)+new)/n."""
    c = np.asarray(close, dtype=float)
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    ag, al = gain[:n].mean(), loss[:n].mean()

    def val(g, lo):
        if lo == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / lo)

    out[n] = val(ag, al)
    for i in range(n, len(d)):
        ag = (ag * (n - 1) + gain[i]) / n
        al = (al * (n - 1) + loss[i]) / n
        out[i + 1] = val(ag, al)
    return out


def zscore_ret(close, k=3, window=504):
    """z of the k-day return vs the k-day returns whose windows ended on or before
    t-k (rolling, last `window` of them): the event window is excluded."""
    s = pd.Series(np.asarray(close, dtype=float))
    r = s / s.shift(k) - 1.0
    base = r.shift(k)
    mu = base.rolling(window, min_periods=window).mean()
    sd = base.rolling(window, min_periods=window).std(ddof=1)
    return ((r - mu) / sd).to_numpy()


def range_position(high, low, close, window=252):
    h = pd.Series(np.asarray(high, dtype=float)).rolling(window, min_periods=window).max()
    lo = pd.Series(np.asarray(low, dtype=float)).rolling(window, min_periods=window).min()
    c = pd.Series(np.asarray(close, dtype=float))
    rng = (h - lo).replace(0, np.nan)
    return ((c - lo) / rng).to_numpy()


def sma_distance(close, window=200):
    c = pd.Series(np.asarray(close, dtype=float))
    sma = c.rolling(window, min_periods=window).mean()
    return (c / sma - 1.0).to_numpy()


def volume_ratio(vol, window=20):
    v = pd.Series(np.asarray(vol, dtype=float))
    prior = v.rolling(window, min_periods=window).mean().shift(1)
    return (v / prior.replace(0, np.nan)).to_numpy()


def volume_spike(vol, mult=3.0, window=20):
    """Volume >= mult x prior-20d average on day t or day t-1."""
    vr = volume_ratio(vol, window)
    cur = np.nan_to_num(vr, nan=0.0) >= mult
    prev = np.concatenate([[False], cur[:-1]])
    return cur | prev


COMMON_SPLITS = [2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 1.5, 1.25, 4 / 3, 2.5,
                 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 1 / 1.5, 1 / 2.5]


def _near_split_ratio(x, tol):
    return any(abs(x / s - 1.0) <= tol for s in COMMON_SPLITS)


def detect_split_days(adj_close, unadj_close, vol=None, tol=0.05):
    """Return (split_days, artifact_days), each a list of (index, ratio).

    split_days: the adjusted and unadjusted one-day ratios disagree by a common split
      ratio g = unadj_ratio/adj_ratio (FMP adjusted the split away; shares scale 1/g).
    artifact_days: both series jump by a split-like ratio with no volume spike, i.e. an
      unadjusted split left in the adjusted data.
    """
    a = np.asarray(adj_close, dtype=float)
    u = np.asarray(unadj_close, dtype=float)
    ra = a[1:] / a[:-1]
    ru = u[1:] / u[:-1]
    g = ru / ra
    splits, arts = [], []
    vr = volume_ratio(vol) if vol is not None else None
    for j in range(len(g)):
        i = j + 1
        if not np.isfinite(g[j]) or ra[j] <= 0:
            continue
        if abs(g[j] - 1.0) > 0.15 and _near_split_ratio(g[j], tol):
            splits.append((i, float(g[j])))
        elif abs(math.log(ra[j])) > 0.25 and _near_split_ratio(ra[j], 0.03):
            spike = vr is not None and np.isfinite(vr[i]) and vr[i] >= 3.0
            if not spike:
                arts.append((i, float(ra[j])))
    return splits, arts


def artifact_mask(n, split_days, art_days, before=25):
    """True where an event must be dropped: a split/artifact day lies in [i-before, i]
    (the 3-day return window and the 20d volume baseline would be contaminated)."""
    m = np.zeros(n, dtype=bool)
    for i, _ in list(split_days) + list(art_days):
        m[i:min(n, i + before + 1)] = True
    return m


def decluster(trigger, gap=20):
    """First trigger day in a cluster; the next event needs index >= previous + gap."""
    out, last = [], None
    for i, t in enumerate(trigger):
        if t and (last is None or i - last >= gap):
            out.append(i)
            last = i
    return out


def forward_returns(open_, close, i, horizons, entry="open"):
    """entry='open': next day's open (i+1) to close at i+h. entry='close': signal-day
    close to close at i+h. NaN if the horizon runs past the data."""
    if entry == "open":
        if i + 1 >= len(close):
            return {h: np.nan for h in horizons}
        px = open_[i + 1]
    else:
        px = close[i]
    out = {}
    for h in horizons:
        j = i + h
        out[h] = close[j] / px - 1.0 if (j < len(close) and px > 0) else np.nan
    return out


def max_adverse_excursion(low, i, days=60, entry_price=None):
    """Worst low over bars i+1..i+days vs entry price (<= 0). NaN if fewer bars exist."""
    if i + days >= len(low) or entry_price is None or not entry_price > 0:
        return np.nan
    return float(np.min(low[i + 1:i + 1 + days]) / entry_price - 1.0)


def stats(x):
    x = np.asarray([v for v in x if v is not None and np.isfinite(v)], dtype=float)
    if len(x) == 0:
        return {"n": 0, "median": np.nan, "mean": np.nan, "win": np.nan,
                "p10": np.nan, "p90": np.nan}
    return {"n": len(x), "median": float(np.median(x)), "mean": float(x.mean()),
            "win": float((x > 0).mean()), "p10": float(np.percentile(x, 10)),
            "p90": float(np.percentile(x, 90))}


def _clean(vals, clusters):
    pairs = [(v, c) for v, c in zip(vals, clusters) if v is not None and np.isfinite(v)]
    return (np.array([p[0] for p in pairs], dtype=float), [p[1] for p in pairs])


def _resample(vals, cid, counts):
    """Values of the resampled clusters: each cluster k is repeated counts[k] times."""
    return np.repeat(vals, counts[cid])


def cluster_bootstrap_ci(vals, clusters, stat="median", n_boot=2000, seed=1):
    """95% CI of a statistic, resampling whole clusters (calendar months)."""
    x, cl = _clean(vals, clusters)
    if len(x) == 0:
        return (np.nan, np.nan)
    keys = {k: i for i, k in enumerate(sorted(set(cl), key=str))}
    cid = np.array([keys[c] for c in cl])
    rng = np.random.default_rng(seed)
    f = np.median if stat == "median" else np.mean
    res = np.empty(n_boot)
    for b in range(n_boot):
        counts = np.bincount(rng.integers(0, len(keys), len(keys)), minlength=len(keys))
        res[b] = f(_resample(x, cid, counts))
    return (float(np.percentile(res, 2.5)), float(np.percentile(res, 97.5)))


def cluster_bootstrap_diff(va, ca, vb, cb, stat="median", n_boot=2000, seed=1):
    """Difference (A - B) of a statistic, cluster bootstrap over the union of clusters.
    Returns (diff, lo, hi, p_two_sided). p = 2 * share of bootstrap diffs on the other
    side of zero, floored at 1/(n_boot+1)."""
    f = np.median if stat == "median" else np.mean
    xa, cla = _clean(va, ca)
    xb, clb = _clean(vb, cb)
    if len(xa) == 0 or len(xb) == 0:
        return (np.nan, np.nan, np.nan, np.nan)
    keys = {k: i for i, k in enumerate(sorted(set(cla) | set(clb), key=str))}
    ia = np.array([keys[c] for c in cla])
    ib = np.array([keys[c] for c in clb])
    diff = float(f(xa) - f(xb))
    rng = np.random.default_rng(seed)
    res = []
    for _ in range(n_boot):
        counts = np.bincount(rng.integers(0, len(keys), len(keys)), minlength=len(keys))
        sa, sb = _resample(xa, ia, counts), _resample(xb, ib, counts)
        if len(sa) and len(sb):
            res.append(f(sa) - f(sb))
    if not res:
        return (diff, np.nan, np.nan, np.nan)
    res = np.array(res)
    lo, hi = np.percentile(res, [2.5, 97.5])
    p = max(2 * min((res <= 0).mean(), (res >= 0).mean()), 1.0 / (len(res) + 1))
    return (diff, float(lo), float(hi), float(min(p, 1.0)))


def percentile_rank(value, history):
    h = np.asarray([x for x in history if x is not None and np.isfinite(x)], dtype=float)
    if len(h) == 0:
        return np.nan
    return float((h <= value).mean() * 100.0)


def own_history_flag(current, history, pct=20.0, min_n=60, max_n=120):
    """current <= pct-th percentile of the last <= max_n history points (needs >= min_n).
    True/False, or None when history is too short or current is invalid."""
    h = [x for x in history if x is not None and np.isfinite(x)][-max_n:]
    if current is None or not np.isfinite(current) or len(h) < min_n:
        return None
    return bool(current <= np.percentile(h, pct))


# ------------------------ fundamentals (point in time) -----------------------
def _days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def duration_series(facts, tags, unit="USD"):
    """Discrete quarters from XBRL duration facts, point in time.

    For each (start,end) period only the EARLIEST filed value is kept (the original
    filing, never a later restatement). Quarters come from direct ~3-month facts or from
    differences of cumulative (6M/9M/12M) facts sharing a start date; a derived quarter
    is available only when both components are filed (max of the two filed dates).
    Tags merge in priority order per quarter end. Returns [(end, val, filed)] by end.
    """
    got = {}
    for tag in tags:
        recs = {}
        for f in facts.get(tag, {}).get("units", {}).get(unit, []):
            if "start" not in f:
                continue
            key = (f["start"], f["end"])
            if key not in recs or f["filed"] < recs[key]["filed"]:
                recs[key] = f
        q, by_start = {}, {}
        for (s, e), f in recs.items():
            d = _days(s, e)
            if 80 <= d <= 100:
                q[e] = (f["val"], f["filed"])
            by_start.setdefault(s, []).append((e, f["val"], f["filed"], d))
        for lst in by_start.values():
            lst.sort()
            prev = None
            for e, v, fl, d in lst:
                if d < 80:
                    continue
                if prev is not None and e not in q and 80 <= _days(prev[0], e) <= 100:
                    q[e] = (v - prev[1], max(fl, prev[2]))
                prev = (e, v, fl)
        for e, (v, fl) in q.items():
            got.setdefault(e, (v, fl))
    return sorted((e, v, fl) for e, (v, fl) in got.items())


def instant_series(facts, tags, unit="USD", max_duration=5):
    """Balance-sheet facts: earliest-filed value per end date; tags merged by priority.
    Facts with a start date are skipped when longer than max_duration days."""
    got = {}
    for tag in tags:
        recs = {}
        for f in facts.get(tag, {}).get("units", {}).get(unit, []):
            if f.get("start") and _days(f["start"], f["end"]) > max_duration:
                continue
            e = f["end"]
            if e not in recs or f["filed"] < recs[e]["filed"]:
                recs[e] = f
        for e, f in recs.items():
            got.setdefault(e, (f["val"], f["filed"]))
    return sorted((e, v, fl) for e, (v, fl) in got.items())


def ttm_at(quarters, d):
    """Sum of the latest 4 consecutive quarters with filed <= d. None if unavailable."""
    qs = sorted((e, v) for e, v, fl in quarters if fl <= d)
    if len(qs) < 4:
        return None
    last4 = qs[-4:]
    if not (240 <= _days(last4[0][0], last4[-1][0]) <= 290):
        return None
    return float(sum(v for _, v in last4))


def instant_at(series, d, max_age_days=200):
    """Latest value (by period end) with filed <= d, not older than max_age_days."""
    best = None
    for e, v, fl in series:
        if fl <= d and (best is None or e > best[0]):
            best = (e, v)
    if best is None or _days(best[0], d) > max_age_days:
        return None
    return float(best[1])


def quality_gate(nd_over_ebitda, icr, fcf_ttm, rules=RULES):
    """Block D. Each input may be None (not computable).
    FAIL if any computable test fails; PASS if all three computable and pass;
    UNKNOWN otherwise."""
    tests = [
        None if nd_over_ebitda is None else nd_over_ebitda <= rules["nd_ebitda_max"],
        None if icr is None else icr >= rules["icr_min"],
        None if fcf_ttm is None else fcf_ttm > 0,
    ]
    if any(t is False for t in tests):
        return "FAIL"
    if all(t is True for t in tests):
        return "PASS"
    return "UNKNOWN"


def quality_gate_lenient(nd_over_ebitda, icr, fcf_ttm, rules=RULES):
    """EXPLORATORY variant (added after the first run, labelled as such): uncomputable
    tests are dropped; FAIL if any computable test fails; PASS needs >= 2 computable
    tests, all passing; UNKNOWN otherwise."""
    tests = [None if nd_over_ebitda is None else nd_over_ebitda <= rules["nd_ebitda_max"],
             None if icr is None else icr >= rules["icr_min"],
             None if fcf_ttm is None else fcf_ttm > 0]
    known = [t for t in tests if t is not None]
    if any(t is False for t in known):
        return "FAIL"
    return "PASS" if len(known) >= 2 else "UNKNOWN"


def classify(a_flags, c_flags, d_state, rules=RULES):
    """Decision state. a_flags: list of 0/1; c_flags: list of True/False/None;
    d_state: PASS/FAIL/UNKNOWN. FUNDAMENTAL-RISK takes precedence over a price state.
    Returns (state, a_count, c_count)."""
    a = int(sum(a_flags))
    c = int(sum(1 for x in c_flags if x))
    if d_state == "FAIL":
        return "FUNDAMENTAL-RISK", a, c
    if a >= rules["a_min_flags"]:
        if c >= 1 and d_state == "PASS":
            return "LIKELY-OVERSOLD-PROXY", a, c
        return "PRICE-STRESSED", a, c
    return "NONE", a, c


# ================================== DATA I/O =================================
class BudgetExceeded(RuntimeError):
    pass


class FMP:
    """Disk-cached, budgeted, paced FMP client. The API key is read from SSM in
    process and never printed; it is redacted from any error text."""

    def __init__(self, offline=False):
        self.offline = offline
        self._key = None
        self._last = 0.0
        (CACHE / "fmp").mkdir(parents=True, exist_ok=True)
        self.budget_file = CACHE / "fmp_budget.json"

    def calls_used(self):
        if self.budget_file.exists():
            return json.loads(self.budget_file.read_text())["calls"]
        return 0

    def _bump(self):
        self.budget_file.write_text(json.dumps({"calls": self.calls_used() + 1}))

    @staticmethod
    def cache_path(endpoint, params):
        h = hashlib.sha1(json.dumps({"e": endpoint, "p": {k: str(v) for k, v in sorted(params.items())}},
                                    sort_keys=True).encode()).hexdigest()[:14]
        return CACHE / "fmp" / f"{endpoint.replace('/', '_')}_{params.get('symbol', 'x')}_{h}.json"

    def _api_key(self):
        if self._key is None:
            import boto3
            ssm = boto3.client("ssm", region_name="us-east-2")
            self._key = ssm.get_parameter(Name="/stock-screener/fmp-api-key",
                                          WithDecryption=True)["Parameter"]["Value"]
        return self._key

    def get(self, endpoint, **params):
        path = self.cache_path(endpoint, params)
        if path.exists():
            return json.loads(path.read_text())
        if self.offline:
            raise RuntimeError(f"offline: cache miss for {endpoint} {params.get('symbol', '')}")
        if self.calls_used() >= FMP_BUDGET:
            raise BudgetExceeded(f"FMP budget of {FMP_BUDGET} calls reached")
        import requests
        key = self._api_key()
        wait = FMP_PACING_S - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._bump()
        self._last = time.time()
        try:
            r = requests.get(FMP_BASE + endpoint, params={**params, "apikey": key}, timeout=90)
        except requests.RequestException as e:
            raise RuntimeError(str(e).replace(key, "***")) from None
        if not r.ok:
            raise RuntimeError(f"FMP {endpoint} HTTP {r.status_code}: {r.text[:150].replace(key, '***')}")
        data = r.json()
        path.write_text(json.dumps(data))
        return data


def edgar_get(url, cache_name, offline=False):
    path = CACHE / "edgar" / cache_name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return json.loads(path.read_text())
    if offline:
        raise RuntimeError(f"offline: cache miss for {cache_name}")
    import requests
    time.sleep(0.2)
    r = requests.get(url, headers={"User-Agent": SEC_UA}, timeout=90)
    if r.status_code == 404:
        path.write_text("null")
        return None
    r.raise_for_status()
    data = r.json()
    path.write_text(json.dumps(data))
    return data


SCREENER_PARAMS = dict(marketCapMoreThan=300000000, country="US", isEtf="false", isFund="false",
                       isActivelyTrading="true", limit=10000)


def load_pipeline_sources(offline):
    """Symbols from the latest S3 step1 universe and DynamoDB LATEST items (read-only)."""
    path = CACHE / "universe_sources.json"
    if path.exists():
        return json.loads(path.read_text())
    if offline:
        raise RuntimeError("offline: universe_sources.json missing")
    import boto3
    s3 = boto3.client("s3", region_name="us-east-2")
    bucket = "stock-screener-raw-data-116488731375"
    keys = [o["Key"] for p in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="pipeline/")
            for o in p.get("Contents", []) if "/step1_fundamentals_" in o["Key"]]
    latest = sorted(keys)[-1]
    step1 = json.loads(s3.get_object(Bucket=bucket, Key=latest)["Body"].read())
    s3_syms = sorted({s["symbol"] for s in step1["stocks"]})
    ddb = boto3.resource("dynamodb", region_name="us-east-2").Table("stock-screener-data")
    ddb_syms, kw = set(), dict(FilterExpression="SK = :s AND begins_with(PK, :p)",
                               ExpressionAttributeValues={":s": "LATEST", ":p": "STOCK#"},
                               ProjectionExpression="PK")
    while True:
        resp = ddb.scan(**kw)
        ddb_syms |= {i["PK"].split("#", 1)[1] for i in resp["Items"]}
        if "LastEvaluatedKey" not in resp:
            break
        kw["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    out = {"step1_key": latest, "step1": s3_syms, "dynamo": sorted(ddb_syms)}
    path.write_text(json.dumps(out))
    return out


def build_candidates(fmp, offline):
    """(a) step1 universe symbols and (b) DynamoDB symbols, restricted to a US listing
    with market cap >= $300M (FMP screener, CURRENT cap) and an SEC CIK."""
    scr = fmp.get("company-screener", **SCREENER_PARAMS)
    src = load_pipeline_sources(offline)
    cik_raw = edgar_get("https://www.sec.gov/files/company_tickers.json", "company_tickers.json", offline)
    cik = {v["ticker"].upper(): int(v["cik_str"]) for v in cik_raw.values()}
    wanted = set(src["step1"]) | set(src["dynamo"])
    by_sym = {r["symbol"]: r for r in scr}
    cands = []
    for sym in sorted(wanted):
        r = by_sym.get(sym)
        if not r or r.get("exchangeShortName") not in ("NYSE", "NASDAQ", "AMEX"):
            continue
        if sym not in cik or (r.get("marketCap") or 0) < 300_000_000:
            continue
        cands.append({"symbol": sym, "sector": r.get("sector") or "Unknown", "mcap_now": r["marketCap"],
                      "cik": cik[sym], "in_step1": sym in set(src["step1"]),
                      "in_dynamo": sym in set(src["dynamo"])})
    stats_ = {"screener_us_ge300m": len(scr), "step1": len(src["step1"]), "dynamo": len(src["dynamo"]),
              "candidates": len(cands)}
    return cands, stats_


def load_companyfacts(cik, offline):
    return edgar_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
                     f"facts_{cik:010d}.json", offline)


def edgar_usable(cf):
    if not cf:
        return False
    g = cf.get("facts", {}).get("us-gaap", {})
    oi = g.get("OperatingIncomeLoss", {}).get("units", {}).get("USD", [])
    cfo = g.get("NetCashProvidedByUsedInOperatingActivities", {}).get("units", {}).get("USD", [])
    return len(oi) >= 30 and len(cfo) >= 30 and max(f["filed"] for f in oi) >= "2026-01-01"


def draw_sample(cands, n, seed, offline, forced=("HLIO",)):
    """Stratified (by sector, proportional) seeded random sample of tickers that have
    usable EDGAR XBRL. Financial Services are excluded (EBITDA / net debt are not
    meaningful). Selection never looks at prices or outcomes."""
    rnd = random.Random(seed)
    pool = [c for c in cands if c["sector"] != "Financial Services"]
    by_sector = {}
    for c in pool:
        by_sector.setdefault(c["sector"], []).append(c)
    total = len(pool)
    quota = {s: n * len(v) / total for s, v in by_sector.items()}
    base = {s: int(q) for s, q in quota.items()}
    rem = n - sum(base.values())
    for s in sorted(quota, key=lambda s: quota[s] - base[s], reverse=True)[:rem]:
        base[s] += 1
    chosen, rejected = [], 0
    forced_c = [c for c in cands if c["symbol"] in forced]
    for c in forced_c:
        if edgar_usable(load_companyfacts(c["cik"], offline)):
            chosen.append(c)
            base[c["sector"]] = max(0, base.get(c["sector"], 0) - 1)
    for s in sorted(by_sector):
        lst = [c for c in by_sector[s] if c["symbol"] not in {x["symbol"] for x in chosen}]
        rnd.shuffle(lst)
        got = 0
        for c in lst:
            if got >= base[s]:
                break
            if edgar_usable(load_companyfacts(c["cik"], offline)):
                chosen.append(c)
                got += 1
            else:
                rejected += 1
    return chosen, rejected


def instant_at_ex(series, d, max_age_days=200):
    """Like instant_at but returns (period_end, value) or None."""
    best = None
    for e, v, fl in series:
        if fl <= d and (best is None or e > best[0]):
            best = (e, v)
    if best is None or _days(best[0], d) > max_age_days:
        return None
    return best[0], float(best[1])


def price_frame(adj_rows, un_rows):
    """Merge FMP dividend-adjusted OHLCV with the non-split-adjusted close.
    Columns: date, o, h, l, c (adjusted), v (split-adjusted shares), uc (unadjusted close)."""
    a = pd.DataFrame(adj_rows)
    a = a.rename(columns={"adjOpen": "o", "adjHigh": "h", "adjLow": "l", "adjClose": "c", "volume": "v"})
    u = pd.DataFrame(un_rows)
    ucol = "adjClose" if "adjClose" in u.columns else "close"
    u = u[["date", ucol]].rename(columns={ucol: "uc"})
    df = a[["date", "o", "h", "l", "c", "v"]].merge(u, on="date", how="inner")
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return df[(df.c > 0) & (df.o > 0) & (df.uc > 0)].reset_index(drop=True)


class Fund:
    """Point-in-time fundamentals for one company from EDGAR companyfacts."""
    EBIT = ["OperatingIncomeLoss"]
    DA = ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
          "DepreciationAmortizationAndAccretionNet", "Depreciation",
          "DepreciationAmortizationAndOther", "OtherDepreciationAndAmortization"]
    INTEREST = ["InterestExpense", "InterestExpenseDebt", "InterestAndDebtExpense",
                "InterestExpenseNonoperating", "InterestPaidNet", "InterestPaid"]
    # Net interest (income less expense, negative = net expense): used last, sign flipped.
    INTEREST_NET = ["InterestIncomeExpenseNonoperatingNet", "InterestIncomeExpenseNet"]
    CFO = ["NetCashProvidedByUsedInOperatingActivities",
           "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
    CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
             "PaymentsForCapitalImprovements", "PaymentsToAcquireOtherPropertyPlantAndEquipment"]
    LTD_NC = ["LongTermDebtNoncurrent", "LongTermNotesPayable", "SeniorNotes", "ConvertibleNotesPayableNoncurrent",
              "LongTermLineOfCredit", "NotesPayableNoncurrent", "ConvertibleDebtNoncurrent",
              "SecuredLongTermDebt", "UnsecuredLongTermDebt"]
    ST_DEBT = ["ShortTermBorrowings", "CommercialPaper", "LineOfCredit", "NotesPayableCurrent"]
    NI = ["NetIncomeLoss", "ProfitLoss"]

    def __init__(self, cf, splits):
        g = cf["facts"].get("us-gaap", {})
        dei = cf["facts"].get("dei", {})
        self.ebit = duration_series(g, self.EBIT)
        self.da = duration_series(g, self.DA)
        self.interest = duration_series(g, self.INTEREST)
        net = duration_series(g, self.INTEREST_NET)
        have = {e for e, _, _ in self.interest}
        self.interest += [(e, -v, fl) for e, v, fl in net if e not in have and v < 0]
        self.interest.sort()
        self.cfo = duration_series(g, self.CFO)
        self.capex = duration_series(g, self.CAPEX)
        self.ni = duration_series(g, self.NI)
        self.lt = instant_series(g, ["LongTermDebt"])
        self.ltnc = instant_series(g, self.LTD_NC)
        self.ltcur = instant_series(g, ["LongTermDebtCurrent", "DebtCurrent"])
        self.stb = instant_series(g, self.ST_DEBT)
        self.cp = []
        self.cash = instant_series(g, ["CashAndCashEquivalentsAtCarryingValue",
                                       "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"])
        self.shares = instant_series(dei, ["EntityCommonStockSharesOutstanding"], unit="shares")
        self.shares += instant_series(g, ["WeightedAverageNumberOfDilutedSharesOutstanding",
                                          "WeightedAverageNumberOfSharesOutstandingBasic"],
                                      unit="shares", max_duration=100)
        self.has_any_debt_tag = any(g.get(t) for t in ["LongTermDebt", "LongTermDebtCurrent", "DebtCurrent"]
                                    + self.LTD_NC + self.ST_DEBT + ["CommercialPaper"])
        self.splits = splits  # [(date_str, g)]; share count scales by 1/g

    def split_scale(self, after, upto):
        s = 1.0
        for ds, g in self.splits:
            if after < ds <= upto:
                s /= g
        return s

    def debt_at(self, d):
        lt = instant_at(self.lt, d)
        if lt is not None:
            base = lt
        else:
            nc = instant_at(self.ltnc, d)
            if nc is None:
                return None if self.has_any_debt_tag else 0.0
            base = nc + (instant_at(self.ltcur, d) or 0.0)
        return base + (instant_at(self.stb, d) or 0.0) + (instant_at(self.cp, d) or 0.0)

    def metrics(self, d, px_unadj):
        """Everything known on date d (facts filed <= d) with unadjusted price px_unadj."""
        m = dict.fromkeys(["mcap", "ebitda", "ebit", "interest", "fcf", "ni", "net_debt", "ev",
                           "ev_ebitda", "pe", "fcf_yield", "nd_ebitda", "icr"])
        sh = instant_at_ex(self.shares, d)
        if sh is not None:
            m["mcap"] = px_unadj * sh[1] * self.split_scale(sh[0], d)
        ebit, da = ttm_at(self.ebit, d), ttm_at(self.da, d)
        m["ebit"] = ebit
        m["ebitda"] = None if (ebit is None or da is None) else ebit + da
        m["ni"] = ttm_at(self.ni, d)
        cfo, capex = ttm_at(self.cfo, d), ttm_at(self.capex, d)
        m["fcf"] = None if (cfo is None or capex is None) else cfo - abs(capex)
        debt, cash = self.debt_at(d), instant_at(self.cash, d)
        if debt is not None and cash is not None:
            m["net_debt"] = debt - cash
        if m["mcap"] and m["net_debt"] is not None:
            m["ev"] = m["mcap"] + m["net_debt"]
        if m["ev"] is not None and m["ebitda"] is not None and m["ebitda"] > 0:
            m["ev_ebitda"] = m["ev"] / m["ebitda"]
        if m["mcap"] and m["ni"] is not None and m["ni"] > 0:
            m["pe"] = m["mcap"] / m["ni"]
        if m["mcap"] and m["fcf"] is not None:
            m["fcf_yield"] = m["fcf"] / m["mcap"]
        nd, eb = m["net_debt"], m["ebitda"]
        if nd is not None and eb is not None:
            m["nd_ebitda"] = 0.0 if nd <= 0 else (nd / eb if eb > 0 else float("inf"))
        it = ttm_at(self.interest, d)
        m["interest"] = it
        if ebit is not None:
            if it is not None and it > 0:
                m["icr"] = ebit / it
            elif debt is not None and debt <= 0:
                m["icr"] = float("inf")
        return m


def month_grid(dates):
    """Last trading date of each calendar month, ascending."""
    last = {}
    for d in dates:
        last[d[:7]] = d
    return [last[k] for k in sorted(last)]


def valuation_history(fund, fr, grid, start="2009-06-30"):
    """[(date, ev_ebitda or nan, pe or nan)] at each grid date, using only facts filed
    by that date and that date's unadjusted close."""
    out = []
    px = dict(zip(fr.date, fr.uc))
    for d in grid:
        if d < start:
            continue
        m = fund.metrics(d, px[d])
        out.append((d, m["ev_ebitda"] if m["ev_ebitda"] is not None else np.nan,
                    m["pe"] if m["pe"] is not None else np.nan))
    return out


def valuation_flag_at(hist, event_date, cur_ev, cur_pe, rules=RULES):
    """Own-history flag. Uses EV/EBITDA if computable now, else P/E. Only grid points
    strictly before the event month are used. Returns (flag, metric_name, pct_rank)."""
    prior = [h for h in hist if h[0][:7] < event_date[:7]]
    for name, cur, idx in (("EV/EBITDA", cur_ev, 1), ("P/E", cur_pe, 2)):
        if cur is not None and np.isfinite(cur):
            series = [h[idx] for h in prior]
            flag = own_history_flag(cur, series, rules["own_hist_pct"],
                                    rules["own_hist_min_months"], rules["own_hist_max_months"])
            rank = percentile_rank(cur, [x for x in series if np.isfinite(x)][-rules["own_hist_max_months"]:])
            return flag, name, rank
    return None, None, np.nan


# ================================= STUDY CODE ================================
def ticker_features(fr):
    c, h, lo, v = fr.c.to_numpy(), fr.h.to_numpy(), fr.l.to_numpy(), fr.v.to_numpy()
    ret3 = pd.Series(c) / pd.Series(c).shift(3) - 1.0
    f = {
        "ret3": ret3.to_numpy(),
        "z": zscore_ret(c, 3, RULES["z_window"]),
        "rsi": rsi_wilder(c, 14),
        "rangepos": range_position(h, lo, c, 252),
        "sma200": sma_distance(c, 200),
        "volratio": volume_ratio(v, 20),
        "volspike": volume_spike(v, RULES["vol_mult"], 20),
    }
    valid = np.ones(len(c), dtype=bool)
    for k in ("ret3", "z", "rsi", "rangepos", "sma200", "volratio"):
        valid &= np.isfinite(f[k])
    f["valid"] = valid
    with np.errstate(invalid="ignore"):
        f["trigger"] = valid & ((f["ret3"] <= RULES["trigger_ret3"]) | (f["z"] <= RULES["trigger_z"]))
    f["a_flags"] = np.vstack([
        (f["rsi"] <= RULES["rsi_max"]), (f["z"] <= RULES["trigger_z"]),
        (f["rangepos"] <= RULES["range_pos_max"]), (f["sma200"] <= RULES["sma200_dist_max"]),
        f["volspike"]]).T.astype(int)
    return f


def all_day_forward(open_, close, horizons):
    """Per-horizon mean over every day of the h-day open(t+1)->close(t+h) return."""
    n = len(close)
    out = {}
    for h in horizons:
        j = np.arange(0, n - h)
        r = close[j + h] / open_[j + 1] - 1.0 if len(j) else np.array([])
        out[h] = float(np.mean(r)) if len(r) else np.nan
    return out


A_NAMES = ["rsi<=30", "z<=-3", "range<=10%", "sma200<=-20%", "vol>=3x"]


def fundamental_state(fund, hist, d, px_unadj, mcap_ok, a_flags):
    """Blocks C and D (and the decision state) from facts filed on or before d."""
    m = fund.metrics(d, px_unadj) if fund is not None else None
    if m is not None and not mcap_ok:
        for k in ("mcap", "ev", "ev_ebitda", "pe", "fcf_yield"):
            m[k] = None
    flag_val, metric, rank = (valuation_flag_at(hist, d, m["ev_ebitda"], m["pe"])
                              if m is not None else (None, None, np.nan))
    fcf_flag = None if (m is None or m["fcf_yield"] is None) else bool(m["fcf_yield"] >= RULES["fcf_yield_min"])
    out = {"c_val": flag_val, "c_metric": metric, "val_pct_rank": rank, "c_fcf": fcf_flag,
           "ev_ebitda": None if m is None else m["ev_ebitda"],
           "fcf_yield": None if m is None else m["fcf_yield"],
           "nd_ebitda": None if m is None else m["nd_ebitda"],
           "icr": None if m is None else m["icr"],
           "fcf_ttm": None if m is None else m["fcf"]}
    out["d_state"] = "UNKNOWN" if m is None else quality_gate(m["nd_ebitda"], m["icr"], m["fcf"])
    out["d_len"] = "UNKNOWN" if m is None else quality_gate_lenient(m["nd_ebitda"], m["icr"], m["fcf"])
    out["state"], _, out["c_count"] = classify(a_flags, [out["c_val"], out["c_fcf"]], out["d_state"])
    return out


def build_events(symbol, fr, feat, fund, mcap_ok, spy, week_frac, splits, arts):
    H = RULES["horizons"]
    dates = fr.date.tolist()
    o, c, lo = fr.o.to_numpy(), fr.c.to_numpy(), fr.l.to_numpy()
    mask = artifact_mask(len(fr), splits, arts)
    idxs = decluster(feat["trigger"], RULES["event_gap"])
    base = all_day_forward(o, c, H)
    grid = month_grid(dates)
    hist = valuation_history(fund, fr, grid) if fund is not None else []
    rows, dropped = [], 0
    for i in idxs:
        if mask[i]:
            dropped += 1
            continue
        d = dates[i]
        fw_o = forward_returns(o, c, i, H, "open")
        fw_c = forward_returns(o, c, i, H, "close")
        entry_o = o[i + 1] if i + 1 < len(o) else np.nan
        row = {"symbol": symbol, "date": d, "idx": i, "month": d[:7], "year": int(d[:4]),
               "ret3": feat["ret3"][i], "z": feat["z"][i], "rsi": feat["rsi"][i],
               "rangepos": feat["rangepos"][i], "sma200": feat["sma200"][i],
               "volratio": feat["volratio"][i]}
        for k, nm in enumerate(A_NAMES):
            row[f"a{k + 1}"] = int(feat["a_flags"][i, k])
        row["a_count"] = int(feat["a_flags"][i].sum())
        wk = date.fromisoformat(d).isocalendar()[:2]
        row["mkt_wide"] = bool(week_frac.get(wk, 0.0) >= 0.10)
        # SPY legs
        spy_i = spy["pos"].get(d)
        row["spy_ret3"] = (spy["c"][spy_i] / spy["c"][spy_i - 3] - 1.0) if spy_i is not None and spy_i >= 3 else np.nan
        for h in H:
            row[f"ret_{h}"] = fw_o[h]
            row[f"retc_{h}"] = fw_c[h]
            row[f"base_{h}"] = base[h]
            sr = np.nan
            if i + 1 < len(dates) and i + h < len(dates):
                s0, s1 = spy["pos"].get(dates[i + 1]), spy["pos"].get(dates[i + h])
                if s0 is not None and s1 is not None:
                    sr = spy["c"][s1] / spy["o"][s0] - 1.0
            sc = np.nan
            s0c, s1c = spy["pos"].get(d), (spy["pos"].get(dates[i + h]) if i + h < len(dates) else None)
            if s0c is not None and s1c is not None:
                sc = spy["c"][s1c] / spy["c"][s0c] - 1.0
            row[f"spy_{h}"] = sr
            row[f"ex_{h}"] = fw_o[h] - sr if np.isfinite(sr) else np.nan
            row[f"exc_{h}"] = fw_c[h] - sc if np.isfinite(sc) else np.nan
            row[f"ab_{h}"] = fw_o[h] - base[h] if np.isfinite(fw_o[h]) else np.nan
        row["mae60"] = max_adverse_excursion(lo, i, RULES["mae_days"], entry_o)
        # volatility-normalised MAE: MAE / (trailing-252d daily sigma * sqrt(60)); uses data to day i only
        dr = np.diff(np.log(c[max(0, i - 252):i + 1]))
        sig = float(np.std(dr, ddof=1)) if len(dr) > 100 else np.nan
        row["vol252"] = sig
        row["mae60_n"] = row["mae60"] / (sig * math.sqrt(RULES["mae_days"])) if np.isfinite(sig) and sig > 0 else np.nan
        # fundamentals as known on the signal date
        row.update(fundamental_state(fund, hist, d, fr.uc.iloc[i], mcap_ok, feat["a_flags"][i]))
        row["stressed"] = row["a_count"] >= RULES["a_min_flags"]
        row["likely"] = bool(row["stressed"] and row["c_count"] >= 1 and row["d_state"] == "PASS")
        row["frisk"] = row["d_state"] == "FAIL"
        row["likely_len"] = bool(row["stressed"] and row["c_count"] >= 1 and row["d_len"] == "PASS")
        row["c_known"] = (row["c_val"] is not None) or (row["c_fcf"] is not None)
        rows.append(row)
    return rows, dropped


def load_universe_data(fmp, sample, fetch):
    """Returns (frames, spy). Tickers whose price data cannot be loaded are listed in
    `missing` (budget exhausted or offline cache miss)."""
    q = dict(**{"from": PRICE_FROM, "to": PRICE_TO})
    spy_rows = fmp.get("historical-price-eod/dividend-adjusted", symbol="SPY", **q)
    sp = pd.DataFrame(spy_rows).rename(columns={"adjOpen": "o", "adjClose": "c"})
    sp = sp.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    spy = {"o": sp.o.to_numpy(), "c": sp.c.to_numpy(), "pos": {d: i for i, d in enumerate(sp.date)}}
    frames, missing = {}, []
    for t in sample:
        sym = t["symbol"]
        try:
            adj = fmp.get("historical-price-eod/dividend-adjusted", symbol=sym, **q)
            un = fmp.get("historical-price-eod/non-split-adjusted", symbol=sym, **q)
        except (RuntimeError, BudgetExceeded) as e:
            missing.append((sym, str(e)[:80]))
            if isinstance(e, BudgetExceeded):
                break
            continue
        if not adj or not un or isinstance(adj, dict) or isinstance(un, dict):
            missing.append((sym, "empty response"))
            continue
        fr = price_frame(adj, un)
        if len(fr) >= 800:
            frames[sym] = fr
        else:
            missing.append((sym, f"only {len(fr)} bars"))
    return frames, spy, missing


def run_events(sample, frames, spy, offline):
    info = {t["symbol"]: t for t in sample}
    feats, splitinfo, week_has, week_trig = {}, {}, {}, {}
    for sym, fr in frames.items():
        f = ticker_features(fr)
        sp_days, art_days = detect_split_days(fr.c.to_numpy(), fr.uc.to_numpy(), fr.v.to_numpy())
        feats[sym], splitinfo[sym] = f, (sp_days, art_days)
        dts = fr.date.to_numpy()
        hs, ts = set(), set()
        for i in np.flatnonzero(f["valid"]):
            wk = date.fromisoformat(dts[i]).isocalendar()[:2]
            hs.add(wk)
            if f["trigger"][i]:
                ts.add(wk)
        for wk in hs:
            week_has[wk] = week_has.get(wk, 0) + 1
        for wk in ts:
            week_trig[wk] = week_trig.get(wk, 0) + 1
    week_frac = {wk: week_trig.get(wk, 0) / n for wk, n in week_has.items() if n >= 20}
    rows, dropped, fundinfo, funds = [], {}, {}, {}
    for sym, fr in frames.items():
        cf = load_companyfacts(info[sym]["cik"], offline)
        sp_days, art_days = splitinfo[sym]
        sp_dates = [(fr.date.iloc[i], g) for i, g in sp_days]
        fund = Fund(cf, sp_dates)
        funds[sym] = fund
        last = fr.date.iloc[-1]
        mc = fund.metrics(last, fr.uc.iloc[-1])["mcap"]
        ratio = (mc / info[sym]["mcap_now"]) if mc else np.nan
        mcap_ok = bool(np.isfinite(ratio) and 0.75 <= ratio <= 1.33)
        fundinfo[sym] = {"mcap_ratio": ratio, "mcap_ok": mcap_ok, "n_splits": len(sp_days),
                         "n_artifacts": len(art_days)}
        r, dr = build_events(sym, fr, feats[sym], fund, mcap_ok, spy, week_frac, sp_days, art_days)
        rows += r
        dropped[sym] = dr
    ev = pd.DataFrame(rows)
    return ev, dropped, fundinfo, feats, week_frac, funds


# ------------------------------ analysis helpers -----------------------------
H = RULES["horizons"]
MIN_N = 15


def pct(x, d=1):
    return "n/a" if x is None or not np.isfinite(x) else f"{x * 100:+.{d}f}%"


def pct_plain(x, d=0):
    return "n/a" if x is None or not np.isfinite(x) else f"{x * 100:.{d}f}%"


def masks(ev):
    t = lambda col, val: ev[col].eq(val)  # noqa: E731
    return {
        "ALL trigger events": pd.Series(True, index=ev.index),
        "PRICE-STRESSED (A>=3)": ev.stressed,
        "LIKELY-OVERSOLD-PROXY": ev.likely,
        "FUNDAMENTAL-RISK (D fail)": ev.frisk,
        "D gate PASS": t("d_state", "PASS"),
        "D gate FAIL": t("d_state", "FAIL"),
        "D gate UNKNOWN": t("d_state", "UNKNOWN"),
        "NOT stressed (A<3)": ~ev.stressed,
        "[expl.] LIKELY with lenient D": ev.likely_len,
        "[expl.] lenient D PASS": ev.d_len.eq("PASS"),
        "[expl.] lenient D FAIL": ev.d_len.eq("FAIL"),
    }


def subset_masks(ev):
    return {"all events": pd.Series(True, index=ev.index),
            "ex market-wide weeks": ~ev.mkt_wide,
            "idiosyncratic only": (~ev.mkt_wide) & (ev.spy_ret3 >= -0.03)}


def period_masks(ev):
    return {"all years": pd.Series(True, index=ev.index),
            "pre-2020": ev.year < 2020, "2020+": ev.year >= 2020}


def table_stats(ev, groups, col, horizons=H):
    lines = ["| group | h | n | median | mean | win | p10 | p90 |", "|---|---|---|---|---|---|---|---|"]
    for name, m in groups.items():
        for h in horizons:
            s = stats(ev.loc[m, f"{col}_{h}"].tolist())
            flag = " †" if s["n"] < MIN_N else ""
            lines.append(f"| {name} | +{h} | {s['n']}{flag} | {pct(s['median'])} | {pct(s['mean'])} | "
                         f"{pct_plain(s['win'])} | {pct(s['p10'])} | {pct(s['p90'])} |")
    return "\n".join(lines)


def run_comparisons(ev, pairs, n_boot):
    """pairs: [(label, maskA, maskB)]. Runs every pair x subset x period x horizon x stat
    where both cells have n >= MIN_N. Returns a DataFrame of all tests run."""
    rows = []
    subs, pers = subset_masks(ev), period_masks(ev)
    k = 0
    for label, ma, mb in pairs:
        for sn, sm in subs.items():
            for pn, pm in pers.items():
                a, b = ma & sm & pm, mb & sm & pm
                for h in H:
                    col = f"ex_{h}"
                    na = int(ev.loc[a, col].notna().sum())
                    nb = int(ev.loc[b, col].notna().sum())
                    for stat in ("median", "mean"):
                        row = {"pair": label, "subset": sn, "period": pn, "h": h, "stat": stat,
                               "nA": na, "nB": nb}
                        if na >= MIN_N and nb >= MIN_N:
                            k += 1
                            d, lo, hi, p = cluster_bootstrap_diff(
                                ev.loc[a, col].tolist(), ev.loc[a, "month"].tolist(),
                                ev.loc[b, col].tolist(), ev.loc[b, "month"].tolist(),
                                stat, n_boot, seed=k)
                            row.update({"diff": d, "lo": lo, "hi": hi, "p": p})
                        rows.append(row)
    return pd.DataFrame(rows)


def comp_table(tests, pair, subset="all events", period="all years", stat_list=("median", "mean")):
    t = tests[(tests.pair == pair) & (tests.subset == subset) & (tests.period == period)]
    lines = ["| h | stat | nA | nB | diff (A-B) | 95% CI | p |", "|---|---|---|---|---|---|---|"]
    for _, r in t.sort_values(["h", "stat"]).iterrows():
        if r.stat not in stat_list:
            continue
        if "diff" in r and pd.notna(r.get("diff")):
            lines.append(f"| +{r.h} | {r.stat} | {r.nA} | {r.nB} | {pct(r['diff'])} | "
                         f"[{pct(r.lo)}, {pct(r.hi)}] | {r.p:.3f} |")
        else:
            lines.append(f"| +{r.h} | {r.stat} | {r.nA} | {r.nB} | n<{MIN_N}, not tested | | |")
    return "\n".join(lines)


def hlio_section(ev, frames, feats, offline, info, spy):
    fr, f = frames["HLIO"], feats["HLIO"]
    dts = fr.date.tolist()
    out = ["| date | ret3 | z | RSI | range pos | vs 200d SMA | vol ratio | A flags |", "|---|---|---|---|---|---|---|---|"]
    ix = {}
    for d in ("2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"):
        if d in dts:
            i = dts.index(d)
            ix[d] = i
            out.append(f"| {d} | {pct(f['ret3'][i])} | {f['z'][i]:.2f} | {f['rsi'][i]:.1f} | "
                       f"{pct_plain(f['rangepos'][i], 1)} | {pct(f['sma200'][i])} | {f['volratio'][i]:.1f}x | "
                       f"{int(f['a_flags'][i].sum())} of 5 |")
    i6 = ix.get("2026-11-06")
    lines = ["\n".join(out), ""]
    if i6 is not None:
        pr = lambda col, v, lower: (ev[col] <= v).mean() * 100 if lower else (ev[col] >= v).mean() * 100  # noqa: E731
        lines.append("Where HLIO's Nov 6 values sit among all sample trigger events (share of events at least as extreme):\n")
        lines.append("| feature | HLIO Nov 6 | share of events at least as extreme |")
        lines.append("|---|---|---|")
        for col, v, lower, label in (("rsi", f["rsi"][i6], True, "RSI(14)"), ("z", f["z"][i6], True, "3-day z"),
                                     ("rangepos", f["rangepos"][i6], True, "52w range position"),
                                     ("sma200", f["sma200"][i6], True, "distance to 200d SMA"),
                                     ("volratio", f["volratio"][i6], False, "volume ratio"),
                                     ("ret3", f["ret3"][i6], True, "3-day return")):
            vtxt = f"{v:.2f}" if col in ("rsi", "z", "volratio") else pct(v, 1)
            lines.append(f"| {label} | {vtxt} | {pr(col, v, lower):.1f}% |")
    return "\n".join(lines), ix


def build_pairs(ev):
    m = masks(ev)
    pairs = [
        ("LIKELY vs other trigger events", ev.likely, ~ev.likely),
        ("LIKELY vs other PRICE-STRESSED", ev.likely, ev.stressed & ~ev.likely),
        ("D PASS vs D FAIL", m["D gate PASS"], m["D gate FAIL"]),
        ("FUNDAMENTAL-RISK vs other", ev.frisk, ~ev.frisk),
        ("PRICE-STRESSED vs not", ev.stressed, ~ev.stressed),
    ]
    pairs.append(("EXPLORATORY LIKELY(lenient D) vs other trigger events", ev.likely_len, ~ev.likely_len))
    pairs.append(("EXPLORATORY D-lenient PASS vs FAIL", ev.d_len.eq("PASS"), ev.d_len.eq("FAIL")))
    for k, nm in enumerate(A_NAMES):
        pairs.append((f"flag {nm}", ev[f"a{k + 1}"].eq(1), ev[f"a{k + 1}"].eq(0)))
    pairs.append(("flag own-history valuation <=20th pct", ev.c_val.eq(True), ev.c_val.eq(False)))
    pairs.append(("flag FCF yield >=5%", ev.c_fcf.eq(True), ev.c_fcf.eq(False)))
    for k in range(1, 6):
        pairs.append((f"A>={k} vs A<{k}", ev.a_count >= k, ev.a_count < k))
    return pairs


def make_tables(ev, tests, meta):
    m = masks(ev)
    L = []
    add = L.append
    add("## T1. Sample\n")
    add("| item | value |\n|---|---|")
    for k, v in meta["sample_rows"]:
        add(f"| {k} | {v} |")
    add("\n## T2. Event coverage by block\n")
    n = len(ev)
    cov = [("trigger events after de-clustering and split-artifact drop", n),
           ("Block A computable (all 5 price flags)", n),
           ("Block C own-history valuation flag known", int(ev.c_val.notna().sum())),
           ("current valuation metric computable: EV/EBITDA", int((ev.c_metric == "EV/EBITDA").sum())),
           ("current valuation metric computable: P/E (EBITDA unavailable)", int((ev.c_metric == "P/E").sum())),
           ("Block C FCF-yield flag known", int(ev.c_fcf.notna().sum())),
           ("Block D PASS", int(ev.d_state.eq("PASS").sum())),
           ("Block D FAIL", int(ev.d_state.eq("FAIL").sum())),
           ("Block D UNKNOWN (some test not computable, none failed)", int(ev.d_state.eq("UNKNOWN").sum())),
           ("Block D computable tests: net debt/EBITDA", int(ev.nd_ebitda.notna().sum())),
           ("Block D computable tests: EBIT/interest", int(ev.icr.notna().sum())),
           ("Block D computable tests: TTM FCF", int(ev.fcf_ttm.notna().sum())),
           ("[exploratory lenient D] PASS / FAIL / UNKNOWN",
            f"{int(ev.d_len.eq('PASS').sum())} / {int(ev.d_len.eq('FAIL').sum())} / {int(ev.d_len.eq('UNKNOWN').sum())}"),
           ("[exploratory lenient D] LIKELY", int(ev.likely_len.sum())),
           ("all of C (either flag) and D known", int((ev.c_known & ev.d_state.ne("UNKNOWN")).sum())),
           ("PRICE-STRESSED (A>=3)", int(ev.stressed.sum())),
           ("LIKELY-OVERSOLD-PROXY", int(ev.likely.sum())),
           ("FUNDAMENTAL-RISK", int(ev.frisk.sum())),
           ("market-wide-week events (>=10% of universe triggered that week)", int(ev.mkt_wide.sum())),
           ("idiosyncratic events", int(((~ev.mkt_wide) & (ev.spy_ret3 >= -0.03)).sum()))]
    add("| item | events |\n|---|---|")
    for k, v in cov:
        add(f"| {k} | {v} |")
    add("\n## T3. Forward excess return vs SPY (entry = next-day open), by state, all events\n")
    add(table_stats(ev, m, "ex"))
    add("\n† n < 15: no conclusions drawn.\n")
    add("\n## T4. Raw forward return (not market-adjusted), same entry\n")
    add(table_stats(ev, {k: m[k] for k in ("ALL trigger events", "LIKELY-OVERSOLD-PROXY", "FUNDAMENTAL-RISK (D fail)",
                                           "D gate PASS", "D gate FAIL", "[expl.] LIKELY with lenient D")}, "ret"))
    add("\n## T5. Forward return minus the same ticker's all-day unconditional average (matched baseline)\n")
    add(table_stats(ev, {k: m[k] for k in ("ALL trigger events", "LIKELY-OVERSOLD-PROXY", "D gate PASS", "D gate FAIL")},
                    "ab"))
    add("\n## T6. Entry at signal-day close (sensitivity), excess vs SPY\n")
    add(table_stats(ev, {k: m[k] for k in ("ALL trigger events", "LIKELY-OVERSOLD-PROXY", "D gate PASS", "D gate FAIL")},
                    "exc", horizons=(20, 60)))
    add("\n## T7. Maximum adverse excursion over the next 60 days (worst low vs entry open)\n")
    add("| group | n | median 3-day drop at signal | median MAE | mean MAE | p10 (worst decile) | share with MAE <= -20% |\n|---|---|---|---|---|---|---|")
    for name in ("ALL trigger events", "LIKELY-OVERSOLD-PROXY", "PRICE-STRESSED (A>=3)", "FUNDAMENTAL-RISK (D fail)",
                 "D gate PASS", "D gate FAIL"):
        x = ev.loc[m[name], "mae60"].dropna()
        flag = " †" if len(x) < MIN_N else ""
        if len(x):
            add(f"| {name} | {len(x)}{flag} | {pct(ev.loc[m[name], 'ret3'].median())} | {pct(x.median())} | "
                f"{pct(x.mean())} | {pct(x.quantile(.10))} | {pct_plain((x <= -0.20).mean())} |")
    add("\nMAE difference tests (A - B, positive = shallower drawdown for A; cluster bootstrap by month; "
        "these 12 tests are included in the T13 count):\n")
    add("| measure | A vs B | stat | nA | nB | diff | 95% CI | p |\n|---|---|---|---|---|---|---|---|")
    extra = 0
    for col, cname in (("mae60", "MAE"), ("mae60_n", "MAE / (daily sigma x sqrt 60)")):
        for lab, ma, mb in (("D PASS vs D FAIL", m["D gate PASS"], m["D gate FAIL"]),
                            ("LIKELY vs other trigger events", ev.likely, ~ev.likely),
                            ("PRICE-STRESSED vs not", ev.stressed, ~ev.stressed)):
            for stat in ("median", "mean"):
                extra += 1
                d, lo, hi, p = cluster_bootstrap_diff(ev.loc[ma, col].tolist(), ev.loc[ma, "month"].tolist(),
                                                      ev.loc[mb, col].tolist(), ev.loc[mb, "month"].tolist(),
                                                      stat, 1000, seed=9000 + extra)
                fmt = pct if col == "mae60" else (lambda v: f"{v:+.2f}")  # noqa: E731
                add(f"| {cname} | {lab} | {stat} | {int(ev.loc[ma, col].notna().sum())} | "
                    f"{int(ev.loc[mb, col].notna().sum())} | {fmt(d)} | [{fmt(lo)}, {fmt(hi)}] | {p:.3f} |")
    add("\n## T8. Headline comparisons (excess vs SPY, cluster bootstrap by calendar month)\n")
    for pair in ("LIKELY vs other trigger events", "LIKELY vs other PRICE-STRESSED", "D PASS vs D FAIL",
                 "FUNDAMENTAL-RISK vs other", "PRICE-STRESSED vs not",
                 "EXPLORATORY LIKELY(lenient D) vs other trigger events", "EXPLORATORY D-lenient PASS vs FAIL"):
        add(f"\n**{pair}** (all events, all years)\n")
        add(comp_table(tests, pair))
    add("\n## T9. LIKELY vs other trigger events by subset and period (medians and means, +20 and +60)\n")
    t = tests[(tests.pair == "LIKELY vs other trigger events") & tests.h.isin([20, 60])]
    add("| subset | period | h | stat | nA | nB | diff | 95% CI | p |\n|---|---|---|---|---|---|---|---|---|")
    for _, r in t.sort_values(["subset", "period", "h", "stat"]).iterrows():
        if pd.notna(r.get("diff")):
            add(f"| {r.subset} | {r.period} | +{r.h} | {r.stat} | {r.nA} | {r.nB} | {pct(r['diff'])} | "
                f"[{pct(r.lo)}, {pct(r.hi)}] | {r.p:.3f} |")
        else:
            add(f"| {r.subset} | {r.period} | +{r.h} | {r.stat} | {r.nA} | {r.nB} | n<{MIN_N} | | |")
    add("\n## T10. D PASS vs D FAIL by subset and period (+20 and +60)\n")
    t = tests[(tests.pair == "D PASS vs D FAIL") & tests.h.isin([20, 60])]
    add("| subset | period | h | stat | nA | nB | diff | 95% CI | p |\n|---|---|---|---|---|---|---|---|---|")
    for _, r in t.sort_values(["subset", "period", "h", "stat"]).iterrows():
        if pd.notna(r.get("diff")):
            add(f"| {r.subset} | {r.period} | +{r.h} | {r.stat} | {r.nA} | {r.nB} | {pct(r['diff'])} | "
                f"[{pct(r.lo)}, {pct(r.hi)}] | {r.p:.3f} |")
        else:
            add(f"| {r.subset} | {r.period} | +{r.h} | {r.stat} | {r.nA} | {r.nB} | n<{MIN_N} | | |")
    add("\n## T11. Single flags, flag=1 vs flag=0 (exploratory; all events, all years; median diff of excess)\n")
    add("| flag | n flag=1 | n flag=0 | +20 diff [CI] | +60 diff [CI] | +120 diff [CI] |\n|---|---|---|---|---|---|")
    labels = [f"flag {nm}" for nm in A_NAMES] + ["flag own-history valuation <=20th pct", "flag FCF yield >=5%"]
    for lab in labels:
        cells, n1, n0 = [], "", ""
        for h in (20, 60, 120):
            r = tests[(tests.pair == lab) & (tests.subset == "all events") & (tests.period == "all years")
                      & (tests.h == h) & (tests.stat == "median")]
            if r.empty:
                cells.append("")
                continue
            r = r.iloc[0]
            n1, n0 = r.nA, r.nB
            cells.append(f"{pct(r['diff'])} [{pct(r.lo)}, {pct(r.hi)}] p={r.p:.2f}" if pd.notna(r.get("diff"))
                         else f"n<{MIN_N}")
        add(f"| {lab[5:]} | {n1} | {n0} | " + " | ".join(cells) + " |")
    add("\n## T12. Sensitivity to the Block A threshold (exploratory; median diff of excess, A>=k vs A<k)\n")
    add("| k | n A>=k | n A<k | +20 diff [CI] | +60 diff [CI] | +120 diff [CI] |\n|---|---|---|---|---|---|")
    for k in range(1, 6):
        lab = f"A>={k} vs A<{k}"
        cells, n1, n0 = [], "", ""
        for h in (20, 60, 120):
            r = tests[(tests.pair == lab) & (tests.subset == "all events") & (tests.period == "all years")
                      & (tests.h == h) & (tests.stat == "median")]
            if r.empty:
                cells.append("")
                continue
            r = r.iloc[0]
            n1, n0 = r.nA, r.nB
            cells.append(f"{pct(r['diff'])} [{pct(r.lo)}, {pct(r.hi)}] p={r.p:.2f}" if pd.notna(r.get("diff"))
                         else f"n<{MIN_N}")
        add(f"| {k} | {n1} | {n0} | " + " | ".join(cells) + " |")
    run = tests[tests["diff"].notna()] if "diff" in tests else tests.iloc[0:0]
    N = len(run) + 12
    add("\n## T13. Multiple comparisons\n")
    add(f"- Bootstrap difference tests actually run (both cells n >= {MIN_N}; {len(run)} grid tests + 12 MAE tests): **{N}**")
    add(f"- Raw p < 0.05: **{int((run.p < 0.05).sum())}** (about {N * 0.05:.0f} expected by chance if nothing were real, "
        f"ignoring that the tests are strongly correlated)")
    add(f"- Bonferroni threshold 0.05/{N} = {0.05 / max(N, 1):.5f}: **{int((run.p < 0.05 / max(N, 1)).sum())}** pass "
        f"(bootstrap p-values cannot go below ~{1.0 / 1001:.3f}, so this cannot be passed by construction; uninformative)")
    pre = run[(run.pair == "LIKELY vs other trigger events") & (run.subset == "all events")
              & (run.period == "all years") & run.h.isin([20, 60])]
    add(f"- Pre-declared primary tests (LIKELY vs other, all events/years, +20 and +60, median and mean): "
        f"{len(pre)} tests, Bonferroni threshold {0.05 / max(len(pre), 1):.4f}, "
        f"passing: {int((pre.p < 0.05 / max(len(pre), 1)).sum())}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true", help="allow network (FMP within budget, EDGAR, S3/DynamoDB read)")
    ap.add_argument("--offline", action="store_true", help="cache only; zero network calls (default)")
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--sample-n", type=int, default=SAMPLE_N)
    args = ap.parse_args()
    offline = not args.fetch or args.offline
    fmp = FMP(offline=offline)
    CACHE.mkdir(parents=True, exist_ok=True)
    sample_path = CACHE / "sample.json"
    if sample_path.exists():
        sj = json.loads(sample_path.read_text())
        sample, cstats = sj["tickers"], sj["candidate_stats"]
    else:
        cands, cstats = build_candidates(fmp, offline)
        sample, rejected = draw_sample(cands, args.sample_n, SEED, offline)
        cstats["rejected_no_usable_edgar"] = rejected
        sample_path.write_text(json.dumps({"seed": SEED, "n_requested": args.sample_n, "tickers": sample,
                                           "candidate_stats": cstats}, indent=1))
    print(f"sample: {len(sample)} tickers; FMP calls so far: {fmp.calls_used()}", flush=True)
    frames, spy, missing = load_universe_data(fmp, sample, not offline)
    print(f"price frames: {len(frames)}; missing: {len(missing)}; FMP calls used: {fmp.calls_used()}", flush=True)
    ev, dropped, fundinfo, feats, week_frac, funds = run_events(sample, frames, spy, offline)
    ev.to_csv(CACHE / "events.csv", index=False)
    print(f"events: {len(ev)}; dropped as split artifacts: {sum(dropped.values())}", flush=True)
    tests = run_comparisons(ev, build_pairs(ev), args.n_boot)
    tests.to_csv(CACHE / "tests.csv", index=False)
    sec = {}
    for s in sample:
        sec[s["sector"]] = sec.get(s["sector"], 0) + 1
    sample_rows = [
        ("random seed", SEED), ("tickers requested / with price data", f"{args.sample_n} / {len(frames)}"),
        ("tickers with price data but missing in sample", len(missing)),
        ("candidate universe (step1 or DynamoDB, US listing, mcap >= $300M now, has CIK)", cstats.get("candidates")),
        ("FMP screener US >= $300M (current)", cstats.get("screener_us_ge300m")),
        ("sectors in sample", ", ".join(f"{k} {v}" for k, v in sorted(sec.items()))),
        ("price date range", f"{min(f.date.iloc[0] for f in frames.values())} to {max(f.date.iloc[-1] for f in frames.values())}"),
        ("event date range", f"{ev.date.min()} to {ev.date.max()}"),
        ("events (de-clustered, 20-day gap)", len(ev)),
        ("trigger events dropped as split artifacts", sum(dropped.values())),
        ("tickers with a detected split", sum(1 for v in fundinfo.values() if v["n_splits"])),
        ("tickers whose reconstructed market cap disagrees with current FMP cap (C-block off)",
         sum(1 for v in fundinfo.values() if not v["mcap_ok"])),
        ("pre-2020 / 2020+ events", f"{int((ev.year < 2020).sum())} / {int((ev.year >= 2020).sum())}"),
        ("FMP calls used (all-time, incl. probes)", fmp.calls_used()),
    ]
    tables = make_tables(ev, tests, {"sample_rows": sample_rows})
    if "HLIO" in frames:
        fr, f = frames["HLIO"], feats["HLIO"]
        hl, ix = hlio_section(ev, frames, feats, offline, {}, spy)
        tables += "\n\n## T14. HLIO against the distribution\n\n" + hl
        if "2026-11-06" in ix:
            i6 = ix["2026-11-06"]
            hist = valuation_history(funds["HLIO"], fr, month_grid(fr.date.tolist()))
            st = fundamental_state(funds["HLIO"], hist, "2026-11-06", fr.uc.iloc[i6],
                                   fundinfo["HLIO"]["mcap_ok"], f["a_flags"][i6])
            tables += ("\n\nHLIO block C/D computed by this script from EDGAR as of 2026-11-06: "
                       f"{json.dumps({k: (None if v is None or (isinstance(v, float) and not np.isfinite(v)) else (round(v, 3) if isinstance(v, float) else v)) for k, v in st.items()})}\n")
        sim = ev[ev.a_count >= 4]
        tables += "\n\nSimilar-stress events (A>=4 flags):\n\n" + table_stats(
            ev, {"A>=4 (all)": ev.a_count >= 4, "A>=4 and D PASS": (ev.a_count >= 4) & ev.d_state.eq("PASS"),
                 "z <= -5": ev.z <= -5}, "ex", horizons=(20, 60, 120))
    (CACHE / "report_tables.md").write_text(tables)
    print(f"tables written to {CACHE / 'report_tables.md'}; FMP calls used: {fmp.calls_used()}")


if __name__ == "__main__":
    main()
