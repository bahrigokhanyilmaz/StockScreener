"""
Unit tests for 3-digit SIC P/E-quartile fallback.

When a 4-digit SIC industry has fewer than 5 valid-P/E peers, its P/E median
falls back to the 3-digit SIC group's median (if that has >= 5). Stops at
3 digits — never rolls up to 2-digit. If the 3-digit group is also thin, the
industry gets no median (blank).
"""
import pytest

from conftest import load_handler

enrichment = load_handler("enrichment")
compute = enrichment.compute_industry_pe_thresholds


@pytest.mark.unit
def test_four_digit_used_when_enough_peers():
    # 3663 has 5 valid P/Es -> uses its own median, no fallback.
    pe_by_sic = {"3663": [10, 12, 14, 16, 18]}
    res = compute(pe_by_sic)
    assert res["median"]["3663"] == 14  # index 2 of sorted 5
    assert res["source"]["3663"] == "4-digit"


@pytest.mark.unit
def test_thin_four_digit_falls_back_to_three_digit():
    # 3663 thin (3 peers) but the 3-digit group 366 has >=5 combined -> fallback.
    pe_by_sic = {
        "3663": [20, 22, 24],          # QCOM-like, thin
        "3661": [8, 10],               # telephone apparatus
        "3669": [12, 15, 18],          # other comms equipment
    }
    res = compute(pe_by_sic)
    # group 366 combined = [8,10,12,15,18,20,22,24] (8 values) median idx 4 = 18
    assert res["median"]["3663"] == 18
    assert res["source"]["3663"] == "3-digit"
    # A sibling that was also thin gets the same group fallback.
    assert res["median"]["3661"] == 18
    assert res["source"]["3661"] == "3-digit"


@pytest.mark.unit
def test_blank_when_both_levels_thin():
    # 3663 has 2, and its 3-digit group 366 total is only 4 -> still < 5 -> blank.
    pe_by_sic = {"3663": [20, 22], "3669": [10, 12]}
    res = compute(pe_by_sic)
    assert "3663" not in res["median"]
    assert "3669" not in res["median"]


@pytest.mark.unit
def test_no_rollup_to_two_digit():
    # Two different 3-digit groups under the same 2-digit major (36). Each is thin
    # and must NOT be merged at the 2-digit level.
    pe_by_sic = {"3612": [5, 6], "3663": [20, 22]}  # 361 vs 366, both thin
    res = compute(pe_by_sic)
    assert res["median"] == {}  # nothing qualifies; no 2-digit merge


@pytest.mark.unit
def test_q1_also_computed():
    pe_by_sic = {"3663": [10, 12, 14, 16, 18, 20, 22, 24]}
    res = compute(pe_by_sic)
    assert "3663" in res["q1"]
    # q1 index = len//4 = 2 -> value 14
    assert res["q1"]["3663"] == 14
