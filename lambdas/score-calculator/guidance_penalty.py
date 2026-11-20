"""
Guidance penalty (Layer 3): turns the stored guidance_signal into a negative
investability adjustment. Negative only (no bonus for good guidance), so the
[0, 100] clamp in calculate_investability_score cannot be pushed above 100.

Why a separate term and not a sentiment adjustment: sentiment is averaged over
many articles, so one negative extraction would be diluted again by the positive
recaps of the same release (HLIO: 3 of 4 relevant articles were recaps).

penalty = -min(15, points) x confidence x decay, only when verdict == "negative".
decay = max(0, 1 - age_days / 30), age counted from the earnings event date.
"""

from datetime import date

MAX_POINTS = 15
DECAY_DAYS = 30
POINTS = (
    ("guidance_vs_prior", "lowered", 6),
    ("guidance_vs_consensus", "below", 5),
    ("margin_outlook", "down", 4),
    ("demand_outlook", "down", 3),
    ("one_time_or_pull_forward_flag", True, 2),
)


def guidance_age_days(signal: dict, today: str):
    """Days from the earnings event to `today` (>= 0), or None if the event date is unusable."""
    try:
        return max(0, (date.fromisoformat(today) - date.fromisoformat(signal["event_date"])).days)
    except (KeyError, TypeError, ValueError):
        return None


def guidance_is_current(signal, today: str) -> bool:
    """True while the signal can still move the score (inside the 30-day decay window)."""
    if not signal:
        return False
    age = guidance_age_days(signal, today)
    return age is not None and age < DECAY_DAYS


def guidance_penalty(signal, today: str) -> tuple:
    """Returns (penalty <= 0, detail). detail lists the scoring components for the breakdown."""
    detail = {"points": 0, "components": [], "confidence": 0.0, "decay": 0.0, "age_days": None}
    if not signal or signal.get("verdict") != "negative":
        return 0.0, detail
    age = guidance_age_days(signal, today)
    if age is None:
        return 0.0, detail

    components = [(name, value, pts) for name, value, pts in POINTS if signal.get(name) == value]
    points = min(MAX_POINTS, sum(pts for _, _, pts in components))
    confidence = min(1.0, max(0.0, float(signal.get("confidence") or 0.0)))
    decay = max(0.0, 1 - age / DECAY_DAYS)
    detail = {
        "points": points,
        "components": [name for name, _, _ in components],
        "confidence": confidence,
        "decay": round(decay, 3),
        "age_days": age,
    }
    return round(-points * confidence * decay, 2), detail
