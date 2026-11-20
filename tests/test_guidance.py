"""
Unit + regression tests for lambdas/sentiment-analyzer/guidance.py (Layer 3).
Bedrock is always a fake client. HLIO inputs come from the real TickerTick feed
fixture (tests/fixtures/hlio/hlio_tickertick_n100.json).
Regression: HLIO fell 26% after Q3 earnings (Q4 guide below consensus, margin down,
demand pulled forward) while every stored article was positive.
"""
import io
import json
import os
import sys
from datetime import datetime, timezone

import pytest

from conftest import LAMBDAS_DIR, load_handler

sa = load_handler("sentiment-analyzer")
sys.path.insert(0, os.path.join(LAMBDAS_DIR, "news-fetcher"))
import guidance as g  # noqa: E402
import news_select as ns  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "hlio")
NOW = datetime(2026, 11, 9, 22, 30, tzinfo=timezone.utc)   # 17:30 ET
HLIO_NAME = "Helios Technologies, Inc."

# What a correct reading of the HLIO Q3 coverage says; canned model reply, not a live call.
HLIO_REPLY = {
    "guidance_vs_prior": "not_stated",
    "guidance_vs_consensus": "below",
    "margin_outlook": "down",
    "demand_outlook": "not_stated",
    "one_time_or_pull_forward_flag": True,
    "analyst_actions": [{"firm": "Stifel", "action": "target cut", "target": "$80"}],
    "key_quotes_or_numbers": ["Q4 sales guide below consensus", "$4-5M pulled forward into Q3"],
    "evidence_fields_present": ["guidance_vs_consensus", "margin_outlook", "one_time_or_pull_forward_flag"],
    "verdict": "negative",
    "confidence": 0.8,
}


def hlio_articles():
    feed = json.load(open(os.path.join(FIXTURES, "hlio_tickertick_n100.json")))
    arts = [{"title": s["title"], "description": s["description"], "source": s["site"],
             "published_at": s["time"], "url": s["url"], "ticker": "HLIO"} for s in feed]
    return ns.filter_company_articles(arts, "HLIO", HLIO_NAME)


class FakeBedrock:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def invoke_model(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return {"body": io.BytesIO(json.dumps({"content": [{"text": text}]}).encode())}


def stock(symbol="HLIO", event_date="2026-11-05", articles=None, **extra):
    return {"symbol": symbol, "company_name": HLIO_NAME,
            "articles": hlio_articles() if articles is None else articles,
            "earnings_event": {"event_key": f"{symbol}:{event_date}", "event_date": event_date,
                               "evidence_titles": []}, **extra}


# ---------- parser ----------

@pytest.mark.unit
def test_parser_accepts_plain_and_fenced_json():
    body = json.dumps(HLIO_REPLY)
    assert g.parse_guidance_response(body)["guidance_vs_consensus"] == "below"
    fenced = g.parse_guidance_response("```json\n" + body + "\n```")
    assert fenced["margin_outlook"] == "down" and fenced["verdict"] == "negative"


@pytest.mark.unit
@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", '{"confidence": NaN}', '{"confidence": Infinity}', None])
def test_parser_returns_none_for_unusable_replies(text):
    assert g.parse_guidance_response(text) is None


@pytest.mark.unit
def test_parser_invalid_enum_becomes_not_stated():
    reply = {**HLIO_REPLY, "guidance_vs_consensus": "way below", "margin_outlook": "down"}
    parsed = g.parse_guidance_response(json.dumps(reply))
    assert parsed["guidance_vs_consensus"] == "not_stated"
    assert parsed["margin_outlook"] == "down"


@pytest.mark.unit
def test_parser_evidence_gating_cannot_invent_consensus():
    reply = {**HLIO_REPLY, "evidence_fields_present": ["margin_outlook"]}   # consensus not listed as stated
    parsed = g.parse_guidance_response(json.dumps(reply))
    assert parsed["guidance_vs_consensus"] == "not_stated"
    assert parsed["one_time_or_pull_forward_flag"] is False
    assert parsed["margin_outlook"] == "down"
    assert parsed["evidence_fields_present"] == ["margin_outlook"]


@pytest.mark.unit
def test_parser_keeps_not_stated_and_defaults():
    parsed = g.parse_guidance_response(json.dumps({"verdict": "sideways", "confidence": "high"}))
    assert all(parsed[f] == "not_stated" for f in g.ENUM_FIELDS)
    assert parsed["one_time_or_pull_forward_flag"] is False
    assert parsed["verdict"] == "neutral" and parsed["confidence"] == 0.0
    assert parsed["analyst_actions"] == [] and parsed["key_quotes_or_numbers"] == []


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [(1.7, 1.0), (-0.2, 0.0), (0.55, 0.55), (True, 0.0), (None, 0.0)])
def test_parser_clamps_confidence(raw, expected):
    parsed = g.parse_guidance_response(json.dumps({**HLIO_REPLY, "confidence": raw}))
    assert parsed["confidence"] == expected


@pytest.mark.unit
def test_parser_limits_lists_and_text():
    reply = {**HLIO_REPLY,
             "analyst_actions": [{"firm": f"F{i}", "action": "downgrade", "target": 50 + i} for i in range(12)]
                                + ["junk", {"firm": "", "action": "x"}],
             "key_quotes_or_numbers": ["a" * 400, "b", "c", "d", 7]}
    parsed = g.parse_guidance_response(json.dumps(reply))
    assert len(parsed["analyst_actions"]) == g.MAX_ANALYST_ACTIONS
    assert parsed["analyst_actions"][0]["target"] == "50"
    assert len(parsed["key_quotes_or_numbers"]) == g.MAX_QUOTES
    assert len(parsed["key_quotes_or_numbers"][0]) == g.QUOTE_CHARS


# ---------- input builder ----------

@pytest.mark.unit
def test_input_puts_9999_char_transcript_first_and_caps_at_16000():
    arts = hlio_articles()
    assert max(len(a["description"]) for a in arts) == 9999          # fixture really holds the transcript
    text, meta = g.build_guidance_input(arts)
    assert text.startswith("PRIMARY SOURCE: Helios (HLIO) Q3 2026 Earnings Call Transcript")
    assert 10_000 < meta["input_chars"] <= g.MAX_INPUT_CHARS
    assert meta["input_titles"][0].endswith("Earnings Call Transcript") and len(meta["input_titles"]) <= 5
    huge = [{"title": "Q3 results", "description": "x" * 50_000, "source": "s", "published_at": 1}]
    assert len(g.build_guidance_input(huge)[0]) == g.MAX_INPUT_CHARS


@pytest.mark.unit
def test_input_trims_snippets_and_skips_articles_without_text():
    arts = [{"title": "Q3 results", "description": "p" * 3000, "source": "a", "published_at": 1},
            {"title": "Stifel cuts price target", "description": "s" * 2000, "source": "b", "published_at": 1},
            {"title": "Nothing here", "description": "z" * 2000, "source": "c", "published_at": 1},
            {"title": "Downgrade", "description": "", "source": "d", "published_at": 1}]
    text, meta = g.build_guidance_input(arts)
    assert "s" * g.SNIPPET_CHARS in text and "s" * (g.SNIPPET_CHARS + 1) not in text
    assert "z" * 50 not in text
    assert g.build_guidance_input([]) == ("", {"input_chars": 0, "input_titles": []})


# ---------- selection: idempotency, cap, priority ----------

@pytest.mark.unit
def test_stock_without_event_or_text_is_not_a_target():
    no_event = stock()
    del no_event["earnings_event"]
    no_text = stock("NOTXT", articles=[{"title": "Q3 results", "description": "", "published_at": 1}])
    assert g.select_guidance_targets([no_event, no_text]) == ([], [])


@pytest.mark.unit
def test_same_event_is_skipped_once_extracted():
    _, meta = g.build_guidance_input(hlio_articles())
    prior = {"event_key": "HLIO:2026-11-05", "input_chars": meta["input_chars"], "extraction_count": 1}
    assert g.select_guidance_targets([stock(prior_guidance=prior)]) == ([], [])


@pytest.mark.unit
def test_new_event_key_is_extracted_even_with_a_prior_signal():
    prior = {"event_key": "HLIO:2026-08-05", "input_chars": 999_999, "extraction_count": 2}
    targets, _ = g.select_guidance_targets([stock(prior_guidance=prior)])
    assert [t["symbol"] for t in targets] == ["HLIO"]


@pytest.mark.unit
def test_one_reextraction_when_text_grew_by_2000_and_never_a_second():
    _, meta = g.build_guidance_input(hlio_articles())
    # The first extraction saw only the release; the 9,999-char transcript arrived later
    grew = {"event_key": "HLIO:2026-11-05", "input_chars": meta["input_chars"] - 2000, "extraction_count": 1}
    assert [t["symbol"] for t in g.select_guidance_targets([stock(prior_guidance=grew)])[0]] == ["HLIO"]
    small = {**grew, "input_chars": meta["input_chars"] - 1999}
    assert g.select_guidance_targets([stock(prior_guidance=small)])[0] == []
    second = {**grew, "extraction_count": 2}
    assert g.select_guidance_targets([stock(prior_guidance=second)])[0] == []


@pytest.mark.unit
def test_cap_defers_extras_and_divergence_goes_first_then_newest_event():
    stocks = [stock(f"S{i:02d}", event_date="2026-11-0%d" % (1 + i % 5)) for i in range(15)]
    stocks[14]["news_price_divergence"] = {"flag": True}
    targets, deferred = g.select_guidance_targets(stocks, max_calls=12)
    assert len(targets) == 12 and len(deferred) == 3
    assert targets[0]["symbol"] == "S14"                                   # divergence first
    dates = [t["earnings_event"]["event_date"] for t in targets[1:]]
    assert dates == sorted(dates, reverse=True)                            # then newest event
    assert set(deferred).isdisjoint({t["symbol"] for t in targets})
    assert g.GUIDANCE_MAX_CALLS == 12


# ---------- extraction with a fake Bedrock ----------

@pytest.mark.unit
def test_hlio_extraction_returns_negative_signal_with_provenance():
    fake = FakeBedrock(HLIO_REPLY)
    signal = g.extract_guidance(stock(), fake, "model-x", NOW)
    assert signal["verdict"] == "negative"
    assert (signal["guidance_vs_consensus"], signal["margin_outlook"]) == ("below", "down")
    assert signal["one_time_or_pull_forward_flag"] is True
    assert signal["event_key"] == "HLIO:2026-11-05" and signal["event_date"] == "2026-11-05"
    assert signal["extraction_count"] == 1 and signal["model"] == "model-x"
    assert signal["extracted_on"] == "2026-11-09" and signal["extracted_at"] == NOW.isoformat()
    assert signal["input_chars"] > 10_000
    # one call, deterministic, capped output, the transcript text is in the prompt
    assert len(fake.calls) == 1
    body = json.loads(fake.calls[0]["body"])
    assert body["temperature"] == 0.0 and body["max_tokens"] == g.MAX_OUTPUT_TOKENS
    assert "Earnings Call Transcript" in body["messages"][0]["content"]
    json.dumps(signal)


@pytest.mark.unit
def test_reextraction_increments_count_and_new_event_resets_it():
    same = {"event_key": "HLIO:2026-11-05", "input_chars": 5000, "extraction_count": 1}
    assert g.extract_guidance(stock(prior_guidance=same), FakeBedrock(HLIO_REPLY), "m", NOW)["extraction_count"] == 2
    other = {"event_key": "HLIO:2026-08-05", "input_chars": 5000, "extraction_count": 2}
    assert g.extract_guidance(stock(prior_guidance=other), FakeBedrock(HLIO_REPLY), "m", NOW)["extraction_count"] == 1


@pytest.mark.unit
@pytest.mark.parametrize("bedrock", [FakeBedrock("garbage"), FakeBedrock(error=RuntimeError("throttled"))])
def test_failed_or_unparseable_call_returns_none(bedrock):
    assert g.extract_guidance(stock(), bedrock, "m", NOW) is None


@pytest.mark.unit
def test_no_text_means_no_bedrock_call():
    fake = FakeBedrock(HLIO_REPLY)
    assert g.extract_guidance(stock(articles=[]), fake, "m", NOW) is None
    assert fake.calls == []


@pytest.mark.integration
def test_handler_attaches_signal_only_to_new_events_and_respects_cap(monkeypatch):
    fake = FakeBedrock(HLIO_REPLY)
    monkeypatch.setattr(sa, "bedrock_client", fake)
    stocks_in = [stock("HLIO"), {"symbol": "CALM", "company_name": "Calm Inc", "articles": []}]
    import pipeline_io
    monkeypatch.setattr(pipeline_io, "read_pipeline_input", lambda event: {"stocks_with_news": stocks_in})
    monkeypatch.setattr(pipeline_io, "write_pipeline_output", lambda result, step_name: result)
    monkeypatch.delenv("DATA_TABLE_NAME", raising=False)
    monkeypatch.delenv("RAW_DATA_BUCKET", raising=False)
    monkeypatch.setattr(sa, "analyze_article", lambda a, t, c, m: {
        **a, "analysis": {"relevant": True, "sentiment": 0.5, "confidence": 0.6, "summary": "", "risk_flags": []}})
    monkeypatch.setattr(sa, "assess_competition", lambda s, a, m: {
        "hhi_score": 3, "competition_score": 3, "ai_threat": "", "competition_reasoning": ""})
    out = sa.handler({}, None)["stocks_with_sentiment"]
    by = {s["symbol"]: s for s in out}
    assert by["HLIO"]["guidance_signal"]["verdict"] == "negative"
    assert "guidance_signal" not in by["CALM"]
    assert len(fake.calls) == 1                       # guidance only; per-article analysis was stubbed
