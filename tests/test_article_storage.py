"""
Regression + unit tests for Step 7 article storage (cleanup d).

Bug: persist_to_dynamodb stored articles[:10] in Bedrock completion order, so
the dashboard could show old or irrelevant articles and drop the newest ones
(MU had 27). The stock score is computed upstream from ALL articles.
"""
import boto3
import pytest

from conftest import load_handler

sc = load_handler("score-calculator")
sa = load_handler("sentiment-analyzer")


def art(i, day, relevant=True, counted=None):
    analysis = {"relevant": relevant, "sentiment": 0.5, "confidence": 0.8, "summary": f"s{i}",
                "risk_flags": []}
    if counted is not None:
        analysis["counted"] = counted
    return {"title": f"title {i}", "url": f"u{i}", "source": "src",
            "published_at": f"2026-11-{day:02d} 10:00:00", "analysis": analysis}


@pytest.mark.regression
def test_newest_ten_relevant_first_sorted_desc_from_27():
    # Completion order is arbitrary: scramble days, make every 3rd irrelevant.
    arts = [art(i, 1 + (i * 7) % 27, relevant=(i % 3 != 0)) for i in range(27)]
    chosen = sc.select_articles_to_store(arts, 10)
    assert len(chosen) == 10
    dates = [a["published_at"] for a in chosen]
    assert dates == sorted(dates, reverse=True)
    assert all(a["analysis"]["relevant"] for a in chosen)       # 18 relevant available
    relevant_days = sorted((a["published_at"] for a in arts if a["analysis"]["relevant"]), reverse=True)
    assert dates == relevant_days[:10]


@pytest.mark.unit
def test_uncounted_duplicates_rank_behind_scored_but_fill_remaining_slots():
    arts = [art(1, 5), art(2, 9, counted=False), art(3, 3)]
    chosen = sc.select_articles_to_store(arts, 2)
    assert [a["title"] for a in chosen] == ["title 1", "title 3"]
    chosen_all = sc.select_articles_to_store(arts, 10)
    assert [a["title"] for a in chosen_all] == ["title 2", "title 1", "title 3"]


@pytest.mark.unit
def test_mixed_date_formats_and_missing_dates():
    a = art(1, 5)
    b = {**art(2, 1), "published_at": 1793000000000}      # epoch ms (2026-10-26)
    c = {**art(3, 1), "published_at": ""}
    d = {**art(4, 1), "published_at": None}
    chosen = sc.select_articles_to_store([c, b, d, a], 10)
    assert [x["title"] for x in chosen][:2] == ["title 1", "title 2"]
    assert {x["title"] for x in chosen[2:]} == {"title 3", "title 4"}


@pytest.mark.unit
def test_empty_and_small_inputs():
    assert sc.select_articles_to_store([], 10) == []
    assert len(sc.select_articles_to_store([art(1, 1)], 10)) == 1


@pytest.mark.integration
def test_persist_stores_scored_flags_and_score_uses_all_articles(dynamodb_table, monkeypatch):
    monkeypatch.setattr(sc, "dynamodb", boto3.resource("dynamodb", region_name="us-east-2"))
    arts = [art(i, 1 + i % 28) for i in range(12)]
    arts[4]["analysis"]["relevant"] = False
    # the sentiment block comes from ALL 12 articles, computed upstream
    agg = sa.calculate_aggregate_sentiment(arts)
    assert agg["article_count"] == 12
    stock = {"symbol": "MU", "passes_screen": True, "sentiment": agg, "articles": arts}
    sc.persist_to_dynamodb([stock], "2026-11-06")

    item = dynamodb_table.get_item(Key={"PK": "STOCK#MU", "SK": "ARTICLES"})["Item"]
    stored = item["articles"]
    assert len(stored) == 10
    dates = [a["published_at"] for a in stored]
    assert dates == sorted(dates, reverse=True)
    assert all(a["scored"] is True for a in stored)          # 11 scored, 10 slots
    assert all("duplicate_of" in a for a in stored)
    latest = dynamodb_table.get_item(Key={"PK": "STOCK#MU", "SK": "LATEST"})["Item"]
    assert latest["sentiment_article_count"] == 12           # truncation did not change the count
    assert latest["sentiment_scored_count"] == agg["scored_count"]
    assert latest["sentiment_score"] == sc._to_decimal(agg["sentiment_score"])
