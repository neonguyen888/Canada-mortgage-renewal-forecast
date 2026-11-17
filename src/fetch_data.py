"""Download the market reference rate from the Bank of Canada and check it before anything uses it.

Author: Neo Nguyen

The series is V122667786: the average 5-year-and-over fixed rate on uninsured residential
mortgages, for funds advanced each month. In the Bank's definition, funds advanced covers new
loans, renewals and refinancing, so renewing borrowers are part of this average. Each run keeps
a dated copy of the raw response, so the forecast can be traced back to the exact data it was
built on, even after the Bank revises past months.

The Government of Canada 5-year bond yield comes from the same API. It is published daily, so
it gets averaged to months here. Only complete months are kept: a half-finished month would
give the regression a yield that is still moving.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import requests

from config import (
    PROCESSED_DIR, RAW_DIR, SERIES_ID, VALET_OBS_URL, VALET_SERIES_URL,
    YIELD_OBS_URL, YIELD_SERIES_ID, YIELD_SERIES_URL,
)


def fetch() -> pd.DataFrame:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    downloaded_at = datetime.now(timezone.utc)

    series_meta = requests.get(VALET_SERIES_URL, timeout=30).json()["seriesDetails"]
    resp = requests.get(VALET_OBS_URL, timeout=60)
    resp.raise_for_status()
    payload = resp.json()

    snap = RAW_DIR / f"boc_{SERIES_ID}_{downloaded_at:%Y%m%d}.json"
    snap.write_text(json.dumps(payload, indent=1))

    rows = [(o["d"], o.get(SERIES_ID, {}).get("v")) for o in payload["observations"]]
    df = pd.DataFrame(rows, columns=["obs_date", "raw_value"])
    df["obs_date"] = pd.to_datetime(df["obs_date"])
    df["rate_pct"] = pd.to_numeric(df["raw_value"], errors="coerce")
    df = df.sort_values("obs_date").reset_index(drop=True)

    # ---- Validation -------------------------------------------------------
    # A missing or doubled month would quietly shift every LAG() and every backtest origin and give
    # numbers that look fine but are wrong, so the pipeline stops here.
    months = df["obs_date"].dt.to_period("M")
    duplicates = int(months.duplicated().sum())
    blanks = int(df["rate_pct"].isna().sum())
    full = pd.period_range(months.min(), months.max(), freq="M")
    missing = sorted(str(m) for m in set(full) - set(months))
    not_first_of_month = int((df["obs_date"].dt.day != 1).sum())
    checks = {
        "duplicate_months": duplicates,
        "blank_values": blanks,
        "missing_months": missing,
        "dates_not_first_of_month": not_first_of_month,
        "min_rate_pct": float(df["rate_pct"].min()),
        "max_rate_pct": float(df["rate_pct"].max()),
    }
    if duplicates or blanks or missing or not_first_of_month:
        raise ValueError(f"Data validation failed: {checks}")

    meta = {
        "series_id": SERIES_ID,
        "label": series_meta.get("label"),
        "description": series_meta.get("description"),
        "source": "Bank of Canada Valet API",
        "observations_url": VALET_OBS_URL,
        "series_url": VALET_SERIES_URL,
        "terms_url": payload.get("terms", {}).get("url"),
        "downloaded_at_utc": downloaded_at.isoformat(timespec="seconds"),
        "raw_snapshot": snap.name,
        "first_month": str(months.min()),
        "latest_observed_month": str(months.max()),
        "n_observations": len(df),
        "frequency": "monthly (dated first day of month)",
        "unit_convention": "Annual nominal rate in percent (4.58 = 4.58%). 1 bp = 0.01 percentage point.",
        "display_name": "Market reference rate",
        "validation": checks,
    }
    meta["bond_yield"] = fetch_yield(downloaded_at, first_month=months.min())
    (PROCESSED_DIR / "source_metadata.json").write_text(json.dumps(meta, indent=2))

    out = df[["obs_date", "rate_pct"]].copy()
    out["month"] = out["obs_date"].dt.strftime("%Y-%m-01")
    out[["month", "rate_pct"]].to_csv(PROCESSED_DIR / "rate_actual.csv", index=False)
    print(json.dumps(meta, indent=2))
    return out


def fetch_yield(downloaded_at: datetime, first_month: pd.Period) -> dict:
    series_meta = requests.get(YIELD_SERIES_URL, timeout=30).json()["seriesDetails"]
    # Start a few months before the mortgage series so the regression's lagged changes exist
    # from the first mortgage month onward.
    start = (first_month - 8).start_time.strftime("%Y-%m-%d")
    resp = requests.get(YIELD_OBS_URL, params={"start_date": start}, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    snap = RAW_DIR / f"boc_{YIELD_SERIES_ID}_{downloaded_at:%Y%m%d}.json"
    snap.write_text(json.dumps(payload, indent=1))

    rows = [(o["d"], o.get(YIELD_SERIES_ID, {}).get("v")) for o in payload["observations"]]
    d = pd.DataFrame(rows, columns=["obs_date", "raw_value"])
    d["obs_date"] = pd.to_datetime(d["obs_date"])
    d["yield_pct"] = pd.to_numeric(d["raw_value"], errors="coerce")

    # Keep complete months only: anything before the month of the download.
    this_month = pd.Timestamp(downloaded_at.date()).to_period("M")
    d = d[d["obs_date"].dt.to_period("M") < this_month]
    monthly = (d.dropna(subset=["yield_pct"])
                .groupby(d["obs_date"].dt.to_period("M"))["yield_pct"]
                .agg(yield_pct="mean", trading_days="count"))

    full = pd.period_range(monthly.index.min(), monthly.index.max(), freq="M")
    checks = {
        "duplicate_days": int(d["obs_date"].duplicated().sum()),
        "blank_days": int(d["yield_pct"].isna().sum()),
        "missing_months": sorted(str(m) for m in set(full) - set(monthly.index)),
        # A normal month has 19-23 trading days. Far fewer means the download was cut short.
        "months_under_15_days": [str(m) for m in monthly.index[monthly["trading_days"] < 15]],
        "min_yield_pct": round(float(monthly["yield_pct"].min()), 4),
        "max_yield_pct": round(float(monthly["yield_pct"].max()), 4),
    }
    if checks["duplicate_days"] or checks["missing_months"] or checks["months_under_15_days"]:
        raise ValueError(f"Bond yield validation failed: {checks}")

    out = monthly.reset_index(names="month")
    out["month"] = out["month"].dt.strftime("%Y-%m-01")
    out["yield_pct"] = out["yield_pct"].round(4)
    out.to_csv(PROCESSED_DIR / "bond_yield_monthly.csv", index=False)
    return {
        "series_id": YIELD_SERIES_ID,
        "label": series_meta.get("label"),
        "description": series_meta.get("description"),
        "observations_url": YIELD_OBS_URL,
        "raw_snapshot": snap.name,
        "frequency": "daily, averaged to calendar months (complete months only)",
        "first_month": out["month"].iloc[0][:7],
        "latest_complete_month": out["month"].iloc[-1][:7],
        "n_months": len(out),
        "validation": checks,
    }


if __name__ == "__main__":
    fetch()
