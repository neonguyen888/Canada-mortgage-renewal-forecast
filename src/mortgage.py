"""Canadian fixed-rate mortgage maths.

Author: Neo Nguyen

Canadian fixed-rate mortgages quote an annual rate compounded twice a year, while payments are
monthly. So the first step is always converting the quoted rate into the equivalent monthly rate;
using rate/12 instead (the US habit) overstates the payment by a few dollars a month.

At renewal the borrower doesn't start over. The payment is recalculated on the balance still owed,
over the years left on the amortization, at the new rate. Everything here follows that setup.
"""
from __future__ import annotations

from config import GRID_MAX_RATE, GRID_MIN_RATE, GRID_STEP


def monthly_rate(annual_rate_pct: float) -> float:
    """Monthly rate that compounds to the same half-year growth as the quoted rate.

    Example: 4.2% quoted -> 2.1% per half year -> (1.021)^(1/6) - 1 = 0.3469% per month.
    """
    return (1 + annual_rate_pct / 100 / 2) ** (1 / 6) - 1


def monthly_payment(balance: float, years_remaining: float, annual_rate_pct: float) -> float:
    """Monthly principal + interest on the balance at renewal over the remaining amortization."""
    if balance <= 0:
        return 0.0
    n = round(years_remaining * 12)
    if n <= 0:
        raise ValueError("years_remaining must be positive")
    i = monthly_rate(annual_rate_pct)
    # At 0% there is no interest to amortize, the annuity formula divides by zero, and the
    # payment is just the balance split evenly across the remaining months.
    if abs(i) < 1e-15:
        return balance / n
    return balance * i / (1 - (1 + i) ** -n)


def payment_change(balance: float, years_remaining: float, old_rate_pct: float, new_rate_pct: float) -> float:
    return monthly_payment(balance, years_remaining, new_rate_pct) - monthly_payment(balance, years_remaining, old_rate_pct)


def threshold_rate(
    balance: float,
    years_remaining: float,
    old_rate_pct: float,
    threshold_cad: float,
    lo: float = GRID_MIN_RATE,
    hi: float = GRID_MAX_RATE,
    tol: float = 1e-10,
) -> float | None:
    """Renewal rate (%) at which the payment rises by exactly `threshold_cad` vs the old rate.

    The payment is strictly increasing in the rate, so bisection on [lo, hi] is exact.
    Returns None when the solution lies outside [lo, hi], the 0% to 10% range the report searches.
    """
    # A higher rate always means a higher payment, so the gap between the payment increase and the
    # threshold changes sign exactly once. Halving the interval until it is tiny finds that point.
    f = lambda r: payment_change(balance, years_remaining, old_rate_pct, r) - threshold_cad
    f_lo, f_hi = f(lo), f(hi)
    if f_lo > 0 or f_hi < 0:
        return None
    if f_lo == 0:
        return lo
    a, b = lo, hi
    while b - a > tol:
        m = (a + b) / 2
        if f(m) < 0:
            a = m
        else:
            b = m
    return (a + b) / 2


def threshold_rate_grid(
    balance: float,
    years_remaining: float,
    old_rate_pct: float,
    threshold_cad: float,
    lo: float = GRID_MIN_RATE,
    hi: float = GRID_MAX_RATE,
    step: float = GRID_STEP,
) -> float | None:
    """Grid version, the same search the SQL view and the Power BI measure do: the lowest grid rate
    whose increase reaches the threshold.

    Result is always within one step (1 bp by default) ABOVE the exact solution.
    """
    k_max = round((hi - lo) / step)
    for k in range(k_max + 1):
        r = round(lo + k * step, 6)
        # The tiny tolerance stops floating-point noise from skipping a grid rate that hits the threshold exactly.
        if payment_change(balance, years_remaining, old_rate_pct, r) >= threshold_cad - 1e-9:
            return r
    return None


