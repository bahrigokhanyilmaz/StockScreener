"""
Price-vs-news divergence (advisory, never gates the screen).

Flags a stock whose news sentiment is clearly positive while its price has just
dropped sharply. Computed from the stored FINAL daily bars only.

Bars are ascending dicts: {"d": "YYYY-MM-DD", "c": close, "v": volume, ...}.
Returns are fractions (-0.307 = -30.7%).
"""

SENTIMENT_MIN = 0.3          # sentiment_score must be above this ("clearly positive")
STRONG_RETURN_3D = -0.10     # 3-session return at or below this: strong tier
MILD_RETURN_3D = -0.07       # at or below this AND volume_ratio >= MILD_VOLUME_RATIO: mild tier
MILD_VOLUME_RATIO = 2.0
LOOKBACK = 5                 # rolling 3-session windows examined, newest first
BASELINE_BARS = 20           # volume baseline: the 20 bars before the window
MIN_BASELINE_BARS = 10


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _volume_ratio(bars: list, i: int):
    """max volume of bars i-2..i over the mean of the prior 20 bars; None if baseline thin."""
    baseline = [b.get("v") for b in bars[max(0, i - 2 - BASELINE_BARS):i - 2]]
    baseline = [v for v in baseline if _num(v) and v > 0]
    if len(baseline) < MIN_BASELINE_BARS:
        return None
    window = [b.get("v") for b in bars[i - 2:i + 1] if _num(b.get("v"))]
    if not window:
        return None
    return max(window) / (sum(baseline) / len(baseline))


def news_price_divergence(bars: list, sentiment_score, lookback: int = LOOKBACK):
    """
    Return the divergence dict when positive news meets a sharp price drop, else None.

    Examines the last `lookback` rolling 3-session windows (so a flag stays lit for
    several sessions after the drop). Picks the strongest qualifying window: strong
    tier first, then the lowest 3-day return.
    """
    if not _num(sentiment_score) or sentiment_score <= SENTIMENT_MIN:
        return None
    bars = [b for b in (bars or []) if isinstance(b, dict) and _num(b.get("c")) and b["c"] > 0
            and isinstance(b.get("d"), str)]
    if len(bars) < 4:
        return None

    best = None
    for i in range(max(3, len(bars) - lookback), len(bars)):
        r3 = round(bars[i]["c"] / bars[i - 3]["c"] - 1, 6)  # rounded so -10.0% sits exactly on the threshold
        ratio = _volume_ratio(bars, i)
        if r3 <= STRONG_RETURN_3D:
            tier = "strong"
        elif r3 <= MILD_RETURN_3D and ratio is not None and ratio >= MILD_VOLUME_RATIO:
            tier = "mild"
        else:
            continue
        key = (tier == "strong", -r3)
        if best is None or key > best[0]:
            best = (key, tier, r3, ratio, bars[i]["d"])

    if best is None:
        return None
    _, tier, r3, ratio, trigger_date = best
    return {
        "flag": True,
        "tier": tier,
        "return_3d": round(r3, 4),
        "return_1d": round(bars[-1]["c"] / bars[-2]["c"] - 1, 4),
        "volume_ratio": round(ratio, 2) if ratio is not None else None,
        "sentiment": round(float(sentiment_score), 3),
        "as_of": bars[-1]["d"],
        "trigger_date": trigger_date,
    }
