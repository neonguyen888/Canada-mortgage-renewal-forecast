"""Regression: does the 5-year bond yield help forecast the mortgage rate?

Author: Neo Nguyen

Banks price 5-year fixed mortgages off the 5-year government bond yield, with a delay of a few
months (borrowers lock in a rate up to 120 days ahead). The model:

    mortgage rate change this month = a + b0 * yield change this month
                                        + b1 * yield change 1 month ago + ... + bK * K months ago

Each b is the share of a yield move that shows up after that many months; their sum is the
total pass-through. Monthly changes are used instead of levels, because two rising series
always look related even when they are not.

- K (number of lags) is chosen by BIC on 2013-2015 only, before the backtest starts.
- The yield is published daily, the mortgage rate about 2 months late, so at each origin the
  model already knows 2 extra months of yields. A second version without that head start
  shows how much of the gain it explains.
"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm

from config import HAC_LAGS, LAG_SELECTION_END, PROCESSED_DIR, REG_MAX_LAG, YIELD_KNOWN_MONTHS
from forecast import MAX_H, MODELS, backtest_origins, load_actuals, score
from intervals import diebold_mariano

REG_MODEL = "Bond-yield regression"
REG_ONE_MONTH = "Regression, 1 month of extra yields"
REG_ORIGIN_ONLY = "Regression, yields to origin only"
# How many months of yields past the origin each version gets. The real lag is about 2 months,
# but the Bank's historical release dates aren't available, so 0 and 1 are shown as well.
REG_VERSIONS = {REG_MODEL: YIELD_KNOWN_MONTHS, REG_ONE_MONTH: 1, REG_ORIGIN_ONLY: 0}
NAIVE, ETS = "Naive (last observed)", "ETS damped trend"


def load_yields() -> pd.Series:
    df = pd.read_csv(PROCESSED_DIR / "bond_yield_monthly.csv", parse_dates=["month"])
    y = df.set_index("month")["yield_pct"].asfreq("MS")
    assert y.notna().all()
    return y


def design(m: pd.Series, y: pd.Series, lag: int) -> pd.DataFrame:
    """One row per month: the change in the mortgage rate and the yield changes at lags 0..lag."""
    dy = y.diff()
    X = pd.DataFrame({f"dy_lag{k}": dy.shift(k) for k in range(lag + 1)})
    X.insert(0, "dm", m.diff().reindex(X.index))
    return X


def fit_ols(m: pd.Series, y: pd.Series, lag: int, hac: bool = False):
    # Only months up to the origin, so the backtest never peeks at the future.
    D = design(m, y, lag).loc[: m.index[-1]].dropna()
    X = sm.add_constant(D.drop(columns="dm"))
    if hac:
        # Newey-West: errors in nearby months are related; this keeps the p-values honest.
        return sm.OLS(D["dm"], X).fit(cov_type="HAC", cov_kwds={"maxlags": HAC_LAGS})
    return sm.OLS(D["dm"], X).fit()


def select_lag(m: pd.Series, y: pd.Series) -> tuple[int, pd.DataFrame]:
    """Pick K by BIC on data up to LAG_SELECTION_END. Every K uses the same months so BIC compares like with like."""
    D = design(m, y, REG_MAX_LAG).loc[:LAG_SELECTION_END].dropna()
    rows = []
    for k in range(REG_MAX_LAG + 1):
        cols = [f"dy_lag{j}" for j in range(k + 1)]
        r = sm.OLS(D["dm"], sm.add_constant(D[cols])).fit()
        rows.append({"max_lag_months": k, "bic": round(r.bic, 2), "aic": round(r.aic, 2), "n_months": int(r.nobs)})
    table = pd.DataFrame(rows)
    return int(table.loc[table.bic.idxmin(), "max_lag_months"]), table


def forecast_regression(m_train: pd.Series, y_known: pd.Series, lag: int, h_max: int = MAX_H) -> list[float]:
    """Forecast 1..h_max months ahead.

    Unknown future yields are assumed flat (change = 0). Predicted monthly changes are added
    to the last published mortgage rate.
    """
    params = fit_ols(m_train, y_known, lag).params.values
    dy = y_known.diff()
    origin = m_train.index[-1]
    level, out = float(m_train.iloc[-1]), []
    for h in range(1, h_max + 1):
        t = origin + pd.DateOffset(months=h)
        x = [1.0] + [float(dy.get(t - pd.DateOffset(months=k), 0.0)) for k in range(lag + 1)]
        level += float(np.dot(params, x))
        out.append(level)
    return out


def run_backtest(m: pd.Series, y: pd.Series, lag: int) -> pd.DataFrame:
    """Naive, ETS and the three regression versions on the same origins, horizons 1 to 6."""
    rows = []
    for o in backtest_origins(m):
        train = m.loc[:o]
        fcs = {
            NAIVE: MODELS[NAIVE](train, MAX_H),
            ETS: MODELS[ETS](train, MAX_H),
        }
        for name, extra in REG_VERSIONS.items():
            fcs[name] = forecast_regression(train, y.loc[: o + pd.DateOffset(months=extra)], lag)
        for name, fc in fcs.items():
            for h in range(1, MAX_H + 1):
                rows.append({"origin_month": o, "target_month": o + pd.DateOffset(months=h), "model": name,
                             "horizon_months": h, "forecast_rate_pct": round(fc[h - 1], 4)})
    return pd.DataFrame(rows)


def coefficient_table(fit) -> tuple[pd.DataFrame, dict]:
    ci = fit.conf_int()
    tab = pd.DataFrame({
        "term": fit.params.index,
        "lag_months": [None if t == "const" else int(t.removeprefix("dy_lag")) for t in fit.params.index],
        "coef": fit.params.values, "std_err_hac": fit.bse.values, "t_stat": fit.tvalues.values,
        "p_value": fit.pvalues.values, "ci95_low": ci[0].values, "ci95_high": ci[1].values,
    })
    lag_rows = tab["lag_months"].notna()
    tab.loc[lag_rows, "cumulative_pass_through"] = tab.loc[lag_rows, "coef"].cumsum()
    # Total pass-through = sum of the b's, with its own standard error.
    names = [t for t in fit.params.index if t != "const"]
    test = fit.t_test(" + ".join(names) + " = 0")
    total = {"pass_through": float(test.effect[0]), "pass_through_se": float(np.ravel(test.sd)[0]),
             "pass_through_p_value": float(test.pvalue)}
    return tab.round(4), total


def main():
    m, y = load_actuals(), load_yields()
    if y.index[-1] < m.index[-1] + pd.DateOffset(months=YIELD_KNOWN_MONTHS):
        warnings.warn("Bond yields do not yet cover two months past the last mortgage month; "
                      "the latest forecast will use fewer known yields than the backtest did.")

    lag, lag_table = select_lag(m, y)
    fit = fit_ols(m, y, lag, hac=True)
    coefs, total = coefficient_table(fit)

    bt = run_backtest(m, y, lag)
    bt_scored, summ = score(bt, m)

    dm_rows = []
    for h in (3, 6):
        w = bt_scored[bt_scored.horizon_months == h].pivot(index="origin_month", columns="model", values="error_bps")
        for other in (ETS, NAIVE):
            diff_bps, stat, p = diebold_mariano(w[REG_MODEL].values / 100, w[other].values / 100, h)
            dm_rows.append({"comparison": f"{REG_MODEL} vs {other}", "horizon_months": h, "n_origins": len(w),
                            "mean_abs_error_diff_bps": round(diff_bps, 2), "dm_stat_hln": round(stat, 3),
                            "p_value": round(p, 4), "significant_at_5pct": p < 0.05})
    dm = pd.DataFrame(dm_rows)

    latest = pd.DataFrame({
        "origin_month": m.index[-1],
        "target_month": [m.index[-1] + pd.DateOffset(months=h) for h in range(1, MAX_H + 1)],
        "horizon_months": range(1, MAX_H + 1),
        "forecast_rate_pct": [round(v, 4) for v in forecast_regression(m, y, lag)],
        "model": REG_MODEL,
    })
    latest["yield_known"] = latest["target_month"] <= y.index[-1]

    month_cols = lambda d: d.assign(**{c: d[c].dt.strftime("%Y-%m-01") for c in ("origin_month", "target_month")})
    month_cols(bt).to_csv(PROCESSED_DIR / "regression_backtest.csv", index=False)
    month_cols(latest).to_csv(PROCESSED_DIR / "regression_forecast_latest.csv", index=False)
    summ.assign(first_origin=summ.first_origin.dt.strftime("%Y-%m"), last_origin=summ.last_origin.dt.strftime("%Y-%m")) \
        .round(4).to_csv(PROCESSED_DIR / "regression_backtest_summary_python.csv", index=False)
    coefs.to_csv(PROCESSED_DIR / "regression_coefficients.csv", index=False)
    dm.to_csv(PROCESSED_DIR / "regression_dm_test.csv", index=False)
    info = {
        "model": REG_MODEL,
        "equation": "change in mortgage rate(t) = a + sum over k=0..K of b_k * change in 5-year yield(t-k)",
        "selected_max_lag_months": lag,
        "lag_selection": {"criterion": "BIC", "data_through": LAG_SELECTION_END, "table": lag_table.to_dict("records")},
        "fit_sample": f"{fit.model.data.row_labels[0]:%Y-%m} to {fit.model.data.row_labels[-1]:%Y-%m}",
        "n_months": int(fit.nobs), "r_squared": round(fit.rsquared, 4), "adj_r_squared": round(fit.rsquared_adj, 4),
        "durbin_watson": round(float(sm.stats.durbin_watson(fit.resid)), 3),
        "standard_errors": f"Newey-West HAC, {HAC_LAGS} lags",
        **{k: round(v, 4) for k, v in total.items()},
        "yields_known_months_in_backtest": YIELD_KNOWN_MONTHS,
        "latest_yield_month": f"{y.index[-1]:%Y-%m}",
        "latest_mortgage_month": f"{m.index[-1]:%Y-%m}",
    }
    (PROCESSED_DIR / "regression_fit.json").write_text(json.dumps(info, indent=2, default=str))

    print(lag_table.to_string(index=False), f"\nSelected K = {lag}\n")
    print(fit.summary().tables[1])
    print(json.dumps({k: info[k] for k in ("r_squared", "adj_r_squared", "pass_through", "pass_through_se",
                                           "pass_through_p_value", "durbin_watson")}, indent=1))
    print(summ[summ.horizon_months.isin([1, 2, 3, 6])].drop(columns=["first_origin", "last_origin"]).round(2).to_string(index=False))
    print(dm.to_string(index=False))
    print(latest.to_string(index=False))


if __name__ == "__main__":
    main()
