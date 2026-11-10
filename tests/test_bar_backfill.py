"""
Regression: Step 7 must only store FINAL daily bars.

HLIO bug (2026-11-09): the pipeline fired at 20:00 UTC = 15:00 EST (before the
16:00 close). Step 7 stored FMP's partial today-bar (c=49.59) and the
"already backfilled today" skip froze it. The final close was 49.48.

FMP and the sleep are monkeypatched; DynamoDB is a moto table.
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from conftest import load_handler

sc = load_handler("score-calculator")

TODAY = "2026-11-09"  # Monday
BEFORE_CLOSE = datetime(2026, 11, 9, 20, 25, tzinfo=timezone.utc)   # 15:25 EST
AFTER_SETTLE = datetime(2026, 11, 9, 22, 10, tzinfo=timezone.utc)    # 17:10 EST


def fmp_bar(d, c, h=None, v=1000):
    return {"date": d, "open": c, "high": h or c, "low": c, "close": c, "volume": v}


class Env:
    def __init__(self, table, monkeypatch, fmp_result):
        import boto3
        self.table = table
        self.calls = []
        self.fmp_result = fmp_result
        monkeypatch.setattr(sc, "dynamodb", boto3.resource("dynamodb", region_name="us-east-2"))
        monkeypatch.setattr(sc, "get_fmp_key", lambda: "TEST_KEY")
        monkeypatch.setattr(sc.time, "sleep", lambda *_: None)
        monkeypatch.setenv("DATA_TABLE_NAME", "stock-screener-data")

        def fake_fetch(symbol, from_date, to_date, key):
            self.calls.append((symbol, from_date, to_date))
            if isinstance(self.fmp_result, Exception):
                raise self.fmp_result
            return self.fmp_result

        monkeypatch.setattr(sc, "_fetch_fmp_bars", fake_fetch, raising=False)

    def item(self, symbol="HLIO"):
        return self.table.get_item(Key={"PK": f"PRICE_HISTORY#{symbol}", "SK": "DAILY"}).get("Item")


@pytest.fixture
def env_factory(dynamodb_table, monkeypatch):
    def make(fmp_result):
        return Env(dynamodb_table, monkeypatch, fmp_result)
    return make


def put_existing(table, symbol="HLIO", **extra):
    item = {"PK": f"PRICE_HISTORY#{symbol}", "SK": "DAILY", "symbol": symbol}
    item.update(extra)
    table.put_item(Item=item)


def dec_bars(*pairs):
    return [{"d": d, "c": Decimal(str(c))} for d, c in pairs]


@pytest.mark.regression
def test_pre_close_run_stores_no_today_bar_and_does_not_freeze(env_factory):
    e = env_factory([fmp_bar("2026-11-09", 49.59, h=49.86, v=2018546), fmp_bar("2026-11-06", 47.44)])
    stocks = [{"symbol": "HLIO", "price": 47.44}]
    out = sc.backfill_price_history(stocks, TODAY, now_utc=BEFORE_CLOSE)
    item = e.item()
    assert [b["d"] for b in item["bars"]] == ["2026-11-06"]
    assert item["last_final_date"] == "2026-11-06"
    assert item["to_date"] == "2026-11-06"
    assert "last_backfill" not in item
    assert stocks[0]["price"] == 47.44 and stocks[0]["price_as_of"] == "2026-11-06"
    assert out == {"HLIO": 47.44}

    # Post-close re-run must refetch (not frozen) and replace with the final bar.
    e.fmp_result = [fmp_bar("2026-11-09", 49.48, h=49.86, v=2400000), fmp_bar("2026-11-06", 47.44)]
    stocks = [{"symbol": "HLIO", "price": 47.44}]
    sc.backfill_price_history(stocks, TODAY, now_utc=AFTER_SETTLE)
    item = e.item()
    assert item["bars"][-1]["d"] == "2026-11-09"
    assert item["bars"][-1]["c"] == Decimal("49.48")
    assert stocks[0]["price"] == 49.48 and stocks[0]["price_as_of"] == "2026-11-09"
    assert len(e.calls) == 2


@pytest.mark.unit
def test_post_close_rerun_skips_with_zero_fmp_calls(env_factory):
    e = env_factory([])
    put_existing(e.table, last_final_date="2026-11-09", fetched_at="2026-11-09T22:05:00Z",
                 bars=dec_bars(("2026-11-06", 47.44), ("2026-11-09", 49.48)))
    stocks = [{"symbol": "HLIO", "price": 1.0}]
    out = sc.backfill_price_history(stocks, TODAY, now_utc=datetime(2026, 11, 9, 22, 30, tzinfo=timezone.utc))
    assert e.calls == []
    assert out == {"HLIO": 49.48}
    assert stocks[0]["price_as_of"] == "2026-11-09"


@pytest.mark.unit
def test_legacy_item_is_refetched_even_if_last_backfill_is_today(env_factory):
    e = env_factory([fmp_bar("2026-11-09", 49.48), fmp_bar("2026-11-06", 47.44)])
    put_existing(e.table, last_backfill=TODAY, bars=dec_bars(("2026-11-09", 49.59)))
    sc.backfill_price_history([{"symbol": "HLIO"}], TODAY, now_utc=AFTER_SETTLE)
    assert len(e.calls) == 1
    assert e.item()["bars"][-1]["c"] == Decimal("49.48")


@pytest.mark.unit
def test_settle_guard_fetched_1620_is_refetched_1705_is_skipped(env_factory):
    e = env_factory([fmp_bar("2026-11-09", 49.48)])
    bars = dec_bars(("2026-11-09", 49.40))
    put_existing(e.table, last_final_date="2026-11-09", fetched_at="2026-11-09T21:20:00Z", bars=bars)
    sc.backfill_price_history([{"symbol": "HLIO"}], TODAY, now_utc=AFTER_SETTLE)
    assert len(e.calls) == 1

    e.calls.clear()
    put_existing(e.table, last_final_date="2026-11-09", fetched_at="2026-11-09T22:05:00Z", bars=bars)
    sc.backfill_price_history([{"symbol": "HLIO"}], TODAY, now_utc=AFTER_SETTLE)
    assert e.calls == []


@pytest.mark.unit
def test_newer_final_session_forces_refetch(env_factory):
    e = env_factory([fmp_bar("2026-11-09", 49.48)])
    put_existing(e.table, last_final_date="2026-11-06", fetched_at="2026-11-06T22:30:00Z",
                 bars=dec_bars(("2026-11-06", 47.44)))
    sc.backfill_price_history([{"symbol": "HLIO"}], TODAY, now_utc=AFTER_SETTLE)
    assert len(e.calls) == 1


@pytest.mark.unit
def test_fmp_error_falls_back_to_existing_final_bar(env_factory):
    e = env_factory(RuntimeError("boom https://x?apikey=SECRET"))
    put_existing(e.table, last_final_date="2026-11-06", fetched_at="2026-11-06T22:30:00Z",
                 bars=dec_bars(("2026-11-05", 46.0), ("2026-11-06", 47.44)))
    stocks = [{"symbol": "HLIO", "price": 1.0}]
    out = sc.backfill_price_history(stocks, TODAY, now_utc=AFTER_SETTLE)
    assert out == {"HLIO": 47.44}
    assert stocks[0]["price_as_of"] == "2026-11-06"


@pytest.mark.unit
def test_error_message_is_not_logged(env_factory, capsys):
    env_factory(RuntimeError("https://x?apikey=SECRET"))
    sc.backfill_price_history([{"symbol": "HLIO"}], TODAY, now_utc=AFTER_SETTLE)
    assert "SECRET" not in capsys.readouterr().out


@pytest.mark.unit
def test_bad_rows_are_skipped_valid_kept(env_factory):
    e = env_factory([
        fmp_bar("2026-11-06", 47.44),
        {"date": "2026-11-05", "open": 1, "high": 1, "low": 1, "close": 0, "volume": 1},     # close 0
        {"date": "bad", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},            # bad date
        {"date": "2026-11-04", "open": "x", "high": 1, "low": 1, "close": 1, "volume": 1},   # non-numeric
        "junk",
    ])
    sc.backfill_price_history([{"symbol": "HLIO"}], TODAY, now_utc=AFTER_SETTLE)
    assert [b["d"] for b in e.item()["bars"]] == ["2026-11-06"]


@pytest.mark.unit
def test_put_failure_does_not_advance_price(env_factory, monkeypatch):
    e = env_factory([fmp_bar("2026-11-09", 49.48), fmp_bar("2026-11-06", 47.44)])
    put_existing(e.table, last_final_date="2026-11-05", fetched_at="2026-11-05T22:30:00Z",
                 bars=dec_bars(("2026-11-05", 46.0)))

    class BoomTable:
        def __init__(self, real):
            self.real = real

        def get_item(self, **kw):
            return self.real.get_item(**kw)

        def put_item(self, **kw):
            raise RuntimeError("write failed")

    class Res:
        def Table(self, name):
            return BoomTable(e.table)

    monkeypatch.setattr(sc, "dynamodb", Res())
    stocks = [{"symbol": "HLIO", "price": 1.0}]
    out = sc.backfill_price_history(stocks, TODAY, now_utc=AFTER_SETTLE)
    assert out == {"HLIO": 46.0}
    assert stocks[0]["price_as_of"] == "2026-11-05"


@pytest.mark.unit
def test_legacy_item_with_fetch_error_falls_back_to_previous_day(env_factory):
    e = env_factory(RuntimeError("x"))
    put_existing(e.table, last_backfill=TODAY,
                 bars=dec_bars(("2026-11-06", 47.44), ("2026-11-09", 49.59)))  # today's bar may be partial
    stocks = [{"symbol": "HLIO"}]
    out = sc.backfill_price_history(stocks, TODAY, now_utc=AFTER_SETTLE)
    assert out == {"HLIO": 47.44}
    assert stocks[0]["price_as_of"] == "2026-11-06"


@pytest.mark.unit
def test_no_bars_anywhere_leaves_price_untouched(env_factory):
    env_factory(RuntimeError("x"))
    stocks = [{"symbol": "HLIO", "price": 12.0}]
    out = sc.backfill_price_history(stocks, TODAY, now_utc=AFTER_SETTLE)
    assert out == {}
    assert stocks[0]["price"] == 12.0 and "price_as_of" not in stocks[0]


@pytest.mark.unit
def test_market_cap_recomputed_from_shares_implied(env_factory):
    env_factory([fmp_bar("2026-11-09", 50.0)])
    stocks = [
        {"symbol": "HLIO", "price": 47.44, "market_cap": 1_640_280_868, "shares_implied": 34_580_000.0},
        {"symbol": "HLIO", "price": 47.44, "market_cap": 1_640_280_868},   # legacy: no shares_implied
    ]
    sc.backfill_price_history(stocks, TODAY, now_utc=AFTER_SETTLE)
    assert stocks[0]["market_cap"] == round(34_580_000.0 * 50.0)
    assert stocks[1]["market_cap"] == 1_640_280_868
