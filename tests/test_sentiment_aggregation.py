"""
Unit + regression tests for sentiment aggregation (cleanup b) and the prompt (cleanup a).

HLIO (Nov 2026): the stored articles contained the same recap twice
("Q3 Earnings Call Highlights", MarketBeat Nov 7 and Defense World Nov 10),
there was no recency weighting, and the prompt marked transcripts and
analyst notes irrelevant for being short.
"""
import json
import os
import re
from datetime import datetime, timezone

import pytest

from conftest import load_handler

sa = load_handler("sentiment-analyzer")

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "hlio")
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def art(title, sentiment, conf=0.8, days_ago=0, relevant=True, source="Zacks", flags=None, published="auto"):
    if published == "auto":
        dt = datetime.fromtimestamp(NOW.timestamp() - days_ago * 86400, tz=timezone.utc)
        published = dt.strftime("%Y-%m-%d %H:%M:%S")
    return {"title": title, "source": source, "published_at": published,
            "analysis": {"relevant": relevant, "sentiment": sentiment, "confidence": conf,
                         "summary": "", "risk_flags": flags or []}}


def hlio_articles():
    stored = json.load(open(os.path.join(FIXTURES, "hlio_articles_stored.json")))
    return [{"title": a["title"], "source": a["source"], "published_at": a["published_at"],
             "analysis": {"relevant": a["relevant"], "sentiment": a["sentiment"],
                          "confidence": a["confidence"], "summary": a["summary"],
                          "risk_flags": a["risk_flags"]}} for a in stored]


@pytest.mark.regression
def test_hlio_duplicate_recap_counts_once():
    arts = hlio_articles()
    agg = sa.calculate_aggregate_sentiment(arts, now=NOW)
    recaps = [a for a in arts if "Q3 Earnings Call Highlights" in a["title"]]
    assert len(recaps) == 2
    assert sorted(a["analysis"]["counted"] for a in recaps) == [False, True]
    dropped = next(a for a in recaps if not a["analysis"]["counted"])
    assert dropped["analysis"]["duplicate_of"] == "Helios Technologies Q3 Earnings Call Highlights"
    assert agg["relevant_count"] == 3 and agg["scored_count"] == 2 and agg["article_count"] == 6
    assert agg["positive_count"] == 2


@pytest.mark.regression
def test_guidance_article_pulls_hlio_aggregate_down():
    base = sa.calculate_aggregate_sentiment(hlio_articles(), now=NOW)
    with_guide = hlio_articles() + [art("Helios Q4 sales guide below consensus", -0.65, conf=0.8, days_ago=2)]
    agg = sa.calculate_aggregate_sentiment(with_guide, now=NOW)
    assert agg["sentiment_score"] < base["sentiment_score"]
    assert agg["sentiment_score"] < 0.3


@pytest.mark.unit
def test_recency_half_life_weights():
    # Two opposite articles, same confidence: the fresher one dominates.
    agg = sa.calculate_aggregate_sentiment(
        [art("old good", 1.0, days_ago=8), art("new bad", -1.0, days_ago=0)], now=NOW)
    # weights 0.25 vs 1.0 -> (0.25 - 1.0) / 1.25 = -0.6
    assert agg["sentiment_score"] == pytest.approx(-0.6, abs=1e-3)
    w = lambda d: sa._article_weight(art("x", 0, conf=1.0, days_ago=d), NOW)
    assert w(0) == pytest.approx(1.0) and w(4) == pytest.approx(0.5)
    assert w(3) == pytest.approx(0.595, abs=1e-3) and w(6) == pytest.approx(0.354, abs=1e-3)
    assert w(400) == pytest.approx(sa.RECENCY_FLOOR)


@pytest.mark.unit
def test_undated_and_future_articles():
    assert sa._article_weight(art("x", 0, conf=1.0, published=None), NOW) == pytest.approx(0.5 ** (7 / 4))
    assert sa._article_weight(art("x", 0, conf=1.0, published="garbage"), NOW) == pytest.approx(0.5 ** (7 / 4))
    assert sa._article_weight(art("x", 0, conf=1.0, days_ago=-1), NOW) == pytest.approx(1.0)  # future skew


@pytest.mark.unit
def test_wire_downweighted_unless_risk_flag():
    wire = art("Co announces record year", 1.0, source="Business Wire")
    indep = art("Analyst sees trouble", -1.0, source="Reuters")
    agg = sa.calculate_aggregate_sentiment([wire, indep], now=NOW)
    assert agg["sentiment_score"] == pytest.approx((0.5 - 1.0) / 1.5, abs=1e-3)
    flagged = art("Co discloses SEC probe", -1.0, source="GlobeNewswire", flags=["SEC_investigation"])
    assert sa._article_weight(flagged, NOW) == pytest.approx(0.8)
    assert sa._article_weight(art("x", 0, source="PR Newswire"), NOW) == pytest.approx(0.4)


@pytest.mark.unit
def test_confidence_is_weighted_mean_and_counts():
    agg = sa.calculate_aggregate_sentiment(
        [art("a story one", 0.5, conf=1.0), art("different thing two", -0.5, conf=0.5)], now=NOW)
    # weights 1.0 and 0.5: confidence = (1.0*1.0 + 0.5*0.5) / 1.5
    assert agg["confidence"] == pytest.approx(1.25 / 1.5, abs=1e-3)
    assert agg["sentiment_score"] == pytest.approx((0.5 - 0.25) / 1.5, abs=1e-3)
    assert (agg["positive_count"], agg["negative_count"], agg["neutral_count"]) == (1, 1, 0)


@pytest.mark.unit
def test_empty_and_all_irrelevant():
    empty = sa.calculate_aggregate_sentiment([], now=NOW)
    assert empty["sentiment_score"] == 0.0 and empty["confidence"] == 0.0
    assert empty["article_count"] == 0 and empty["scored_count"] == 0
    arts = [art("a", 0.9, relevant=False), art("b", -0.9, relevant=False)]
    agg = sa.calculate_aggregate_sentiment(arts, now=NOW)
    assert agg["sentiment_score"] == 0.0 and agg["article_count"] == 2
    assert agg["relevant_count"] == 0 and agg["scored_count"] == 0
    assert all(a["analysis"]["counted"] is False for a in arts)


@pytest.mark.unit
def test_zero_confidence_articles_do_not_divide_by_zero():
    agg = sa.calculate_aggregate_sentiment([art("a", 0.9, conf=0.0)], now=NOW)
    assert agg["sentiment_score"] == 0.0 and agg["confidence"] == 0.0


@pytest.mark.unit
def test_duplicate_does_not_hide_risk_flag():
    a = art("Same lawsuit story", -0.5, conf=0.9, flags=[])
    b = art("Same lawsuit story", -0.5, conf=0.5, flags=["lawsuit"], days_ago=1)
    agg = sa.calculate_aggregate_sentiment([a, b], now=NOW)
    assert agg["scored_count"] == 1 and agg["risk_flags"] == ["lawsuit"]
    assert agg["risk_flags_with_dates"][0]["article_date"] == "2026-11-09"


@pytest.mark.unit
def test_irrelevant_article_does_not_suppress_relevant_duplicate():
    arts = [art("Q3 results out", 0.6, relevant=False), art("Q3 results out", 0.6)]
    agg = sa.calculate_aggregate_sentiment(arts, now=NOW)
    assert agg["scored_count"] == 1


# --- prompt (cleanup a) -------------------------------------------------------

@pytest.mark.regression
def test_prompt_no_longer_marks_stubs_and_analyst_notes_irrelevant():
    p = sa.SENTIMENT_PROMPT
    assert "or stub articles with no real content" not in p
    assert "earnings call transcripts" in p and "price-target notes are ALWAYS relevant" in p
    assert "low confidence" in p


@pytest.mark.unit
def test_prompt_has_negative_anchors_and_wire_clause():
    p = sa.SENTIMENT_PROMPT
    for phrase in ("guidance below consensus", "margin compression", "price target cut",
                   "analyst downgrade", "promotional", "ONLY if the text states the consensus"):
        assert phrase in p


@pytest.mark.unit
def test_prompt_formats_and_keeps_json_contract():
    out = sa.SENTIMENT_PROMPT.format(ticker="HLIO", company_name="Helios", title="t", source="s", description="d")
    assert "Article title: t" in out
    keys = re.findall(r'^\s+"(\w+)":', out, flags=re.M)
    assert keys == ["relevant", "sentiment", "confidence", "summary", "risk_flags"]
