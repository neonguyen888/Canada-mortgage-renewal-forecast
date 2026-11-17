"""Renewal triage: which borrowers should the team contact first?

Author: Neo Nguyen

An extension of the portfolio analysis. The renewal team can only reach so many borrowers a
week, so the question is the order. Three steps:

1. Segment. Each loan is priced at three renewal rates (forecast -50 bps, forecast, +50 bps).
   A loan that goes over the $250 threshold in all three is a near-certain case; one that only
   goes over if rates rise is a maybe. Since a higher rate always means a higher payment, there
   are only four possible segments.
2. Rank. Most certain segment first, then the earliest renewal date, then the biggest increase.
3. Schedule. A loan can be contacted from 60 days before renewal until 7 days before (time to
   act on the call). Each week the team first takes the loans for which this week is the last
   chance, in rank order, then fills any spare slots with the next loans in rank. A loan whose
   notice period runs out before its turn counts as missed.

   Ranking alone is not enough. When near-certain loans renewing in two months take this week's
   slots, likely loans renewing next week run out of time, and the team misses more borrowers
   than it would by simply working through renewal dates.

$250 measures how much the payment rises. Whether a borrower can afford it needs income and
other debts, which this synthetic book doesn't have.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import PROCESSED_DIR
from mortgage import monthly_payment
from portfolio import AS_OF

THRESHOLD = 250.0
SHIFTS_BPS = (-50, 0, 50)
CAPACITIES = (100, 200, 300, 400)
DEFAULT_CAPACITY = 300
LOOKAHEAD_DAYS = 60
MIN_NOTICE_DAYS = 7
START = AS_OF + pd.Timedelta(days=1)          # first renewal in the window

SEGMENTS = {
    1: "Over in every scenario",
    2: "Over unless rates fall",
    3: "Over only if rates rise",
    4: "Under in every scenario",
}


def segment_of(over_low: bool, over_base: bool, over_high: bool) -> tuple[int, str]:
    """Segment from the three scenario results (rate -50 bps, forecast, +50 bps)."""
    order = 1 if over_low else 2 if over_base else 3 if over_high else 4
    return order, SEGMENTS[order]


def rank_loans(df: pd.DataFrame) -> pd.DataFrame:
    """Add priority_rank (1 = contact first). loan_id breaks any remaining tie so the order is fixed."""
    out = df.sort_values(["segment_order", "renewal_date", "payment_change_base", "loan_id"],
                         ascending=[True, True, False, True]).copy()
    out["priority_rank"] = np.arange(1, len(out) + 1)
    return out


def schedule(ranked: pd.DataFrame, capacity: int, start: pd.Timestamp = START,
             lookahead_days: int = LOOKAHEAD_DAYS, min_notice_days: int = MIN_NOTICE_DAYS) -> pd.Series:
    """Contact week (1, 2, ...) for each loan_id, or NaN if its notice period ran out first."""
    r = ranked.sort_values("priority_rank")
    week = pd.Series(np.nan, index=r["loan_id"].values)
    renew = r["renewal_date"].values
    ids = r["loan_id"].values
    last_start = r["renewal_date"].max() - pd.Timedelta(days=min_notice_days)
    k, week_start = 1, start
    while week_start <= last_start:
        days = (renew - np.datetime64(week_start)) / np.timedelta64(1, "D")
        open_ = week.isna().values & (days >= min_notice_days) & (days <= lookahead_days)
        last_chance = open_ & (days < min_notice_days + 7)       # too late by next week
        # last-chance loans first, then the rest; each group is already in priority order
        picks = np.concatenate([ids[last_chance], ids[open_ & ~last_chance]])[:capacity]
        week.loc[picks] = k
        k, week_start = k + 1, week_start + pd.Timedelta(days=7)
    return week


def build_book() -> pd.DataFrame:
    loans = pd.read_csv(PROCESSED_DIR / "renewal_portfolio.csv", parse_dates=["renewal_date", "renewal_month"])
    names = {-50: "low", 0: "base", 50: "high"}
    for s in SHIFTS_BPS:
        loans[f"payment_change_{names[s]}"] = [
            monthly_payment(b, y, n + s / 100) - monthly_payment(b, y, o)
            for b, y, o, n in zip(loans.balance_at_renewal, loans.years_remaining,
                                  loans.original_rate_pct, loans.renewal_rate_pct)]
    seg = [segment_of(lo > THRESHOLD, ba > THRESHOLD, hi > THRESHOLD)
           for lo, ba, hi in zip(loans.payment_change_low, loans.payment_change_base, loans.payment_change_high)]
    loans["segment_order"] = [s[0] for s in seg]
    loans["segment"] = [s[1] for s in seg]
    loans["days_to_renewal"] = (loans.renewal_date - AS_OF).dt.days
    return rank_loans(loans)


def single_scenario_rank(book: pd.DataFrame, which: str) -> pd.DataFrame:
    """Ranking a team might build from one scenario only: over the threshold first, then date."""
    d = book.drop(columns="priority_rank").assign(
        segment_order=lambda x: np.where(x[f"payment_change_{which}"] > THRESHOLD, 1, 2),
        payment_change_base=lambda x: x[f"payment_change_{which}"])
    return rank_loans(d)


def capacity_table(book: pd.DataFrame) -> pd.DataFrame:
    b = book.set_index("loan_id")
    rise, seg = b["payment_change_base"], b["segment_order"]
    over = rise > THRESHOLD
    by_date = book.assign(segment_order=1)           # first come, first served: renewal date only
    rows = []
    for c in CAPACITIES:
        wk = schedule(book, c)
        wk_date = schedule(rank_loans(by_date.drop(columns="priority_rank")), c)
        hit, hit_date = wk.notna(), wk_date.notna()
        # How much would a list built from one scenario change if rates moved 50 bps?
        base_set = set(schedule(single_scenario_rank(book, "base"), c).dropna().index)
        moved = {w: len(base_set - set(schedule(single_scenario_rank(book, w), c).dropna().index))
                 for w in ("low", "high")}
        rows.append({
            "capacity_per_week": c,
            "n_contacted": int(hit.sum()),
            "n_over_threshold": int(over.sum()),
            "over_contacted": int((hit & over).sum()),
            "over_missed": int((~hit & over).sum()),
            # renew inside the notice period from day one: nobody can reach these, whatever the rule
            "over_too_soon_to_reach": int((over & (b["renewal_date"] - START
                                                   < pd.Timedelta(days=MIN_NOTICE_DAYS))).sum()),
            "segment1_missed": int((~hit & (seg == 1)).sum()),
            "segment2_missed": int((~hit & (seg == 2)).sum()),
            "missed_rise_per_month": round(float(rise[~hit & over].sum()), 0),
            "over_missed_if_by_date_only": int((~hit_date & over).sum()),
            "segment1_missed_if_by_date_only": int((~hit_date & (seg == 1)).sum()),
            "missed_rise_per_month_if_by_date_only": round(float(rise[~hit_date & over].sum()), 0),
            "single_scenario_list_changes_if_rates_fall": moved["low"],
            "single_scenario_list_changes_if_rates_rise": moved["high"],
        })
    return pd.DataFrame(rows)


def main():
    book = build_book()
    book["contact_week"] = book["loan_id"].map(schedule(book, DEFAULT_CAPACITY))
    cols = ["loan_id", "renewal_date", "renewal_month", "days_to_renewal", "balance_at_renewal",
            "payment_change_low", "payment_change_base", "payment_change_high",
            "segment_order", "segment", "priority_rank", "contact_week"]
    out = book[cols].copy()
    for c in ("payment_change_low", "payment_change_base", "payment_change_high"):
        out[c] = out[c].round(2)
    for c in ("renewal_date", "renewal_month"):
        out[c] = out[c].dt.strftime("%Y-%m-%d")
    out.to_csv(PROCESSED_DIR / "triage_loans.csv", index=False)
    cap = capacity_table(book)
    cap.to_csv(PROCESSED_DIR / "triage_capacity.csv", index=False)

    print(book.groupby(["segment_order", "segment"]).size().rename("loans").to_string(), "\n")
    print(cap.to_string(index=False))


if __name__ == "__main__":
    main()
