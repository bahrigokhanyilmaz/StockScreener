"""
Unit tests for the 'revenue acceleration underpriced' advisory flag.

Flags a stock when BOTH:
  - forward revenue growth > trailing revenue growth (accelerating), AND
  - its P/S is below its industry-median P/S (cheap vs peers on sales).

Display-only badge; never gates the screen.
"""
import pytest
from conftest import load_handler

enrichment = load_handler("enrichment")
flag = enrichment.is_revenue_accel_underpriced


@pytest.mark.unit
def test_flagged_when_accelerating_and_cheap():
    # fwd 0.15 > trailing 0.05 (accelerating); P/S 1.5 < industry median 3.0
    assert flag(fwd_rev=0.15, trailing_rev=0.05, ps=1.5, industry_median_ps=3.0) is True


@pytest.mark.unit
def test_not_flagged_when_not_accelerating():
    # fwd <= trailing -> not accelerating
    assert flag(fwd_rev=0.05, trailing_rev=0.10, ps=1.5, industry_median_ps=3.0) is False


@pytest.mark.unit
def test_not_flagged_when_not_cheap():
    # P/S >= industry median -> already priced
    assert flag(fwd_rev=0.15, trailing_rev=0.05, ps=4.0, industry_median_ps=3.0) is False


@pytest.mark.unit
def test_equal_growth_is_not_acceleration():
    assert flag(fwd_rev=0.10, trailing_rev=0.10, ps=1.5, industry_median_ps=3.0) is False


@pytest.mark.unit
@pytest.mark.parametrize("fwd,trail,ps,med", [
    (None, 0.05, 1.5, 3.0),
    (0.15, None, 1.5, 3.0),
    (0.15, 0.05, None, 3.0),
    (0.15, 0.05, 1.5, None),
])
def test_missing_inputs_not_flagged(fwd, trail, ps, med):
    assert flag(fwd_rev=fwd, trailing_rev=trail, ps=ps, industry_median_ps=med) is False


@pytest.mark.unit
def test_nonpositive_ps_not_flagged():
    # negative/zero P/S is a data artifact, don't flag
    assert flag(fwd_rev=0.15, trailing_rev=0.05, ps=0, industry_median_ps=3.0) is False
    assert flag(fwd_rev=0.15, trailing_rev=0.05, ps=-1, industry_median_ps=3.0) is False


ps_median = enrichment.compute_industry_ps_medians


@pytest.mark.unit
def test_ps_median_four_digit_when_enough():
    r = ps_median({"3663": [1, 2, 3, 4, 5]})
    assert r["median"]["3663"] == 3
    assert r["source"]["3663"] == "4-digit"


@pytest.mark.unit
def test_ps_median_three_digit_fallback():
    # 3663 thin, 3-digit group 366 combined >=5 -> fallback
    r = ps_median({"3663": [2, 3], "3669": [1, 4, 5]})
    # group 366 = [1,2,3,4,5] median idx 2 = 3
    assert r["median"]["3663"] == 3
    assert r["source"]["3663"] == "3-digit"


@pytest.mark.unit
def test_ps_median_blank_when_both_thin():
    r = ps_median({"3663": [2, 3], "3669": [1, 4]})  # group total 4 < 5
    assert "3663" not in r["median"]


@pytest.mark.unit
def test_ps_median_no_two_digit_rollup():
    r = ps_median({"3612": [1, 2], "3663": [3, 4]})  # different 3-digit groups
    assert r["median"] == {}
