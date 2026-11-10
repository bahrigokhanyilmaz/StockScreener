"""Tests for scripts/repair_provisional_bars.py (dry-run default, conditional --apply)."""
import importlib.util
import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "repair_provisional_bars", os.path.join(REPO, "scripts", "repair_provisional_bars.py"))
rep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rep)

KEY = "SECRETKEY123"
TARGET = "2026-11-09"
AFTER = datetime(2026, 11, 9, 22, 30, tzinfo=timezone.utc)    # 17:30 EST
BEFORE = datetime(2026, 11, 9, 21, 30, tzinfo=timezone.utc)   # 16:30 EST

PROVISIONAL = {"d": TARGET, "o": Decimal("48.0"), "h": Decimal("49.86"), "l": Decimal("47.9"),
               "c": Decimal("49.59"), "v": Decimal("2018546")}
FINAL = {"d": TARGET, "o": 48.0, "h": 49.86, "l": 47.9, "c": 49.48, "v": 2400000}


def seed(table, with_score=True, with_latest=True, mark=None, latest_extra=None):
    table.put_item(Item={"PK": "PRICE_HISTORY#HLIO", "SK": "DAILY", "symbol": "HLIO",
                         "bars": [{"d": "2026-11-06", "c": Decimal("47.44")}, dict(PROVISIONAL)],
                         "last_final_date": TARGET, "fetched_at": "2026-11-09T20:25:00Z"})
    if with_latest:
        item = {"PK": "STOCK#HLIO", "SK": "LATEST", "symbol": "HLIO", "price": Decimal("49.59"),
                "price_as_of": TARGET, "shares_implied": Decimal("34000000")}
        item.update(latest_extra or {})
        table.put_item(Item=item)
    if with_score:
        table.put_item(Item={"PK": "STOCK#HLIO", "SK": f"SCORE#{TARGET}", "price": Decimal("49.59")})
    if mark:
        table.put_item(Item={"PK": "STOCK#HLIO", "SK": "TRACKING", **mark})


class Fetcher:
    def __init__(self, result=FINAL):
        self.calls = 0
        self.result = result

    def __call__(self, sym, date, key):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class WriteGuard:
    """Wraps a real table; any write explodes."""
    def __init__(self, real):
        self.real = real

    def scan(self, **kw):
        return self.real.scan(**kw)

    def get_item(self, **kw):
        return self.real.get_item(**kw)

    def __getattr__(self, name):
        raise AssertionError(f"dry run attempted a write: {name}")


# ---- pure functions -------------------------------------------------------

@pytest.mark.unit
def test_redact_removes_key_and_apikey_param():
    out = rep.redact(f"boom https://x/y?symbol=A&apikey={KEY}&z=1 {KEY}", KEY)
    assert KEY not in out and "apikey=***" in out


@pytest.mark.unit
def test_find_candidates_only_newest_bar_on_date():
    items = [{"PK": "PRICE_HISTORY#A", "bars": [{"d": "2026-11-09"}]},
             {"PK": "PRICE_HISTORY#B", "bars": [{"d": "2026-11-06"}]},
             {"PK": "PRICE_HISTORY#C", "bars": [{"d": "2026-11-09"}, {"d": "2026-11-10"}]},
             {"PK": "PRICE_HISTORY#D", "bars": []}]
    assert [i["PK"] for i in rep.find_candidates(items, "2026-11-09")] == ["PRICE_HISTORY#A"]


@pytest.mark.unit
def test_plan_repairs_tolerance_and_exact_volume():
    stored = {"A": {"d": "x", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100}}
    same = {"A": {"d": "x", "o": 1.004, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100}}
    assert rep.plan_repairs(stored, same) == []
    vol = {"A": {"d": "x", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 101}}
    assert rep.plan_repairs(stored, vol)[0]["fields_changed"] == ["v"]
    close = {"A": {"d": "x", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.51, "v": 100}}
    assert rep.plan_repairs(stored, close)[0]["fields_changed"] == ["c"]
    assert rep.plan_repairs(stored, {}) == []


# ---- CLI behaviour --------------------------------------------------------

@pytest.mark.unit
def test_bad_date_exits_2(dynamodb_table, capsys):
    assert rep.main(["--date", "11/09/2026"], now_utc=AFTER, table=dynamodb_table, key=KEY) == 2
    assert rep.main(["--date", "2026-13-40"], now_utc=AFTER, table=dynamodb_table, key=KEY) == 2


@pytest.mark.unit
def test_apply_before_1700_et_refused(dynamodb_table):
    seed(dynamodb_table)
    f = Fetcher()
    assert rep.main(["--date", TARGET, "--apply"], now_utc=BEFORE, table=dynamodb_table, key=KEY, fetch=f) == 2
    assert f.calls == 0


@pytest.mark.unit
def test_dry_run_makes_no_writes_one_call_per_candidate_and_never_prints_key(dynamodb_table, capsys):
    seed(dynamodb_table)
    f = Fetcher()
    rc = rep.main(["--date", TARGET], now_utc=AFTER, table=WriteGuard(dynamodb_table), key=KEY, fetch=f)
    out = capsys.readouterr().out
    assert rc == 0 and f.calls == 1
    assert "HLIO" in out and "49.48" in out and "nothing written" in out
    assert KEY not in out
    bars = dynamodb_table.get_item(Key={"PK": "PRICE_HISTORY#HLIO", "SK": "DAILY"})["Item"]["bars"]
    assert bars[-1]["c"] == Decimal("49.59")


@pytest.mark.unit
def test_fetch_error_message_is_redacted_and_exit_1(dynamodb_table, capsys):
    seed(dynamodb_table)
    f = Fetcher(RuntimeError(f"failed https://x?apikey={KEY}"))
    rc = rep.main(["--date", TARGET], now_utc=AFTER, table=WriteGuard(dynamodb_table), key=KEY, fetch=f)
    out = capsys.readouterr().out
    assert rc == 1 and KEY not in out and "RuntimeError" in out


@pytest.mark.unit
def test_apply_updates_bar_latest_score_and_market_cap(dynamodb_table):
    seed(dynamodb_table)
    rc = rep.main(["--date", TARGET, "--apply"], now_utc=AFTER, table=dynamodb_table, key=KEY, fetch=Fetcher())
    assert rc == 0
    ph = dynamodb_table.get_item(Key={"PK": "PRICE_HISTORY#HLIO", "SK": "DAILY"})["Item"]
    assert ph["bars"][-1]["c"] == Decimal("49.48") and ph["bars"][0]["c"] == Decimal("47.44")
    assert ph["last_final_date"] == TARGET and ph["to_date"] == TARGET
    assert ph["fetched_at"] == "2026-11-09T22:30:00Z"
    latest = dynamodb_table.get_item(Key={"PK": "STOCK#HLIO", "SK": "LATEST"})["Item"]
    assert latest["price"] == Decimal("49.48") and latest["price_as_of"] == TARGET
    assert latest["market_cap"] == Decimal(round(34_000_000 * 49.48))
    score = dynamodb_table.get_item(Key={"PK": "STOCK#HLIO", "SK": f"SCORE#{TARGET}"})["Item"]
    assert score["price"] == Decimal("49.48") and score["price_as_of"] == TARGET


@pytest.mark.unit
def test_apply_does_not_roll_latest_backwards(dynamodb_table, capsys):
    seed(dynamodb_table, latest_extra={"price_as_of": "2026-11-10", "price": Decimal("51.0")})
    rc = rep.main(["--date", TARGET, "--apply"], now_utc=AFTER, table=dynamodb_table, key=KEY, fetch=Fetcher())
    latest = dynamodb_table.get_item(Key={"PK": "STOCK#HLIO", "SK": "LATEST"})["Item"]
    assert latest["price"] == Decimal("51.0") and latest["price_as_of"] == "2026-11-10"
    assert rc == 1 and "UNVERIFIED" in capsys.readouterr().out


@pytest.mark.unit
def test_apply_bar_changed_concurrently_is_unverified(dynamodb_table, monkeypatch, capsys):
    seed(dynamodb_table)

    class Racy(WriteGuard):
        """Pipeline rewrites the bar between our read and our write."""
        def get_item(self, **kw):
            res = self.real.get_item(**kw)
            if kw["Key"]["PK"].startswith("PRICE_HISTORY#"):
                self.real.update_item(
                    Key=kw["Key"], UpdateExpression="SET bars[1].c = :c",
                    ExpressionAttributeValues={":c": Decimal("49.50")})
            return res

        def update_item(self, **kw):
            return self.real.update_item(**kw)

    rc = rep.main(["--date", TARGET, "--apply"], now_utc=AFTER, table=Racy(dynamodb_table), key=KEY, fetch=Fetcher())
    ph = dynamodb_table.get_item(Key={"PK": "PRICE_HISTORY#HLIO", "SK": "DAILY"})["Item"]
    assert ph["bars"][-1]["c"] == Decimal("49.50")  # not clobbered
    assert rc == 1 and "UNVERIFIED" in capsys.readouterr().out


@pytest.mark.unit
def test_missing_score_row_is_skipped_not_created(dynamodb_table, capsys):
    seed(dynamodb_table, with_score=False)
    rc = rep.main(["--date", TARGET, "--apply"], now_utc=AFTER, table=dynamodb_table, key=KEY, fetch=Fetcher())
    assert rc == 0 and "no SCORE#" in capsys.readouterr().out
    assert "Item" not in dynamodb_table.get_item(Key={"PK": "STOCK#HLIO", "SK": f"SCORE#{TARGET}"})


@pytest.mark.unit
def test_mark_equal_to_stored_close_warns_but_is_not_edited(dynamodb_table, capsys):
    seed(dynamodb_table, mark={"mark_date": TARGET, "mark_price": Decimal("49.59")})
    rep.main(["--date", TARGET, "--apply"], now_utc=AFTER, table=dynamodb_table, key=KEY, fetch=Fetcher())
    assert "WARNING HLIO" in capsys.readouterr().out
    t = dynamodb_table.get_item(Key={"PK": "STOCK#HLIO", "SK": "TRACKING"})["Item"]
    assert t["mark_price"] == Decimal("49.59")


@pytest.mark.regression
def test_dry_run_table_survives_none_fields(dynamodb_table, capsys):
    """A stored bar missing h/v made the table format spec hit None and crash."""
    dynamodb_table.put_item(Item={
        "PK": "PRICE_HISTORY#HLIO", "SK": "DAILY", "symbol": "HLIO",
        "bars": [{"d": TARGET, "c": Decimal("49.59")}],
        "last_final_date": TARGET, "fetched_at": "2026-11-09T20:25:00Z"})
    rc = rep.main(["--date", TARGET], now_utc=AFTER, table=WriteGuard(dynamodb_table), key=KEY, fetch=Fetcher())
    out = capsys.readouterr().out
    assert rc == 0 and "HLIO" in out and "-" in out
