"""
Handler-level tests for Step 5 (news-fetcher): TickerTick selection, call budget,
pacing, dedupe, UTC timestamps. HTTP, SSM and time are faked; DynamoDB is moto.

Regression: HLIO fell ~26% after earnings but TickerTick (the only source with
the guidance and price-target cuts) was queried only when FMP returned fewer
than 4 articles, and with n=6 the newest six stories were wrong-company noise.
"""
import io
import json
import os
import sys
import time as real_time
import types
import pytest
from conftest import load_handler

fh = load_handler("news-fetcher")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "hlio")
HLIO_NAME = "Helios Technologies, Inc."


def _fixture(name):
    return json.load(open(os.path.join(FIXTURES, name)))


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Clock:
    """Fake monotonic clock; sleep advances it, calls advance it by `latency`."""
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []
        self.call_starts = []

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


class Ctx:
    def __init__(self, clock, total_ms=900_000):
        self.clock, self.total_ms, self.t0 = clock, total_ms, clock.now

    def get_remaining_time_in_millis(self):
        return self.total_ms - (self.clock.now - self.t0) * 1000


@pytest.fixture
def env(dynamodb_table, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(fh, "time", types.SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    monkeypatch.setitem(sys.modules, "pipeline_io", types.SimpleNamespace(
        read_pipeline_input=lambda e: e, write_pipeline_output=lambda r, step_name: r))
    monkeypatch.setattr(fh, "get_fmp_key", lambda: "k")
    monkeypatch.setattr(fh, "new_york_date", lambda now=None: "2026-11-09")
    monkeypatch.delenv("RAW_DATA_BUCKET", raising=False)
    return types.SimpleNamespace(clock=clock, table=dynamodb_table, monkeypatch=monkeypatch)


def _stock(sym, name="Acme Corp"):
    return {"symbol": sym, "company_name": name, "average_volume": 500_000, "analyst_recommendation": 2.0}


def _install_http(env, tt_stories=None, latency=1.5):
    """Fake TickerTick over urlopen. Returns the list of requested URLs."""
    urls = []

    def fake_urlopen(req, timeout=0):
        urls.append(req.full_url)
        env.clock.call_starts.append(env.clock.now)
        env.clock.now += latency
        return _Resp(json.dumps({"stories": tt_stories or []}).encode())

    env.monkeypatch.setattr(fh.urllib.request, "urlopen", fake_urlopen)
    return urls


def _fmp(env, per_symbol):
    """per_symbol: {sym: [article, ...]} returned by the FMP batch call."""
    env.monkeypatch.setattr(fh, "fetch_news_batch",
                            lambda syms, key, articles_per_stock=6: {s: list(per_symbol.get(s, [])) for s in syms})
    env.monkeypatch.setattr(fh, "fetch_news_for_ticker", lambda sym, key, max_articles=10: [])


def _fmp_filler(n, day="2026-11-01"):
    return [{"title": f"Filler story {i}", "description": "x" * 100, "published_at": f"{day} 10:00:00",
             "source": "s", "url": "", "ticker": ""} for i in range(n)]


def _run(env, stocks, with_ctx=True):
    ctx = Ctx(env.clock) if with_ctx else None
    return fh.handler({"passing_stocks": stocks}, ctx)


# ---------- HLIO end to end ----------

@pytest.mark.regression
def test_hlio_after_earnings_gets_tickertick_even_with_enough_fmp_articles(env):
    fmp = _fixture("hlio_fmp_step5.json")
    assert len(fmp) >= 4                                   # old rule: no TickerTick call
    _fmp(env, {"HLIO": fmp})
    urls = _install_http(env, _fixture("hlio_tickertick_n100.json"))

    out = _run(env, [_stock("HLIO", HLIO_NAME)])
    stock = out["stocks_with_news"][0]
    titles = [a["title"] for a in stock["articles"]]

    assert len(urls) == 1 and "q=tt:hlio" in urls[0]
    assert "n=50" in urls[0]                               # n=6 never reached the earnings items
    assert stock["earnings_event"] == {
        "event_date": "2026-11-05", "event_key": "HLIO:2026-11-05",
        "evidence_titles": stock["earnings_event"]["evidence_titles"]}
    assert len(stock["articles"]) <= fh.MAX_ARTICLES_PER_STOCK
    assert any("Releases Q4 2026 Earnings Guidance" in t for t in titles)
    assert any("Price Target Cut to $65.00 by Analysts at Stifel" in t for t in titles)
    for noise in ("Sanmina", "AMD Stocks", "Titan"):
        assert not any(noise in t for t in titles)


@pytest.mark.regression
def test_transcript_triplicate_keeps_the_9999_char_copy(env):
    release = [a for a in _fixture("hlio_fmp_step5.json") if "Third Quarter 2026 Results" in a["title"]]
    _fmp(env, {"HLIO": release + _fmp_filler(5)})              # one earnings title triggers the call
    _install_http(env, _fixture("hlio_tickertick_n100.json"))
    out = _run(env, [_stock("HLIO", HLIO_NAME)])
    transcripts = [a for a in out["stocks_with_news"][0]["articles"]
                   if a["title"] == "Helios (HLIO) Q3 2026 Earnings Call Transcript"]
    assert [len(a["description"]) for a in transcripts] == [9999]


@pytest.mark.unit
def test_fmp_only_duplicate_titles_are_removed(env):
    dup = {"title": "Acme Corp Q3 call", "description": "short", "published_at": "2026-10-01 10:00:00",
           "source": "a", "url": "", "ticker": ""}
    dup2 = {**dup, "description": "a much longer description of the same piece", "source": "b"}
    _fmp(env, {"ACME": [dup, dup2] + _fmp_filler(4)})
    _install_http(env)
    out = _run(env, [_stock("ACME")])
    same = [a for a in out["stocks_with_news"][0]["articles"] if a["title"] == "Acme Corp Q3 call"]
    assert len(same) == 1 and same[0]["source"] == "b"


@pytest.mark.regression
def test_tickertick_timestamps_are_utc_regardless_of_tz(monkeypatch):
    story = {"title": "t", "description": "d", "url": "u", "site": "s", "time": 1794302466000}
    monkeypatch.setattr(fh.urllib.request, "urlopen",
                        lambda req, timeout=0: _Resp(json.dumps({"stories": [story]}).encode()))
    results = set()
    old_tz = os.environ.get("TZ")
    try:
        for tz in ("UTC", "Asia/Tokyo", "America/Los_Angeles"):
            os.environ["TZ"] = tz
            real_time.tzset()
            results.add(fh.fetch_tickertick_news("ACME")[0]["published_at"])
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        real_time.tzset()
    assert results == {"2026-11-10 09:21:06"}


# ---------- budget ----------

def _many(n):
    return [_stock(f"S{i:03d}") for i in range(n)]


@pytest.mark.unit
def test_call_count_never_exceeds_cap(env):
    _fmp(env, {})                                           # every stock under-covered
    urls = _install_http(env)
    _run(env, _many(70))
    assert len(urls) == fh.TT_MAX_CALLS


@pytest.mark.unit
def test_pacing_is_start_to_start(env):
    _fmp(env, {})
    _install_http(env, latency=1.5)
    _run(env, _many(4))
    starts = env.clock.call_starts
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(gaps) == 3
    assert all(abs(g - fh.TT_PACE_SECONDS) < 1e-6 for g in gaps)       # not 6.5 + latency
    assert all(abs(s - (fh.TT_PACE_SECONDS - 1.5)) < 1e-6 for s in env.clock.sleeps if s > 3)


@pytest.mark.unit
def test_phase_budget_stops_the_loop(env):
    _fmp(env, {})
    urls = _install_http(env, latency=15.0)                 # every call times out
    _run(env, _many(70))
    # 15 s per call (no wait needed since latency > pace) => budget hit at 420 s
    assert len(urls) == int(fh.TT_PHASE_BUDGET_S // 15)
    starts = env.clock.call_starts
    assert starts[-1] - starts[0] <= fh.TT_PHASE_BUDGET_S


@pytest.mark.unit
def test_low_remaining_time_skips_tickertick_entirely(env):
    _fmp(env, {})
    urls = _install_http(env)
    out = fh.handler({"passing_stocks": _many(5)}, Ctx(env.clock, total_ms=fh.TT_MIN_REMAINING_MS - 1))
    assert urls == []
    assert len(out["stocks_with_news"]) == 5                # FMP output still produced


@pytest.mark.unit
def test_no_context_does_not_crash(env):
    _fmp(env, {})
    _install_http(env)
    assert len(_run(env, _many(2), with_ctx=False)["stocks_with_news"]) == 2


@pytest.mark.unit
def test_well_covered_stock_without_event_or_divergence_is_not_queried(env):
    _fmp(env, {"ACME": _fmp_filler(6)})
    urls = _install_http(env)
    out = _run(env, [_stock("ACME")])
    assert urls == []
    assert "earnings_event" not in out["stocks_with_news"][0]


@pytest.mark.integration
def test_divergence_flag_from_latest_triggers_call_and_is_passed_on(env):
    from decimal import Decimal
    env.table.put_item(Item={
        "PK": "STOCK#ACME", "SK": "LATEST", "symbol": "ACME", "tracking_status": "ACTIVE",
        "last_updated": "2026-11-08T00:00:00Z", "company_name": "Acme Corp",
        "news_price_divergence": {"flag": True, "tier": "strong", "return_3d": Decimal("-0.307")},
    })
    _fmp(env, {"ACME": _fmp_filler(6)})
    urls = _install_http(env)
    out = _run(env, [])
    assert len(urls) == 1
    div = out["stocks_with_news"][0]["news_price_divergence"]
    assert div["tier"] == "strong" and div["return_3d"] == pytest.approx(-0.307)
    json.dumps(out)                                         # no Decimals left in the pipeline payload


@pytest.mark.unit
def test_load_latest_signals_reads_flag_and_prior_event(env):
    env.table.put_item(Item={"PK": "STOCK#AAA", "SK": "LATEST", "guidance_signal": {"event_date": "2026-11-05"}})
    env.table.put_item(Item={"PK": "STOCK#BBB", "SK": "LATEST"})
    got = fh.load_latest_signals(env.table, ["AAA", "BBB", "CCC"])
    assert got["AAA"] == {"news_price_divergence": None, "prior_event_date": "2026-11-05"}
    assert got["BBB"] == {"news_price_divergence": None, "prior_event_date": None}
    assert got["CCC"] == {"news_price_divergence": None, "prior_event_date": None}
