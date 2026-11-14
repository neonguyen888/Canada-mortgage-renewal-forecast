"""Tests for the renewal triage rules. Author: Neo Nguyen"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from triage import rank_loans, schedule, segment_of  # noqa: E402

START = pd.Timestamp("2026-11-15")


def book(rows):
    """rows: (loan_id, renewal date, segment_order, payment change at the forecast rate)"""
    return pd.DataFrame(rows, columns=["loan_id", "renewal_date", "segment_order", "payment_change_base"]) \
             .assign(renewal_date=lambda d: pd.to_datetime(d.renewal_date))


def test_segment_follows_the_three_scenarios():
    assert segment_of(True, True, True)[0] == 1
    assert segment_of(False, True, True)[0] == 2
    assert segment_of(False, False, True)[0] == 3
    assert segment_of(False, False, False)[0] == 4


def test_rank_puts_certain_loans_first_then_earliest_renewal_then_biggest_rise():
    r = rank_loans(book([(1, "2026-12-20", 2, 900), (2, "2026-12-01", 1, 300),
                         (3, "2026-12-20", 1, 500), (4, "2026-12-20", 1, 700)]))
    assert r.sort_values("priority_rank").loan_id.tolist() == [2, 4, 3, 1]


def test_short_notice_loan_is_missed():
    # renews 3 days after the first week starts, inside the 7-day notice period
    r = rank_loans(book([(1, "2026-11-18", 1, 500), (2, "2026-12-10", 1, 400)]))
    week = schedule(r, capacity=10, start=START)
    assert pd.isna(week[1]) and week[2] == 1


def test_large_capacity_misses_only_short_notice():
    r = rank_loans(book([(i, d, 1, 300) for i, d in enumerate(pd.date_range("2026-11-25", periods=60, freq="D"))]))
    week = schedule(r, capacity=1000, start=START)
    assert week.notna().all()


def test_lookahead_blocks_far_loans():
    # 75 days out: outside the 60-day lookahead in week 1, contacted once it comes into range
    r = rank_loans(book([(1, "2027-01-29", 1, 500)]))
    week = schedule(r, capacity=10, start=START, lookahead_days=60)
    assert week[1] > 1


def test_last_chance_loan_beats_a_more_certain_loan_that_can_wait():
    # loan 1: less certain, but week 1 is its last chance (renews in 10 days)
    # loan 2: more certain, renews in 40 days, can wait a week
    r = rank_loans(book([(1, "2026-11-25", 2, 300), (2, "2026-12-25", 1, 900)]))
    week = schedule(r, capacity=1, start=START)
    assert week[1] == 1 and week[2] == 2


def test_tight_capacity_misses_some_loans():
    r = rank_loans(book([(i, "2026-11-30", 1, 300) for i in range(5)]))
    week = schedule(r, capacity=2, start=START)
    # notice runs out after week 2 (starts 22 Nov, 8 days before renewal), so 4 contacted, 1 missed
    assert int(week.notna().sum()) == 4
