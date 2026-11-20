"""
Integration tests: Step 7 persists news_price_divergence + sentiment counts into
LATEST (moto DynamoDB), leaves unflagged stocks without the field, keeps
tracking fields and canonical price untouched, and the API passes the new
fields through. Real HLIO bars/articles are used as fixtures.
"""
import json
import os
from decimal import Decimal

import boto3
import pytest

from conftest import load_handler

sc = load_handler("score-calculator")
api = load_handler("api")

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "hlio")


def _fixture(name):
    return json.load(open(os.path.join(FIXTURES, name)))


def _dec(obj):
    return json.loads(json.dumps(obj), parse_float=Decimal)


@pytest.fixture
def env(dynamodb_table, monkeypatch):
    monkeypatch.setattr(sc, "dynamodb", boto3.resource("dynamodb", region_name="us-east-2"))
    monkeypatch.setenv("DATA_TABLE_NAME", "stock-screener-data")
    monkeypatch.setattr(api, "dynamodb", boto3.resource("dynamodb", region_name="us-east-2"))
    monkeypatch.setattr(api, "TABLE_NAME", "stock-screener-data")
    return dynamodb_table


def _seed_bars(table, symbol, bars):
    table.put_item(Item={"PK": f"PRICE_HISTORY#{symbol}", "SK": "DAILY", "symbol": symbol,
                         "bars": _dec(bars)})


def _stock(symbol="HLIO", sentiment=0.727, scored=2, total=6, **extra):
    return {"symbol": symbol, "passes_screen": True, "price": 44.35, "price_as_of": "2026-11-19",
            "sentiment": {"sentiment_score": sentiment, "confidence": 0.8,
                          "scored_count": scored, "article_count": total, "risk_flags": []},
            "articles": [], **extra}


@pytest.mark.integration
def test_flag_written_to_latest_for_hlio(env):
    bars = [b for b in _fixture("hlio_price_bars.json") if b["d"] <= "2026-11-06"]
    _seed_bars(env, "HLIO", bars)
    env.put_item(Item={"PK": "STOCK#HLIO", "SK": "TRACKING", "symbol": "HLIO",
                       "tracking_status": "ACTIVE", "first_tracked": "2026-10-01",
                       "mark_price": Decimal("66.95"), "mark_date": "2026-11-04"})
    stocks = [_stock()]
    sc.attach_news_price_divergence(stocks, env)
    sc.persist_to_dynamodb(stocks, "2026-11-06")

    latest = env.get_item(Key={"PK": "STOCK#HLIO", "SK": "LATEST"})["Item"]
    div = latest["news_price_divergence"]
    assert div["flag"] is True and div["tier"] == "strong"
    assert float(div["return_3d"]) == pytest.approx(-0.3067, abs=1e-4)
    assert latest["sentiment_scored_count"] == 2 and latest["sentiment_article_count"] == 6
    # canonical price and tracking fields untouched by the new fields
    assert latest["price"] == Decimal("44.35") and latest["price_as_of"] == "2026-11-19"
    tracking = env.get_item(Key={"PK": "STOCK#HLIO", "SK": "TRACKING"})["Item"]
    assert tracking["mark_price"] == Decimal("66.95") and tracking["mark_date"] == "2026-11-04"
    assert tracking["first_tracked"] == "2026-10-01"


@pytest.mark.integration
def test_unflagged_stock_has_no_field_and_stale_flag_clears(env):
    _seed_bars(env, "CALM", [{"d": f"2026-10-{i + 1:02d}", "c": 50, "v": 100} for i in range(25)])
    # yesterday's LATEST carried a flag; today's run must not keep it
    env.put_item(Item={"PK": "STOCK#CALM", "SK": "LATEST", "symbol": "CALM",
                       "news_price_divergence": {"flag": True}})
    stocks = [_stock("CALM")]
    stocks[0]["news_price_divergence"] = {"flag": True}   # stale in-memory value must be dropped
    sc.attach_news_price_divergence(stocks, env)
    assert "news_price_divergence" not in stocks[0]
    sc.persist_to_dynamodb(stocks, "2026-11-06")
    latest = env.get_item(Key={"PK": "STOCK#CALM", "SK": "LATEST"})["Item"]
    assert "news_price_divergence" not in latest


@pytest.mark.integration
def test_missing_bars_or_sentiment_is_skipped(env):
    stocks = [_stock("NOBARS"), {"symbol": "NOSENT", "sentiment": {}}]
    sc.attach_news_price_divergence(stocks, env)
    assert all("news_price_divergence" not in s for s in stocks)


@pytest.mark.integration
def test_api_passes_divergence_and_article_flags_through(env):
    env.put_item(Item=_dec({
        "PK": "STOCK#HLIO", "SK": "LATEST", "symbol": "HLIO",
        "news_price_divergence": {"flag": True, "tier": "strong", "return_3d": -0.3067,
                                  "return_1d": -0.2105, "volume_ratio": 6.86, "sentiment": 0.727,
                                  "as_of": "2026-11-06", "trigger_date": "2026-11-06"},
        "sentiment_scored_count": 2, "sentiment_article_count": 6}))
    env.put_item(Item=_dec({
        "PK": "STOCK#HLIO", "SK": "ARTICLES", "symbol": "HLIO",
        "articles": [{"title": "t", "scored": False, "duplicate_of": "u", "relevant": True}]}))
    stock = json.loads(api.get_stock_detail("hlio")["body"])["stock"]
    assert stock["news_price_divergence"]["return_3d"] == -0.3067
    assert stock["sentiment_scored_count"] == 2 and stock["sentiment_article_count"] == 6
    arts = json.loads(api.get_stock_news("hlio")["body"])["articles"]
    assert arts[0]["scored"] is False and arts[0]["duplicate_of"] == "u"


@pytest.mark.integration
def test_api_list_includes_divergence(env):
    env.put_item(Item=_dec({
        "PK": "STOCK#HLIO", "SK": "LATEST", "symbol": "HLIO", "tracking_status": "ACTIVE",
        "last_updated": "2026-11-06T22:00:00+00:00", "investability_score": 55,
        "news_price_divergence": {"flag": True, "tier": "strong", "return_3d": -0.3067}}))
    body = json.loads(api.get_stocks()["body"])
    hlio = next(s for s in body["stocks"] if s["symbol"] == "HLIO")
    assert hlio["news_price_divergence"]["tier"] == "strong"
