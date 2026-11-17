"""Build the PostgreSQL schema, load the data, run the checks, and export CSVs for Power BI.

Author: Neo Nguyen

The SQL files are plain text so they can also be run by hand in psql. This script runs them in
order inside one transaction: create tables, load the CSVs from data/processed, create views,
run the checks. If any check fails, the transaction is rolled back, the database keeps its
last good version, and no CSV is exported.
"""
from __future__ import annotations

from config import POWERBI_DIR, PROCESSED_DIR, ROOT
from db import connect, ensure_database, load_csv, query

# Table <- CSV written by the Python scripts. Order matters: fact_triage_loan points at
# fact_renewal_loan, and both backtest tables point at fact_rate_actual.
LOADS = {
    "fact_rate_actual": "rate_actual.csv",
    "fact_forecast": "forecast_latest.csv",
    "fact_backtest": "backtest_forecasts.csv",
    "fact_forecast_interval": "forecast_intervals.csv",
    "fact_dm_test": "dm_test.csv",
    "fact_renewal_loan": "renewal_portfolio.csv",
    "fact_bond_yield": "bond_yield_monthly.csv",
    "fact_regression_coef": "regression_coefficients.csv",
    "fact_model_backtest": "regression_backtest.csv",
    "fact_regression_forecast": "regression_forecast_latest.csv",
    "fact_regression_dm": "regression_dm_test.csv",
    "fact_triage_loan": "triage_loans.csv",
    "fact_triage_capacity": "triage_capacity.csv",
}

# Tables and views the Power BI report reads, one CSV each, grouped by page.
EXPORTS = [
    # Overview
    "vw_rate_outlook_band", "dim_month", "vw_forecast_comparison", "fact_triage_loan",
    # Rates and accuracy
    "vw_model_comparison", "vw_backtest_by_period", "fact_dm_test", "fact_regression_dm", "vw_rate_vs_yield", "fact_regression_coef",
    # Payment impact
    "vw_rate_grid",
    # Renewal triage
    "fact_triage_capacity", "vw_triage_segment",
]


def main():
    POWERBI_DIR.mkdir(parents=True, exist_ok=True)
    ensure_database()
    sql = lambda f: (ROOT / "sql" / f).read_text()
    with connect() as con:                       # one transaction: commit on success, roll back on error
        con.execute(sql("01_tables.sql"))
        for table, csv in LOADS.items():
            n = load_csv(con, table, PROCESSED_DIR / csv)
            print(f"  loaded {n:>6,} rows into {table}")
        con.execute(sql("02_views.sql"))

        checks = query(con, sql("03_checks.sql"))
        checks["passed"] = checks["actual"] == checks["expected"]
        print(checks.to_string(index=False))
        if not checks["passed"].all():
            raise SystemExit("SQL integrity checks FAILED: rebuild rolled back, nothing exported")

        for old in POWERBI_DIR.glob("*.csv"):        # clear old exports so nothing stale is left behind
            old.unlink()
        for name in EXPORTS:
            query(con, f"SELECT * FROM {name}").to_csv(POWERBI_DIR / f"{name}.csv", index=False)
        print(f"\nExported {len(EXPORTS)} CSVs to {POWERBI_DIR.relative_to(ROOT)}/")
        print(query(con, "SELECT * FROM vw_backtest_summary").to_string(index=False))
        print(query(con, "SELECT * FROM vw_kpi").to_string(index=False))


if __name__ == "__main__":
    main()
