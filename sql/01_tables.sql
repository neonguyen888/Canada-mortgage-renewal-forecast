-- =============================================================================================
-- 01_tables.sql  (PostgreSQL)
-- Author: Neo Nguyen
--
-- Creates the mortgage schema: the payment function, empty fact tables and small lookups.
-- src/run_sql.py then loads data/processed/*.csv into the fact tables.
--   fact_...  the data itself (one row = one month, forecast or loan)
--   dim_...   small lookup lists used for filtering and grouping
-- origin_month = month the forecast was made, target_month = month it predicts.
-- =============================================================================================

DROP SCHEMA IF EXISTS mortgage CASCADE;
CREATE SCHEMA mortgage;
SET search_path TO mortgage;


-- ---------------------------------------------------------------------------------------------
-- Payment formula
-- ---------------------------------------------------------------------------------------------

-- Monthly payment, Canadian style (interest compounds twice a year).
-- Same formula as src/mortgage.py, so SQL and Python match to the cent.
CREATE FUNCTION monthly_payment(balance double precision, years double precision, rate_pct double precision)
RETURNS double precision
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN rate_pct = 0 THEN balance / (years * 12)
        ELSE balance * (power(1 + rate_pct / 200.0, 1.0 / 6) - 1)
             / (1 - power(power(1 + rate_pct / 200.0, 1.0 / 6), -(years * 12)))
    END
$$;


-- ---------------------------------------------------------------------------------------------
-- Market reference rate: actuals, forecasts, backtest
-- ---------------------------------------------------------------------------------------------

-- One row per month: the published mortgage rate.            (rate_actual.csv)
CREATE TABLE fact_rate_actual (
    month     date             PRIMARY KEY,
    rate_pct  double precision NOT NULL
);

-- Latest forecast, 1 to 6 months ahead, for each model.       (forecast_latest.csv)
-- is_selected_model = the model the selection rule picked.
CREATE TABLE fact_forecast (
    origin_month       date             NOT NULL,
    target_month       date             NOT NULL,
    model              text             NOT NULL,
    horizon_months     integer          NOT NULL,
    forecast_rate_pct  double precision NOT NULL,
    is_selected_model  boolean          NOT NULL,
    PRIMARY KEY (origin_month, target_month, model)
);

-- Past forecasts from the backtest (3 and 6 months ahead).    (backtest_forecasts.csv)
-- Every target month must exist in the actuals, so the foreign key catches a bad month.
CREATE TABLE fact_backtest (
    origin_month       date             NOT NULL,
    target_month       date             NOT NULL REFERENCES fact_rate_actual (month),
    model              text             NOT NULL,
    horizon_months     integer          NOT NULL,
    forecast_rate_pct  double precision NOT NULL,
    PRIMARY KEY (origin_month, target_month, model)
);

-- 80% band around the latest forecast, one row per month ahead.   (forecast_intervals.csv)
CREATE TABLE fact_forecast_interval (
    origin_month       date             NOT NULL,
    target_month       date             NOT NULL,
    horizon_months     integer          PRIMARY KEY,
    model              text             NOT NULL,
    forecast_rate_pct  double precision NOT NULL,
    lower_80_pct       double precision NOT NULL,
    upper_80_pct       double precision NOT NULL,
    band_width_bps     double precision NOT NULL,
    n_errors           integer          NOT NULL
);

-- Is ETS really better than naive? Test result per horizon.   (dm_test.csv)
CREATE TABLE fact_dm_test (
    horizon_months           integer          PRIMARY KEY,
    n_origins                integer          NOT NULL,
    mean_abs_error_diff_bps  double precision NOT NULL,
    dm_stat_hln              double precision NOT NULL,
    p_value                  double precision NOT NULL,
    significant_at_5pct      boolean          NOT NULL
);


-- ---------------------------------------------------------------------------------------------
-- Lookups
-- ---------------------------------------------------------------------------------------------

-- Example loan behind the CAD error ($400k, 20 years left). Matches config.py.
CREATE TABLE dim_profile AS
SELECT
    400000.0::double precision AS balance,
    20                         AS years_remaining;

-- Four rate periods, used to see when the models went wrong.
CREATE TABLE dim_rate_period (
    period_order  integer PRIMARY KEY,
    period_name   text    NOT NULL,
    start_month   date    NOT NULL,
    end_month     date    NOT NULL
);
INSERT INTO dim_rate_period VALUES
    (1, '2016–2019 pre-pandemic',  DATE '2016-01-01', DATE '2019-12-01'),
    (2, '2020–2021 pandemic lows', DATE '2020-01-01', DATE '2021-12-01'),
    (3, '2022–2023 tightening',    DATE '2022-01-01', DATE '2023-12-01'),
    (4, '2024–2026 easing',        DATE '2024-01-01', DATE '2026-12-01');

-- Payment increases the user can pick: $100, $250, $500.
CREATE TABLE dim_threshold (
    threshold_cad double precision PRIMARY KEY
);
INSERT INTO dim_threshold VALUES (100.0), (250.0), (500.0);


-- ---------------------------------------------------------------------------------------------
-- Synthetic 90-day renewal portfolio (src/portfolio.py)
-- ---------------------------------------------------------------------------------------------

-- One row per made-up loan renewing in the next 90 days.     (renewal_portfolio.csv)
CREATE TABLE fact_renewal_loan (
    loan_id             text             PRIMARY KEY,
    origination_month   date             NOT NULL,
    renewal_date        date             NOT NULL,
    renewal_month       date             NOT NULL,
    original_principal  double precision NOT NULL,
    amortization_years  integer          NOT NULL,
    original_rate_pct   double precision NOT NULL,
    renewal_rate_pct    double precision NOT NULL,
    balance_at_renewal  double precision NOT NULL,
    years_remaining     integer          NOT NULL
);


-- ---------------------------------------------------------------------------------------------
-- Bond-yield regression (src/regression.py)
-- ---------------------------------------------------------------------------------------------

-- One row per month: 5-year government bond yield (daily values averaged).   (bond_yield_monthly.csv)
CREATE TABLE fact_bond_yield (
    month         date             PRIMARY KEY,
    yield_pct     double precision NOT NULL,
    trading_days  integer          NOT NULL
);

-- Regression results: how much of a yield change reaches the mortgage rate, month by month.
-- lag_months is empty for the intercept.                      (regression_coefficients.csv)
CREATE TABLE fact_regression_coef (
    term                     text             PRIMARY KEY,
    lag_months               integer,
    coef                     double precision NOT NULL,
    std_err_hac              double precision NOT NULL,
    t_stat                   double precision NOT NULL,
    p_value                  double precision NOT NULL,
    ci95_low                 double precision NOT NULL,
    ci95_high                double precision NOT NULL,
    cumulative_pass_through  double precision
);

-- Backtest of all 5 models (naive, ETS, 3 regression versions), 1 to 6 months ahead.   (regression_backtest.csv)
CREATE TABLE fact_model_backtest (
    origin_month       date             NOT NULL,
    target_month       date             NOT NULL REFERENCES fact_rate_actual (month),
    model              text             NOT NULL,
    horizon_months     integer          NOT NULL,
    forecast_rate_pct  double precision NOT NULL,
    PRIMARY KEY (origin_month, target_month, model)
);

-- Latest regression forecast. yield_known = that month's bond yield is already out.   (regression_forecast_latest.csv)
CREATE TABLE fact_regression_forecast (
    origin_month       date             NOT NULL,
    target_month       date             NOT NULL,
    horizon_months     integer          PRIMARY KEY,
    forecast_rate_pct  double precision NOT NULL,
    model              text             NOT NULL,
    yield_known        boolean          NOT NULL
);

-- Is the regression really better than ETS and naive? Test result per horizon.   (regression_dm_test.csv)
CREATE TABLE fact_regression_dm (
    comparison               text             NOT NULL,
    horizon_months           integer          NOT NULL,
    n_origins                integer          NOT NULL,
    mean_abs_error_diff_bps  double precision NOT NULL,
    dm_stat_hln              double precision NOT NULL,
    p_value                  double precision NOT NULL,
    significant_at_5pct      boolean          NOT NULL,
    PRIMARY KEY (comparison, horizon_months)
);


-- ---------------------------------------------------------------------------------------------
-- Renewal triage (src/triage.py)
-- ---------------------------------------------------------------------------------------------

-- One row per loan: payment rise in the 3 scenarios, segment, priority rank,
-- and the week it gets a call at 300 calls a week (empty = missed).   (triage_loans.csv)
CREATE TABLE fact_triage_loan (
    loan_id              text             PRIMARY KEY REFERENCES fact_renewal_loan (loan_id),
    renewal_date         date             NOT NULL,
    renewal_month        date             NOT NULL,
    days_to_renewal      integer          NOT NULL,
    balance_at_renewal   double precision NOT NULL,
    payment_change_low   double precision NOT NULL,
    payment_change_base  double precision NOT NULL,
    payment_change_high  double precision NOT NULL,
    segment_order        integer          NOT NULL CHECK (segment_order BETWEEN 1 AND 4),
    segment              text             NOT NULL,
    priority_rank        integer          NOT NULL UNIQUE,
    contact_week         integer
);

-- One row per weekly call capacity: borrowers reached and missed, triage vs renewal-date order.   (triage_capacity.csv)
CREATE TABLE fact_triage_capacity (
    capacity_per_week                            integer          PRIMARY KEY,
    n_contacted                                  integer          NOT NULL,
    n_over_threshold                             integer          NOT NULL,
    over_contacted                               integer          NOT NULL,
    over_missed                                  integer          NOT NULL,
    over_too_soon_to_reach                       integer          NOT NULL,
    segment1_missed                              integer          NOT NULL,
    segment2_missed                              integer          NOT NULL,
    missed_rise_per_month                        double precision NOT NULL,
    over_missed_if_by_date_only                  integer          NOT NULL,
    segment1_missed_if_by_date_only              integer          NOT NULL,
    missed_rise_per_month_if_by_date_only        double precision NOT NULL,
    single_scenario_list_changes_if_rates_fall   integer          NOT NULL,
    single_scenario_list_changes_if_rates_rise   integer          NOT NULL
);
