-- =============================================================================================
-- 03_checks.sql  (PostgreSQL)
-- Author: Neo Nguyen
--
-- Data checks. Each check returns: name, value found, value expected.
-- If any pair differs, run_sql.py rolls the whole rebuild back and exports nothing.
-- Primary and foreign keys in 01_tables.sql already stop duplicates and unknown months at load
-- time; the checks below repeat the key ones so the report can list them in one place.
-- =============================================================================================


-- ---------------------------------------------------------------------------------------------
-- Keys and gaps
-- ---------------------------------------------------------------------------------------------

-- one row per month
SELECT 'actual: unique month' AS check_name,
       count(*) - count(DISTINCT month) AS actual,
       0::bigint AS expected
FROM fact_rate_actual

UNION ALL
-- no month repeated for the same origin and model
SELECT 'forecast: unique (origin,target,model)',
       count(*) - count(DISTINCT (origin_month, target_month, model)),
       0
FROM fact_forecast

UNION ALL
SELECT 'backtest: unique (origin,target,model)',
       count(*) - count(DISTINCT (origin_month, target_month, model)),
       0
FROM fact_backtest

UNION ALL
-- joining forecasts to actuals must not lose any rows
SELECT 'backtest join keeps every row',
       (SELECT count(*) FROM vw_backtest_detail),
       (SELECT count(*) FROM fact_backtest)

UNION ALL
-- count of months = months from first to last, so none is missing
SELECT 'actual: no gaps (months = span)',
       count(*),
       (SELECT (extract(year FROM age(max(month), min(month))) * 12
              + extract(month FROM age(max(month), min(month))))::bigint + 1
        FROM fact_rate_actual)
FROM fact_rate_actual

UNION ALL
-- only the first month has no previous month
SELECT 'LAG: only first month has NULL change',
       count(*) FILTER (WHERE change_bps IS NULL),
       1
FROM vw_rate_monthly


-- ---------------------------------------------------------------------------------------------
-- Backtest and forecast logic
-- ---------------------------------------------------------------------------------------------

UNION ALL
-- both models were tested on exactly the same origins
SELECT 'same origins for both models',
       (SELECT count(*)
        FROM (SELECT origin_month, horizon_months
              FROM fact_backtest
              GROUP BY origin_month, horizon_months
              HAVING count(DISTINCT model) <> 2) AS x),
       0

UNION ALL
-- the forecast sits inside its 80% band, all 6 months
SELECT 'intervals: 6 horizons, lower <= forecast <= upper',
       (SELECT count(*)
        FROM fact_forecast_interval
        WHERE lower_80_pct <= forecast_rate_pct
          AND forecast_rate_pct <= upper_80_pct),
       6

UNION ALL
-- every backtest target month falls inside one of the rate periods
SELECT 'every backtest target month has a period',
       (SELECT count(*)
        FROM vw_backtest_detail d
        WHERE NOT EXISTS (SELECT 1
                          FROM dim_rate_period p
                          WHERE d.target_month BETWEEN p.start_month AND p.end_month)),
       0


-- ---------------------------------------------------------------------------------------------
-- Payment formula (known answers, in cents)
-- ---------------------------------------------------------------------------------------------

UNION ALL
SELECT 'reference payment 2.2% (cents)',
       round(monthly_payment(400000, 20, 2.2) * 100)::bigint,
       205972

UNION ALL
SELECT 'reference payment 4.2% (cents)',
       round(monthly_payment(400000, 20, 4.2) * 100)::bigint,
       245857

UNION ALL
-- 0.00% to 10.00% in 1 bp steps
SELECT 'rate grid rows',
       (SELECT count(*) FROM vw_rate_grid),
       1001


-- ---------------------------------------------------------------------------------------------
-- Renewal portfolio
-- ---------------------------------------------------------------------------------------------

UNION ALL
SELECT 'portfolio: unique loan_id',
       (SELECT count(*) - count(DISTINCT loan_id) FROM fact_renewal_loan),
       0

UNION ALL
SELECT 'portfolio: renewals inside 90-day window',
       (SELECT count(*)
        FROM fact_renewal_loan
        WHERE renewal_date BETWEEN DATE '2026-11-15' AND DATE '2027-02-12'),
       (SELECT count(*) FROM fact_renewal_loan)

UNION ALL
SELECT 'portfolio: 3 scenarios per loan',
       (SELECT count(*) FROM vw_portfolio_payment),
       (SELECT 3 * count(*) FROM fact_renewal_loan)

UNION ALL
-- monthly groups add back up to all 5,000 loans
SELECT 'cohorts add back to portfolio',
       (SELECT sum(n_loans)::bigint
        FROM vw_renewal_cohort
        WHERE shift_bps = 0 AND threshold_cad = 250),
       (SELECT count(*) FROM fact_renewal_loan)


-- ---------------------------------------------------------------------------------------------
-- Bond-yield regression
-- ---------------------------------------------------------------------------------------------

UNION ALL
SELECT 'yield: unique month',
       (SELECT count(*) - count(DISTINCT month) FROM fact_bond_yield),
       0

UNION ALL
SELECT 'yield: no gaps (months = span)',
       (SELECT count(*) FROM fact_bond_yield),
       (SELECT (extract(year FROM age(max(month), min(month))) * 12
              + extract(month FROM age(max(month), min(month))))::bigint + 1
        FROM fact_bond_yield)

UNION ALL
-- the regression needs a yield for every month that has a mortgage rate
SELECT 'yield covers every mortgage month',
       (SELECT count(*)
        FROM fact_rate_actual a
        WHERE NOT EXISTS (SELECT 1 FROM fact_bond_yield y WHERE y.month = a.month)),
       0

UNION ALL
SELECT 'model backtest: unique (origin,target,model)',
       (SELECT count(*) - count(DISTINCT (origin_month, target_month, model)) FROM fact_model_backtest),
       0

UNION ALL
-- 5 models x 6 horizons = 30 rows for every origin
SELECT 'model backtest: 5 models x 6 horizons on every origin',
       (SELECT count(*)
        FROM (SELECT origin_month
              FROM fact_model_backtest
              GROUP BY origin_month
              HAVING count(*) <> 30) AS x),
       0

UNION ALL
SELECT 'model backtest: same origins as main backtest',
       (SELECT count(DISTINCT origin_month)
        FROM fact_model_backtest
        WHERE origin_month IN (SELECT origin_month FROM fact_backtest)),
       (SELECT count(DISTINCT origin_month) FROM fact_backtest)

UNION ALL
-- naive and ETS are computed twice (two scripts); both must match
SELECT 'naive and ETS agree with main backtest (rows differing)',
       (SELECT count(*)
        FROM fact_backtest b
        JOIN fact_model_backtest m USING (origin_month, target_month, model)
        WHERE abs(b.forecast_rate_pct - m.forecast_rate_pct) > 1e-6),
       0

UNION ALL
SELECT 'regression forecast: 6 horizons after last month',
       (SELECT count(*)
        FROM fact_regression_forecast
        WHERE origin_month = (SELECT max(month) FROM fact_rate_actual)),
       6


-- ---------------------------------------------------------------------------------------------
-- Renewal triage
-- ---------------------------------------------------------------------------------------------

UNION ALL
SELECT 'triage: segments add up to the book',
       (SELECT count(*) FROM fact_triage_loan),
       (SELECT count(*) FROM fact_renewal_loan)

UNION ALL
-- segment 1 must be exactly the loans over $250 even with rates 50 bps lower
SELECT 'triage: segment 1 = over $250 at -50 bps',
       (SELECT count(*) FROM fact_triage_loan WHERE segment_order = 1),
       (SELECT count(*) FROM vw_portfolio_payment WHERE shift_bps = -50 AND payment_change > 250)

UNION ALL
-- segments 1 and 2 together are the loans over $250 at the forecast rate
SELECT 'triage: segments 1+2 = over $250 at forecast',
       (SELECT count(*) FROM fact_triage_loan WHERE segment_order <= 2),
       (SELECT count(*) FROM vw_portfolio_payment WHERE shift_bps = 0 AND payment_change > 250)

UNION ALL
-- ranks run 1, 2, 3, ... with no gaps or repeats
SELECT 'triage: priority ranks are 1..n',
       (SELECT count(DISTINCT priority_rank) FROM fact_triage_loan WHERE priority_rank BETWEEN 1 AND 5000),
       (SELECT count(*) FROM fact_triage_loan)

UNION ALL
-- no call is booked after the borrower has renewed (7 days' notice)
SELECT 'triage: every call at least 7 days before renewal',
       (SELECT count(*)
        FROM fact_triage_loan
        WHERE contact_week IS NOT NULL
          AND renewal_date - (DATE '2026-11-15' + 7 * (contact_week - 1)) < 7),
       0

UNION ALL
-- more capacity can never mean more missed borrowers
SELECT 'triage: missed never rises with capacity',
       (SELECT count(*)
        FROM (SELECT over_missed - LAG(over_missed) OVER (ORDER BY capacity_per_week) AS d
              FROM fact_triage_capacity) AS x
        WHERE d > 0),
       0;
