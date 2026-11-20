"""
Integration tests (moto DynamoDB): guidance_signal in LATEST/SCORE# (Step 7), carry-forward
across runs, Decimal round-trip, item size, tracking fields untouched, and the API/news-fetcher
passing the new fields through.
Regression: LATEST is a whole-item put_item, so a signal written on earnings day would be
erased by the next day's run unless Step 7 carries it forward.
"""
import json
import os
from decimal import Decimal

import boto3
import pytest

from conftest import load_handler

sc = load_handler("score-calculator")
api = load_handler("api")

SIGNAL = {
    "guidance_vs_prior": "not_stated", "guidance_vs_consensus": "below", "margin_outlook": "down",
    "demand_outlook": "not_stated", "one_time_or_pull_forward_flag": True,
    "analyst_actions": [{"firm": "Stifel", "action": "target cut", "target": "$80"}],
    "key_quotes_or_numbers": ["Q4 sales guide below consensus"],
    "evidence_fields_present": ["guidance_vs_consensus", "margin_outlook", "one_time_or_pull_forward_flag"],
    "verdict": "negative", "confidence": 0.8, "event_key": "HLIO:2026-11-05", "event_date": "2026-11-05",
    "extracted_at": "2026-11-05T22:30:00+00:00", "extracted_on": "2026-11-05", "extraction_count": 1,
    "input_chars": 12000, "input_titles": ["Helios (HLIO) Q3 2026 Earnings Call Transcript"], "model": "m",
}


def _dec(obj):
    return json.loads(json.dumps(obj), parse_float=Decimal)


@pytest.fixture
def env(dynamodb_table, monkeypatch):
    monkeypatch.setattr(sc, "dynamodb", boto3.resource("dynamodb", region_name="us-east-2"))
    monkeypatch.setenv("DATA_TABLE_NAME", "stock-screener-data")
    monkeypatch.setattr(api, "dynamodb", boto3.resource("dynamodb", region_name="us-east-2"))
    monkeypatch.setattr(api, "TABLE_NAME", "stock-screener-data")
    return dynamodb_table


def _stock(**extra):
    return {"symbol": "HLIO", "passes_screen": True, "price": 44.35, "price_as_of": "2026-11-19",
            "sentiment": {"sentiment_score": 0.7, "confidence": 0.8, "scored_count": 2, "article_count": 6,
                          "risk_flags": []}, "articles": [], **extra}


def _seed_tracking(table):
    table.put_item(Item={"PK": "STOCK#HLIO", "SK": "TRACKING", "symbol": "HLIO", "tracking_status": "ACTIVE",
                         "first_tracked": "2026-10-01", "mark_price": Decimal("66.95"), "mark_date": "2026-11-04",
                         "last_passed": "2026-11-04"})


@pytest.mark.integration
def test_extraction_day_writes_latest_and_dated_copy_and_keeps_tracking(env):
    _seed_tracking(env)
    sc.persist_to_dynamodb([_stock(guidance_signal=SIGNAL)], "2026-11-05")
    latest = env.get_item(Key={"PK": "STOCK#HLIO", "SK": "LATEST"})["Item"]
    assert latest["guidance_signal"]["guidance_vs_consensus"] == "below"
    assert latest["guidance_signal"]["confidence"] == Decimal("0.8")           # float -> Decimal round trip
    score = env.get_item(Key={"PK": "STOCK#HLIO", "SK": "SCORE#2026-11-05"})["Item"]
    assert score["guidance_signal"]["event_key"] == "HLIO:2026-11-05"
    tracking = env.get_item(Key={"PK": "STOCK#HLIO", "SK": "TRACKING"})["Item"]
    assert tracking["mark_price"] == Decimal("66.95") and tracking["mark_date"] == "2026-11-04"
    assert tracking["first_tracked"] == "2026-10-01"
    assert latest["price"] == Decimal("44.35") and latest["price_as_of"] == "2026-11-19"


@pytest.mark.integration
def test_carried_signal_is_in_latest_but_not_copied_to_a_new_dated_score(env):
    stock = _stock(guidance_signal=SIGNAL)          # extracted_on 2026-11-05, run date 2026-11-06
    sc.persist_to_dynamodb([stock], "2026-11-06")
    assert "guidance_signal" in env.get_item(Key={"PK": "STOCK#HLIO", "SK": "LATEST"})["Item"]
    assert "guidance_signal" not in env.get_item(Key={"PK": "STOCK#HLIO", "SK": "SCORE#2026-11-06"})["Item"]


@pytest.mark.regression
def test_signal_survives_the_next_run_through_carry_forward(env):
    sc.persist_to_dynamodb([_stock(guidance_signal=SIGNAL)], "2026-11-05")
    next_run = [_stock()]                           # Step 6 extracts nothing the next day
    sc._carry_forward_guidance(env, next_run, "2026-11-06")
    assert next_run[0]["guidance_signal"]["guidance_vs_consensus"] == "below"
    assert isinstance(next_run[0]["guidance_signal"]["confidence"], float)    # no Decimals in the pipeline payload
    json.dumps(next_run[0]["guidance_signal"])
    sc.persist_to_dynamodb(next_run, "2026-11-06")
    assert "guidance_signal" in env.get_item(Key={"PK": "STOCK#HLIO", "SK": "LATEST"})["Item"]


@pytest.mark.integration
def test_new_extraction_wins_over_stored_and_expired_signal_is_dropped(env):
    sc.persist_to_dynamodb([_stock(guidance_signal=SIGNAL)], "2026-11-05")
    fresh = _stock(guidance_signal={**SIGNAL, "verdict": "neutral"})
    sc._carry_forward_guidance(env, [fresh], "2026-11-06")
    assert fresh["guidance_signal"]["verdict"] == "neutral"
    expired = [_stock()]
    sc._carry_forward_guidance(env, expired, "2026-12-05")                     # day 30
    assert "guidance_signal" not in expired[0]
    nothing = [{"symbol": "NEW", "sentiment": {}}]
    sc._carry_forward_guidance(env, nothing, "2026-11-06")
    assert "guidance_signal" not in nothing[0]


@pytest.mark.integration
def test_latest_item_with_signal_stays_small(env):
    sc.persist_to_dynamodb([_stock(guidance_signal=SIGNAL)], "2026-11-05")
    item = env.get_item(Key={"PK": "STOCK#HLIO", "SK": "LATEST"})["Item"]
    size = len(json.dumps(item, default=str))
    assert size < 20_000 and len(json.dumps(item["guidance_signal"], default=str)) < 4_000


@pytest.mark.integration
def test_end_to_end_penalty_reaches_latest_investability(env):
    plain = sc.calculate_investability_score(_stock(fundamental_score=60.0), [], "2026-11-06")
    hit = sc.calculate_investability_score(_stock(fundamental_score=60.0, guidance_signal=SIGNAL), [], "2026-11-06")
    sc.persist_to_dynamodb([hit], "2026-11-06")
    latest = env.get_item(Key={"PK": "STOCK#HLIO", "SK": "LATEST"})["Item"]
    assert float(latest["investability_score"]) < plain["investability_score"]


@pytest.mark.integration
def test_unscored_article_is_stored_with_raw_snippet_not_relied_on_summary(env):
    arts = [
        {"title": "Scored", "published_at": "2026-11-06 10:00:00", "description": "x" * 500,
         "analysis": {"relevant": True, "sentiment": 0.5, "confidence": 0.8, "summary": "ok", "risk_flags": []}},
        {"title": "Transcript", "published_at": "2026-11-05 10:00:00", "description": "t" * 500,
         "analysis": {"relevant": False, "sentiment": 0.0, "confidence": 0.3,
                      "summary": "A scheduling announcement", "risk_flags": []}},
    ]
    sc.persist_to_dynamodb([_stock(articles=arts)], "2026-11-06")
    stored = {a["title"]: a for a in env.get_item(Key={"PK": "STOCK#HLIO", "SK": "ARTICLES"})["Item"]["articles"]}
    assert stored["Scored"]["scored"] is True and "snippet" not in stored["Scored"]
    assert stored["Transcript"]["scored"] is False
    assert stored["Transcript"]["snippet"] == "t" * sc.UNSCORED_SNIPPET_CHARS
    assert stored["Transcript"]["confidence"] == Decimal("0.3")


@pytest.mark.integration
def test_api_returns_guidance_and_news_fields_as_floats(env):
    env.put_item(Item=_dec({
        "PK": "STOCK#HLIO", "SK": "LATEST", "symbol": "HLIO", "tracking_status": "ACTIVE",
        "last_updated": "2026-11-06T22:00:00+00:00", "investability_score": 47.2, "guidance_signal": SIGNAL}))
    env.put_item(Item=_dec({
        "PK": "STOCK#HLIO", "SK": "ARTICLES", "symbol": "HLIO",
        "articles": [{"title": "t", "scored": False, "relevant": False, "confidence": 0.3, "snippet": "s"}]}))
    listed = next(s for s in json.loads(api.get_stocks()["body"])["stocks"] if s["symbol"] == "HLIO")
    detail = json.loads(api.get_stock_detail("hlio")["body"])["stock"]
    for stock in (listed, detail):
        assert stock["guidance_signal"]["confidence"] == 0.8
        assert stock["guidance_signal"]["guidance_vs_consensus"] == "below"
    art = json.loads(api.get_stock_news("hlio")["body"])["articles"][0]
    assert art["scored"] is False and art["relevant"] is False and art["confidence"] == 0.3
    assert art["snippet"] == "s"
