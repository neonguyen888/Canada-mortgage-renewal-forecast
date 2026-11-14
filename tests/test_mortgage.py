"""Unit tests for the mortgage maths. Author: Neo Nguyen

The reference case ($400k, 20 years, 2.2% to 4.2%) was worked out by hand before the code was written.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mortgage import (  # noqa: E402
    monthly_payment, payment_change, threshold_rate, threshold_rate_grid,
)

B, Y = 400_000, 20


def test_reference_case():
    assert round(monthly_payment(B, Y, 2.2), 2) == 2059.72
    assert round(monthly_payment(B, Y, 4.2), 2) == 2458.57
    assert round(payment_change(B, Y, 2.2, 4.2), 2) == 398.85


def test_zero_rate_is_straight_line():
    assert monthly_payment(B, Y, 0.0) == pytest.approx(B / 240)


def test_rate_unchanged_and_decrease():
    assert payment_change(B, Y, 4.0, 4.0) == 0
    assert payment_change(B, Y, 4.0, 3.5) < 0


@pytest.mark.parametrize("t", [100, 250, 500])
def test_threshold_rate_rate_increase(t):
    r = threshold_rate(B, Y, 2.2, t)
    assert r is not None and r > 2.2
    assert payment_change(B, Y, 2.2, r) == pytest.approx(t, abs=1e-4)


def test_threshold_from_zero_old_rate():
    r = threshold_rate(B, Y, 0.0, 250)
    assert r is not None and payment_change(B, Y, 0.0, r) == pytest.approx(250, abs=1e-4)


def test_threshold_exact_hit_is_at_threshold():
    # 4.2% gives exactly the reference increase -> solving for that increase returns 4.2%
    t = payment_change(B, Y, 2.2, 4.2)
    assert threshold_rate(B, Y, 2.2, t) == pytest.approx(4.2, abs=1e-8)


def test_threshold_zero_means_old_rate():
    assert threshold_rate(B, Y, 3.0, 0) == pytest.approx(3.0, abs=1e-8)


def test_solution_outside_search_range():
    # A $5,000/month increase on this loan needs a rate far above 10%
    assert threshold_rate(B, Y, 2.2, 5000, lo=0, hi=10) is None
    assert threshold_rate_grid(B, Y, 2.2, 5000, lo=0, hi=10) is None
    # Negative threshold below the floor of the range (payment can't fall that much)
    assert threshold_rate(B, Y, 2.2, -3000, lo=0, hi=10) is None


@pytest.mark.parametrize("old", [0.0, 1.5, 2.2, 3.89, 5.0])
@pytest.mark.parametrize("t", [100, 250, 500])
def test_grid_within_one_bp_of_exact(old, t):
    exact = threshold_rate(B, Y, old, t)
    grid = threshold_rate_grid(B, Y, old, t)
    assert exact is not None and grid is not None
    assert 0 <= grid - exact < 0.01 + 1e-9
