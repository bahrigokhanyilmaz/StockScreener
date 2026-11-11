"""Unit tests for the pure functions of scripts/backtest_oversold.py (research backtest)."""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import backtest_oversold as bt  # noqa: E402

pytestmark = pytest.mark.unit


# ------------------------------- indicators ---------------------------------
def test_rsi_wilder_matches_hand_computed_series():
    # n=3 so it is checkable by hand. changes: +1 -1 +2 -1 +2
    # seed: avg gain 3/3=1, avg loss 1/3 -> RS 3 -> RSI 75
    # d=-1: ag=(1*2+0)/3=2/3, al=(1/3*2+1)/3=5/9 -> RS 1.2 -> 54.5454...
    # d=+2: ag=(2/3*2+2)/3=10/9, al=(5/9*2+0)/3=10/27 -> RS 3 -> 75
    r = bt.rsi_wilder([10, 11, 10, 12, 11, 13], n=3)
    assert np.isnan(r[:3]).all()
    assert r[3] == pytest.approx(75.0)
    assert r[4] == pytest.approx(100 - 100 / (1 + 1.2))
    assert r[5] == pytest.approx(75.0)


def test_rsi_all_up_is_100_and_all_down_is_0():
    assert bt.rsi_wilder(list(range(1, 30)), 14)[-1] == pytest.approx(100.0)
    assert bt.rsi_wilder(list(range(30, 1, -1)), 14)[-1] == pytest.approx(0.0)


def _walk(n, seed=0):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))


def test_zscore_excludes_event_window_and_matches_manual():
    c = _walk(700)
    t, k, w = 650, 3, 504
    z = bt.zscore_ret(c, k, w)
    r = lambda j: c[j] / c[j - k] - 1  # noqa: E731
    base = [r(j) for j in range(t - k - w + 1, t - k + 1)]  # windows ended on/before t-3
    assert len(base) == w
    expected = (r(t) - np.mean(base)) / np.std(base, ddof=1)
    assert z[t] == pytest.approx(expected)
    # the event window itself (returns ending t-2..t) must not influence the baseline
    c2 = c.copy()
    c2[t - 2:t] *= 0.5
    base2 = [c2[j] / c2[j - k] - 1 for j in range(t - k - w + 1, t - k + 1)]
    assert np.allclose(base, base2)
    assert np.isnan(bt.zscore_ret(c, k, w)[:w + k - 1]).all()


def test_range_sma_volume():
    n = 300
    c = np.linspace(100, 200, n)
    assert bt.range_position(c + 1, c - 1, c, 252)[-1] == pytest.approx((200 - (c[-252] - 1)) / ((201) - (c[-252] - 1)))
    assert bt.sma_distance(np.full(250, 50.0), 200)[-1] == 0.0
    v = np.full(40, 100.0)
    v[30] = 450
    assert bt.volume_ratio(v, 20)[30] == pytest.approx(4.5)
    spike = bt.volume_spike(v, 3.0, 20)
    assert spike[30] and spike[31] and not spike[32] and not spike[29]


# ------------------------------ split artifacts ------------------------------
def _series_with_split(adjusted_ok=True, volume_spike=False):
    n = 60
    base = 100 + np.sin(np.arange(n)) * 0.5          # calm
    unadj = base.copy()
    unadj[30:] = unadj[30:] / 2.0                      # 2:1 split on day 30
    adj = base.copy() if adjusted_ok else unadj.copy()  # adjusted series ignores / includes the split
    vol = np.full(n, 1_000.0)
    if volume_spike:
        vol[30] = 9_000.0
    return adj, unadj, vol


def test_split_is_detected_when_adjusted_and_unadjusted_disagree():
    adj, unadj, vol = _series_with_split(adjusted_ok=True)
    splits, arts = bt.detect_split_days(adj, unadj, vol)
    assert [i for i, _ in splits] == [30] and arts == []
    assert splits[0][1] == pytest.approx(0.5, rel=0.02)


def test_unadjusted_split_left_in_adjusted_series_is_an_artifact():
    adj, unadj, vol = _series_with_split(adjusted_ok=False)
    splits, arts = bt.detect_split_days(adj, unadj, vol)
    assert splits == [] and [i for i, _ in arts] == [30]


def test_real_crash_with_volume_spike_is_not_an_artifact():
    adj, unadj, vol = _series_with_split(adjusted_ok=False, volume_spike=True)
    splits, arts = bt.detect_split_days(adj, unadj, vol)
    assert splits == [] and arts == []


def test_artifact_mask_covers_window_after_split_only():
    m = bt.artifact_mask(100, [(30, 0.5)], [], before=25)
    assert not m[29] and m[30] and m[55] and not m[56]


# ------------------------------ events / returns -----------------------------
def test_decluster_first_in_cluster_with_gap():
    trig = np.zeros(80, dtype=bool)
    trig[[5, 6, 7, 30, 40, 50]] = True
    assert bt.decluster(trig, 20) == [5, 30, 50]


def test_forward_returns_entry_next_open_vs_signal_close():
    o = np.array([10, 10, 20, 20, 20, 30.0])
    c = np.array([10, 10, 20, 20, 30, 30.0])
    fo = bt.forward_returns(o, c, 1, (2, 3, 9), "open")   # entry open[2]=20
    assert fo[2] == pytest.approx(0.0) and fo[3] == pytest.approx(0.5) and np.isnan(fo[9])
    fc = bt.forward_returns(o, c, 1, (3,), "close")       # entry close[1]=10
    assert fc[3] == pytest.approx(2.0)


def test_max_adverse_excursion():
    low = np.array([9, 9, 18, 15, 25, 25.0])
    assert bt.max_adverse_excursion(low, 1, 3, entry_price=20.0) == pytest.approx(15 / 20 - 1)
    assert np.isnan(bt.max_adverse_excursion(low, 1, 10, entry_price=20.0))


def test_stats_ignores_nan():
    s = bt.stats([0.1, -0.1, np.nan, 0.3])
    assert s["n"] == 3 and s["median"] == pytest.approx(0.1) and s["win"] == pytest.approx(2 / 3)


# ---------------------------------- bootstrap --------------------------------
def test_bootstrap_detects_clear_difference_and_not_noise():
    rng = np.random.default_rng(3)
    cl = [f"2015-{(i % 12) + 1:02d}" for i in range(200)]
    a = rng.normal(0.10, 0.05, 200).tolist()
    b = rng.normal(0.00, 0.05, 200).tolist()
    d, lo, hi, p = bt.cluster_bootstrap_diff(a, cl, b, cl, "median", 400, seed=1)
    assert lo > 0 and p < 0.05 and d == pytest.approx(0.10, abs=0.02)
    b2 = rng.normal(0.10, 0.05, 200).tolist()
    d, lo, hi, p = bt.cluster_bootstrap_diff(a, cl, b2, cl, "mean", 400, seed=1)
    assert lo < 0 < hi
    lo, hi = bt.cluster_bootstrap_ci(a, cl, "mean", 400, seed=1)
    assert lo < 0.10 < hi


def test_bootstrap_is_deterministic():
    cl = ["a", "b", "c", "d"] * 5
    x = list(np.linspace(-0.1, 0.1, 20))
    assert bt.cluster_bootstrap_ci(x, cl, "median", 200, 7) == bt.cluster_bootstrap_ci(x, cl, "median", 200, 7)


# ---------------------------- no look-ahead guarantee ------------------------
def _frame(n=900, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    return pd.DataFrame({"date": pd.bdate_range("2010-01-01", periods=n).strftime("%Y-%m-%d"),
                         "o": c * 0.999, "h": c * 1.01, "l": c * 0.99, "c": c,
                         "v": rng.integers(1_000, 2_000, n).astype(float), "uc": c})


def test_features_at_t_unchanged_when_future_prices_change():
    fr = _frame()
    t = 700
    base = bt.ticker_features(fr)
    fr2 = fr.copy()
    for col in ("o", "h", "l", "c", "uc"):
        fr2.loc[t + 1:, col] = fr2.loc[t + 1:, col] * 3.7
    fr2.loc[t + 1:, "v"] *= 50
    alt = bt.ticker_features(fr2)
    for k in ("ret3", "z", "rsi", "rangepos", "sma200", "volratio", "volspike", "trigger", "valid"):
        assert base[k][t] == alt[k][t] or (np.isnan(base[k][t]) and np.isnan(alt[k][t])), k
    assert (base["a_flags"][t] == alt["a_flags"][t]).all()
    truncated = bt.ticker_features(fr.iloc[:t + 1].reset_index(drop=True))
    assert truncated["rsi"][t] == pytest.approx(base["rsi"][t])
    assert truncated["z"][t] == pytest.approx(base["z"][t])


# --------------------------- point-in-time fundamentals ----------------------
def _fact(start, end, val, filed):
    return {"start": start, "end": end, "val": val, "filed": filed}


def _facts_year():
    """FY2020 quarterly operating income 10 each; Q4 only known via the 10-K (FY=40)."""
    return {"OperatingIncomeLoss": {"units": {"USD": [
        _fact("2020-01-01", "2020-03-31", 10, "2020-05-01"),
        _fact("2020-01-01", "2020-06-30", 20, "2020-08-01"),
        _fact("2020-01-01", "2020-09-30", 30, "2020-11-01"),
        _fact("2020-01-01", "2020-12-31", 40, "2021-02-15"),
    ]}}}


def test_duration_series_derives_quarters_with_filing_dates():
    q = bt.duration_series(_facts_year(), ["OperatingIncomeLoss"])
    assert [(e, v) for e, v, _ in q] == [("2020-03-31", 10), ("2020-06-30", 10),
                                          ("2020-09-30", 10), ("2020-12-31", 10)]
    assert q[-1][2] == "2021-02-15"  # Q4 usable only once the 10-K is filed


def test_ttm_uses_only_filings_on_or_before_the_date():
    q = bt.duration_series(_facts_year(), ["OperatingIncomeLoss"])
    assert bt.ttm_at(q, "2021-02-14") is None           # Q4 not filed yet, only 3 quarters
    assert bt.ttm_at(q, "2021-02-15") == 40.0


def test_restatement_filed_later_does_not_leak_into_earlier_dates():
    facts = _facts_year()
    facts["OperatingIncomeLoss"]["units"]["USD"].append(_fact("2020-01-01", "2020-12-31", 99, "2022-03-01"))
    q = bt.duration_series(facts, ["OperatingIncomeLoss"])
    assert bt.ttm_at(q, "2021-06-01") == 40.0           # original, not the 2022 restatement


def test_instant_at_respects_filed_date_and_staleness():
    s = bt.instant_series({"Cash": {"units": {"USD": [
        {"end": "2020-12-31", "val": 5, "filed": "2021-02-15"},
        {"end": "2020-12-31", "val": 7, "filed": "2022-01-01"}]}}}, ["Cash"])
    assert bt.instant_at(s, "2021-02-14") is None
    assert bt.instant_at(s, "2021-03-01") == 5.0         # earliest filed value
    assert bt.instant_at(s, "2022-03-01") is None        # stale (> 200 days)


def test_fund_metrics_unchanged_by_facts_filed_after_the_date():
    def facts(extra=False):
        g = {"OperatingIncomeLoss": _facts_year()["OperatingIncomeLoss"],
             "DepreciationDepletionAndAmortization": {"units": {"USD": [
                 _fact("2020-01-01", "2020-03-31", 2, "2020-05-01"),
                 _fact("2020-01-01", "2020-06-30", 4, "2020-08-01"),
                 _fact("2020-01-01", "2020-09-30", 6, "2020-11-01"),
                 _fact("2020-01-01", "2020-12-31", 8, "2021-02-15")]}},
             "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
                 {"end": "2020-12-31", "val": 100, "filed": "2021-02-15"}]}},
             "LongTermDebt": {"units": {"USD": [{"end": "2020-12-31", "val": 300, "filed": "2021-02-15"}]}}}
        if extra:
            g["LongTermDebt"]["units"]["USD"].append({"end": "2021-03-31", "val": 9_999, "filed": "2021-05-01"})
        return {"facts": {"us-gaap": g, "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": "2021-02-01", "val": 10, "filed": "2021-02-15"}]}}}}}
    a = bt.Fund(facts(False), []).metrics("2021-03-01", 20.0)
    b = bt.Fund(facts(True), []).metrics("2021-03-01", 20.0)
    assert a == b
    assert a["ebitda"] == 48 and a["net_debt"] == 200 and a["mcap"] == 200
    assert a["ev_ebitda"] == pytest.approx(400 / 48) and a["nd_ebitda"] == pytest.approx(200 / 48)


def test_split_scale_adjusts_stale_share_count():
    f = bt.Fund({"facts": {"us-gaap": {}, "dei": {}}}, [("2021-06-01", 0.5)])  # 2:1 split
    assert f.split_scale("2021-02-01", "2021-07-01") == pytest.approx(2.0)
    assert f.split_scale("2021-06-02", "2021-07-01") == pytest.approx(1.0)


# ---------------------------- gate / classification --------------------------
def test_quality_gate():
    assert bt.quality_gate(2.0, 5.0, 10.0) == "PASS"
    assert bt.quality_gate(3.0, 5.0, 10.0) == "FAIL"
    assert bt.quality_gate(2.0, 3.9, 10.0) == "FAIL"
    assert bt.quality_gate(2.0, 5.0, -1.0) == "FAIL"
    assert bt.quality_gate(None, 5.0, 10.0) == "UNKNOWN"
    assert bt.quality_gate(None, 5.0, -1.0) == "FAIL"   # a computable failure still fails


def test_classify_states():
    assert bt.classify([1, 1, 1, 0, 0], [True, None], "PASS")[0] == "LIKELY-OVERSOLD-PROXY"
    assert bt.classify([1, 1, 1, 0, 0], [False, False], "PASS")[0] == "PRICE-STRESSED"
    assert bt.classify([1, 1, 1, 0, 0], [True, True], "UNKNOWN")[0] == "PRICE-STRESSED"
    assert bt.classify([1, 1, 0, 0, 0], [True, True], "PASS")[0] == "NONE"
    assert bt.classify([1, 1, 1, 1, 1], [True, True], "FAIL")[0] == "FUNDAMENTAL-RISK"


def test_own_history_flag():
    hist = list(range(1, 101))                # 1..100, 20th percentile ~ 20.8
    assert bt.own_history_flag(15, hist) is True
    assert bt.own_history_flag(50, hist) is False
    assert bt.own_history_flag(15, hist[:30]) is None   # < 60 points
    assert bt.own_history_flag(float("nan"), hist) is None
    assert bt.percentile_rank(10, hist) == pytest.approx(10.0)


# ---------------------------------- FMP client --------------------------------
def test_fmp_offline_miss_raises_and_cache_hit_needs_no_network(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "CACHE", tmp_path)
    f = bt.FMP(offline=True)
    with pytest.raises(RuntimeError, match="offline"):
        f.get("profile", symbol="X")
    bt.FMP.cache_path("profile", {"symbol": "X"}).write_text(json.dumps([{"ok": 1}]))
    assert f.get("profile", symbol="X") == [{"ok": 1}]
    assert f.calls_used() == 0


def test_fmp_budget_stops_before_any_network_call(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "CACHE", tmp_path)
    f = bt.FMP(offline=False)
    f.budget_file.write_text(json.dumps({"calls": bt.FMP_BUDGET}))
    monkeypatch.setattr(f, "_api_key", lambda: pytest.fail("key must not be read when over budget"))
    with pytest.raises(bt.BudgetExceeded):
        f.get("profile", symbol="Y")


def test_fmp_errors_never_contain_the_api_key(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(bt, "CACHE", tmp_path)
    monkeypatch.setattr(bt, "FMP_PACING_S", 0)
    f = bt.FMP(offline=False)
    f._key = "SECRETKEY123"

    def boom(url, params=None, timeout=None):
        raise requests.ConnectionError(f"failed {url}?apikey={params['apikey']}")
    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(RuntimeError) as e:
        f.get("profile", symbol="Z")
    assert "SECRETKEY123" not in str(e.value)
    assert f.calls_used() == 1


def test_quality_gate_lenient_drops_uncomputable_tests():
    assert bt.quality_gate_lenient(None, 5.0, 10.0) == "PASS"          # 2 of 3 computable, all pass
    assert bt.quality_gate_lenient(None, None, 10.0) == "UNKNOWN"      # only 1 computable
    assert bt.quality_gate_lenient(None, 2.0, 10.0) == "FAIL"          # a computable failure still fails
