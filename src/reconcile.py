"""Check that SQL and Python agree before anyone sees a number.

Author: Neo Nguyen

The same metrics are computed twice: once in pandas, once in PostgreSQL. If the two disagree, one of
them has a bug (a bad join, a wrong filter, an off-by-one month). This script compares backtest
MAE, threshold rates and the portfolio totals, and writes threshold_reference.csv with the answers
the Power BI measures should reproduce.
"""
from __future__ import annotations

import pandas as pd

from config import POWERBI_DIR, PROCESSED_DIR
from db import connect, query
from mortgage import monthly_payment, threshold_rate, threshold_rate_grid

TOL_MAE = 0.01  # bps and CAD


def main():
    con = connect()
    sql = query(con, "SELECT model, horizon_months, mae_bps, mae_cad_per_month, n_origins FROM vw_backtest_summary")
    py = pd.read_csv(PROCESSED_DIR / "backtest_summary_python.csv")
    m = sql.merge(py, on=["model", "horizon_months"], suffixes=("_sql", "_py"))
    assert len(m) == len(py) == 4
    m["diff_bps"] = (m.mae_bps_sql - m.mae_bps_py).abs()
    m["diff_cad"] = (m.mae_cad_per_month_sql - m.mae_cad_per_month_py).abs()
    print(m[["model", "horizon_months", "mae_bps_sql", "mae_bps_py", "mae_cad_per_month_sql",
             "mae_cad_per_month_py", "n_origins_sql", "n_origins_py"]].to_string(index=False))
    assert (m.diff_bps <= TOL_MAE).all() and (m.diff_cad <= TOL_MAE).all() and (m.n_origins_sql == m.n_origins_py).all()
    print("Backtest MAE: SQL == Python (within 0.01)\n")

    # Same comparison for the model table (naive, ETS, three regression versions, horizons 1-6).
    sql = query(con, "SELECT model, horizon_months, mae_bps, mae_cad_per_month FROM vw_model_comparison")
    py = pd.read_csv(PROCESSED_DIR / "regression_backtest_summary_python.csv")
    m = sql.merge(py, on=["model", "horizon_months"], suffixes=("_sql", "_py"))
    assert len(m) == len(py) == 30
    assert ((m.mae_bps_sql - m.mae_bps_py).abs() <= TOL_MAE).all()
    assert ((m.mae_cad_per_month_sql - m.mae_cad_per_month_py).abs() <= TOL_MAE).all()
    print("Model comparison MAE (5 models x 6 horizons): SQL == Python (within 0.01)\n")

    # Threshold rate: SQL grid search vs Python exact (bisection) vs Python grid
    rows = []
    for bal, yrs, old in [(400_000, 20, 2.2), (400_000, 20, 4.58), (650_000, 22, 1.89), (250_000, 15, 0.0)]:
        for t in (100, 250, 500):
            exact = threshold_rate(bal, yrs, old, t)
            grid_py = threshold_rate_grid(bal, yrs, old, t)
            grid_sql = con.execute(
                "SELECT min(rate_pct) FROM vw_rate_grid WHERE monthly_payment(%s, %s, rate_pct) - monthly_payment(%s, %s, %s) >= %s - 1e-9",
                [bal, yrs, bal, yrs, old, t]).fetchone()[0]
            assert grid_py == grid_sql, (bal, yrs, old, t, grid_py, grid_sql)
            assert 0 <= grid_py - exact < 0.01 + 1e-9
            rows.append({"balance": bal, "years_remaining": yrs, "old_rate_pct": old, "threshold_cad": t,
                         "payment_before": round(monthly_payment(bal, yrs, old), 2),
                         "threshold_rate_exact_pct": round(exact, 4), "threshold_rate_grid_pct": grid_py,
                         "grid_minus_exact_bps": round((grid_py - exact) * 100, 2)})
    ref = pd.DataFrame(rows)
    ref.to_csv(POWERBI_DIR / "threshold_reference.csv", index=False)
    print(ref.to_string(index=False))
    print("\nThreshold rate: SQL grid == Python grid; grid within 1 bp above exact solution")

    # Portfolio: recompute every loan's payment change in Python and compare the totals with SQL.
    loans = pd.read_csv(PROCESSED_DIR / "renewal_portfolio.csv")
    py_change = [monthly_payment(b, y, n) - monthly_payment(b, y, o) for b, y, o, n in
                 zip(loans.balance_at_renewal, loans.years_remaining, loans.original_rate_pct, loans.renewal_rate_pct)]
    py_above = sum(c > 250 for c in py_change)
    sql_total, sql_above = con.execute(
        "SELECT sum(payment_change), count(*) FILTER (WHERE payment_change > 250) FROM vw_portfolio_payment WHERE shift_bps = 0").fetchone()
    assert abs(sum(py_change) - sql_total) < 0.01 and py_above == sql_above, (sum(py_change), sql_total, py_above, sql_above)
    print(f"Portfolio: {len(loans):,} loans, {py_above:,} above $250 in both Python and SQL; "
          f"total change ${sql_total:,.2f}/month matches")

    # Triage segments: Python builds them from three scenarios; SQL counts the same scenarios
    # straight from vw_portfolio_payment. Both must agree.
    tri = pd.read_csv(PROCESSED_DIR / "triage_loans.csv")
    py_seg = tri.segment_order.value_counts().sort_index().tolist()
    over = {s: set(query(con, "SELECT loan_id FROM vw_portfolio_payment WHERE shift_bps = %s AND payment_change > 250",
                          [s]).loan_id) for s in (-50, 0, 50)}
    sql_seg = [len(over[-50]), len(over[0] - over[-50]), len(over[50] - over[0]), len(loans) - len(over[50])]
    assert py_seg == sql_seg, (py_seg, sql_seg)
    print(f"Triage segments {py_seg}: SQL == Python")
    con.close()


if __name__ == "__main__":
    main()
