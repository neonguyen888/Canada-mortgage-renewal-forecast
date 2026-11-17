"""Static charts for the written report (reports/figures/*.png).

Author: Neo Nguyen

Everything is read back from the PostgreSQL views, so the charts show exactly the numbers the
SQL checks and the Power BI CSVs use. Colours follow the dashboard: blue for actual data
and headers, teal for forecasts, amber for anything above the payment threshold, grey for the
comparison model, purple for the bond-yield regression. Forecasts are always dashed so they can
be told apart without colour.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")                      # write files only, no window
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter, MultipleLocator

from config import PROFILE_BALANCE, PROFILE_OLD_RATE, PROFILE_YEARS_REMAINING, ROOT
from db import connect, query
from mortgage import monthly_payment, payment_change, threshold_rate

FIG_DIR = ROOT / "reports" / "figures"
# Series colours passed the dataviz palette check (lightness, chroma, colour-blind separation,
# 3:1 contrast on white). Grey is only for the comparison series, so it stays muted on purpose.
NAVY, TEAL, AMBER, PURPLE = "#2F5F96", "#16917F", "#C26B12", "#6E52A3"
GREY, INK, MUTED, RULE = "#A7B0BA", "#1B1F24", "#5B6670", "#D5DAE0"
MODEL_COLOURS = {"ETS damped trend": TEAL, "Naive (last observed)": GREY}
YIELD_SOURCE = ("Source: Bank of Canada, V122667786 (mortgage rate) and BD.CDN.5YR.DQ.YLD (GoC 5-year yield, "
                "monthly average). Chart: Neo Nguyen.")
SOURCE = "Source: Bank of Canada, series V122667786 (uninsured 5y+ fixed, funds advanced). Chart: Neo Nguyen."

# House style: a headline that states the finding, a grey subtitle with the units, horizontal
# gridlines only, no y-axis line, and a source note at the bottom.
plt.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 150, "font.family": ["Arial", "Helvetica", "DejaVu Sans"], "font.size": 10,
    "axes.titlesize": 10.5, "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlepad": 8,
    "axes.edgecolor": RULE, "axes.labelcolor": MUTED, "axes.labelsize": 9.5, "xtick.color": MUTED, "ytick.color": MUTED,
    "xtick.major.size": 3, "xtick.major.width": 0.8, "xtick.major.pad": 4, "ytick.major.size": 0, "ytick.major.pad": 6,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": "#ECEEF1", "grid.linewidth": 0.8, "axes.axisbelow": True,
    "legend.frameon": False, "legend.fontsize": 9, "text.parse_math": False, "text.color": INK,
})
BAR = dict(edgecolor="white", linewidth=1.2)    # thin white gap between neighbouring bars
pct = FuncFormatter(lambda v, _: f"{v:.1f}%")
cad = FuncFormatter(lambda v, _: f"-${-v:,.0f}" if v < 0 else f"${v:,.0f}")


def save(fig, name: str, title: str, subtitle: str, note: str = SOURCE):
    h = fig.get_figheight()
    fig.add_artist(plt.Rectangle((0.012, 1 - 0.09 / h), 0.045, 0.045 / h, transform=fig.transFigure, color=NAVY, lw=0))
    fig.text(0.012, 1 - 0.17 / h, title, fontsize=13.5, fontweight="bold", color=INK, va="top")
    fig.text(0.012, 1 - 0.47 / h, subtitle, fontsize=9.5, color=MUTED, va="top")
    fig.text(0.012, 0.012, note, fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.035, 1, 1 - 0.78 / h))
    fig.savefig(FIG_DIR / name, facecolor="white")
    plt.close(fig)
    print("  wrote", name)


# ---------------------------------------------------------------------------------------------
# Rate outlook
# ---------------------------------------------------------------------------------------------
def chart_rate_outlook(con):
    a = query(con, "SELECT month, rate_pct FROM fact_rate_actual ORDER BY month")
    f = query(con, "SELECT month, rate_pct, lower_80_pct, upper_80_pct FROM vw_rate_outlook_band WHERE series='Forecast' ORDER BY month")
    f.loc[f.lower_80_pct.isna(), ["lower_80_pct", "upper_80_pct"]] = f.loc[f.lower_80_pct.isna(), "rate_pct"]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(a.month, a.rate_pct, color=NAVY, lw=2, label="Actual")
    ax.fill_between(f.month, f.lower_80_pct, f.upper_80_pct, color=TEAL, alpha=0.18, lw=0, label="80% band (from backtest errors)")
    ax.plot(f.month, f.rate_pct, color=TEAL, lw=2, ls="--", label="ETS forecast")
    last = a.iloc[-1]
    ax.annotate(f"{last.rate_pct:.2f}%\n{last.month:%b %Y}", (last.month, last.rate_pct), xytext=(-60, 25),
                textcoords="offset points", fontsize=9, color=NAVY, arrowprops=dict(arrowstyle="-", color=NAVY, lw=0.8))
    end = f.iloc[-1]
    ax.annotate(f"{end.rate_pct:.2f}%\n{end.month:%b %Y}", (end.month, end.rate_pct), xytext=(8, -4),
                textcoords="offset points", fontsize=9, color=TEAL)
    ax.yaxis.set_major_formatter(pct)
    ax.set_xlim(a.month.min(), end.month + pd.DateOffset(months=8))
    ax.legend(loc="upper left")
    save(fig, "01_rate_history_forecast.png", "Mortgage rates have turned up again after two years of easing",
         "Market reference rate, uninsured 5-year+ fixed, % a year, with the ETS forecast and its 80% band")


def chart_monthly_change(con):
    d = query(con, "SELECT month, change_bps FROM vw_rate_monthly WHERE change_bps IS NOT NULL ORDER BY month")
    fig, ax = plt.subplots(figsize=(10, 3.8))
    colours = np.where(d.change_bps >= 0, NAVY, GREY)
    ax.bar(d.month, d.change_bps, width=22, color=colours)
    ax.axhline(0, color=INK, lw=0.8)
    big = d.loc[[d.change_bps.idxmax(), d.change_bps.idxmin()]]
    for _, r in big.iterrows():
        ax.annotate(f"{r.change_bps:+.0f}", (r.month, r.change_bps), xytext=(0, 4 if r.change_bps > 0 else -12),
                    textcoords="offset points", ha="center", fontsize=8, color=INK)
    save(fig, "02_monthly_change_bps.png", "The biggest monthly jumps came during the 2022 rate hikes",
         "Change in the market reference rate from the month before, bps. Blue: up. Grey: down")


# ---------------------------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------------------------
def chart_backtest_paths(con):
    d = query(con, "SELECT target_month, model, horizon_months, forecast_rate_pct, actual_rate_pct FROM vw_backtest_detail ORDER BY target_month")
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    for ax, h in zip(axes, (3, 6)):
        x = d[d.horizon_months == h]
        act = x.drop_duplicates("target_month")
        ax.plot(act.target_month, act.actual_rate_pct, color=NAVY, lw=2, label="Actual")
        for m, c in MODEL_COLOURS.items():
            y = x[x.model == m]
            ax.plot(y.target_month, y.forecast_rate_pct, color=c, lw=1.4, ls="--", label=f"{m} forecast")
        ax.yaxis.set_major_formatter(pct)
        ax.set_title(f"Forecast made {h} months earlier")
    axes[0].legend(loc="upper left")
    save(fig, "03_backtest_forecast_vs_actual.png", "Both models were late to catch the 2022 rise",
         "Backtest forecasts against the actual market reference rate, % a year")


def shade_periods(ax, con):
    p = query(con, "SELECT period_name, start_month, end_month FROM dim_rate_period ORDER BY period_order")
    for i, r in p.iterrows():
        if i % 2:
            ax.axvspan(r.start_month, r.end_month + pd.DateOffset(months=1), color="#F2F4F6", zorder=0)
        ax.text(r.start_month + pd.DateOffset(months=1), 0.97, r.period_name, transform=ax.get_xaxis_transform(),
                fontsize=8, color=MUTED, va="top")


def chart_error_over_time(con):
    d = query(con, "SELECT target_month, model, abs(error_bps) AS abs_err FROM vw_backtest_detail WHERE horizon_months=6 ORDER BY target_month")
    fig, ax = plt.subplots(figsize=(10, 4.5))
    shade_periods(ax, con)
    for m, c in MODEL_COLOURS.items():
        y = d[d.model == m].set_index("target_month").abs_err
        ax.plot(y.index, y.values, color=c, lw=0.8, alpha=0.45)
        ax.plot(y.index, y.rolling(6, min_periods=3).mean(), color=c, lw=2, label=f"{m} (6-month rolling mean)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=2)
    save(fig, "04_error_over_time.png", "Forecast errors peaked during the 2022 to 2023 tightening",
         "Size of each 6-month-ahead miss by target month, bps. Thick lines: 6-month rolling mean")


def n_origins(con) -> int:
    return int(query(con, "SELECT count(DISTINCT origin_month) FROM fact_backtest").iloc[0, 0])


def chart_mae_summary(con):
    d = query(con, "SELECT model, horizon_months, mae_bps, mae_cad_per_month FROM vw_backtest_summary")
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, col, title, fmt in [(axes[0], "mae_bps", "Basis points", "{:.1f}"),
                                (axes[1], "mae_cad_per_month", "CAD a month on a $400k, 20-year loan", "${:.0f}")]:
        for i, (m, c) in enumerate(MODEL_COLOURS.items()):
            y = d[d.model == m].sort_values("horizon_months")
            xs = np.arange(len(y)) + (i - 0.5) * 0.32
            bars = ax.bar(xs, y[col], width=0.32, color=c, label=m, **BAR)
            for b, v in zip(bars, y[col]):
                ax.text(b.get_x() + b.get_width() / 2, v, fmt.format(v), ha="center", va="bottom", fontsize=8.5)
        ax.set_xticks([0, 1], ["3 months ahead", "6 months ahead"])
        ax.set_title(title)
    axes[0].legend(loc="upper left")
    save(fig, "05_mae_by_model.png", "ETS misses by less than the naive forecast at both horizons",
         f"Average size of a forecast miss (mean absolute error) over {n_origins(con)} rolling backtests")


def chart_mae_by_period(con):
    d = query(con, "SELECT period_order, period_name, model, mae_bps FROM vw_backtest_by_period WHERE horizon_months=6 ORDER BY period_order")
    periods = d.drop_duplicates("period_order").period_name.tolist()
    fig, ax = plt.subplots(figsize=(10, 4))
    for i, (m, c) in enumerate(MODEL_COLOURS.items()):
        y = d[d.model == m].set_index("period_name").loc[periods, "mae_bps"]
        xs = np.arange(len(periods)) + (i - 0.5) * 0.32
        bars = ax.bar(xs, y.values, width=0.32, color=c, label=m, **BAR)
        for b, v in zip(bars, y.values):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8.5)
    ax.set_xticks(range(len(periods)), periods)
    ax.legend(loc="upper left")
    save(fig, "06_mae_by_period.png", "Both models struggled most while rates were climbing",
         "Mean absolute error 6 months ahead, bps, by rate period")


# ---------------------------------------------------------------------------------------------
# Payment impact (illustrative loan)
# ---------------------------------------------------------------------------------------------
def chart_payment_curve(con):
    latest = query(con, "SELECT latest_rate_pct FROM vw_kpi").iloc[0, 0]
    rates = np.arange(1.0, 7.01, 0.01)
    change = [payment_change(PROFILE_BALANCE, PROFILE_YEARS_REMAINING, PROFILE_OLD_RATE, r) for r in rates]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(rates, change, color=NAVY, lw=2)
    for t, c in [(100, GREY), (250, AMBER), (500, GREY)]:
        r = threshold_rate(PROFILE_BALANCE, PROFILE_YEARS_REMAINING, PROFILE_OLD_RATE, t)
        ax.axhline(t, color=c, lw=1, ls=":")
        ax.plot([r], [t], "o", ms=8, color=c, mec="white", mew=2)
        ax.annotate(f"+${t}/mo at {r:.2f}%", (r, t), xytext=(-110, 8), textcoords="offset points", fontsize=9, color=INK)
    lc = payment_change(PROFILE_BALANCE, PROFILE_YEARS_REMAINING, PROFILE_OLD_RATE, latest)
    r250 = threshold_rate(PROFILE_BALANCE, PROFILE_YEARS_REMAINING, PROFILE_OLD_RATE, 250)
    ax.axvline(latest, color=TEAL, lw=1.2, ls="--")
    ax.annotate(f"Latest market rate {latest:.2f}%\n→ +${lc:,.0f}/mo", (latest, lc), xytext=(10, -40),
                textcoords="offset points", fontsize=9, color=TEAL)
    ax.axhline(0, color=INK, lw=0.8)
    ax.xaxis.set_major_formatter(pct)
    ax.yaxis.set_major_formatter(cad)
    ax.set_xlabel("Renewal rate")
    save(fig, "07_payment_curve_threshold.png",
         f"The payment rises $250 a month at {r250:.2f}%, and today's rate is {latest:.2f}%",
         f"Change in monthly payment at renewal, CAD: ${PROFILE_BALANCE:,.0f} left, {PROFILE_YEARS_REMAINING} years to go, "
         f"old rate {PROFILE_OLD_RATE}%",
         "Illustrative loan. Semi-annual compounding, monthly principal and interest. Chart: Neo Nguyen.")


def chart_sensitivity_heatmap():
    balances = np.arange(200_000, 800_001, 100_000)
    rates = np.arange(3.0, 6.01, 0.5)
    z = np.array([[payment_change(b, PROFILE_YEARS_REMAINING, PROFILE_OLD_RATE, r) for r in rates] for b in balances])
    fig, ax = plt.subplots(figsize=(10, 5))
    im = ax.imshow(z, cmap="Blues", aspect="auto", origin="lower")
    for i in range(len(balances)):
        for j in range(len(rates)):
            v = z[i, j]
            over = v > 250
            ax.text(j, i, f"${v:,.0f}", ha="center", va="center", fontsize=8.5,
                    color="white" if v > z.max() * 0.55 else INK, fontweight="bold" if over else "normal")
            if over:
                ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False, ec=AMBER, lw=1.6))
    ax.set_xticks(range(len(rates)), [f"{r:.1f}%" for r in rates])
    ax.set_yticks(range(len(balances)), [f"${b/1000:,.0f}k" for b in balances])
    ax.set_xlabel("Renewal rate")
    ax.set_ylabel("Balance at renewal")
    ax.grid(False)
    cb = fig.colorbar(im, ax=ax, format=cad, shrink=0.85)
    cb.outline.set_visible(False)
    save(fig, "08_sensitivity_heatmap.png", "The larger the balance, the lower the rate that triggers a $250 rise",
         f"Rise in monthly payment against an old rate of {PROFILE_OLD_RATE}%, {PROFILE_YEARS_REMAINING} years left, CAD. "
         "Amber outline: above $250", "Illustrative loans only. Chart: Neo Nguyen.")


def chart_scenarios(con):
    latest = query(con, "SELECT latest_rate_pct FROM vw_kpi").iloc[0, 0]
    before = monthly_payment(PROFILE_BALANCE, PROFILE_YEARS_REMAINING, PROFILE_OLD_RATE)
    labels, vals = ["Before renewal\n(2.20%)"], [before]
    for name, shift in [("Rate -50 bps", -0.5), ("Latest market rate", 0.0), ("Rate +50 bps", 0.5)]:
        r = latest + shift
        labels.append(f"{name}\n({r:.2f}%)")
        vals.append(monthly_payment(PROFILE_BALANCE, PROFILE_YEARS_REMAINING, r))
    colours = [GREY] + [AMBER if v - before > 250 else TEAL for v in vals[1:]]
    fig, ax = plt.subplots(figsize=(10, 4.2))
    bars = ax.bar(labels, vals, color=colours, width=0.5, **BAR)
    for b, v in zip(bars, vals):
        tag = "" if v == before else f"\n(+${v - before:,.0f})"
        ax.text(b.get_x() + b.get_width() / 2, v, f"${v:,.0f}{tag}", ha="center", va="bottom", fontsize=9)
    ax.yaxis.set_major_formatter(cad)
    ax.set_ylim(0, max(vals) * 1.2)
    save(fig, "09_rate_scenarios.png", f"Even a rate 50 bps lower leaves the payment ${vals[1] - before:,.0f} a month higher",
         f"Monthly payment, CAD: ${PROFILE_BALANCE:,.0f} left, {PROFILE_YEARS_REMAINING} years to go. Amber: rise above $250",
         "Illustrative loan. The ±50 bps bars are what-if rate moves; forecast uncertainty is the 80% band. Chart: Neo Nguyen.")


# ---------------------------------------------------------------------------------------------
# 90-day renewal portfolio
# ---------------------------------------------------------------------------------------------
def chart_portfolio_cohorts(con):
    d = query(con, """SELECT renewal_month, n_loans, n_above_threshold, pct_above_threshold
                  FROM vw_renewal_cohort WHERE threshold_cad=250 AND shift_bps=0 ORDER BY renewal_month""")
    fig, ax = plt.subplots(figsize=(10, 4.5))
    x = d.renewal_month.dt.strftime("%b %Y")
    ax.bar(x, d.n_above_threshold, color=AMBER, width=0.5, label="Payment up more than $250", **BAR)
    ax.bar(x, d.n_loans - d.n_above_threshold, bottom=d.n_above_threshold, color=GREY, width=0.5, label="$250 or less", **BAR)
    for i, r in d.iterrows():
        ax.text(i, r.n_loans, f"{r.n_above_threshold:,} of {r.n_loans:,}\n({r.pct_above_threshold:.1f}%)", ha="center", va="bottom", fontsize=9)
    ax.set_ylim(0, d.n_loans.max() * 1.25)
    ax.legend(loc="upper right")
    share = 100 * d.n_above_threshold.sum() / d.n_loans.sum()
    save(fig, "10_portfolio_by_month.png", f"{share:.0f}% of renewing loans face a rise above $250 a month",
         "Loans renewing in each month of the 90-day window, at the forecast rate", "Synthetic portfolio of 5,000 loans renewing 15 Nov 2026 to 12 Feb 2027 (Nov and Feb are partial months). Chart: Neo Nguyen.")


def chart_portfolio_distribution(con):
    d = query(con, "SELECT scenario, shift_bps, payment_change FROM vw_portfolio_payment")
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.8), sharey=True)
    bins = np.arange(0, 1801, 50)
    for ax, (shift, name) in zip(axes, [(-50, "Rate -50 bps"), (0, "Forecast rate"), (50, "Rate +50 bps")]):
        v = d[d.shift_bps == shift].payment_change
        ax.hist(v[v <= 250], bins=bins, color=GREY, **BAR)
        ax.hist(v[v > 250], bins=bins, color=AMBER, **BAR)
        ax.axvline(250, color=INK, lw=0.8, ls=":")
        ax.set_title(f"{name}: {100 * (v > 250).mean():.0f}% above $250")
        ax.xaxis.set_major_locator(MultipleLocator(500))
        ax.xaxis.set_major_formatter(cad)
        ax.set_xlabel("Monthly payment change")
    save(fig, "11_portfolio_distribution.png", "A 50 bps move shifts the whole book, but most loans stay above $250",
         "Number of loans by change in monthly payment, under three renewal-rate scenarios. Amber: above $250",
         "Synthetic portfolio, 5,000 loans. Chart: Neo Nguyen.")


def chart_balance_bands(con):
    d = query(con, "SELECT balance_band, shift_bps, scenario, pct_above_250 FROM vw_balance_band_cohort ORDER BY balance_band, shift_bps")
    bands = d.balance_band.unique().tolist()
    fig, ax = plt.subplots(figsize=(10, 4))
    styles = {-50: (GREY, "Rate -50 bps"), 0: (NAVY, "Forecast rate"), 50: (AMBER, "Rate +50 bps")}
    for i, (shift, (c, name)) in enumerate(styles.items()):
        y = d[d.shift_bps == shift].set_index("balance_band").loc[bands, "pct_above_250"]
        xs = np.arange(len(bands)) + (i - 1) * 0.26
        bars = ax.bar(xs, y.values, width=0.26, color=c, label=name, **BAR)
        for b, v in zip(bars, y.values):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.0f}%", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(range(len(bands)), [b[3:] for b in bands])
    ax.set_ylim(0, 125)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%" if v <= 100 else ""))
    ax.legend(loc="upper left", ncol=3)
    save(fig, "12_portfolio_balance_bands.png", "Larger balances cross the $250 threshold first",
         "Share of loans whose payment rises more than $250 a month, by balance at renewal",
         "Synthetic portfolio, 5,000 loans. Chart: Neo Nguyen.")


# ---------------------------------------------------------------------------------------------
# Bond-yield regression
# ---------------------------------------------------------------------------------------------
def chart_rate_vs_yield(con):
    d = query(con, "SELECT month, rate_pct, yield_pct, spread_bps FROM vw_rate_vs_yield ORDER BY month")
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
    ax.plot(d.month, d.rate_pct, color=NAVY, lw=2)
    ax.plot(d.month, d.yield_pct, color=MUTED, lw=1.6)
    a, y = d.dropna(subset=["rate_pct"]).iloc[-1], d.iloc[-1]
    ax.annotate(f"Mortgage rate {a.rate_pct:.2f}%\n({a.month:%b %Y})", (a.month, a.rate_pct), xytext=(8, 6),
                textcoords="offset points", fontsize=9, color=NAVY)
    ax.annotate(f"5-year bond yield {y.yield_pct:.2f}%\n({y.month:%b %Y})", (y.month, y.yield_pct), xytext=(8, -26),
                textcoords="offset points", fontsize=9, color=MUTED)
    ax.yaxis.set_major_formatter(pct)
    ax.set_xlim(d.month.min(), d.month.max() + pd.DateOffset(months=20))
    s = d.dropna(subset=["spread_bps"])
    ax2.fill_between(s.month, 0, s.spread_bps, color=GREY, alpha=0.5, lw=0)
    ax2.plot(s.month, s.spread_bps, color=MUTED, lw=1)
    ax2.set_title("Spread: mortgage rate minus bond yield, bps")
    ax2.xaxis.set_major_locator(mdates.YearLocator(2))
    ax2.set_xticks([t for t in ax2.get_xticks() if d.month.min().year <= mdates.num2date(t).year <= d.month.max().year])
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    save(fig, "13_rate_vs_bond_yield.png", "The mortgage rate follows the 5-year bond yield, a few months behind",
         "Market reference rate and Government of Canada 5-year benchmark yield, % a year", YIELD_SOURCE)


def chart_lag_coefficients(con):
    c = query(con, "SELECT lag_months, coef, ci95_low, ci95_high, cumulative_pass_through FROM fact_regression_coef "
               "WHERE lag_months IS NOT NULL ORDER BY lag_months")
    fig, ax = plt.subplots(figsize=(10, 4.5))
    x = c.lag_months.astype(int)
    err = [c.coef - c.ci95_low, c.ci95_high - c.coef]
    ax.bar(x, c.coef, width=0.45, color=PURPLE, label="Share passed through in that month", **BAR)
    ax.errorbar(x, c.coef, yerr=err, fmt="none", ecolor=INK, elinewidth=1, capsize=4, label="95% confidence interval")
    ax.plot(x, c.cumulative_pass_through, color=NAVY, lw=2, marker="o", ms=7, label="Cumulative pass-through")
    for xi, v in zip(x, c.cumulative_pass_through):
        # Labels sit up and to the left so they clear the error bars.
        ax.annotate(f"{v:.2f}", (xi, v), xytext=(-10, 8), textcoords="offset points", ha="right", fontsize=9, color=NAVY)
    ax.set_xticks(x, ["Same month" if k == 0 else f"{k} month{'s' if k > 1 else ''} later" for k in x])
    ax.set_ylim(0, 1.1)
    ax.legend(loc="upper left")
    total = c.cumulative_pass_through.iloc[-1]
    save(fig, "14_regression_lag_coefficients.png",
         f"{total:.2f} of a bond-yield move reaches the mortgage rate within {x.max()} months",
         "Change in the mortgage rate per 1-point change in the 5-year yield, by months after the move", YIELD_SOURCE)


def chart_model_comparison(con):
    d = query(con, "SELECT model, horizon_months, mae_bps FROM vw_model_comparison ORDER BY horizon_months")
    style = {
        "Naive (last observed)": dict(color=GREY, ls="-", marker="s"),
        "ETS damped trend": dict(color=TEAL, ls="-", marker="o"),
        "Regression, yields to origin only": dict(color=PURPLE, ls=":", marker="^", alpha=0.75),
        "Bond-yield regression": dict(color=PURPLE, ls="-", marker="D", lw=2.4),
    }
    fig, ax = plt.subplots(figsize=(10, 5))
    for m, kw in style.items():
        y = d[d.model == m]
        ax.plot(y.horizon_months, y.mae_bps, label=m, ms=6, **{"lw": 1.8, **kw})
        last = y.iloc[-1]
        ax.annotate(f"{last.mae_bps:.0f} bps", (last.horizon_months, last.mae_bps), xytext=(8, -3),
                    textcoords="offset points", fontsize=9, color=INK)
    ax.set_xticks(range(1, 7), [f"{h} mo" for h in range(1, 7)])
    ax.set_xlim(0.7, 6.6)
    ax.set_xlabel("Months after the last published month")
    ax.legend(loc="upper left")
    save(fig, "15_model_comparison_mae.png", "Adding the bond yield cuts forecast error at every horizon",
         f"Mean absolute error over {n_origins(con)} rolling backtests, bps", YIELD_SOURCE)


def chart_forecast_comparison(con):
    a = query(con, "SELECT month, rate_pct FROM fact_rate_actual WHERE month >= DATE '2024-01-01' ORDER BY month")
    band = query(con, "SELECT target_month, forecast_rate_pct, lower_80_pct, upper_80_pct FROM fact_forecast_interval ORDER BY target_month")
    r = query(con, "SELECT target_month, regression_rate_pct, ets_rate_pct, yield_known FROM vw_forecast_comparison ORDER BY target_month")
    last = a.iloc[-1]
    start = pd.DataFrame({"target_month": [last.month], "regression_rate_pct": [last.rate_pct], "ets_rate_pct": [last.rate_pct]})
    rr = pd.concat([start, r], ignore_index=True)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(a.month, a.rate_pct, color=NAVY, lw=2, label="Actual")
    ax.fill_between(band.target_month, band.lower_80_pct, band.upper_80_pct, color=TEAL, alpha=0.15, lw=0,
                    label="ETS 80% band")
    ax.plot(rr.target_month, rr.ets_rate_pct, color=TEAL, lw=2, ls="--", label="ETS forecast (selected model)")
    ax.plot(rr.target_month, rr.regression_rate_pct, color=PURPLE, lw=2, ls="--", label="Bond-yield regression forecast")
    known = r[r.yield_known]
    ax.scatter(known.target_month, known.regression_rate_pct, color=PURPLE, s=45, zorder=3,
               label="Yield for that month already published")
    for _, row in r[r.target_month.isin([r.target_month.iloc[2], r.target_month.iloc[-1]])].iterrows():
        ax.annotate(f"{row.regression_rate_pct:.2f}%", (row.target_month, row.regression_rate_pct), xytext=(-6, 10), ha="right",
                    textcoords="offset points", fontsize=9, color=PURPLE)
        ax.annotate(f"{row.ets_rate_pct:.2f}%", (row.target_month, row.ets_rate_pct), xytext=(6, -14),
                    textcoords="offset points", fontsize=9, color=TEAL)
    ax.yaxis.set_major_formatter(pct)
    ax.set_xlim(a.month.min(), r.target_month.max() + pd.DateOffset(months=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.legend(loc="lower left")
    save(fig, "16_forecast_comparison.png", "The bond-yield model expects a steeper rise than ETS",
         "Market reference rate and the two forecasts, % a year. Filled dots: the bond yield for that month is already published",
         YIELD_SOURCE)


# ---------------------------------------------------------------------------------------------
# Renewal triage
# ---------------------------------------------------------------------------------------------
def chart_triage_capacity(con):
    d = query(con, "SELECT * FROM fact_triage_capacity ORDER BY capacity_per_week")
    fig, ax = plt.subplots(figsize=(10, 4.6))
    x = np.arange(len(d))
    for off, col, colour, label in [(-0.19, "over_missed_if_by_date_only", GREY, "Call in renewal-date order"),
                                    (0.19, "over_missed", NAVY, "Triage: last-chance loans first, then by segment")]:
        bars = ax.bar(x + off, d[col], width=0.36, color=colour, label=label, **BAR)
        for b, v in zip(bars, d[col]):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:,}", ha="center", va="bottom", fontsize=8.5)
    floor = int(d.over_too_soon_to_reach.iloc[0])
    ax.axhline(floor, color=AMBER, lw=1.4, ls=":", label=f"{floor} renew within 7 days, too soon for any rule")
    ax.set_xticks(x, [f"{c} calls a week" for c in d.capacity_per_week])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.legend(loc="upper right")
    r300 = d.set_index("capacity_per_week").loc[300]
    save(fig, "17_triage_capacity.png",
         f"At 300 calls a week, triage reaches {int(r300.over_missed_if_by_date_only - r300.over_missed):,} more borrowers facing a big rise",
         "Borrowers whose payment rises more than $250 a month and who never got a call, by weekly call capacity", "Synthetic book of 5,000 loans renewing 15 Nov 2026 to 12 Feb 2027, "
                                         "ETS forecast rate. Chart: Neo Nguyen.")


def main():
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    con = connect()
    chart_rate_outlook(con)
    chart_monthly_change(con)
    chart_backtest_paths(con)
    chart_error_over_time(con)
    chart_mae_summary(con)
    chart_mae_by_period(con)
    chart_payment_curve(con)
    chart_sensitivity_heatmap()
    chart_scenarios(con)
    chart_portfolio_cohorts(con)
    chart_portfolio_distribution(con)
    chart_balance_bands(con)
    chart_rate_vs_yield(con)
    chart_lag_coefficients(con)
    chart_model_comparison(con)
    chart_forecast_comparison(con)
    chart_triage_capacity(con)
    con.close()


if __name__ == "__main__":
    main()
