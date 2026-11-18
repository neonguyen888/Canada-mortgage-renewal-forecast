"""Build the Power BI project (PBIP) for the mortgage renewal report.

Author: Neo Nguyen

The report is written as a Power BI Project: plain text files (TMDL for the data model, PBIR
JSON for the report) that Power BI Desktop opens directly.
Everything comes from this script, so layout, colours and DAX live in one place and the whole
report can be rebuilt with:

    .venv/bin/python powerbi/build_pbip.py

What it writes:
  MortgageRenewal.pbip                    the file you double-click in Power BI Desktop
  MortgageRenewal.SemanticModel/          tables, relationships and DAX measures (TMDL)
  MortgageRenewal.Report/                 4 pages, their visuals, the theme and background images

The data itself is not copied. The model reads the CSVs in data/powerbi through a parameter
called DataFolder, which points at this repository on GitHub and can be changed to a local folder.

File formats follow Microsoft's published schemas (developer.microsoft.com/json-schemas/fabric)
and the PBIP sample projects Microsoft's team publishes on GitHub.
"""
from __future__ import annotations

import json
import math
import shutil
import uuid
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

HERE = Path(__file__).resolve().parent
NAME = "MortgageRenewal"
SM = HERE / f"{NAME}.SemanticModel"
RP = HERE / f"{NAME}.Report"


def backtest_span() -> str:
    """'123 rolling forecasts made each month from January 2016 to March 2026', read from the backtest summary."""
    import csv
    from datetime import date
    with open(HERE.parent / "data" / "processed" / "backtest_summary_python.csv", newline="") as f:
        row = next(csv.DictReader(f))
    month = lambda ym: date(int(ym[:4]), int(ym[5:7]), 1).strftime("%B %Y")
    return (f"{row['n_origins']} rolling forecasts made each month from "
            f"{month(row['first_origin'])} to {month(row['last_origin'])}")
PAGE_W, PAGE_H = 1280, 720
# Public copy of data/powerbi on GitHub, so the report refreshes on any machine. A local folder
# path such as C:\repo\data\powerbi works too.
DEFAULT_DATA_FOLDER = "https://raw.githubusercontent.com/neonguyen888/Canada-mortgage-renewal-forecast/main/data/powerbi/"

# ---------------------------------------------------------------------------------------------
# Design tokens. A light finance-report look that matches the charts in reports/figures: light grey
# canvas, white panels, a navy navigation rail, and the same series colours as src/charts.py
# (navy for actual data, teal for the ETS forecast, amber for whatever needs attention, purple for
# the bond-yield model, grey for comparisons), which passed a colour-blind check there.
# "blue" keeps its old key so every chart call still reads "actual data".
# ---------------------------------------------------------------------------------------------
C = {
    "canvas": "#F4F6F9", "panel": "#FFFFFF", "line": "#D5DAE0", "tile_hi": "#FFF7EE",
    "ink": "#1B1F24", "ink2": "#5B6670", "ink3": "#6B7682", "grid": "#ECEEF1",
    "blue": "#2F5F96", "teal": "#16917F", "teal_dim": "#8CCFC3", "amber": "#C26B12",
    "purple": "#6E52A3", "purple2": "#9A86C8", "purple3": "#C2B6E0", "slate": "#A7B0BA", "coral": "#B5473A",
    "rail": "#1F3A5F", "rail_sel": "#2F5F96", "rail_hover": "#28497A", "rail_ink": "#FFFFFF", "rail_ink2": "#B5C6DB",
}
FONT, FONT_SEMI, FONT_BOLD, FONT_NUM = "Segoe UI", "Segoe UI Semibold", "Segoe UI Bold", "Segoe UI Semibold"
# Pages in navigation order. Overview opens first.
PAGES = [("overview", "Overview"), ("accuracy", "Rates & accuracy"), ("payment", "Payment impact"),
         ("triage", "Renewal triage"), ("about", "About")]

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"
S_VISUAL = f"{SCHEMA}/item/report/definition/visualContainer/2.0.0/schema.json"
S_PAGE = f"{SCHEMA}/item/report/definition/page/1.4.0/schema.json"


def tag(*parts: str) -> str:
    """Stable lineage tag, so rebuilding the project doesn't change every file."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "mortgage-renewal/" + "/".join(parts)))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, obj) -> None:
    write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


# =============================================================================================
# Semantic model (TMDL)
# =============================================================================================
def q(name: str) -> str:
    """Quote a TMDL/DAX object name when it isn't a plain identifier."""
    return name if name.replace("_", "").isalnum() else "'" + name.replace("'", "''") + "'"


M_TYPES = {"date": "type date", "double": "type number", "int64": "Int64.Type", "string": "type text", "boolean": "type logical"}
TMDL_TYPES = {"date": "dateTime", "double": "double", "int64": "int64", "string": "string", "boolean": "boolean"}


class Col:
    def __init__(self, name, src=None, typ="string", fmt=None, hidden=False, sort_by=None, summarize="none", desc=None):
        self.name, self.src, self.typ, self.fmt = name, src or name, typ, fmt
        self.hidden, self.sort_by, self.summarize, self.desc = hidden, sort_by, summarize, desc


class Table:
    def __init__(self, name, cols, m_source=None, csv=None, extra_steps="", desc=None, hidden=False):
        self.name, self.cols, self.csv, self.desc, self.hidden = name, cols, csv, desc, hidden
        self.m_source, self.extra_steps = m_source, extra_steps
        self.measures: list[tuple[str, str, str | None, str, str | None]] = []

    def m(self) -> str:
        if self.m_source:
            return self.m_source
        keep = ", ".join(f'"{c.src}"' for c in self.cols if c.src in self.csv_cols)
        types = ", ".join(f'{{"{c.src}", {M_TYPES[c.typ]}}}' for c in self.cols if c.src in self.csv_cols)
        renames = ", ".join(f'{{"{c.src}", "{c.name}"}}' for c in self.cols if c.src in self.csv_cols and c.src != c.name)
        last = "Typed"
        lines = [
            "let",
            # DataFolder can be a web address (the GitHub copy) or a folder on this machine
            '    IsWeb = Text.StartsWith(DataFolder, "http"),',
            '    Folder = Text.TrimEnd(DataFolder, {"\\", "/"}) & (if IsWeb then "/" else "\\"),',
            f'    Raw = if IsWeb then Web.Contents(Folder & "{self.csv}") else File.Contents(Folder & "{self.csv}"),',
            '    Source = Csv.Document(Raw, [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),',
            "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
            f"    Kept = Table.SelectColumns(Promoted, {{{keep}}}),",
            "    Blanks = Table.ReplaceValue(Kept, \"\", null, Replacer.ReplaceValue, Table.ColumnNames(Kept)),",
            # en-US so "4.58" stays 4.58 on a Windows machine set to a comma-decimal locale
            f'    Typed = Table.TransformColumnTypes(Blanks, {{{types}}}, "en-US")',
        ]
        if renames:
            lines[-1] += ","
            lines.append(f"    Renamed = Table.RenameColumns(Typed, {{{renames}}})")
            last = "Renamed"
        if self.extra_steps:
            lines[-1] += ","
            lines += ["    " + s for s in self.extra_steps.strip().splitlines()]
            last = self.extra_steps.strip().splitlines()[-1].split("=")[0].strip()
        lines += ["in", f"    {last}"]
        return "\n".join(lines)

    def measure(self, name, expr, fmt=None, folder="", desc=None):
        self.measures.append((name, expr.strip("\n").rstrip(), fmt, folder, desc))

    def tmdl(self) -> str:
        out = []
        if self.desc:
            out.append(f"/// {self.desc}")
        out += [f"table {q(self.name)}", f"\tlineageTag: {tag(self.name)}", ""]
        for name, expr, fmt, folder, desc in self.measures:
            if desc:
                out.append(f"\t/// {desc}")
            body = [ln for ln in expr.splitlines()]
            if len(body) == 1:
                out.append(f"\tmeasure {q(name)} = {body[0].strip()}")
            else:
                out.append(f"\tmeasure {q(name)} =")
                base = min(len(ln) - len(ln.lstrip()) for ln in body if ln.strip())
                out += ["\t\t\t" + ln[base:] if ln.strip() else "" for ln in body]
            if fmt:
                out.append(f"\t\tformatString: {fmt}")
            if folder:
                out.append(f"\t\tdisplayFolder: {folder}")
            out += [f"\t\tlineageTag: {tag(self.name, 'measure', name)}", ""]
        for c in self.cols:
            if c.desc:
                out.append(f"\t/// {c.desc}")
            out += [f"\tcolumn {q(c.name)}", f"\t\tdataType: {TMDL_TYPES[c.typ]}"]
            if c.hidden or self.hidden:
                out.append("\t\tisHidden")
            if c.fmt:
                out.append(f"\t\tformatString: {c.fmt}")
            out += [f"\t\tlineageTag: {tag(self.name, 'col', c.name)}", f"\t\tsummarizeBy: {c.summarize}",
                    f"\t\tsourceColumn: {c.name}"]
            if c.sort_by:
                out.append(f"\t\tsortByColumn: {q(c.sort_by)}")
            out.append("")
            if c.typ == "date":
                out += ["\t\tannotation UnderlyingDateTimeDataType = Date", ""]
            out += ["\t\tannotation SummarizationSetBy = User", ""]
        out += [f"\tpartition {q(self.name)} = m", "\t\tmode: import", "\t\tsource ="]
        out += ["\t\t\t\t" + ln for ln in self.m().splitlines()]
        out += ["", "\tannotation PBI_ResultType = Table", ""]
        return "\n".join(out)


def small_table(name, cols, rows, desc):
    """A short lookup table typed in M, for slicer choices and scenario axes."""
    tdef = ", ".join(f"{q_m(c.name)} = {M_TYPES[c.typ].replace('type ', '')}" for c in cols)
    body = ", ".join("{" + ", ".join(json.dumps(v) for v in r) + "}" for r in rows)
    src = f"let\n    Source = #table(type table [{tdef}], {{{body}}})\nin\n    Source"
    return Table(name, cols, m_source=src, desc=desc)


def q_m(name):
    return name if name.replace("_", "").isalnum() else f'#"{name}"'


def payment_dax(balance, years, rate):
    """DAX variables for the Canadian payment formula (same as src/mortgage.py and the SQL function).
    The result is in _pay; the caller writes the RETURN."""
    return (f"VAR _b = {balance}\nVAR _n = {years} * 12\nVAR _r = {rate}\n"
            "VAR _i = POWER(1 + _r / 200, 1 / 6) - 1\n"
            "VAR _pay = IF(_r = 0, DIVIDE(_b, _n), _b * _i / (1 - POWER(1 + _i, -_n)))")


def build_tables() -> list[Table]:
    T = []
    # ---- data tables -------------------------------------------------------------------------
    T.append(Table("Rate Outlook", [
        Col("Month", "month", "date", "mmm yyyy"), Col("Rate", "rate_pct", "double", hidden=True),
        Col("Series", "series", hidden=True), Col("Horizon", "horizon_months", "int64", hidden=True),
        Col("Lower 80", "lower_80_pct", "double", hidden=True),
        Col("Upper 80", "upper_80_pct", "double", hidden=True),
    ], csv="vw_rate_outlook_band.csv", hidden=True,
        desc="Market reference rate, actual and ETS forecast, with the 80% band on forecast months."))
    T.append(Table("Calendar", [
        Col("Month", "month", "date", "mmm yyyy"), Col("Year", "year", "int64"),
        Col("Month label", "month_label", sort_by="Month"),
    ], csv="dim_month.csv", desc="One row per month, from the first observation to the last forecast month."))
    T.append(Table("Forecast Comparison", [
        Col("Target month", "target_month", "date", "mmm yyyy"), Col("Horizon", "horizon_months", "int64"),
        Col("ETS rate", "ets_rate_pct", "double", "0.00"), Col("Bond-yield rate", "regression_rate_pct", "double", "0.00"),
        Col("Gap bps", "gap_bps", "double", "0.0"), Col("Yield known", "yield_known", "boolean"),
    ], csv="vw_forecast_comparison.csv", hidden=True, desc="ETS and bond-yield forecasts for the same six months."))
    T.append(Table("Loans", [
        Col("Loan ID", "loan_id"), Col("Renewal date", "renewal_date", "date", "d mmm yyyy"),
        Col("Renewal month", "renewal_month", "date", "mmm yyyy"), Col("Days to renewal", "days_to_renewal", "int64"),
        Col("Balance", "balance_at_renewal", "double", "\\$#,0"),
        Col("Rise if rates fall", "payment_change_low", "double", "\\$#,0.00"),
        Col("Rise at forecast", "payment_change_base", "double", "\\$#,0.00"),
        Col("Rise if rates rise", "payment_change_high", "double", "\\$#,0.00"),
        Col("Segment order", "segment_order", "int64", hidden=True), Col("Segment", "segment", sort_by="Segment order"),
        Col("Priority rank", "priority_rank", "int64"), Col("Call week at 300", "contact_week", "int64"),
    ], csv="fact_triage_loan.csv",
        desc="Synthetic book of 5,000 loans renewing in the next 90 days, with the payment rise in three rate scenarios."))
    T.append(Table("Model Accuracy", [
        Col("Model", "model", hidden=True), Col("Horizon", "horizon_months", "int64"),
        Col("MAE bps", "mae_bps", "double", "0.0"), Col("MAE CAD", "mae_cad_per_month", "double", "\\$#,0.00"),
        Col("Bias bps", "bias_bps", "double", "0.0"), Col("Origins", "n_origins", "int64"),
        Col("Rank", "rank_at_horizon", "int64"),
        Col("Model label", "Model label", sort_by="Model order"), Col("Model order", "Model order", "int64", hidden=True),
    ], csv="vw_model_comparison.csv", desc="Backtest error for every model, 1 to 6 months ahead.", extra_steps="""
Labels = [#"Bond-yield regression" = "Bond-yield model", #"Regression, 1 month of extra yields" = "Bond yield, 1-month head start", #"Regression, yields to origin only" = "Bond yield, no head start", #"ETS damped trend" = "ETS", #"Naive (last observed)" = "Naive"],
Orders = [#"Bond-yield regression" = 1, #"Regression, 1 month of extra yields" = 2, #"Regression, yields to origin only" = 3, #"ETS damped trend" = 4, #"Naive (last observed)" = 5],
WithLabel = Table.AddColumn(Renamed, "Model label", each Record.FieldOrDefault(Labels, [Model], [Model]), type text),
WithOrder = Table.AddColumn(WithLabel, "Model order", each Record.FieldOrDefault(Orders, [Model], 9), Int64.Type)
"""))
    T.append(Table("Accuracy By Period", [
        Col("Period order", "period_order", "int64", hidden=True), Col("Period", "period_name", sort_by="Period order"),
        Col("Model", "model"), Col("Horizon", "horizon_months", "int64"), Col("MAE bps", "mae_bps", "double", "0.0"),
        Col("MAE CAD", "mae_cad_per_month", "double", "\\$#,0.00"), Col("Bias bps", "bias_bps", "double", "0.0"),
        Col("Forecasts", "n_forecasts", "int64"),
    ], csv="vw_backtest_by_period.csv", desc="ETS and naive backtest error split by rate period."))
    T.append(Table("ETS Test", [
        Col("Horizon", "horizon_months", "int64"), Col("Origins", "n_origins", "int64"),
        Col("Mean difference bps", "mean_abs_error_diff_bps", "double", "0.00"), Col("DM statistic", "dm_stat_hln", "double", "0.000"),
        Col("p-value", "p_value", "double", "0.000"), Col("Significant", "significant_at_5pct", "boolean"),
    ], csv="fact_dm_test.csv", hidden=True, desc="Diebold-Mariano test of ETS against the naive model."))
    T.append(Table("Bond-yield Test", [
        Col("Comparison", "comparison"), Col("Horizon", "horizon_months", "int64"), Col("Origins", "n_origins", "int64"),
        Col("Mean difference bps", "mean_abs_error_diff_bps", "double", "0.00"), Col("DM statistic", "dm_stat_hln", "double", "0.000"),
        Col("p-value", "p_value", "double", "0.0000"), Col("Significant", "significant_at_5pct", "boolean"),
    ], csv="fact_regression_dm.csv", hidden=True, desc="Diebold-Mariano tests of the bond-yield model against ETS and naive."))
    T.append(Table("Rate vs Yield", [
        Col("Month", "month", "date", "mmm yyyy"), Col("Mortgage rate", "rate_pct", "double", "0.00"),
        Col("Bond yield", "yield_pct", "double", "0.00"), Col("Spread bps", "spread_bps", "double", "0"),
        Col("Mortgage rate published", "mortgage_rate_published", "boolean"),
    ], csv="vw_rate_vs_yield.csv", desc="Mortgage rate next to the GoC 5-year bond yield, monthly."))
    T.append(Table("Pass-through", [
        Col("Term", "term"), Col("Lag months", "lag_months", "int64"), Col("Coefficient", "coef", "double", "0.00"),
        Col("Std error", "std_err_hac", "double", "0.000"), Col("t stat", "t_stat", "double", "0.00"),
        Col("p-value", "p_value", "double", "0.000"), Col("CI low", "ci95_low", "double", "0.00"),
        Col("CI high", "ci95_high", "double", "0.00"), Col("Cumulative", "cumulative_pass_through", "double", "0.00"),
    ], csv="fact_regression_coef.csv", hidden=True, desc="Share of a bond-yield move that reaches the mortgage rate, by month."))
    T.append(Table("Rate Grid", [
        Col("Rate", "rate_pct", "double", "0.00\\%"),
    ], csv="vw_rate_grid.csv", desc="Every rate from 0.00% to 10.00% in 0.01 steps, for the payment curve and threshold search."))
    T.append(Table("Call Capacity", [
        Col("Calls per week", "capacity_per_week", "int64"), Col("Contacted", "n_contacted", "int64"),
        Col("Over threshold", "n_over_threshold", "int64"), Col("Over contacted", "over_contacted", "int64"),
        Col("Over missed", "over_missed", "int64"), Col("Too soon to reach", "over_too_soon_to_reach", "int64"),
        Col("Segment 1 missed", "segment1_missed", "int64"), Col("Segment 2 missed", "segment2_missed", "int64"),
        Col("Missed rise per month", "missed_rise_per_month", "double", "\\$#,0"),
        Col("Over missed, date order", "over_missed_if_by_date_only", "int64"),
        Col("Segment 1 missed, date order", "segment1_missed_if_by_date_only", "int64"),
        Col("Missed rise per month, date order", "missed_rise_per_month_if_by_date_only", "double", "\\$#,0"),
        Col("List changes if rates fall", "single_scenario_list_changes_if_rates_fall", "int64"),
        Col("List changes if rates rise", "single_scenario_list_changes_if_rates_rise", "int64"),
        Col("Capacity", "Capacity", sort_by="Calls per week"),
    ], csv="fact_triage_capacity.csv", desc="Borrowers reached and missed at each weekly call capacity, triage rule against renewal-date order.",
        extra_steps='Labelled = Table.AddColumn(Renamed, "Capacity", each Text.From([Calls per week]) & " calls a week", type text)'))
    T.append(Table("Segments", [
        Col("Segment order", "segment_order", "int64", hidden=True), Col("Segment", "segment", sort_by="Segment order"),
        Col("Loans", "n_loans", "int64", "#,0"), Col("Median rise", "median_rise_at_forecast", "double", "\\$#,0"),
        Col("Lowest rise if rates fall", "lowest_rise_if_rates_fall", "double", "\\$#,0"),
        Col("Called at 300 a week", "n_contacted_at_300", "int64", "#,0"),
        Col("Share called at 300", "pct_contacted_at_300", "double", "0.0"),
    ], csv="vw_triage_segment.csv", desc="The four triage segments with their size and typical payment rise."))

    # ---- small lookup tables (slicer choices and scenario axes) ---------------------------------
    T.append(small_table("Scenario", [Col("Scenario", sort_by="Order"), Col("Shift bps", typ="int64"), Col("Order", typ="int64", hidden=True)],
                         [["-50 bps", -50, 1], ["Base", 0, 2], ["+50 bps", 50, 3]], "Rate scenario chosen on the overview page."))
    T.append(small_table("Scenario Axis", [Col("Scenario", sort_by="Order"), Col("Shift bps", typ="int64"), Col("Order", typ="int64", hidden=True)],
                         [["-50 bps", -50, 1], ["Base forecast", 0, 2], ["+50 bps", 50, 3]],
                         "A copy of the scenarios used as a chart axis, so every bar stays visible whatever the slicer says."))
    T.append(small_table("Threshold", [Col("Threshold label", sort_by="Threshold"), Col("Threshold", typ="double")],
                         [["$100", 100.0], ["$250", 250.0], ["$500", 500.0]], "Payment increase the user wants to watch."))
    T.append(small_table("Balance Choice", [Col("Balance label", sort_by="Balance"), Col("Balance", typ="double")],
                         [["$250k", 250000.0], ["$400k", 400000.0], ["$600k", 600000.0], ["$800k", 800000.0]], "Balance at renewal."))
    T.append(small_table("Years Choice", [Col("Years label", sort_by="Years"), Col("Years", typ="int64")],
                         [["15 yrs", 15], ["20 yrs", 20], ["25 yrs", 25]], "Years left on the amortization."))
    T.append(small_table("Rate Before Choice", [Col("Rate before label", sort_by="Rate"), Col("Rate", typ="double")],
                         [["1.9%", 1.9], ["2.2%", 2.2], ["2.6%", 2.6], ["3.0%", 3.0]], "Rate on the term that is ending."))
    T.append(small_table("Horizon Choice", [Col("Horizon label", sort_by="Months"), Col("Months", typ="int64")],
                         [["3 months", 3], ["6 months", 6]], "Forecast horizon for the accuracy page."))
    T.append(small_table("Capacity Choice", [Col("Calls per week label", sort_by="Calls per week"), Col("Calls per week", typ="int64")],
                         [["100", 100], ["200", 200], ["300", 300], ["400", 400]], "Weekly call capacity of the renewal team."))
    # The rates behind these options are measures, so they follow the data without a second CSV read.
    T.append(small_table("Renewal Rate Choice", [Col("Renewal rate option", sort_by="Order"), Col("Order", typ="int64", hidden=True)],
                         [["Latest", 1], ["ETS 3 mo", 2], ["ETS 6 mo", 3], ["Bond yield 6 mo", 4]],
                         "Renewal rates to try: today's market rate and the forecasts."))

    measures = Table("measures_calc", [Col("Placeholder", hidden=True)],
                     m_source="let\n    Source = #table(type table [Placeholder = text], {})\nin\n    Source",
                     desc="All report measures live here.")
    add_measures(measures)
    T.append(measures)
    return T


def delta_text(d: str) -> str:
    """DAX text for a change in basis points held in variable d: "▲ 8 bps", "▼ 3 bps" or "no change".
    The arrow carries the direction, so the meaning doesn't depend on colour."""
    return (f'IF({d} > 0, "▲ ", IF({d} < 0, "▼ ", "")) & '
            f'IF({d} = 0, "no change", FORMAT(ABS({d}), "0") & " bps")')


def add_measures(t: Table) -> None:
    pct, cad, cad2 = "0.00\\%", "\\$#,0", "\\$#,0.00"
    F = "Rates"
    t.measure("Last Actual Month", "CALCULATE(MAX('Rate Outlook'[Month]), 'Rate Outlook'[Series] = \"Actual\", REMOVEFILTERS())",
              "mmm yyyy", F, "Latest month the Bank of Canada has published.")
    t.measure("Latest Rate", """
        VAR _m = [Last Actual Month]
        RETURN CALCULATE(MAX('Rate Outlook'[Rate]), 'Rate Outlook'[Series] = "Actual", 'Rate Outlook'[Month] = _m, REMOVEFILTERS())
        """, pct, F, "Market reference rate in the latest published month.")
    t.measure("Previous Actual Rate", """
        VAR _m = EDATE([Last Actual Month], -1)
        RETURN CALCULATE(MAX('Rate Outlook'[Rate]), 'Rate Outlook'[Series] = "Actual", 'Rate Outlook'[Month] = _m, REMOVEFILTERS())
        """, pct, F, "Market reference rate one month before the latest published month.")
    t.measure("Latest Rate Change bps", """
        VAR _p = [Previous Actual Rate]
        RETURN IF(ISBLANK(_p), BLANK(), ROUND(([Latest Rate] - _p) * 100, 0))
        """, "+0;-0;0", F, "Change in the market reference rate since the month before, in basis points.")
    t.measure("Latest Rate Caption", f"""
        VAR _d = [Latest Rate Change bps]
        RETURN IF(ISBLANK(_d), FORMAT([Last Actual Month], "mmm yyyy") & ", observed",
                  {delta_text("_d")} & " vs " & FORMAT(EDATE([Last Actual Month], -1), "mmm yyyy"))
        """, None, F)
    t.measure("Data Through Short", '"Data to " & FORMAT([Last Actual Month], "mmm yyyy")', None, F,
              "Shown at the foot of the navigation rail.")
    t.measure("ETS 3-Month Forecast", "CALCULATE(MAX('Forecast Comparison'[ETS rate]), 'Forecast Comparison'[Horizon] = 3, REMOVEFILTERS())", pct, F)
    t.measure("ETS 3-Month Caption", f"""
        VAR _m = CALCULATE(MAX('Forecast Comparison'[Target month]), 'Forecast Comparison'[Horizon] = 3, REMOVEFILTERS())
        VAR _d = ROUND(([ETS 3-Month Forecast] - [Latest Rate]) * 100, 0)
        RETURN {delta_text("_d")} & " vs today, " & FORMAT(_m, "mmm yyyy")
        """, None, F)
    t.measure("Observed Rate", "CALCULATE(MAX('Rate Outlook'[Rate]), 'Rate Outlook'[Series] = \"Actual\")", pct, F)
    t.measure("ETS Forecast", "CALCULATE(MAX('Rate Outlook'[Rate]), 'Rate Outlook'[Series] = \"Forecast\")", pct, F,
              "ETS forecast. The last observed month is included so the line starts where the actuals end.")
    for name, col in [("Band Low", "Lower 80"), ("Band High", "Upper 80")]:
        t.measure(name, f"""
            VAR _v = MAX('Rate Outlook'[{col}])
            RETURN IF(ISBLANK(_v), IF(MAX('Calendar'[Month]) = [Last Actual Month], [Observed Rate]), _v)
            """, pct, F, "Edge of the 80% band built from past ETS errors.")
    t.measure("Bond-yield Forecast", """
        VAR _v = MAX('Forecast Comparison'[Bond-yield rate])
        RETURN IF(ISBLANK(_v), IF(MAX('Calendar'[Month]) = [Last Actual Month], [Observed Rate]), _v)
        """, pct, F, "Forecast from the regression on the 5-year bond yield.")
    t.measure("Mortgage Rate Line", "MAX('Rate vs Yield'[Mortgage rate])", pct, F)
    t.measure("Bond Yield Line", "MAX('Rate vs Yield'[Bond yield])", pct, F)
    t.measure("Pass-through Share", "CALCULATE(MAX('Pass-through'[Cumulative]), REMOVEFILTERS())", "0.00", F,
              "Share of a 1-point bond-yield move that reaches the mortgage rate within four months.")

    F = "Renewal book"
    rise = "SWITCH(_s, -50, 'Loans'[Rise if rates fall], 50, 'Loans'[Rise if rates rise], 'Loans'[Rise at forecast])"
    t.measure("Scenario Shift", "SELECTEDVALUE('Scenario'[Shift bps], 0)", "0", F, "Rate scenario picked on the overview page, in bps.")
    t.measure("Threshold Value", "SELECTEDVALUE('Threshold'[Threshold], 250)", cad, F, "Payment increase being watched, $250 unless changed.")
    t.measure("Loan Count", "COUNTROWS('Loans')", "#,0", F)
    t.measure("Loans Above Threshold", f"""
        VAR _s = [Scenario Shift]
        VAR _t = [Threshold Value]
        RETURN COUNTROWS(FILTER('Loans', {rise} > _t)) + 0
        """, "#,0", F, "Loans whose monthly payment rises by more than the threshold in the chosen scenario.")
    t.measure("Share Above Threshold", "DIVIDE([Loans Above Threshold], [Loan Count])", "0.0%", F)
    t.measure("Total Payment Increase", f"""
        VAR _s = [Scenario Shift]
        RETURN SUMX('Loans', {rise})
        """, "\\$#,0.00", F, "Extra paid each month by the whole book in the chosen scenario.")
    t.measure("Loans Above Caption", 'FORMAT([Share Above Threshold], "0.0%") & " of " & FORMAT([Loan Count], "#,0") & " simulated loans"', None, F)
    t.measure("Payment Increase Caption", 'FORMAT([Total Payment Increase] * 12 / 1000000, "$#,0.0") & "M a year across the book"', None, F)
    t.measure("Overview Headline", """
        VAR _s = [Scenario Shift]
        VAR _tail = SWITCH(_s, -50, " if rates are 50 bps lower", 50, " if rates are 50 bps higher", "")
        RETURN FORMAT([Share Above Threshold], "0.0%") & " of renewing borrowers face a payment rise above "
            & FORMAT([Threshold Value], "$#,0") & " a month" & _tail
        """, None, F, "Headline for the overview page, in the scenario picked there.")
    t.measure("Renewal Window Label", """
        VAR _a = CALCULATE(MIN('Loans'[Renewal date]), REMOVEFILTERS())
        VAR _b = CALCULATE(MAX('Loans'[Renewal date]), REMOVEFILTERS())
        RETURN "90-day renewal window, " & FORMAT(_a, "d mmm yyyy") & " to " & FORMAT(_b, "d mmm yyyy")
        """, None, F)
    t.measure("Share Above, Each Scenario", f"""
        VAR _s = SELECTEDVALUE('Scenario Axis'[Shift bps])
        VAR _t = [Threshold Value]
        RETURN DIVIDE(COUNTROWS(FILTER('Loans', {rise} > _t)), COUNTROWS('Loans'))
        """, "0.0%", F, "Share over the threshold for the scenario on the chart axis, whatever the slicer says.")
    t.measure("Scenario Bar Colour", f"""IF(SELECTEDVALUE('Scenario Axis'[Shift bps]) = [Scenario Shift], "{C['amber']}", "{C['slate']}")""",
              None, F, "Amber for the scenario picked in the slicer.")

    F = "Accuracy"
    t.measure("Selected Horizon", "SELECTEDVALUE('Horizon Choice'[Months], 3)", "0", F)
    t.measure("MAE at Horizon", """
        VAR _h = [Selected Horizon]
        RETURN CALCULATE(MAX('Model Accuracy'[MAE bps]), 'Model Accuracy'[Horizon] = _h)
        """, '0.0" bps"', F, "Average size of a forecast miss, in basis points, at the chosen horizon.")
    for short, model in [("ETS", "ETS damped trend"), ("Naive", "Naive (last observed)"), ("Bond-yield", "Bond-yield regression")]:
        t.measure(f"{short} MAE", f"""
            VAR _h = [Selected Horizon]
            RETURN CALCULATE(MAX('Model Accuracy'[MAE bps]), 'Model Accuracy'[Model] = "{model}", 'Model Accuracy'[Horizon] = _h, REMOVEFILTERS('Model Accuracy'[Model label]))
            """, '0.0" bps"', F)
    t.measure("Naive MAE CAD", """
        VAR _h = [Selected Horizon]
        RETURN CALCULATE(MAX('Model Accuracy'[MAE CAD]), 'Model Accuracy'[Model] = "Naive (last observed)", 'Model Accuracy'[Horizon] = _h, REMOVEFILTERS('Model Accuracy'[Model label]))
        """, cad2, F)
    t.measure("ETS MAE Caption", """
        VAR _h = [Selected Horizon]
        VAR _p = CALCULATE(MAX('ETS Test'[p-value]), 'ETS Test'[Horizon] = _h)
        RETURN FORMAT(1 - DIVIDE([ETS MAE], [Naive MAE]), "0%") & " smaller than naive, p = " & FORMAT(_p, "0.00")
        """, None, F)
    t.measure("Bond-yield MAE Caption", """
        VAR _h = [Selected Horizon]
        VAR _p = CALCULATE(MAX('Bond-yield Test'[p-value]), 'Bond-yield Test'[Comparison] = "Bond-yield regression vs ETS damped trend", 'Bond-yield Test'[Horizon] = _h)
        RETURN FORMAT(1 - DIVIDE([Bond-yield MAE], [ETS MAE]), "0%") & " smaller than ETS, " & IF(_p < 0.001, "p < 0.001", "p = " & FORMAT(_p, "0.000"))
        """, None, F)
    t.measure("Naive MAE Caption", 'FORMAT([Naive MAE CAD], "$#,0.00") & " a month on a $400k loan"', None, F)
    t.measure("Pass-through Caption", '"of a 1-point yield move, within 4 months"', None, F)
    t.measure("Accuracy Headline", """
        VAR _b = [Bond-yield MAE]
        VAR _e = [ETS MAE]
        VAR _lead = "The bond-yield model misses by " & FORMAT(_b, "0.0") & " bps on average, "
        RETURN IF(_b <= _e, _lead & FORMAT(1 - DIVIDE(_b, _e), "0%") & " less than ETS",
                  _lead & FORMAT(DIVIDE(_b, _e) - 1, "0%") & " more than ETS")
        """, None, F, "Headline for the accuracy page, at the horizon picked there.")
    t.measure("Model Bar Colour", f"""
        SWITCH(
            SELECTEDVALUE('Model Accuracy'[Model label]),
            "Bond-yield model", "{C['purple']}",
            "Bond yield, 1-month head start", "{C['purple2']}",
            "Bond yield, no head start", "{C['purple3']}",
            "ETS", "{C['teal']}",
            "{C['slate']}"
        )
        """, None, F)
    for short, model in [("ETS", "ETS damped trend"), ("Naive", "Naive (last observed)")]:
        t.measure(f"{short} MAE by Period", f"""
            VAR _h = [Selected Horizon]
            RETURN CALCULATE(MAX('Accuracy By Period'[MAE bps]), 'Accuracy By Period'[Model] = "{model}", 'Accuracy By Period'[Horizon] = _h)
            """, "0.0", F)

    F = "Payment impact"
    t.measure("Chosen Balance", "SELECTEDVALUE('Balance Choice'[Balance], 400000)", cad, F)
    t.measure("Chosen Years Left", "SELECTEDVALUE('Years Choice'[Years], 20)", "0", F)
    t.measure("Chosen Rate Before", "SELECTEDVALUE('Rate Before Choice'[Rate], 2.2)", pct, F)
    t.measure("ETS 6-Month Forecast", "CALCULATE(MAX('Forecast Comparison'[ETS rate]), 'Forecast Comparison'[Horizon] = 6, REMOVEFILTERS())", pct, "Rates")
    t.measure("Bond-yield 6-Month Forecast", "CALCULATE(MAX('Forecast Comparison'[Bond-yield rate]), 'Forecast Comparison'[Horizon] = 6, REMOVEFILTERS())", pct, "Rates")
    t.measure("Chosen Renewal Rate", """
        SWITCH(
            SELECTEDVALUE('Renewal Rate Choice'[Renewal rate option], "Latest"),
            "ETS 3 mo", [ETS 3-Month Forecast],
            "ETS 6 mo", [ETS 6-Month Forecast],
            "Bond yield 6 mo", [Bond-yield 6-Month Forecast],
            [Latest Rate]
        )
        """, pct, F, "Renewal rate picked in the slicer: today's rate or one of the forecasts.")
    t.measure("Payment Before", payment_dax("[Chosen Balance]", "[Chosen Years Left]", "[Chosen Rate Before]") + "\nRETURN _pay", cad2, F,
              "Monthly payment before renewal. Canadian semi-annual compounding, same formula as the Python and SQL code.")
    t.measure("Payment After", payment_dax("[Chosen Balance]", "[Chosen Years Left]", "[Chosen Renewal Rate]") + "\nRETURN _pay", cad2, F,
              "Monthly payment at the chosen renewal rate.")
    t.measure("Monthly Change", "[Payment After] - [Payment Before]", "+\\$#,0.00;-\\$#,0.00;\\$0.00", F)
    t.measure("Payment Before Caption", '"at " & FORMAT([Chosen Rate Before], "0.00") & "%"', None, F)
    t.measure("Payment After Caption", '"at " & FORMAT([Chosen Renewal Rate], "0.00") & "%"', None, F)
    t.measure("Monthly Change Caption", 'FORMAT([Monthly Change] * 12, "$#,0") & " a year, threshold " & FORMAT([Threshold Value], "$#,0")', None, F)
    t.measure("Payment Headline", """
        VAR _c = [Monthly Change]
        VAR _lead = "A " & FORMAT([Chosen Balance] / 1000, "$#,0") & "k renewal at " & FORMAT([Chosen Renewal Rate], "0.00") & "% "
        RETURN _lead & IF(_c >= 0, "adds", "saves") & " " & FORMAT(ABS(_c), "$#,0.00") & " a month"
        """, None, F, "Headline for the payment page. A renewal rate below the old rate saves money.")
    curve = payment_dax("[Chosen Balance]", "[Chosen Years Left]", "_rate")
    t.measure("Threshold Rate", f"""
        VAR _base = [Payment Before]
        VAR _t = [Threshold Value]
        VAR _bal = [Chosen Balance]
        VAR _n = [Chosen Years Left] * 12
        RETURN
            MINX(
                FILTER(
                    'Rate Grid',
                    VAR _r = 'Rate Grid'[Rate]
                    VAR _i = POWER(1 + _r / 200, 1 / 6) - 1
                    VAR _p = IF(_r = 0, DIVIDE(_bal, _n), _bal * _i / (1 - POWER(1 + _i, -_n)))
                    RETURN _p - _base >= _t - 0.000000001
                ),
                'Rate Grid'[Rate]
            )
        """, pct, F, "Lowest rate on the 1 bp grid where the payment rise reaches the threshold. Within 1 bp of the exact answer.")
    t.measure("Threshold Rate Caption", """
        VAR _tr = [Threshold Rate]
        VAR _d = ([Chosen Renewal Rate] - _tr) * 100
        RETURN IF(ISBLANK(_tr), "Not reached between 0% and 10%",
                  "Renewal rate is " & FORMAT(ABS(_d), "0") & " bps " & IF(_d >= 0, "above", "below") & " it")
        """, None, F)
    t.measure("Change on Curve", "VAR _rate = SELECTEDVALUE('Rate Grid'[Rate])\n" + curve
              + "\nRETURN IF(NOT ISBLANK(_rate) && _rate >= 0.5 && _rate <= 8, _pay - [Payment Before])",
              cad, F, "Payment change at each rate on the curve, 0.5% to 8%.")
    t.measure("Threshold Line", """
        VAR _rate = SELECTEDVALUE('Rate Grid'[Rate])
        RETURN IF(NOT ISBLANK(_rate) && _rate >= 0.5 && _rate <= 8, [Threshold Value])
        """, cad, F)
    t.measure("Selected Rate Point", """
        VAR _rate = SELECTEDVALUE('Rate Grid'[Rate])
        RETURN IF(NOT ISBLANK(_rate) && ABS(_rate - ROUND([Chosen Renewal Rate], 2)) < 0.00001, [Monthly Change])
        """, cad, F, "One point on the curve at the chosen renewal rate.")
    t.measure("Change at Scenario", "VAR _rate = [Chosen Renewal Rate] + SELECTEDVALUE('Scenario Axis'[Shift bps], 0) / 100\n" + curve
              + "\nRETURN _pay - [Payment Before]", "+\\$#,0;-\\$#,0", F, "Payment change at the renewal rate, 50 bps lower and 50 bps higher.")
    t.measure("Scenario Change Colour", f"""IF([Change at Scenario] > [Threshold Value], "{C['amber']}", "{C['slate']}")""", None, F)

    F = "Triage"
    t.measure("Chosen Calls per Week", "SELECTEDVALUE('Capacity Choice'[Calls per week], 300)", "#,0", F)
    for name, col, desc in [
        ("Missed with Triage", "Over missed", "Borrowers over the $250 threshold who never get a call, using the triage rule."),
        ("Missed in Date Order", "Over missed, date order", "Same, when the team simply calls in renewal-date order."),
        ("Near-certain Missed", "Segment 1 missed", None),
        ("Near-certain Missed, Date Order", "Segment 1 missed, date order", None),
        ("Missed Rise with Triage", "Missed rise per month", None),
        ("Missed Rise in Date Order", "Missed rise per month, date order", None),
    ]:
        t.measure(name, f"""
            VAR _c = [Chosen Calls per Week]
            RETURN CALCULATE(SUM('Call Capacity'[{col}]), 'Call Capacity'[Calls per week] = _c)
            """, cad if "Rise" in name else "#,0", F, desc)
    t.measure("Extra Borrowers Reached", "[Missed in Date Order] - [Missed with Triage]", "+#,0;-#,0;0", F)
    t.measure("Triage Headline", """
        VAR _x = [Extra Borrowers Reached]
        VAR _lead = "At " & FORMAT([Chosen Calls per Week], "#,0") & " calls a week, "
        RETURN _lead & IF(_x > 0, "triage reaches " & FORMAT(_x, "#,0") & " more at-risk borrowers than calling by date",
                   IF(_x = 0, "triage and calling by date reach the same number of at-risk borrowers",
                      "calling by date reaches " & FORMAT(-_x, "#,0") & " more at-risk borrowers than triage"))
        """, None, F, "Headline for the triage page, at the call capacity picked there.")
    t.measure("Missed with Triage Caption", '"borrowers over +$250 never called"', None, F)
    t.measure("Missed in Date Order Caption", '"same capacity, calling by renewal date"', None, F)
    t.measure("Extra Reached Caption", 'FORMAT([Missed Rise in Date Order] - [Missed Rise with Triage], "$#,0") & " a month of rises caught in time"', None, F)
    t.measure("Near-certain Caption", '"against " & FORMAT([Near-certain Missed, Date Order], "#,0") & " in date order"', None, F)
    t.measure("Missed by Capacity, Date Order", "SUM('Call Capacity'[Over missed, date order])", "#,0", F)
    t.measure("Missed by Capacity, Triage", "SUM('Call Capacity'[Over missed])", "#,0", F)


def check_names(tables: list[Table]) -> None:
    taken = {t.name.lower() for t in tables} | {c.name.lower() for t in tables for c in t.cols}
    clashes = [m[0] for t in tables for m in t.measures if m[0].lower() in taken]
    assert not clashes, f"measures that share a name with a table or column: {clashes}"


def write_model(tables: list[Table]) -> None:
    check_names(tables)
    if SM.exists():
        shutil.rmtree(SM)
    write_json(SM / "definition.pbism", {"$schema": f"{SCHEMA}/item/semanticModel/definitionProperties/1.0.0/schema.json",
                                         "version": "4.0", "settings": {}})
    d = SM / "definition"
    write(d / "database.tmdl", "database\n\tcompatibilityLevel: 1601\n\n")
    order = ["DataFolder"] + [t.name for t in tables]
    write(d / "model.tmdl", "\n".join([
        "model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tdiscourageImplicitMeasures", "\tsourceQueryCulture: en-US", "\tdataAccessOptions",
        "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull", "",
        "annotation __PBI_TimeIntelligenceEnabled = 0", "",
        f"annotation PBI_QueryOrder = {json.dumps(order)}", "",
    ] + [f"ref table {q(t.name)}" for t in tables]) + "\n\n")
    write(d / "expressions.tmdl", "\n".join([
        "/// Where the CSVs exported by src/run_sql.py live: the GitHub copy by default, or a local",
        "/// data/powerbi folder. Change it in Transform data > Edit parameters, then refresh.",
        f'expression DataFolder = "{DEFAULT_DATA_FOLDER}" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]',
        f"\tlineageTag: {tag('DataFolder')}", "", "\tannotation PBI_ResultType = Text", "", ""]))
    write(d / "relationships.tmdl", "\n".join([
        f"relationship {tag('rel', 'outlook-calendar')}", "\tfromColumn: 'Rate Outlook'.Month", "\ttoColumn: Calendar.Month", "",
        f"relationship {tag('rel', 'forecast-calendar')}", "\tfromColumn: 'Forecast Comparison'.'Target month'", "\ttoColumn: Calendar.Month", "", ""]))
    for t in tables:
        if t.csv:
            t.csv_cols = set(csv_header(t.csv))
            missing = [c.src for c in t.cols if c.src not in t.csv_cols and not t.extra_steps]
            assert not missing, (t.name, missing)
        write(d / "tables" / f"{t.name}.tmdl", t.tmdl())


def csv_header(name: str) -> list[str]:
    return (HERE.parent / "data" / "powerbi" / name).read_text(encoding="utf-8").splitlines()[0].split(",")


# =============================================================================================
# Report (PBIR JSON)
# =============================================================================================
def lit(v: str) -> dict:
    return {"expr": {"Literal": {"Value": v}}}


def text(s: str) -> dict:
    return lit("'" + s.replace("'", "''") + "'")


def num(n) -> dict:
    return lit(f"{n}D")


def boolean(b: bool) -> dict:
    return lit("true" if b else "false")


def colour(hex_: str) -> dict:
    return {"solid": {"color": text(hex_)}}


def measure_colour(name: str) -> dict:
    return {"solid": {"color": {"expr": {"Measure": {"Expression": {"SourceRef": {"Entity": "measures_calc"}}, "Property": name}}}}}


def mref(name):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": "measures_calc"}}, "Property": name}}


def cref(table, col):
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": col}}


DISPLAY = {
    "Observed Rate": "Observed", "ETS Forecast": "ETS forecast", "Band Low": "80% band, low", "Band High": "80% band, high",
    "Bond-yield Forecast": "Bond-yield forecast", "Mortgage Rate Line": "Mortgage rate", "Bond Yield Line": "5-year bond yield",
    "Missed by Capacity, Date Order": "Calling by renewal date", "Missed by Capacity, Triage": "Triage rule",
    "ETS MAE by Period": "ETS", "Naive MAE by Period": "Naive", "Loan Count": "Loans", "Loans Above Threshold": "Above +$250",
    "Share Above Threshold": "Share above", "Change on Curve": "Payment change", "Threshold Line": "Threshold",
    "Selected Rate Point": "Chosen rate", "MAE at Horizon": "Average miss (bps)", "Share Above, Each Scenario": "Share above +$250",
    "Change at Scenario": "Payment change",
}


def proj_m(name):
    p = {"field": mref(name), "queryRef": f"measures_calc.{name}", "nativeQueryRef": name}
    if name in DISPLAY:
        p["displayName"] = DISPLAY[name]
    return p


def proj_c(table, col):
    return {"field": cref(table, col), "queryRef": f"{table}.{col}", "nativeQueryRef": col, "active": True}


def props(**kw) -> list:
    return [{"properties": kw}]


def label_height(size_pt: float) -> int:
    """Smallest text box height (px) that shows one line of size_pt text without a scrollbar."""
    return math.ceil(size_pt * 4 / 3 * 1.45) + 20


def series(qref: str, **kw) -> dict:
    return {"properties": kw, "selector": {"metadata": qref}}


NO_CHROME = {"title": props(show=boolean(False)), "background": props(show=boolean(False)),
             "border": props(show=boolean(False)), "dropShadow": props(show=boolean(False))}


class Page:
    def __init__(self, name, display, bg_panels):
        self.name, self.display, self.bg_panels, self.visuals, self._z = name, display, bg_panels, [], 1000

    def add(self, vname, x, y, w, h, visual, filters=None):
        self._z += 100
        v = {"$schema": S_VISUAL, "name": vname,
             "position": {"x": x, "y": y, "z": self._z, "height": h, "width": w, "tabOrder": self._z}, "visual": visual}
        if filters:
            v["filterConfig"] = {"filters": filters}
        self.visuals.append(v)

    # ---- building blocks ---------------------------------------------------------------------
    def label(self, vname, x, y, w, h, runs, align="left"):
        """Static text. runs = [(text, size_pt, colour, font)]."""
        para = {"textRuns": [{"value": t, "textStyle": {"fontFamily": f, "fontSize": f"{s}pt", "color": c}} for t, s, c, f in runs]}
        if align != "left":
            para["horizontalTextAlignment"] = align
        # Power BI pads text boxes, so a box sized to the font clips the text and shows a scrollbar.
        # The text sits at the top, so a taller transparent box only adds room underneath.
        h = max(h, label_height(max(s for _, s, _, _ in runs)))
        y = min(y, PAGE_H - h)
        self.add(vname, x, y, w, h, {"visualType": "textbox", "objects": {"general": props(paragraphs=[para])},
                                     "visualContainerObjects": NO_CHROME, "drillFilterOtherVisuals": True})

    def card(self, vname, x, y, w, h, measure, size=26, colour_hex=C["ink"], font=FONT_NUM, millions=False, full=False):
        labels = dict(color=colour(colour_hex), fontSize=num(size), fontFamily=text(font))
        if millions:
            # Let the card scale to millions. Scaling with ",," in the format string as well
            # makes Power BI print "$2.21,,MM".
            labels.update(labelDisplayUnits=num(1000000), labelPrecision=lit("2L"))
        elif full:
            # The card's default "Auto" units turn $2,059.72 into $2.06K.
            labels.update(labelDisplayUnits=num(1))
        self.add(vname, x, y, w, h, {
            "visualType": "card",
            "query": {"queryState": {"Values": {"projections": [proj_m(measure)]}}},
            "objects": {"labels": props(**labels),
                        "categoryLabels": props(show=boolean(False))},
            "visualContainerObjects": NO_CHROME, "drillFilterOtherVisuals": True})

    def headline(self, vname, x, y, w, h, measure, size=18, colour_hex=C["ink"], font=FONT_BOLD):
        """Left-aligned text from a measure. A card centres its value, so this uses a multi-row card
        with the category label, accent bar and outline turned off."""
        self.add(vname, x, y, w, h, {
            "visualType": "multiRowCard",
            "query": {"queryState": {"Values": {"projections": [proj_m(measure)]}}},
            "objects": {"dataLabels": props(color=colour(colour_hex), fontSize=num(size), fontFamily=text(font)),
                        "categoryLabels": props(show=boolean(False)),
                        "card": props(outline=text("None"), barShow=boolean(False), cardPadding=num(0))},
            "visualContainerObjects": NO_CHROME, "drillFilterOtherVisuals": True})

    def kpi(self, prefix, x, y, w, title, value_measure, caption_measure, accent=None, millions=False):
        self.label(f"{prefix}_label", x + 8, y + 8, w - 16, 26, [(title, 10, C["ink2"], FONT)], align="center")
        self.card(f"{prefix}_value", x + 8, y + 30, w - 16, 40, value_measure, 24, accent or C["ink"],
                  millions=millions, full=not millions)
        self.card(f"{prefix}_caption", x + 8, y + 66, w - 16, 26, caption_measure, 10, C["ink2"], FONT)

    def slicer(self, vname, x, y, w, h, table, col, default, header=None, item_size=10, dropdown=False):
        objects = {
            "data": props(mode=text("Dropdown" if dropdown else "HorizontalList")),
            "general": [{"properties": {
                "orientation": lit("1D"),
                "filter": {"filter": {"Version": 2, "From": [{"Name": "s", "Entity": table, "Type": 0}], "Where": [{"Condition": {"In": {
                    "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": col}}],
                    "Values": [[text(default)["expr"]]]}}}]}}}}],
            "selection": props(singleSelect=boolean(True), strictSingleSelect=boolean(True), selectAllCheckboxEnabled=boolean(False)),
            "items": props(fontColor=colour(C["ink"]), background=colour(C["panel"]), textSize=num(item_size), fontFamily=text(FONT)),
            "header": props(show=boolean(header is not None), **({"text": text(header), "fontColor": colour(C["ink2"]),
                                                                 "textSize": num(9), "fontFamily": text(FONT)} if header else {})),
        }
        self.add(vname, x, y, w, h, {
            "visualType": "slicer",
            "query": {"queryState": {"Values": {"projections": [proj_c(table, col)]}},
                      "sortDefinition": {"sort": [{"field": cref(table, col), "direction": "Ascending"}], "isDefaultSort": True}},
            "objects": objects, "visualContainerObjects": NO_CHROME, "drillFilterOtherVisuals": True})

    def line_chart(self, vname, x, y, w, h, category, measures, styles, filters=None, value_fmt=None, legend=True, x_scalar=True,
                   y_start=None):
        """measures: list of names; styles: {name: dict(colour, width, dash, marker)}."""
        data_points, line_styles = [], []
        for m in measures:
            st = styles[m]
            data_points.append(series(f"measures_calc.{m}", fill=colour(st["colour"])))
            ls = {"strokeWidth": num(st.get("width", 2)), "lineStyle": text(st.get("dash", "solid")),
                  "showMarker": boolean(st.get("marker", False))}
            if st.get("marker"):
                ls["markerSize"] = num(7)
                ls["markerShape"] = text("circle")
            line_styles.append(series(f"measures_calc.{m}", **ls))
        objects = {
            "dataPoint": data_points, "lineStyles": line_styles,
            "legend": props(show=boolean(legend), position=text("Top"), labelColor=colour(C["ink2"]), fontSize=num(9), showTitle=boolean(False)),
            "categoryAxis": props(axisType=text("Scalar" if x_scalar else "Categorical"), labelColor=colour(C["ink3"]), fontSize=num(9),
                                  showAxisTitle=boolean(False), gridlineShow=boolean(False)),
            "valueAxis": props(labelColor=colour(C["ink3"]), fontSize=num(9), showAxisTitle=boolean(False),
                               gridlineColor=colour(C["grid"]), gridlineStyle=text("solid")),
        }
        if value_fmt:
            objects["valueAxis"][0]["properties"]["labelDisplayUnits"] = num(1)
        if y_start is not None:
            objects["valueAxis"][0]["properties"]["start"] = num(y_start)
        self.add(vname, x, y, w, h, {
            "visualType": "lineChart",
            "query": {"queryState": {"Category": {"projections": [proj_c(*category)]},
                                     "Y": {"projections": [proj_m(m) for m in measures]}},
                      "sortDefinition": {"sort": [{"field": cref(*category), "direction": "Ascending"}], "isDefaultSort": True}},
            "objects": objects, "visualContainerObjects": NO_CHROME, "drillFilterOtherVisuals": True}, filters)

    def bar_chart(self, vname, x, y, w, h, category, measure, colour_measure=None, fixed_colour=None, horizontal=True, label_fmt=None):
        fill = measure_colour(colour_measure) if colour_measure else colour(fixed_colour or C["slate"])
        selector = {"data": [{"dataViewWildcard": {"matchingOption": 1}}]} if colour_measure else None
        dp = {"properties": {"fill": fill}}
        if selector:
            dp["selector"] = selector
        self.add(vname, x, y, w, h, {
            "visualType": "clusteredBarChart" if horizontal else "clusteredColumnChart",
            "query": {"queryState": {"Category": {"projections": [proj_c(*category)]}, "Y": {"projections": [proj_m(measure)]}},
                      "sortDefinition": {"sort": [{"field": cref(*category), "direction": "Ascending"}], "isDefaultSort": True}},
            "objects": {
                "dataPoint": [dp],
                "labels": props(show=boolean(True), color=colour(C["ink"]), fontSize=num(10), fontFamily=text(FONT_SEMI)),
                "valueAxis": props(show=boolean(False), gridlineShow=boolean(False)),
                # maxMarginFactor: room for the category names (default 25%), so long names aren't cut
                "categoryAxis": props(labelColor=colour(C["ink2"]), fontSize=num(10), showAxisTitle=boolean(False),
                                      maxMarginFactor=num(45)),
                "legend": props(show=boolean(False)),
            },
            "visualContainerObjects": NO_CHROME, "drillFilterOtherVisuals": True})

    def column_pair(self, vname, x, y, w, h, category, measures, colours):
        self.add(vname, x, y, w, h, {
            "visualType": "clusteredColumnChart",
            "query": {"queryState": {"Category": {"projections": [proj_c(*category)]}, "Y": {"projections": [proj_m(m) for m in measures]}},
                      "sortDefinition": {"sort": [{"field": cref(*category), "direction": "Ascending"}], "isDefaultSort": True}},
            "objects": {
                "dataPoint": [series(f"measures_calc.{m}", fill=colour(c)) for m, c in zip(measures, colours)],
                "labels": props(show=boolean(True), color=colour(C["ink"]), fontSize=num(9)),
                "legend": props(show=boolean(True), position=text("Top"), labelColor=colour(C["ink2"]), fontSize=num(9), showTitle=boolean(False)),
                "valueAxis": props(labelColor=colour(C["ink3"]), fontSize=num(9), showAxisTitle=boolean(False),
                                   gridlineColor=colour(C["grid"]), gridlineStyle=text("solid")),
                "categoryAxis": props(labelColor=colour(C["ink2"]), fontSize=num(10), showAxisTitle=boolean(False)),
            },
            "visualContainerObjects": NO_CHROME, "drillFilterOtherVisuals": True})

    def table(self, vname, x, y, w, h, fields, sort=None, size=10, row_padding=5):
        projections = [proj_c(*f) if isinstance(f, tuple) else proj_m(f) for f in fields]
        # In a table every field shares one role; Power BI shows only the "active" ones
        # when some are marked, so mark measures too or they disappear.
        for pr in projections:
            pr["active"] = True
        query = {"queryState": {"Values": {"projections": projections}}}
        if sort:
            query["sortDefinition"] = {"sort": [{"field": cref(*sort), "direction": "Ascending"}], "isDefaultSort": True}
        self.add(vname, x, y, w, h, {
            "visualType": "tableEx", "query": query,
            "objects": {
                "columnHeaders": props(fontColor=colour(C["ink2"]), backColor=colour(C["panel"]), fontFamily=text(FONT_SEMI),
                                       fontSize=num(size), outlineStyle=lit("0D")),
                "values": props(fontColorPrimary=colour(C["ink"]), backColorPrimary=colour(C["panel"]),
                                fontColorSecondary=colour(C["ink"]), backColorSecondary=colour(C["panel"]), fontSize=num(size),
                                fontFamily=text(FONT)),
                "grid": props(gridVertical=boolean(False), gridHorizontal=boolean(True), gridHorizontalColor=colour(C["line"]),
                              rowPadding=num(row_padding), outlineColor=colour(C["line"])),
                "total": props(totals=boolean(False)),
            },
            "visualContainerObjects": NO_CHROME, "drillFilterOtherVisuals": True})

    def json(self) -> dict:
        return {
            "$schema": S_PAGE, "name": self.name, "displayName": self.display, "displayOption": "FitToPage",
            "height": PAGE_H, "width": PAGE_W,
            "objects": {
                "background": props(image={"image": {
                    "name": text(f"{self.name}.png"),
                    "url": {"expr": {"ResourcePackageItem": {"PackageName": "RegisteredResources", "PackageType": 1, "ItemName": f"bg_{self.name}.png"}}},
                    "scaling": text("Fit")}}, transparency=num(0)),
                "outspace": props(color=colour(C["canvas"]), transparency=num(0)),
            },
        }


def date_from(day: str) -> list:
    """Visual-level filter: Calendar[Month] on or after a date."""
    return [{"name": "FilterCalendarFrom", "field": cref("Calendar", "Month"), "type": "Advanced", "howCreated": "User",
             "filter": {"Version": 2, "From": [{"Name": "c", "Entity": "Calendar", "Type": 0}], "Where": [{"Condition": {"Comparison": {
                 "ComparisonKind": 2, "Left": {"Column": {"Expression": {"SourceRef": {"Source": "c"}}, "Property": "Month"}},
                 "Right": {"Literal": {"Value": f"datetime'{day}T00:00:00'"}}}}}]}}]


# ---- page layouts --------------------------------------------------------------------------------
# A navy rail on the left holds the page buttons. The content area to its right has a headline band,
# a row of four KPI tiles and the main panels, with a source line at the foot.
M = 24                              # outer margin
RAIL_W = 168                        # navigation rail
X0 = RAIL_W + M                     # left edge of the content area
CONTENT_W = PAGE_W - X0 - M
TILE_Y, TILE_H = 104, 96            # KPI row under the headline band
PANEL_Y, PANEL_BOTTOM = 212, 680    # main panels
LEFT_W = round(CONTENT_W * 0.6) - 6  # two-column panels: about 3/5 and 2/5
RIGHT_X = X0 + LEFT_W + 12
RIGHT_W = PAGE_W - M - RIGHT_X
SOURCE_RATE = "Source: Bank of Canada series V122667786 (uninsured 5-year+ fixed). Loans are simulated."


def nav_button(p: Page, target: str, label: str, y: float) -> None:
    """A button on the rail that opens another page. The current page's button is filled."""
    current = target == p.name
    fill, ink = (C["rail_sel"], C["rail_ink"]) if current else (C["rail"], C["rail_ink2"])
    state = lambda sid, **kw: {"properties": kw, "selector": {"id": sid}}
    p.add(f"nav_{target}", 12, y, RAIL_W - 24, 36, {
        "visualType": "actionButton",
        "objects": {
            "fill": [state("default", show=boolean(True), fillColor=colour(fill), transparency=num(0)),
                     state("hover", show=boolean(True), fillColor=colour(C["rail_hover"] if not current else fill), transparency=num(0))],
            "text": [state("default", show=boolean(True), text=text(label), fontColor=colour(ink), fontSize=num(10),
                           fontFamily=text(FONT_SEMI if current else FONT), horizontalAlignment=text("left"), leftMargin=num(12)),
                     state("hover", fontColor=colour(C["rail_ink"]))],
            "icon": [state("default", show=boolean(False))],
            "outline": [state("default", show=boolean(False))],
            "shape": props(tileShape=text("rectangleRounded"), roundEdge=num(6)),
        },
        "visualContainerObjects": {**NO_CHROME, "visualLink": props(
            show=boolean(True), type=text("PageNavigation"), navigationSection=text(target))},
        "drillFilterOtherVisuals": True})


def rail(p: Page) -> None:
    """Report name, one button per page, and the month the data runs to. The rail itself is drawn
    into the background image."""
    p.label("rail_title", 14, 18, RAIL_W - 28, 64,
            [("Mortgage renewal\n", 12, C["rail_ink"], FONT_BOLD), ("Canada, 5-year fixed", 9, C["rail_ink2"], FONT)])
    for i, (name, display) in enumerate(PAGES):
        nav_button(p, name, display, 96 + i * 44)
    p.card("rail_data", 12, PAGE_H - 52, RAIL_W - 24, 30, "Data Through Short", 9, C["rail_ink2"], FONT)


def header(p: Page, headline_measure: str | None = None, headline_text: str | None = None,
           subtitle_measure: str | None = None, subtitle_text: str | None = None) -> None:
    """Headline that states the finding, with a grey line of scope and units under it. A short navy
    rule above the headline is drawn into the background, as in the report's charts."""
    if headline_measure:
        p.headline("hdr_headline", X0 - 8, 26, CONTENT_W + 8, 46, headline_measure)
    else:
        p.label("hdr_headline", X0 - 4, 26, CONTENT_W + 4, 46, [(headline_text, 18, C["ink"], FONT_BOLD)])
    if subtitle_measure:
        p.headline("hdr_subtitle", X0 - 8, 66, CONTENT_W + 8, 30, subtitle_measure, size=10, colour_hex=C["ink2"], font=FONT)
    elif subtitle_text:
        p.label("hdr_subtitle", X0 - 4, 64, CONTENT_W + 4, 28, [(subtitle_text, 10, C["ink2"], FONT)])


def footer(p: Page, source: str) -> None:
    p.label("footer_left", X0 - 4, 684, CONTENT_W - 200, 20, [(source, 8, C["ink3"], FONT)])
    p.label("footer_right", PAGE_W - M - 200, 684, 200, 20, [("Neo Nguyen", 8, C["ink3"], FONT)], align="right")


def panel_title(p: Page, vname, x, y, w, title, note=None):
    p.label(f"{vname}_title", x + 10, y + 8, w * 0.6 if note else w - 20, 30, [(title, 12, C["ink"], FONT_SEMI)])
    if note:
        p.label(f"{vname}_note", x + w * 0.45, y + 10, w * 0.55 - 12, 26, [(note, 9, C["ink3"], FONT)], align="right")


def tiles(n, highlight_first=False, y=TILE_Y, h=TILE_H):
    w = CONTENT_W / n
    out = [("tilebar", X0, y, CONTENT_W, h)]
    if highlight_first:
        out.append(("tile_hi", X0, y, w, h))
    out += [("divider", X0 + i * w, y + 14, 1, h - 28) for i in range(1, n)]
    return out, w


def rate_legend(p, vname, x, y, keys):
    """Legend drawn as text, so the two band edges show as one "80% band" entry."""
    runs = []
    for name, col, mark in keys:
        runs += [(mark + " ", 9, col, FONT), (name + "     ", 9, C["ink2"], FONT)]
    p.label(vname, x, y, 600, label_height(9), runs)


def build_pages() -> list[Page]:
    pages = []
    line_styles = {
        "Observed Rate": {"colour": C["blue"], "width": 2.5},
        "ETS Forecast": {"colour": C["teal"], "width": 2.5, "dash": "dashed"},
        "Band Low": {"colour": C["teal_dim"], "width": 1, "dash": "dotted"},
        "Band High": {"colour": C["teal_dim"], "width": 1, "dash": "dotted"},
        "Bond-yield Forecast": {"colour": C["purple"], "width": 2.5, "dash": "dashed"},
    }
    full_h = PANEL_BOTTOM - PANEL_Y

    # ---- 1. Overview: the shape of the brief -------------------------------------------------
    bg, tw = tiles(4, highlight_first=True)
    bg += [("panel", X0, PANEL_Y, LEFT_W, 300), ("panel", RIGHT_X, PANEL_Y, RIGHT_W, 300),
           ("panel", X0, 524, CONTENT_W, PANEL_BOTTOM - 524)]
    p = Page("overview", "Overview", bg)
    rail(p)
    header(p, headline_text="Canadian Mortgage Renewal", subtitle_measure="Renewal Window Label")
    p.kpi("kpi_above", X0, TILE_Y, tw, "Loans above +$250 a month", "Loans Above Threshold", "Loans Above Caption", C["amber"])
    p.kpi("kpi_total", X0 + tw, TILE_Y, tw, "Total payment increase", "Total Payment Increase", "Payment Increase Caption", millions=True)
    p.kpi("kpi_latest", X0 + 2 * tw, TILE_Y, tw, "Market benchmark", "Latest Rate", "Latest Rate Caption")
    p.kpi("kpi_ets3", X0 + 3 * tw, TILE_Y, tw, "3-month forecast", "ETS 3-Month Forecast", "ETS 3-Month Caption", C["teal"])
    panel_title(p, "outlook", X0, PANEL_Y, LEFT_W, "Mortgage rate outlook", "% a year, 5-year+ fixed")
    rate_legend(p, "outlook_legend", X0 + 12, PANEL_Y + 34, [("Observed", C["blue"], "—"), ("ETS forecast", C["teal"], "- -"),
                                                             ("80% band", C["teal_dim"], "···")])
    p.line_chart("outlook_chart", X0 + 8, PANEL_Y + 60, LEFT_W - 16, 232, ("Calendar", "Month"),
                 ["Observed Rate", "ETS Forecast", "Band Low", "Band High"], line_styles, filters=date_from("2021-01-01"), legend=False)
    panel_title(p, "stress", RIGHT_X, PANEL_Y, RIGHT_W, "Rate stress test", "Portfolio scenario")
    p.slicer("stress_slicer", RIGHT_X + 12, PANEL_Y + 40, RIGHT_W - 24, 44, "Scenario", "Scenario", "Base")
    p.bar_chart("stress_bars", RIGHT_X + 8, PANEL_Y + 90, RIGHT_W - 16, 168, ("Scenario Axis", "Scenario"), "Share Above, Each Scenario",
                colour_measure="Scenario Bar Colour")
    p.label("stress_caption", RIGHT_X + 12, PANEL_Y + 258, RIGHT_W - 24, 30,
            [("Share of renewing loans with a payment rise above $250 a month", 9, C["ink3"], FONT)])
    panel_title(p, "months", X0, 524, CONTENT_W, "Renewals by month", "Chosen scenario. November and February are part months.")
    # Compact rows so all four renewal months fit without a scrollbar.
    p.table("months_table", X0 + 10, 556, CONTENT_W - 20, 116,
            [("Loans", "Renewal month"), "Loan Count", "Loans Above Threshold", "Share Above Threshold"], sort=("Loans", "Renewal month"),
            size=9, row_padding=1)
    footer(p, SOURCE_RATE)
    pages.append(p)

    # ---- 2. Rates and forecast accuracy -------------------------------------------------------
    bg, tw = tiles(4)
    half = (full_h - 12) / 2
    low_y = PANEL_Y + half + 12
    bg += [("panel", X0, PANEL_Y, LEFT_W, half), ("panel", RIGHT_X, PANEL_Y, RIGHT_W, half),
           ("panel", X0, low_y, LEFT_W, half), ("panel", RIGHT_X, low_y, RIGHT_W, half)]
    p = Page("accuracy", "Rates & accuracy", bg)
    rail(p)
    header(p, headline_text="Where rates are heading, and how far to trust it", subtitle_text=f"Backtest: {backtest_span()}")
    p.kpi("kpi_ets", X0, TILE_Y, tw, "ETS, average miss", "ETS MAE", "ETS MAE Caption", C["teal"])
    p.kpi("kpi_bond", X0 + tw, TILE_Y, tw, "Bond-yield model, average miss", "Bond-yield MAE", "Bond-yield MAE Caption", C["purple"])
    p.kpi("kpi_naive", X0 + 2 * tw, TILE_Y, tw, "Naive, average miss", "Naive MAE", "Naive MAE Caption")
    p.kpi("kpi_pass", X0 + 3 * tw, TILE_Y, tw, "Bond-yield pass-through", "Pass-through Share", "Pass-through Caption")
    panel_title(p, "history", X0, PANEL_Y, LEFT_W, "Rate history and the two forecasts", "% a year, 5-year+ fixed")
    rate_legend(p, "history_legend", X0 + 12, PANEL_Y + 32, [("Observed", C["blue"], "—"), ("ETS forecast", C["teal"], "- -"),
                                                             ("Bond-yield forecast", C["purple"], "- -"), ("ETS 80% band", C["teal_dim"], "···")])
    p.line_chart("history_chart", X0 + 8, PANEL_Y + 56, LEFT_W - 16, half - 62, ("Calendar", "Month"),
                 ["Observed Rate", "ETS Forecast", "Bond-yield Forecast", "Band Low", "Band High"], line_styles,
                 filters=date_from("2016-01-01"), legend=False, y_start=1)
    panel_title(p, "models", RIGHT_X, PANEL_Y, RIGHT_W, "Average miss by model")
    p.slicer("horizon_slicer", RIGHT_X + RIGHT_W - 192, PANEL_Y + 8, 180, 32, "Horizon Choice", "Horizon label", "3 months")
    p.bar_chart("models_bars", RIGHT_X + 8, PANEL_Y + 46, RIGHT_W - 16, half - 52, ("Model Accuracy", "Model label"), "MAE at Horizon",
                colour_measure="Model Bar Colour")
    panel_title(p, "yield", X0, low_y, LEFT_W, "Mortgage rate and the 5-year bond yield", "The yield moves first")
    p.line_chart("yield_chart", X0 + 8, low_y + 36, LEFT_W - 16, half - 42, ("Rate vs Yield", "Month"), ["Mortgage Rate Line", "Bond Yield Line"],
                 {"Mortgage Rate Line": {"colour": C["blue"], "width": 2.5}, "Bond Yield Line": {"colour": C["purple"], "width": 2}})
    panel_title(p, "periods", RIGHT_X, low_y, RIGHT_W, "ETS and naive, by rate period", "Average miss, bps")
    p.column_pair("periods_chart", RIGHT_X + 8, low_y + 36, RIGHT_W - 16, half - 42, ("Accuracy By Period", "Period"),
                  ["ETS MAE by Period", "Naive MAE by Period"], [C["teal"], C["slate"]])
    footer(p, "Source: Bank of Canada series V122667786 and BD.CDN.5YR.DQ.YLD (5-year Government of Canada bond yield).")
    pages.append(p)

    # ---- 3. Payment impact for one borrower --------------------------------------------------
    strip_y, strip_h = TILE_Y, 76
    kpi_y = strip_y + strip_h + 12
    main_y = kpi_y + TILE_H + 12
    bg = [("panel", X0, strip_y, CONTENT_W, strip_h)]
    tb, tw = tiles(4, y=kpi_y)
    bg += tb + [("panel", X0, main_y, LEFT_W, PANEL_BOTTOM - main_y), ("panel", RIGHT_X, main_y, RIGHT_W, PANEL_BOTTOM - main_y)]
    p = Page("payment", "Payment impact", bg)
    rail(p)
    header(p, headline_text="What renewal costs one borrower",
           subtitle_text="Fixed-rate renewal, Canadian semi-annual compounding, principal and interest only")
    # Power BI won't shrink a tile below about 66 px, so the three number pickers are dropdowns and the
    # renewal rate and threshold, the two what-if choices, stay as tiles with room for every option.
    x = X0 + 12
    for vname, w, table, col, default, head, dropdown in [
        ("s_balance", 143, "Balance Choice", "Balance label", "$400k", "Balance at renewal", True),
        ("s_years", 143, "Years Choice", "Years label", "20 yrs", "Years left to pay", True),
        ("s_before", 143, "Rate Before Choice", "Rate before label", "2.2%", "Rate before renewal", True),
        ("s_renewal", 362, "Renewal Rate Choice", "Renewal rate option", "Latest", "Renewal rate", False),
        ("s_threshold", 216, "Threshold", "Threshold label", "$250", "Payment rise to watch", False),
    ]:
        p.slicer(vname, x, strip_y + 6, w, 64, table, col, default, header=head, item_size=9, dropdown=dropdown)
        x += w + 8
    p.kpi("kpi_before", X0, kpi_y, tw, "Payment before renewal", "Payment Before", "Payment Before Caption")
    p.kpi("kpi_after", X0 + tw, kpi_y, tw, "Payment after renewal", "Payment After", "Payment After Caption")
    p.kpi("kpi_change", X0 + 2 * tw, kpi_y, tw, "Change each month", "Monthly Change", "Monthly Change Caption", C["amber"])
    p.kpi("kpi_threshold", X0 + 3 * tw, kpi_y, tw, "Rate where the rise hits the threshold", "Threshold Rate", "Threshold Rate Caption", C["teal"])
    main_h = PANEL_BOTTOM - main_y
    panel_title(p, "curve", X0, main_y, LEFT_W, "Change in monthly payment by renewal rate", "Dot: chosen rate. Dashed: threshold")
    p.line_chart("curve_chart", X0 + 8, main_y + 36, LEFT_W - 16, main_h - 44, ("Rate Grid", "Rate"),
                 ["Change on Curve", "Threshold Line", "Selected Rate Point"],
                 {"Change on Curve": {"colour": C["blue"], "width": 2.5},
                  "Threshold Line": {"colour": C["amber"], "width": 1.5, "dash": "dashed"},
                  "Selected Rate Point": {"colour": C["amber"], "width": 0, "marker": True}}, legend=False)
    panel_title(p, "scen", RIGHT_X, main_y, RIGHT_W, "Same borrower, rate 50 bps lower or higher")
    p.bar_chart("scen_bars", RIGHT_X + 8, main_y + 40, RIGHT_W - 16, main_h - 80, ("Scenario Axis", "Scenario"), "Change at Scenario",
                colour_measure="Scenario Change Colour", horizontal=False)
    p.label("scen_note", RIGHT_X + 12, PANEL_BOTTOM - 40, RIGHT_W - 24, 30, [("Amber: rise above the threshold", 9, C["ink3"], FONT)])
    footer(p, "Payments use the Canadian semi-annual compounding formula, the same one as the Python and SQL code.")
    pages.append(p)

    # ---- 4. Renewal triage ---------------------------------------------------------------------
    bg, tw = tiles(4)
    seg_h = 236
    rule_y = PANEL_Y + seg_h + 12
    bg += [("panel", X0, PANEL_Y, LEFT_W, full_h), ("panel", RIGHT_X, PANEL_Y, RIGHT_W, seg_h),
           ("panel", RIGHT_X, rule_y, RIGHT_W, PANEL_BOTTOM - rule_y)]
    p = Page("triage", "Renewal triage", bg)
    rail(p)
    header(p, headline_text="Who the renewal team should call first",
           subtitle_text="Each week: borrowers whose last chance it is, then by segment, renewal date and size of rise")
    p.kpi("kpi_missed", X0, TILE_Y, tw, "Missed with triage", "Missed with Triage", "Missed with Triage Caption", C["amber"])
    p.kpi("kpi_date", X0 + tw, TILE_Y, tw, "Missed in date order", "Missed in Date Order", "Missed in Date Order Caption")
    p.kpi("kpi_extra", X0 + 2 * tw, TILE_Y, tw, "Extra borrowers reached", "Extra Borrowers Reached", "Extra Reached Caption", C["teal"])
    p.kpi("kpi_certain", X0 + 3 * tw, TILE_Y, tw, "Near-certain cases missed", "Near-certain Missed", "Near-certain Caption")
    panel_title(p, "capacity", X0, PANEL_Y, LEFT_W, "Borrowers over +$250 not reached, by weekly capacity")
    p.slicer("capacity_slicer", X0 + 12, PANEL_Y + 40, 300, 52, "Capacity Choice", "Calls per week label", "300", header="Calls per week")
    p.label("capacity_note", X0 + 330, PANEL_Y + 58, LEFT_W - 342, 26,
            [("339 renew in week one, before any call", 9, C["ink3"], FONT)], align="right")
    p.column_pair("capacity_chart", X0 + 8, PANEL_Y + 100, LEFT_W - 16, full_h - 108, ("Call Capacity", "Capacity"),
                  ["Missed by Capacity, Date Order", "Missed by Capacity, Triage"], [C["slate"], C["teal"]])
    panel_title(p, "segments", RIGHT_X, PANEL_Y, RIGHT_W, "Segments", "Called at 300 a week")
    p.table("segments_table", RIGHT_X + 10, PANEL_Y + 40, RIGHT_W - 20, seg_h - 48,
            [("Segments", "Segment"), ("Segments", "Loans"), ("Segments", "Median rise"), ("Segments", "Called at 300 a week")],
            sort=("Segments", "Segment"))
    panel_title(p, "rule", RIGHT_X, rule_y, RIGHT_W, "How the call list works")
    p.label("rule_text", RIGHT_X + 10, rule_y + 34, RIGHT_W - 20, PANEL_BOTTOM - rule_y - 38, [
        ("A borrower can be called from 60 days to 7 days before renewal. Each week the team first calls the people for whom "
         "this week is the last chance, then fills spare slots with the most certain cases.\n\n"
         "Ranking by segment alone did worse than calling by date, because certain cases renewing in two months took the slots.\n\n"
         "A rise over $250 is not the same as unaffordable. That needs income and other debts.", 9, C["ink2"], FONT)])
    footer(p, "Loans are simulated (5,000 renewing in the next 90 days, fixed seed).")
    pages.append(p)

    # ---- 5. About this project -----------------------------------------------------------------
    p = Page("about", "About", [])
    rail(p)
    header(p, headline_text="About this project",
           subtitle_text="What this report answers, where the data comes from and how it was built")
    footer(p, "Built from code: Python, PostgreSQL and a generated Power BI project.")
    pages.append(p)
    return pages


# =============================================================================================
# Theme and background images
# =============================================================================================
def theme() -> dict:
    ink, ink2, ink3 = C["ink"], C["ink2"], C["ink3"]
    solid = lambda h: {"solid": {"color": h}}
    return {
        "name": "Mortgage Finance Light",
        "dataColors": [C["blue"], C["teal"], C["amber"], C["purple"], C["slate"], C["coral"], "#6F93BF", "#8CCFC3"],
        "foreground": ink, "foregroundNeutralSecondary": ink2, "foregroundNeutralTertiary": ink3,
        "background": C["panel"], "backgroundLight": C["canvas"], "backgroundNeutral": C["line"],
        "tableAccent": C["teal"], "good": C["teal"], "neutral": C["amber"], "bad": C["coral"],
        "maximum": C["amber"], "center": C["slate"], "minimum": C["panel"],
        "textClasses": {
            "callout": {"fontSize": 26, "fontFace": FONT_NUM, "color": ink},
            "title": {"fontSize": 12, "fontFace": FONT_SEMI, "color": ink},
            "header": {"fontSize": 12, "fontFace": FONT_SEMI, "color": ink},
            "label": {"fontSize": 10, "fontFace": FONT, "color": ink2},
        },
        "visualStyles": {
            "*": {"*": {
                "background": [{"show": False}],
                "border": [{"show": False}],
                "dropShadow": [{"show": False}],
                "title": [{"fontColor": solid(ink), "fontSize": 12, "fontFamily": FONT_SEMI}],
                "categoryAxis": [{"labelColor": solid(ink3), "gridlineShow": False, "showAxisTitle": False}],
                "valueAxis": [{"labelColor": solid(ink3), "gridlineColor": solid(C["grid"]), "showAxisTitle": False}],
                "legend": [{"labelColor": solid(ink2)}],
                "outspacePane": [{"backgroundColor": solid(C["panel"]), "foregroundColor": solid(ink), "transparency": 0,
                                  "borderColor": solid(C["line"])}],
                "filterCard": [{"$id": "Applied", "backgroundColor": solid("#EAF0F7"), "foregroundColor": solid(ink), "transparency": 0},
                               {"$id": "Available", "backgroundColor": solid(C["panel"]), "foregroundColor": solid(ink), "transparency": 0}],
            }},
            "page": {"*": {"background": [{"color": solid(C["canvas"]), "transparency": 0}],
                           "outspace": [{"color": solid(C["canvas"]), "transparency": 0}]}},
            "card": {"*": {"labels": [{"color": solid(ink), "fontSize": 26, "fontFamily": FONT_NUM}],
                           "categoryLabels": [{"show": False}]}},
            "slicer": {"*": {"items": [{"fontColor": solid(ink), "background": solid(C["panel"])}],
                             "header": [{"fontColor": solid(ink2)}]}},
            "tableEx": {"*": {"columnHeaders": [{"fontColor": solid(ink2), "backColor": solid(C["panel"])}],
                              "values": [{"fontColorPrimary": solid(ink), "backColorPrimary": solid(C["panel"]),
                                          "fontColorSecondary": solid(ink), "backColorSecondary": solid(C["panel"])}],
                              "grid": [{"gridVertical": False, "gridHorizontalColor": solid(C["line"])}]}},
        },
    }


def draw_background(page: Page, path: Path) -> None:
    """2x PNG with the rail, the headline rule, panels and tiles. Visuals sit on top with transparent
    backgrounds."""
    s = 2
    fig = plt.figure(figsize=(PAGE_W * s / 100, PAGE_H * s / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, PAGE_W)
    ax.set_ylim(PAGE_H, 0)
    ax.axis("off")
    fig.patch.set_facecolor(C["canvas"])
    ax.add_patch(Rectangle((0, 0), RAIL_W, PAGE_H, color=C["rail"], lw=0))       # navigation rail
    ax.add_patch(Rectangle((X0, 18), 40, 4, color=C["blue"], lw=0))               # rule above the headline
    for kind, x, y, w, h in page.bg_panels:
        if kind in ("panel", "tilebar"):
            ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=8",
                                        facecolor=C["panel"], edgecolor=C["line"], linewidth=1.0))
        elif kind == "tile_hi":
            ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=8",
                                        facecolor=C["tile_hi"], edgecolor=C["line"], linewidth=1.0))
            ax.add_patch(Rectangle((x + 1, y), w - 2, 3, color=C["amber"], lw=0))
        elif kind == "divider":
            ax.add_patch(Rectangle((x, y), w, h, color=C["line"], lw=0))
    fig.savefig(path, facecolor=C["canvas"])
    plt.close(fig)


# =============================================================================================
# Write the report
# =============================================================================================
def write_report(pages: list[Page]) -> None:
    for sub in ("definition", "StaticResources/RegisteredResources"):
        if (RP / sub).exists():
            shutil.rmtree(RP / sub)
    base = RP / "StaticResources/SharedResources/BaseThemes/CY24SU10.json"
    assert base.exists(), "the Power BI base theme file is missing"
    write_json(RP / "definition.pbir", {"$schema": f"{SCHEMA}/item/report/definitionProperties/2.0.0/schema.json",
                                        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    d = RP / "definition"
    write_json(d / "version.json", {"$schema": f"{SCHEMA}/item/report/definition/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    images = [{"name": f"bg_{p.name}.png", "path": f"bg_{p.name}.png", "type": "Image"} for p in pages]
    write_json(d / "report.json", {
        "$schema": f"{SCHEMA}/item/report/definition/report/1.3.0/schema.json",
        "layoutOptimization": "None",
        "themeCollection": {
            "baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.61", "type": "SharedResources"},
            "customTheme": {"name": "MortgageNavy.json", "reportVersionAtImport": "5.61", "type": "RegisteredResources"},
        },
        "objects": {"outspacePane": [{"properties": {"expanded": boolean(False), "visible": boolean(True)}}]},
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": "MortgageNavy.json", "path": "MortgageNavy.json", "type": "CustomTheme"}] + images},
        ],
        "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarized",
                     "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True, "useEnhancedTooltips": True},
    })
    write_json(d / "pages" / "pages.json", {"$schema": f"{SCHEMA}/item/report/definition/pagesMetadata/1.0.0/schema.json",
                                            "pageOrder": [p.name for p in pages], "activePageName": pages[0].name})
    res = RP / "StaticResources" / "RegisteredResources"
    res.mkdir(parents=True, exist_ok=True)
    write_json(res / "MortgageNavy.json", theme())
    for p in pages:
        write_json(d / "pages" / p.name / "page.json", p.json())
        for v in p.visuals:
            write_json(d / "pages" / p.name / "visuals" / v["name"] / "visual.json", v)
        draw_background(p, res / f"bg_{p.name}.png")


def main():
    tables = build_tables()
    write_model(tables)
    pages = build_pages()
    write_report(pages)
    write_json(HERE / f"{NAME}.pbip", {"$schema": f"{SCHEMA}/pbip/pbipProperties/1.0.0/schema.json", "version": "1.0",
                                       "artifacts": [{"report": {"path": f"{NAME}.Report"}}], "settings": {"enableAutoRecovery": True}})
    n_measures = sum(len(t.measures) for t in tables)
    print(f"Wrote {len(tables)} tables, {n_measures} measures, {len(pages)} pages, "
          f"{sum(len(p.visuals) for p in pages)} visuals to {HERE.relative_to(HERE.parent)}/")


if __name__ == "__main__":
    main()
