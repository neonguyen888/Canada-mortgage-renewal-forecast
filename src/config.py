"""Project-wide settings.

Author: Neo Nguyen

Every number that shapes the analysis lives here: where the data comes from, how the backtest is
set up, which loan the CAD figures are based on, and the rule for picking a model. The selection
rule was written down before the first backtest ran, so the result could not influence it.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
POWERBI_DIR = ROOT / "data" / "powerbi"
# PostgreSQL connection. Postgres.app on a Mac accepts the logged-in user with no password.
# Override with e.g. MORTGAGE_PG_DSN="host=localhost dbname=mortgage_renewal user=me password=..."
PG_DSN = os.environ.get("MORTGAGE_PG_DSN", "host=localhost port=5432 dbname=mortgage_renewal")
PG_SCHEMA = "mortgage"

SERIES_ID = "V122667786"
VALET_OBS_URL = f"https://www.bankofcanada.ca/valet/observations/{SERIES_ID}/json"
VALET_SERIES_URL = f"https://www.bankofcanada.ca/valet/series/{SERIES_ID}/json"

# Backtest design
HORIZONS = (3, 6)                 # months ahead of the forecast origin (last observed month)
MIN_TRAIN_MONTHS = 36             # expanding window; series starts 2013-01, so first origin has 3 years of history
EVAL_START = "2016-01"            # first forecast origin evaluated (same for both models)

# The illustrative loan. Turning a rate error into dollars needs a loan, and this one is used everywhere
# (CAD error metric, payment charts, dashboard defaults) so the figures line up.
PROFILE_BALANCE = 400_000.0
PROFILE_YEARS_REMAINING = 20
PROFILE_OLD_RATE = 2.2            # % at the original 5-year term (illustrative)

# Power BI can't solve an equation, so it searches this 1 bp grid for the threshold rate instead.
GRID_MIN_RATE, GRID_MAX_RATE, GRID_STEP = 0.00, 10.00, 0.01   # percent

# Model-selection rule, fixed before any backtest was run.
SELECTION_RULE = (
    "Adopt damped-trend ETS only if its MAE (bps) beats the naive baseline at both "
    "3- and 6-month horizons on identical origins; otherwise use the naive baseline."
)

# ---- Bond-yield regression (src/regression.py) ----------------------------------------------
# Lenders price 5-year fixed mortgages off the Government of Canada 5-year bond yield, which the
# Bank of Canada publishes every business day. The mortgage series arrives about two months later,
# so at any forecast origin the yields for the next two months are already known.
YIELD_SERIES_ID = "BD.CDN.5YR.DQ.YLD"
YIELD_OBS_URL = f"https://www.bankofcanada.ca/valet/observations/{YIELD_SERIES_ID}/json"
YIELD_SERIES_URL = f"https://www.bankofcanada.ca/valet/series/{YIELD_SERIES_ID}/json"
YIELD_KNOWN_MONTHS = 2            # months of yields available beyond the last mortgage month
REG_MAX_LAG = 6                   # longest lag tried, in months
LAG_SELECTION_END = "2015-12"     # lag length is chosen on data up to here, before the first backtest origin
HAC_LAGS = 3                      # Newey-West lags for the coefficient standard errors
