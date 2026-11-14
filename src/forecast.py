"""Rolling-origin backtest (naive vs damped-trend ETS) and the forecast from the latest month.

Author: Neo Nguyen

The backtest replays history. Starting in January 2016, we pretend each month is "today", fit
both models on the data up to that month only, and forecast 3 and 6 months ahead. Then we move
forward one month and do it again: 122 times in total. Comparing those forecasts with what the
rate actually did gives an honest view of how each model would have performed in real use.

A horizon of h means h months after the last observed month, not after the day the data is
published. The Bank of Canada releases this series about 2-3 months late, so in calendar terms a
"3-month" forecast is close to a nowcast. The README covers this in more detail.
"""
from __future__ import annotations

import json
import warnings

import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from config import (
    EVAL_START, HORIZONS, MIN_TRAIN_MONTHS, PROCESSED_DIR, PROFILE_BALANCE,
    PROFILE_YEARS_REMAINING, SELECTION_RULE,
)
from mortgage import monthly_payment

MAX_H = max(HORIZONS)


def load_actuals() -> pd.Series:
    df = pd.read_csv(PROCESSED_DIR / "rate_actual.csv", parse_dates=["month"])
    s = df.set_index("month")["rate_pct"].asfreq("MS")
    assert s.notna().all()
    return s


# The naive model says next month looks like this month. It sounds too simple, but interest rates
# move slowly, and a lot of fancier models fail to beat it. Any model we adopt has to clear this bar.
def forecast_naive(train: pd.Series, h: int) -> list[float]:
    return [float(train.iloc[-1])] * h


# ETS with a damped trend follows the recent direction of travel but lets the trend fade out, so a
# rate rise doesn't get projected upward forever. Parameters are re-estimated at every origin.
def forecast_ets(train: pd.Series, h: int) -> list[float]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = ExponentialSmoothing(
            train.values, trend="add", damped_trend=True, initialization_method="estimated"
        ).fit(optimized=True)
    return [float(x) for x in fit.forecast(h)]


MODELS = {"Naive (last observed)": forecast_naive, "ETS damped trend": forecast_ets}


# One definition of the origins, shared by every model and every script, so all comparisons
# use the same 122 months.
def backtest_origins(s: pd.Series) -> list[pd.Timestamp]:
    last = s.index[-1]
    return [o for o in s.index
            if o >= pd.Timestamp(EVAL_START)
            and o + pd.DateOffset(months=MAX_H) <= last
            and s.index.get_loc(o) + 1 >= MIN_TRAIN_MONTHS]


def run_backtest(s: pd.Series) -> pd.DataFrame:
    rows = []
    for o in backtest_origins(s):
        # Only data up to the origin. If later months leaked in here the backtest would look
        # better than the model could ever do in practice.
        train = s.loc[:o]
        for name, fn in MODELS.items():
            fc = fn(train, MAX_H)
            for h in HORIZONS:
                target = o + pd.DateOffset(months=h)
                rows.append({"origin_month": o, "target_month": target, "model": name,
                             "horizon_months": h, "forecast_rate_pct": round(fc[h - 1], 4)})
    return pd.DataFrame(rows)


def score(bt: pd.DataFrame, s: pd.Series) -> pd.DataFrame:
    bt = bt.copy()
    bt["actual_rate_pct"] = bt["target_month"].map(s)
    bt["error_bps"] = (bt["forecast_rate_pct"] - bt["actual_rate_pct"]) * 100
    # The CAD error prices the same loan twice, once at the forecast rate and once at the rate
    # that actually showed up, so the difference is purely the cost of the forecast miss.
    pay = lambda r: monthly_payment(PROFILE_BALANCE, PROFILE_YEARS_REMAINING, r)
    bt["payment_at_forecast"] = bt["forecast_rate_pct"].map(pay)
    bt["payment_at_actual"] = bt["actual_rate_pct"].map(pay)
    bt["error_cad"] = bt["payment_at_forecast"] - bt["payment_at_actual"]
    summ = (bt.groupby(["model", "horizon_months"])
              .agg(mae_bps=("error_bps", lambda x: x.abs().mean()),
                   mae_cad_per_month=("error_cad", lambda x: x.abs().mean()),
                   bias_bps=("error_bps", "mean"),
                   n_origins=("origin_month", "nunique"),
                   first_origin=("origin_month", "min"),
                   last_origin=("origin_month", "max"))
              .reset_index())
    return bt, summ


# Applies the rule from config.py as written. If ETS loses at either horizon, the naive model ships.
def select_model(summ: pd.DataFrame) -> str:
    p = summ.pivot(index="horizon_months", columns="model", values="mae_bps")
    ets_wins_all = all(p.loc[h, "ETS damped trend"] < p.loc[h, "Naive (last observed)"] for h in HORIZONS)
    return "ETS damped trend" if ets_wins_all else "Naive (last observed)"


def final_forecast(s: pd.Series, chosen: str) -> pd.DataFrame:
    o = s.index[-1]
    rows = []
    for name, fn in MODELS.items():
        for h, v in enumerate(fn(s, MAX_H), start=1):
            rows.append({"origin_month": o, "target_month": o + pd.DateOffset(months=h), "model": name,
                         "horizon_months": h, "forecast_rate_pct": round(v, 4),
                         "is_selected_model": name == chosen})
    return pd.DataFrame(rows)


def main():
    s = load_actuals()
    bt = run_backtest(s)
    bt_scored, summ = score(bt, s)
    chosen = select_model(summ)
    fc = final_forecast(s, chosen)

    fmt = lambda d: d.assign(**{c: d[c].dt.strftime("%Y-%m-01") for c in d.columns if c.endswith("_month") and c != "horizon_months"})
    fmt(bt).to_csv(PROCESSED_DIR / "backtest_forecasts.csv", index=False)
    fmt(fc).to_csv(PROCESSED_DIR / "forecast_latest.csv", index=False)
    summ_out = summ.assign(first_origin=summ.first_origin.dt.strftime("%Y-%m"), last_origin=summ.last_origin.dt.strftime("%Y-%m"))
    summ_out.round(4).to_csv(PROCESSED_DIR / "backtest_summary_python.csv", index=False)
    (PROCESSED_DIR / "model_selection.json").write_text(json.dumps(
        {"rule": SELECTION_RULE, "selected_model": chosen,
         "cad_profile": {"balance": PROFILE_BALANCE, "years_remaining": PROFILE_YEARS_REMAINING,
                         "note": "Same hypothetical loan priced at forecast rate vs actual rate"}}, indent=2))
    print(summ_out.round(2).to_string(index=False))
    print("\nSelected model:", chosen)
    print(fc[fc.is_selected_model].to_string(index=False))


if __name__ == "__main__":
    main()
