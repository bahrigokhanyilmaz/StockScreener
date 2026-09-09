"""
Unit tests for the core screening filter logic (apply_filter).

Locks in two contract-critical behaviors from the design principles:
  - "Missing Data = FAIL": a None value never passes.
  - percent_as_decimal conversion: data stored as 0.20 must compare correctly
    against a slider threshold expressed as 20.
"""
import pytest

from conftest import load_handler

screener = load_handler("stock-screener")
apply_filter = screener.apply_filter


@pytest.mark.unit
class TestApplyFilterRatio:
    def test_max_passes_when_below_threshold(self):
        assert apply_filter(15.0, "max", 50.0, "ratio") is True

    def test_max_fails_when_above_threshold(self):
        assert apply_filter(60.0, "max", 50.0, "ratio") is False

    def test_max_passes_at_exact_threshold(self):
        assert apply_filter(50.0, "max", 50.0, "ratio") is True

    def test_min_passes_when_above_threshold(self):
        assert apply_filter(2.0, "min", 1.0, "ratio") is True

    def test_min_fails_when_below_threshold(self):
        assert apply_filter(0.5, "min", 1.0, "ratio") is False


@pytest.mark.unit
class TestApplyFilterMissingData:
    def test_none_fails_min(self):
        assert apply_filter(None, "min", 1.0, "ratio") is False

    def test_none_fails_max(self):
        assert apply_filter(None, "max", 50.0, "ratio") is False


@pytest.mark.unit
class TestApplyFilterPercentConversion:
    def test_percent_min_passes(self):
        # data 0.22 (22%) vs slider 20 -> 0.20; 0.22 >= 0.20 -> pass
        assert apply_filter(0.22, "min", 20.0, "percent_as_decimal") is True

    def test_percent_min_fails(self):
        # data 0.05 (5%) vs slider 20 -> 0.20; 0.05 < 0.20 -> fail
        assert apply_filter(0.05, "min", 20.0, "percent_as_decimal") is False

    def test_percent_zero_threshold_positive_value(self):
        # "operating margin > 0%" — data 0.03 vs threshold 0 -> pass
        assert apply_filter(0.03, "min", 0.0, "percent_as_decimal") is True

    def test_percent_zero_threshold_negative_value(self):
        # negative operating margin fails the > 0% gate
        assert apply_filter(-0.01, "min", 0.0, "percent_as_decimal") is False


@pytest.mark.unit
def test_unknown_filter_type_fails_safe():
    assert apply_filter(1.0, "bogus", 1.0, "ratio") is False
