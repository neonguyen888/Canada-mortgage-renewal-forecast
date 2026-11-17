-- =============================================================================================
-- 02_views.sql  (PostgreSQL)
-- Author: Neo Nguyen
--
-- Views (saved queries) that feed the charts and the Power BI CSVs.
-- round() in PostgreSQL works on numeric, so double values are cast with ::numeric first.
-- Sections:
--   1. Rate history and outlook
--   2. Backtest
--   3. Payment impact for one loan
--   4. 90-day renewal portfolio
--   5. Bond-yield regression
--   6. Renewal triage
-- =============================================================================================

SET search_path TO mortgage;


-- =============================================================================================
-- 1. Rate history and outlook
-- =============================================================================================

-- dim_month
-- Calendar: every month from the first data point to the last forecast month.
CREATE VIEW dim_month AS
SELECT
    m::date                          AS month,
    extract(year FROM m)::integer    AS year,
    extract(month FROM m)::integer   AS month_num,
    to_char(m, 'Mon YYYY')           AS month_label
FROM generate_series(
    (SELECT min(month) FROM fact_rate_actual)::timestamp,
    (SELECT max(target_month) FROM fact_forecast)::timestamp,
    INTERVAL '1 month'
) AS t(m);


-- vw_rate_monthly
-- Change from last month, in bps.
-- Only computed when the previous row really is last month (guards against gaps).
CREATE VIEW vw_rate_monthly AS
SELECT
    month,
    rate_pct,
    CASE
        WHEN LAG(month) OVER w = month - INTERVAL '1 month'
        THEN round(((rate_pct - LAG(rate_pct) OVER w) * 100)::numeric, 1)
    END AS change_bps
FROM fact_rate_actual
WINDOW w AS (ORDER BY month);


-- vw_rate_outlook
-- History + forecast in one table, for the line chart.
-- The last actual month is repeated so the forecast line connects to it.
CREATE VIEW vw_rate_outlook AS
-- actual months
SELECT
    month,
    rate_pct,
    'Actual'::text   AS series,
    NULL::integer    AS horizon_months
FROM fact_rate_actual

UNION ALL

-- joining point: last actual month, labelled as forecast horizon 0
SELECT
    (SELECT max(month) FROM fact_rate_actual),
    (SELECT rate_pct FROM fact_rate_actual ORDER BY month DESC LIMIT 1),
    'Forecast',
    0

UNION ALL

-- forecast months
SELECT
    target_month,
    forecast_rate_pct,
    'Forecast',
    horizon_months
FROM fact_forecast
WHERE is_selected_model;


-- vw_rate_outlook_band
-- Same as above, plus the 80% band on forecast months.
CREATE VIEW vw_rate_outlook_band AS
SELECT
    o.*,
    i.lower_80_pct,
    i.upper_80_pct
FROM vw_rate_outlook o
LEFT JOIN fact_forecast_interval i
       ON o.series = 'Forecast'
      AND i.target_month = o.month;


-- vw_kpi
-- One row for the KPI cards: latest rate and the 3- and 6-month forecasts.
CREATE VIEW vw_kpi AS
SELECT
    a.month              AS latest_month,
    a.rate_pct           AS latest_rate_pct,
    f3.target_month      AS f3_month,
    f3.forecast_rate_pct AS f3_rate_pct,
    f6.target_month      AS f6_month,
    f6.forecast_rate_pct AS f6_rate_pct,
    f3.model             AS selected_model
FROM (SELECT * FROM fact_rate_actual ORDER BY month DESC LIMIT 1) a
JOIN fact_forecast f3
  ON f3.is_selected_model AND f3.horizon_months = 3
JOIN fact_forecast f6
  ON f6.is_selected_model AND f6.horizon_months = 6;


-- =============================================================================================
-- 2. Backtest
-- =============================================================================================

-- vw_backtest_detail
-- Each past forecast next to what actually happened, with the error in bps and in $/month.
CREATE VIEW vw_backtest_detail AS
SELECT
    b.origin_month,
    b.target_month,
    b.model,
    b.horizon_months,
    b.forecast_rate_pct,
    a.rate_pct                                AS actual_rate_pct,
    (b.forecast_rate_pct - a.rate_pct) * 100  AS error_bps,
    monthly_payment(p.balance, p.years_remaining, b.forecast_rate_pct)
      - monthly_payment(p.balance, p.years_remaining, a.rate_pct) AS error_cad_per_month
FROM fact_backtest b
JOIN fact_rate_actual a
  ON a.month = b.target_month
CROSS JOIN dim_profile p;


-- vw_backtest_summary
-- Average error per model and horizon. Negative bias = forecasts were too low on average.
CREATE VIEW vw_backtest_summary AS
SELECT
    model,
    horizon_months,
    round(avg(abs(error_bps))::numeric, 2)            AS mae_bps,
    round(avg(abs(error_cad_per_month))::numeric, 2)  AS mae_cad_per_month,
    round(avg(error_bps)::numeric, 2)                 AS bias_bps,
    count(DISTINCT origin_month)                      AS n_origins,
    min(origin_month)                                 AS first_origin,
    max(origin_month)                                 AS last_origin
FROM vw_backtest_detail
GROUP BY model, horizon_months
ORDER BY horizon_months, mae_bps;


-- vw_backtest_by_period
-- Average error per rate period: when did the models struggle?
CREATE VIEW vw_backtest_by_period AS
SELECT
    p.period_order,
    p.period_name,
    d.model,
    d.horizon_months,
    round(avg(abs(d.error_bps))::numeric, 2)            AS mae_bps,
    round(avg(abs(d.error_cad_per_month))::numeric, 2)  AS mae_cad_per_month,
    round(avg(d.error_bps)::numeric, 2)                 AS bias_bps,
    count(*)                                            AS n_forecasts
FROM vw_backtest_detail d
JOIN dim_rate_period p
  ON d.target_month BETWEEN p.start_month AND p.end_month
GROUP BY p.period_order, p.period_name, d.model, d.horizon_months
ORDER BY p.period_order, d.horizon_months, d.model;


-- =============================================================================================
-- 3. Payment impact for one loan
-- =============================================================================================

-- vw_rate_grid
-- Every rate from 0.00% to 10.00% in 0.01 steps.
-- Power BI searches this list for the rate where the payment rise hits the threshold.
CREATE VIEW vw_rate_grid AS
SELECT
    round(k * 0.01, 2)::double precision AS rate_pct
FROM generate_series(0, 1000) AS t(k);


-- =============================================================================================
-- 4. 90-day renewal portfolio
-- =============================================================================================

-- vw_portfolio_payment
-- Each loan under 3 scenarios (rate -50 bps, forecast, +50 bps) and its payment change.
CREATE VIEW vw_portfolio_payment AS
SELECT
    l.loan_id,
    l.renewal_month,
    l.balance_at_renewal,
    s.scenario,
    s.shift_bps,
    monthly_payment(l.balance_at_renewal, l.years_remaining, l.renewal_rate_pct + s.shift_bps / 100.0)
      - monthly_payment(l.balance_at_renewal, l.years_remaining, l.original_rate_pct) AS payment_change,
    -- a) b) c) d) keep the bands in the right order
    CASE
        WHEN l.balance_at_renewal < 250000 THEN 'a) under $250k'
        WHEN l.balance_at_renewal < 400000 THEN 'b) $250k–$400k'
        WHEN l.balance_at_renewal < 600000 THEN 'c) $400k–$600k'
        ELSE 'd) $600k and over'
    END AS balance_band
FROM fact_renewal_loan l
CROSS JOIN (VALUES
    ('Rate -50 bps',  -50),
    ('Forecast rate',   0),
    ('Rate +50 bps',   50)
) AS s(scenario, shift_bps)
ORDER BY l.loan_id, s.shift_bps;


-- vw_renewal_cohort
-- Per renewal month: how many loans go over each threshold. Rank 1 = worst month.
CREATE VIEW vw_renewal_cohort AS
WITH agg AS (
    SELECT
        p.renewal_month,
        p.scenario,
        p.shift_bps,
        t.threshold_cad,
        count(*)                                                    AS n_loans,
        count(*) FILTER (WHERE p.payment_change > t.threshold_cad)  AS n_above_threshold,
        round(avg(p.payment_change)::numeric, 2)                    AS avg_payment_change,
        round((percentile_cont(0.5) WITHIN GROUP (ORDER BY p.payment_change))::numeric, 2) AS median_payment_change,
        round(sum(p.payment_change)::numeric, 0)                    AS total_payment_change,
        round((sum(p.balance_at_renewal) / 1e6)::numeric, 1)        AS balance_mn
    FROM vw_portfolio_payment p
    CROSS JOIN dim_threshold t
    GROUP BY p.renewal_month, p.scenario, p.shift_bps, t.threshold_cad
)
SELECT
    *,
    round(100.0 * n_above_threshold / n_loans, 1) AS pct_above_threshold,
    RANK() OVER (PARTITION BY scenario, threshold_cad
                 ORDER BY n_above_threshold DESC) AS rank_by_count,
    -- Nov and Feb are partial months, so they are ranked by share as well as by count
    RANK() OVER (PARTITION BY scenario, threshold_cad
                 ORDER BY 1.0 * n_above_threshold / n_loans DESC) AS rank_by_share
FROM agg
ORDER BY threshold_cad, shift_bps, renewal_month;


-- vw_balance_band_cohort
-- Per balance band: share of loans going up more than $250. Bigger balance = bigger jump.
CREATE VIEW vw_balance_band_cohort AS
SELECT
    balance_band,
    scenario,
    shift_bps,
    count(*)                                     AS n_loans,
    count(*) FILTER (WHERE payment_change > 250) AS n_above_250,
    round(100.0 * count(*) FILTER (WHERE payment_change > 250) / count(*), 1) AS pct_above_250,
    round(avg(payment_change)::numeric, 2)       AS avg_payment_change
FROM vw_portfolio_payment
GROUP BY balance_band, scenario, shift_bps
ORDER BY shift_bps, balance_band;


-- =============================================================================================
-- 5. Bond-yield regression
-- =============================================================================================

-- vw_rate_vs_yield
-- Mortgage rate vs 5-year bond yield, and the gap (spread) between them.
-- Recent months have a yield but no mortgage rate yet (it is published later).
CREATE VIEW vw_rate_vs_yield AS
SELECT
    y.month,
    a.rate_pct,
    y.yield_pct,
    round(((a.rate_pct - y.yield_pct) * 100)::numeric, 1) AS spread_bps,
    a.rate_pct IS NOT NULL                                AS mortgage_rate_published
FROM fact_bond_yield y
LEFT JOIN fact_rate_actual a
  ON a.month = y.month
WHERE y.month >= (SELECT min(month) FROM fact_rate_actual)
ORDER BY y.month;


-- vw_model_comparison
-- Average error of every model, 1 to 6 months ahead. Rank 1 = most accurate.
CREATE VIEW vw_model_comparison AS
WITH d AS (
    SELECT
        b.model,
        b.horizon_months,
        b.origin_month,
        (b.forecast_rate_pct - a.rate_pct) * 100 AS error_bps,
        monthly_payment(p.balance, p.years_remaining, b.forecast_rate_pct)
          - monthly_payment(p.balance, p.years_remaining, a.rate_pct) AS error_cad_per_month
    FROM fact_model_backtest b
    JOIN fact_rate_actual a
      ON a.month = b.target_month
    CROSS JOIN dim_profile p
)
SELECT
    model,
    horizon_months,
    round(avg(abs(error_bps))::numeric, 2)            AS mae_bps,
    round(avg(abs(error_cad_per_month))::numeric, 2)  AS mae_cad_per_month,
    round(avg(error_bps)::numeric, 2)                 AS bias_bps,
    count(DISTINCT origin_month)                      AS n_origins,
    RANK() OVER (PARTITION BY horizon_months
                 ORDER BY avg(abs(error_bps)))        AS rank_at_horizon
FROM d
GROUP BY model, horizon_months
ORDER BY horizon_months, mae_bps;


-- vw_forecast_comparison
-- ETS forecast and regression forecast side by side.
CREATE VIEW vw_forecast_comparison AS
SELECT
    r.target_month,
    r.horizon_months,
    e.forecast_rate_pct                                            AS ets_rate_pct,
    r.forecast_rate_pct                                            AS regression_rate_pct,
    round(((r.forecast_rate_pct - e.forecast_rate_pct) * 100)::numeric, 1) AS gap_bps,
    r.yield_known
FROM fact_regression_forecast r
JOIN fact_forecast e
  ON e.target_month = r.target_month
 AND e.model = 'ETS damped trend';


-- =============================================================================================
-- 6. Renewal triage
-- =============================================================================================

-- vw_triage_segment
-- One row per segment: how many loans, typical payment rise, and how many get a call
-- before renewal at 300 calls a week.
CREATE VIEW vw_triage_segment AS
SELECT
    segment_order,
    segment,
    count(*)                                                          AS n_loans,
    round((percentile_cont(0.5) WITHIN GROUP (ORDER BY payment_change_base))::numeric, 0) AS median_rise_at_forecast,
    round(min(payment_change_low)::numeric, 0)                        AS lowest_rise_if_rates_fall,
    count(*) FILTER (WHERE contact_week IS NOT NULL)                  AS n_contacted_at_300,
    round(100.0 * count(*) FILTER (WHERE contact_week IS NOT NULL) / count(*), 1) AS pct_contacted_at_300
FROM fact_triage_loan
GROUP BY segment_order, segment
ORDER BY segment_order;
