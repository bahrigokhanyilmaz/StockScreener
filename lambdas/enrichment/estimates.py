"""
Analyst-estimate snapshot helpers — pure functions, no I/O.

build_estimates_snapshot() condenses the FMP analyst-estimates and
price-target-summary payloads (already fetched by enrichment) into a compact
map that is stored daily so estimate REVISIONS can be measured later.

eps_revision_pct() compares two snapshots by fiscal period end (not position),
so a fiscal-year rollover compares like with like.

Missing data is None, never 0. A genuine EPS of 0.0 is kept.

This is the copy of record. It is vendored byte-identical into
lambdas/enrichment/ and lambdas/score-calculator/.
"""


def _num(v):
    """float(v) for real numbers (bool excluded); None otherwise."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _count(v):
    n = _num(v)
    return int(n) if n is not None else None


def _period_end(entry):
    d = entry.get("date") if isinstance(entry, dict) else None
    return d[:10] if isinstance(d, str) and len(d) >= 10 else None


def has_estimate_data(snapshot) -> bool:
    """True if the snapshot carries any real value besides its fetched_at stamp."""
    return isinstance(snapshot, dict) and any(
        v is not None for k, v in snapshot.items() if k != "fetched_at")


def build_estimates_snapshot(estimates, targets, fetched_at: str) -> dict:
    """
    estimates: list of future annual estimates, nearest first (fy1 = [0], fy2 = [1]).
    targets:   FMP price-target-summary dict.
    fetched_at: ISO UTC timestamp of the FMP fetch.
    """
    ests = estimates if isinstance(estimates, list) else []
    tg = targets if isinstance(targets, dict) else {}

    def entry(i):
        return ests[i] if i < len(ests) and isinstance(ests[i], dict) else {}

    fy1, fy2 = entry(0), entry(1)

    # Target window mirrors enrich_with_fmp: quarter if truthy, else year.
    if tg.get("lastQuarterAvgPriceTarget"):
        avg, cnt, window = tg.get("lastQuarterAvgPriceTarget"), tg.get("lastQuarterCount"), "last_quarter"
    elif tg.get("lastYearAvgPriceTarget"):
        avg, cnt, window = tg.get("lastYearAvgPriceTarget"), tg.get("lastYearCount"), "last_year"
    else:
        avg, cnt, window = None, None, None
    avg = _num(avg)
    if avg is None:
        cnt, window = None, None

    return {
        "fetched_at": fetched_at,
        "eps_fy1": _num(fy1.get("epsAvg")),
        "eps_fy1_period_end": _period_end(fy1),
        "eps_fy1_analysts": _count(fy1.get("numAnalystsEps")),
        "eps_fy2": _num(fy2.get("epsAvg")),
        "eps_fy2_period_end": _period_end(fy2),
        "eps_fy2_analysts": _count(fy2.get("numAnalystsEps")),
        "revenue_fy1": _num(fy1.get("revenueAvg")),
        "target_price_avg": avg,
        "target_price_count": _count(cnt),
        "target_price_window": window,
    }


def eps_revision_pct(prev, curr) -> dict:
    """
    Percent change in forward EPS between two snapshots, matched by period end.
    Returns {"fy1": pct|None, "fy2": pct|None}. Never raises.
    """
    out = {"fy1": None, "fy2": None}
    if not isinstance(prev, dict) or not isinstance(curr, dict):
        return out
    try:
        prev_by_period = {}
        for k in ("fy1", "fy2"):
            pe = prev.get(f"eps_{k}_period_end")
            if pe:
                prev_by_period[pe] = prev.get(f"eps_{k}")
        for k in ("fy1", "fy2"):
            pe = curr.get(f"eps_{k}_period_end")
            if not pe or pe not in prev_by_period:
                continue
            base, now = _num(prev_by_period[pe]), _num(curr.get(f"eps_{k}"))
            if base is None or now is None or base <= 0:
                continue
            out[k] = round((now - base) / base * 100, 2)
    except Exception:
        return {"fy1": None, "fy2": None}
    return out
