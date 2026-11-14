#!/usr/bin/env bash
# Author: Neo Nguyen
# Rebuild everything from scratch, in order:
#   download + validate -> backtest + forecast -> forecast band + DM test -> bond-yield regression
#   -> synthetic portfolio -> renewal triage
#   -> PostgreSQL tables, views and checks -> CSV export -> SQL vs Python reconciliation -> charts
#   -> Power BI project -> tests
set -euo pipefail
cd "$(dirname "$0")"

# Libraries the project needs. On the first run they are installed into a private .venv folder,
# so nothing touches the system Python.
# PostgreSQL itself must already be running (Postgres.app on a Mac); src/run_sql.py creates the database.
PACKAGES="pandas>=2.2 statsmodels>=0.14 psycopg[binary]>=3.2 requests>=2.31 matplotlib>=3.8 pytest>=8.0"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/python -m pip install -q $PACKAGES; }

PY="$(pwd)/.venv/bin/python"
(cd src && "$PY" fetch_data.py && "$PY" forecast.py && "$PY" intervals.py && "$PY" regression.py && "$PY" portfolio.py && "$PY" triage.py \
        && "$PY" run_sql.py && "$PY" reconcile.py && "$PY" charts.py)
"$PY" powerbi/build_pbip.py
"$PY" -m pytest -q tests
rm -rf src/__pycache__ tests/__pycache__ powerbi/__pycache__ .pytest_cache
