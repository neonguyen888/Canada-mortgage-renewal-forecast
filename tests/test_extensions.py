"""Tests for the portfolio balance maths and the Diebold-Mariano helper. Author: Neo Nguyen"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from intervals import diebold_mariano  # noqa: E402
from portfolio import remaining_balance  # noqa: E402


def test_balance_unchanged_before_any_payment():
    assert remaining_balance(500_000, 25, 2.5, 0) == pytest.approx(500_000)


def test_balance_is_zero_at_end_of_amortization():
    assert remaining_balance(500_000, 25, 2.5, 300) == pytest.approx(0, abs=1e-6)
    assert remaining_balance(500_000, 25, 0.0, 300) == pytest.approx(0, abs=1e-6)


def test_balance_after_five_years_is_plausible():
    # Hand check: payment ~$2,240, first-month principal ~$1,210, so roughly $77k (15%) is repaid in 5 years
    b = remaining_balance(500_000, 25, 2.5, 60)
    assert 420_000 < b < 426_000


def test_dm_identical_errors_has_zero_difference():
    e = np.random.default_rng(0).normal(0, 0.2, 100)
    diff, _, _ = diebold_mariano(e + 1e-12, e, 3)
    assert diff == pytest.approx(0, abs=1e-6)


def test_dm_clearly_better_model_is_significant():
    rng = np.random.default_rng(1)
    small, large = rng.normal(0, 0.1, 150), rng.normal(0, 0.5, 150)
    diff, stat, p = diebold_mariano(small, large, 3)
    assert diff < 0 and stat < 0 and p < 0.01
