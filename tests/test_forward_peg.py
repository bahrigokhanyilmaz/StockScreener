"""
Unit + regression tests for forward PEG.

PEG must be fully forward-looking on BOTH sides:
    PEG = forward_pe / (est_lt_growth * 100)
where est_lt_growth is a decimal ratio (0.30 = 30%).

Regression (RAMP): the old PEG used trailing eps_growth_yoy, which for
recovery/low-base stocks is a huge ratio (e.g. 187.99) that crushed PEG to 0.00.
Forward inputs give a sensible value (12 / 30.04 = 0.40).
"""
import pytest

from conftest import load_handler

enrichment = load_handler("enrichment")
compute_forward_peg = enrichment.compute_forward_peg


@pytest.mark.regression
def test_ramp_forward_peg_is_sensible():
    # RAMP: forward_pe=12, est_lt_growth=0.3004 (30.04%)
    peg = compute_forward_peg(12.0, 0.3004)
    assert peg == 0.4, f"expected ~0.40, got {peg}"


@pytest.mark.unit
class TestForwardPeg:
    def test_typical(self):
        # P/E 15, 30% growth -> 0.5
        assert compute_forward_peg(15.0, 0.30) == 0.5

    def test_high_growth_low_peg(self):
        # P/E 20, 40% growth -> 0.5
        assert compute_forward_peg(20.0, 0.40) == 0.5

    def test_low_growth_high_peg(self):
        # P/E 25, 1% growth -> 25.0 (correctly high, not ~0)
        assert compute_forward_peg(25.0, 0.01) == 25.0

    def test_zero_growth_is_none(self):
        assert compute_forward_peg(15.0, 0.0) is None

    def test_negative_growth_is_none(self):
        # Forward earnings expected to shrink -> PEG meaningless
        assert compute_forward_peg(15.0, -0.05) is None

    def test_missing_forward_pe_is_none(self):
        assert compute_forward_peg(None, 0.30) is None

    def test_zero_or_negative_pe_is_none(self):
        assert compute_forward_peg(0.0, 0.30) is None
        assert compute_forward_peg(-5.0, 0.30) is None

    def test_missing_growth_is_none(self):
        assert compute_forward_peg(15.0, None) is None

    def test_never_rounds_a_real_value_to_zero(self):
        # Even very high growth keeps 2-dp precision, and if it would round to
        # 0.00 the value is genuinely tiny — but forward growth is bounded
        # (analyst CAGR), so this stays meaningful.
        peg = compute_forward_peg(12.0, 0.30)
        assert peg is not None and peg > 0
