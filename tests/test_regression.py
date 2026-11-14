"""Tests for the bond-yield regression. Author: Neo Nguyen"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from regression import fit_ols, forecast_regression, select_lag  # noqa: E402

MONTHS = pd.date_range("2012-01-01", "2026-08-01", freq="MS")


def make_series(b=(0.1, 0.3, 0.2), noise=0.0, seed=0):
    """Fake yield (random walk) and a mortgage rate built from it with known lag weights b."""
    rng = np.random.default_rng(seed)
    y = pd.Series(2 + np.cumsum(rng.normal(0, 0.1, len(MONTHS))), index=MONTHS)
    dy = y.diff().fillna(0)
    dm = sum(bk * dy.shift(k).fillna(0) for k, bk in enumerate(b)) + rng.normal(0, noise, len(MONTHS))
    m = 4 + dm.cumsum()
    return m, y


def test_ols_recovers_known_lag_weights():
    m, y = make_series(noise=0.005)
    fit = fit_ols(m, y, lag=2)
    assert fit.params[["dy_lag0", "dy_lag1", "dy_lag2"]].values == pytest.approx([0.1, 0.3, 0.2], abs=0.02)


def test_forecast_uses_known_yields_then_assumes_flat():
    m, y = make_series()                       # exact relationship, no noise
    origin = pd.Timestamp("2020-06-01")
    known = y.loc[: origin + pd.DateOffset(months=2)]
    fc = forecast_regression(m.loc[:origin], known, lag=2, h_max=6)
    # months 1-2: yields are known, so the forecast hits the true value
    assert fc[:2] == pytest.approx(m.loc["2020-07-01":"2020-08-01"].values, abs=1e-6)
    # from month 5 on, every lag points at a month with no yield yet, so the forecast stops moving
    assert fc[4] == pytest.approx(fc[5], abs=1e-9)


def test_lag_choice_ignores_data_after_2015():
    m, y = make_series(noise=0.01)
    chosen, _ = select_lag(m, y)
    y2 = y.copy()
    y2.loc["2016-01-01":] += np.linspace(0, 3, len(y2.loc["2016-01-01":]))   # change only later years
    chosen2, _ = select_lag(m, y2)
    assert chosen == chosen2
