"""Synthetic portfolio of mortgages renewing in the next 90 days.

Author: Neo Nguyen

No real borrower data exists in this project, so the portfolio is simulated with a fixed seed.
The idea is to make it behave like a real renewal book: each loan was signed five years before
it renews, at roughly the market rate of that month, and it renews at roughly the market rate
the model expects for its renewal month. The SQL layer then groups these loans into cohorts.

Every assumption is a constant at the top of this file so it can be changed in one place.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import PROCESSED_DIR
from mortgage import monthly_payment, monthly_rate

SEED = 42
N_LOANS = 5_000
AS_OF = pd.Timestamp("2026-11-14")
WINDOW_DAYS = 90
TERM_MONTHS = 60                     # every loan is coming off a 5-year fixed term
SPREAD_SD_BPS = 15                   # borrower-level discount or premium vs the market average
MIN_RATE = 0.5                       # floor so the spread can't produce silly rates
MEDIAN_PRINCIPAL, PRINCIPAL_SIGMA = 420_000, 0.35
PRINCIPAL_MIN, PRINCIPAL_MAX = 150_000, 1_200_000
AMORT_CHOICES, AMORT_WEIGHTS = (25, 30), (0.75, 0.25)


def remaining_balance(principal: float, amort_years: int, rate_pct: float, months_paid: int) -> float:
    """Balance left after `months_paid` regular payments (standard annuity formula)."""
    pmt = monthly_payment(principal, amort_years, rate_pct)
    i = monthly_rate(rate_pct)
    if i == 0:
        return principal - pmt * months_paid
    g = (1 + i) ** months_paid
    return principal * g - pmt * (g - 1) / i


def simulate() -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    actual = pd.read_csv(PROCESSED_DIR / "rate_actual.csv", parse_dates=["month"]).set_index("month")["rate_pct"]
    fc = pd.read_csv(PROCESSED_DIR / "forecast_latest.csv", parse_dates=["target_month"])
    fc = fc[fc.is_selected_model].set_index("target_month")["forecast_rate_pct"]

    renewal_date = AS_OF + pd.to_timedelta(rng.integers(1, WINDOW_DAYS + 1, N_LOANS), unit="D")
    renewal_month = renewal_date.to_period("M").to_timestamp()
    origination_month = renewal_month - pd.DateOffset(months=TERM_MONTHS)

    spread = rng.normal(0, SPREAD_SD_BPS, N_LOANS) / 100
    original_rate = np.maximum(origination_month.map(actual).values + spread, MIN_RATE)
    # Renewal months past the last forecast fall back to the furthest forecast we have.
    renewal_mkt = np.array([fc.get(m, fc.iloc[-1]) for m in renewal_month])
    renewal_rate = np.maximum(renewal_mkt + spread, MIN_RATE)

    principal = np.clip(rng.lognormal(np.log(MEDIAN_PRINCIPAL), PRINCIPAL_SIGMA, N_LOANS), PRINCIPAL_MIN, PRINCIPAL_MAX)
    amort = rng.choice(AMORT_CHOICES, N_LOANS, p=AMORT_WEIGHTS)

    df = pd.DataFrame({
        "loan_id": [f"L{n:05d}" for n in range(1, N_LOANS + 1)],
        "origination_month": origination_month.strftime("%Y-%m-01"),
        "renewal_date": renewal_date.strftime("%Y-%m-%d"),
        "renewal_month": renewal_month.strftime("%Y-%m-01"),
        "original_principal": principal.round(2),
        "amortization_years": amort,
        "original_rate_pct": original_rate.round(4),
        "renewal_rate_pct": renewal_rate.round(4),
    })
    df["balance_at_renewal"] = [round(remaining_balance(p, a, r, TERM_MONTHS), 2)
                                for p, a, r in zip(df.original_principal, df.amortization_years, df.original_rate_pct)]
    df["years_remaining"] = df.amortization_years - TERM_MONTHS // 12
    return df


def main():
    df = simulate()
    df.to_csv(PROCESSED_DIR / "renewal_portfolio.csv", index=False)
    print(df.head().to_string(index=False))
    print(f"\n{len(df)} loans, renewal months: {sorted(df.renewal_month.unique())}")
    print(df[["original_rate_pct", "renewal_rate_pct", "balance_at_renewal"]].describe().round(2).to_string())


if __name__ == "__main__":
    main()
