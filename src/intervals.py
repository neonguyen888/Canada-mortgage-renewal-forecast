"""Forecast uncertainty and a significance test for the backtest.

Author: Neo Nguyen

Two questions get answered here:

1. How wide should the band around the latest forecast be? The band comes from the selected
   model's own past mistakes. We rerun the rolling-origin backtest for every horizon from 1 to 6
   months, collect "actual minus forecast" at each horizon, and take the 10th and 90th
   percentiles. Adding those to today's forecast gives an 80% band that reflects how this model
   has actually performed on this series, with no normality assumption.

2. Is ETS really better than the naive baseline, or did it get lucky on this sample?
   The Diebold-Mariano test compares the two models' absolute errors origin by origin.
   Forecasts made one month apart overlap, so their errors are correlated; the Newey-West
   variance (lag h-1) and the Harvey-Leybourne-Newbold correction account for that.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from config import PROCESSED_DIR
from forecast import MAX_H, MODELS, backtest_origins, load_actuals

INTERVAL_LEVEL = 0.80
LOWER_Q, UPPER_Q = (1 - INTERVAL_LEVEL) / 2, 1 - (1 - INTERVAL_LEVEL) / 2


def errors_all_horizons(s: pd.Series, model: str) -> pd.DataFrame:
    """Actual minus forecast for horizons 1..6, using the same origins as the main backtest."""
    fn = MODELS[model]
    rows = []
    for o in backtest_origins(s):
        fc = fn(s.loc[:o], MAX_H)
        for h in range(1, MAX_H + 1):
            target = o + pd.DateOffset(months=h)
            rows.append({"origin_month": o, "horizon_months": h, "error_pct": s[target] - fc[h - 1]})
    return pd.DataFrame(rows)


def build_intervals(errors: pd.DataFrame, forecast_latest: pd.DataFrame, model: str) -> pd.DataFrame:
    q = (errors.groupby("horizon_months")["error_pct"]
               .agg(err_lo=lambda x: x.quantile(LOWER_Q), err_hi=lambda x: x.quantile(UPPER_Q), n_errors="count")
               .reset_index())
    fc = forecast_latest[forecast_latest.model == model][["origin_month", "target_month", "horizon_months", "forecast_rate_pct"]]
    out = fc.merge(q, on="horizon_months")
    out["lower_80_pct"] = (out.forecast_rate_pct + out.err_lo).round(4)
    out["upper_80_pct"] = (out.forecast_rate_pct + out.err_hi).round(4)
    out["band_width_bps"] = ((out.upper_80_pct - out.lower_80_pct) * 100).round(1)
    out["model"] = model
    return out.drop(columns=["err_lo", "err_hi"])


def diebold_mariano(e_a: np.ndarray, e_b: np.ndarray, h: int) -> tuple[float, float, float]:
    """DM test on absolute-error loss. Negative statistic means model A has smaller errors.

    Returns (mean loss difference in bps, HLN-corrected statistic, two-sided p-value).
    """
    d = np.abs(e_a) - np.abs(e_b)
    T = len(d)
    d_bar = d.mean()
    dc = d - d_bar
    # Long-run variance: autocovariances up to lag h-1, because h-step forecasts from consecutive
    # origins share h-1 months of unknown future and their errors move together.
    gamma = [np.sum(dc[k:] * dc[: T - k]) / T for k in range(h)]
    lrv = gamma[0] + 2 * sum(gamma[1:])
    dm = d_bar / np.sqrt(lrv / T)
    hln = dm * np.sqrt((T + 1 - 2 * h + h * (h - 1) / T) / T)
    p = 2 * stats.t.sf(abs(hln), df=T - 1)
    return d_bar * 100, hln, p


def main():
    s = load_actuals()
    fc_latest = pd.read_csv(PROCESSED_DIR / "forecast_latest.csv", parse_dates=["origin_month", "target_month"])
    selected = fc_latest.loc[fc_latest.is_selected_model, "model"].iloc[0]

    errs = errors_all_horizons(s, selected)
    intervals = build_intervals(errs, fc_latest, selected)
    intervals.assign(origin_month=intervals.origin_month.dt.strftime("%Y-%m-01"),
                     target_month=intervals.target_month.dt.strftime("%Y-%m-01")) \
             .to_csv(PROCESSED_DIR / "forecast_intervals.csv", index=False)
    print("80% empirical band around the latest forecast:")
    print(intervals.to_string(index=False))

    # DM test reuses the stored 3- and 6-month backtest so it is the exact same sample as the MAE table.
    bt = pd.read_csv(PROCESSED_DIR / "backtest_forecasts.csv", parse_dates=["target_month"])
    bt["error_pct"] = bt["forecast_rate_pct"] - bt["target_month"].map(s)
    rows = []
    for h in sorted(bt.horizon_months.unique()):
        w = bt[bt.horizon_months == h].pivot(index="origin_month", columns="model", values="error_pct")
        diff_bps, stat, p = diebold_mariano(w["ETS damped trend"].values, w["Naive (last observed)"].values, h)
        rows.append({"horizon_months": h, "n_origins": len(w), "mean_abs_error_diff_bps": round(diff_bps, 2),
                     "dm_stat_hln": round(stat, 3), "p_value": round(p, 4),
                     "significant_at_5pct": p < 0.05})
    dm = pd.DataFrame(rows)
    dm.to_csv(PROCESSED_DIR / "dm_test.csv", index=False)
    print("\nDiebold-Mariano (ETS vs naive, absolute error):")
    print(dm.to_string(index=False))


if __name__ == "__main__":
    main()
