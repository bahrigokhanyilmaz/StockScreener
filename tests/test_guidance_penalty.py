"""
Unit tests for lambdas/score-calculator/guidance_penalty.py and its use in
calculate_investability_score. Guidance only ever subtracts, so [0, 100] must hold.
"""
import pytest

from conftest import load_handler

sc = load_handler("score-calculator")
import guidance_penalty as gp  # noqa: E402

HLIO_SIGNAL = {
    "guidance_vs_prior": "not_stated", "guidance_vs_consensus": "below", "margin_outlook": "down",
    "demand_outlook": "not_stated", "one_time_or_pull_forward_flag": True,
    "verdict": "negative", "confidence": 0.8, "event_date": "2026-11-05",
}


def signal(**over):
    return {**HLIO_SIGNAL, **over}


@pytest.mark.unit
@pytest.mark.parametrize("field,value,points", [
    ("guidance_vs_prior", "lowered", 6),
    ("guidance_vs_consensus", "below", 5),
    ("margin_outlook", "down", 4),
    ("demand_outlook", "down", 3),
    ("one_time_or_pull_forward_flag", True, 2),
])
def test_component_points(field, value, points):
    base = {"verdict": "negative", "confidence": 1.0, "event_date": "2026-11-05"}
    penalty, detail = gp.guidance_penalty({**base, field: value}, "2026-11-05")
    assert penalty == -points and detail["points"] == points and detail["components"] == [field]


@pytest.mark.unit
def test_non_negative_components_score_nothing():
    quiet = {"verdict": "negative", "confidence": 1.0, "event_date": "2026-11-05", "guidance_vs_prior": "narrowed",
             "guidance_vs_consensus": "in_line", "margin_outlook": "up", "demand_outlook": "flat",
             "one_time_or_pull_forward_flag": False}
    assert gp.guidance_penalty(quiet, "2026-11-05")[0] == 0


@pytest.mark.unit
def test_points_are_capped_at_15():
    everything = signal(guidance_vs_prior="lowered", demand_outlook="down", confidence=1.0)   # 6+5+4+3+2 = 20
    penalty, detail = gp.guidance_penalty(everything, "2026-11-05")
    assert detail["points"] == 15 and penalty == -15


@pytest.mark.unit
def test_hlio_example_confidence_and_decay():
    # 5 + 4 + 2 = 11 points x 0.8 confidence
    assert gp.guidance_penalty(HLIO_SIGNAL, "2026-11-05")[0] == pytest.approx(-8.8)
    day4, detail = gp.guidance_penalty(HLIO_SIGNAL, "2026-11-09")
    assert day4 == pytest.approx(-11 * 0.8 * (1 - 4 / 30), abs=0.01) and detail["age_days"] == 4
    assert gp.guidance_penalty(HLIO_SIGNAL, "2026-11-19")[0] == pytest.approx(-11 * 0.8 * (1 - 14 / 30), abs=0.01)
    assert gp.guidance_penalty(HLIO_SIGNAL, "2026-12-05")[0] == 0           # day 30
    assert gp.guidance_penalty(HLIO_SIGNAL, "2026-12-20")[0] == 0           # day 45


@pytest.mark.unit
def test_non_negative_verdict_or_missing_signal_gives_zero():
    for verdict in ("neutral", "positive", None):
        assert gp.guidance_penalty(signal(verdict=verdict), "2026-11-06")[0] == 0   # components say bad, verdict disagrees
    assert gp.guidance_penalty(None, "2026-11-06")[0] == 0
    assert gp.guidance_penalty({}, "2026-11-06")[0] == 0
    assert gp.guidance_penalty(signal(event_date="garbage"), "2026-11-06")[0] == 0


@pytest.mark.unit
def test_future_event_date_counts_as_age_zero_and_confidence_is_clamped():
    assert gp.guidance_penalty(signal(confidence=5), "2026-11-04")[0] == pytest.approx(-11)
    assert gp.guidance_penalty(signal(confidence=None), "2026-11-05")[0] == 0


@pytest.mark.unit
def test_is_current_window():
    assert gp.guidance_is_current(HLIO_SIGNAL, "2026-12-04")
    assert not gp.guidance_is_current(HLIO_SIGNAL, "2026-12-05")
    assert not gp.guidance_is_current(None, "2026-11-05")
    assert not gp.guidance_is_current({"event_date": "x"}, "2026-11-05")


def _stock(fundamental=60.0, sentiment=0.7, conf=0.8, comp=3, **extra):
    return {"symbol": "HLIO", "fundamental_score": fundamental,
            "sentiment": {"sentiment_score": sentiment, "confidence": conf, "risk_flags": []},
            "competition": {"competition_score": comp}, **extra}


@pytest.mark.integration
def test_investability_drops_by_the_guidance_penalty_and_shows_in_breakdown():
    without = sc.calculate_investability_score(_stock(), [], "2026-11-06")
    with_g = sc.calculate_investability_score(_stock(guidance_signal=HLIO_SIGNAL), [], "2026-11-06")
    penalty = with_g["score_breakdown"]["guidance_penalty"]
    assert penalty < 0
    assert with_g["investability_score"] == pytest.approx(without["investability_score"] + penalty, abs=0.1)
    assert with_g["score_breakdown"]["guidance_detail"]["components"] == [
        "guidance_vs_consensus", "margin_outlook", "one_time_or_pull_forward_flag"]
    assert without["score_breakdown"]["guidance_penalty"] == 0


@pytest.mark.integration
@pytest.mark.parametrize("fundamental,sentiment,conf,comp", [
    (100, 1.0, 1.0, 1), (0, -1.0, 1.0, 5), (0, 0.0, 0.0, 5), (100, -1.0, 1.0, 1), (5, 1.0, 1.0, 5)])
def test_score_stays_within_0_100_for_extreme_inputs(fundamental, sentiment, conf, comp):
    worst = signal(guidance_vs_prior="lowered", demand_outlook="down", confidence=1.0)
    for sig in (None, worst):
        stock = _stock(fundamental, sentiment, conf, comp, revenue_accel_underpriced=True)
        if sig:
            stock["guidance_signal"] = sig
        assert 0 <= sc.calculate_investability_score(stock, [], "2026-11-05")["investability_score"] <= 100


@pytest.mark.integration
def test_positive_guidance_gives_no_bonus():
    good = signal(verdict="positive", guidance_vs_prior="raised", guidance_vs_consensus="above",
                  margin_outlook="up", one_time_or_pull_forward_flag=False)
    base = sc.calculate_investability_score(_stock(), [], "2026-11-06")["investability_score"]
    assert sc.calculate_investability_score(_stock(guidance_signal=good), [], "2026-11-06")["investability_score"] == base
