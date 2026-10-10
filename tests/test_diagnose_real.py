"""Tests for the real-account error breakdown. Made-up rows and synthetic personas only."""

import re
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from eval.diagnose_real import (
    HORIZON,
    PARTS,
    deposit_evidence,
    main,
    match_recurring,
    origin_record,
    part_of,
    recurring_split,
)
from lookahead.db import connect, connect_readonly
from lookahead.flows import Flow, Txn
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

ORIGINS = (date(2026, 3, 2), date(2026, 6, 1))
CUT_IN, CUT_OUT = 100000, 80000


def row(cents: int) -> Txn:
    return Txn(1, date(2026, 3, 10), cents, "x", "y")


# --- Which part a row belongs to ---


def test_a_row_goes_to_the_first_rule_that_fits() -> None:
    income = Flow("income", "credit memo")
    assert (
        part_of(row(5000), Flow("everyday", "card purchase", netted=True), True, 1, 1) == "netted"
    )
    assert part_of(row(-900), Flow("everyday", "bank fee"), False, CUT_IN, CUT_OUT) == "fees"
    assert part_of(row(-900), Flow("everyday", "fee reversal"), False, 1, 1) == "fees"
    assert (
        part_of(row(-6500), Flow("recurring_outflow", "card purchase"), True, 1, 1) == "recurring"
    )
    assert part_of(row(CUT_IN), income, False, CUT_IN, CUT_OUT) == "big_deposits"
    assert part_of(row(CUT_IN - 1), income, False, CUT_IN, CUT_OUT) == "irregular"
    assert part_of(row(900), Flow("refund", "card return"), False, 1, 1) == "everyday"
    assert part_of(row(900), Flow("refund", "something else"), False, 1, 1) == "other_credits"
    assert part_of(row(-2500), Flow("everyday", "card purchase"), False, 1, CUT_OUT) == "everyday"
    assert part_of(row(-CUT_OUT), Flow("transfer", "x"), False, 1, CUT_OUT) == "big_payments"
    assert part_of(row(-2500), Flow("recurring_outflow", "x"), False, 1, CUT_OUT) == "other_debits"
    assert part_of(row(-2500), Flow("recurring_outflow", "bill payment"), False, 1, CUT_OUT) == (
        "everyday"
    )  # a bill payment in no series counts as spending


# --- The recurring split ---


def test_matching_is_one_to_one_nearest_first_within_ten_days() -> None:
    placed = [(5, -90000, "rent"), (12, -4000, "phone"), (20, -1500, "gym")]
    actual = [(7, -95000, "rent"), (13, -4000, "phone"), (28, -3000, "other"), (29, -1500, "gym")]
    pairs, missed, extra = match_recurring(placed, actual)
    assert sorted(pairs) == [((5, -90000), (7, -95000)), ((12, -4000), (13, -4000)),
                             ((20, -1500), (29, -1500))]  # fmt: skip
    assert missed == [] and extra == [(28, -3000)]
    placed = [(1, -1000, "a")]
    assert match_recurring(placed, [(15, -1000, "a")]) == ([], [(1, -1000)], [(15, -1000)])


def test_the_four_pieces_add_up_to_the_recurring_error_at_every_day() -> None:
    placed = [(5, -90000, "rent"), (12, -4000, "phone"), (20, -1500, "gym"), (25, -700, "x")]
    actual = [(7, -95000, "rent"), (13, -4000, "phone"), (28, -3000, "other"), (29, -1500, "gym")]
    pairs, missed, extra = match_recurring(placed, actual)
    for k in range(1, HORIZON + 1):
        expected = sum(c for d, c, _ in placed if d <= k)
        happened = sum(c for d, c, _ in actual if d <= k)
        assert sum(recurring_split(pairs, missed, extra, k).values()) == expected - happened
    assert recurring_split(pairs, missed, extra, HORIZON)["timing"] == 0  # no effect on day 30
    assert (
        recurring_split(pairs, missed, extra, 6)["timing"] == -90000
    )  # placed by then, not yet real


# --- The whole record, on a synthetic persona ---


@pytest.fixture(scope="module")
def synthetic_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("diag")
    export, _ = write_persona(build_persona("v2-ITIGHT-01").persona, folder, 42, START, END)
    conn = connect(folder / "d.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    conn.close()
    return folder / "d.db"


@pytest.mark.slow
def test_the_pieces_add_up_exactly(synthetic_db: Path) -> None:
    conn = connect_readonly(synthetic_db)
    try:
        for origin in ORIGINS:
            r = origin_record(conn, "acct", origin, 60)
            assert sum(r["error_30"].values()) == pytest.approx(r["total_30"])
            low = sum(r["error_low"].values())  # the parts and the low point
            assert low == pytest.approx(r["lowest_error"])
            for k in range(HORIZON):
                happened = sum(r["happened_cum"][p][k] for p in PARTS)
                assert happened == pytest.approx(r["realized"][k] - r["start_cents"])
                expected = sum(r["expected_cum"][p][k] for p in PARTS)
                assert expected == pytest.approx(r["mean_path"][k] - r["start_cents"])
            assert sum(r["recurring_30"].values()) == pytest.approx(r["error_30"]["recurring"])
            assert sum(r["recurring_low"].values()) == pytest.approx(
                r["error_low"]["recurring"]
            )  # fmt: skip
            assert all(abs(v) < 1 for v in r["happened_cum"]["unexplained"])  # the chain reconciles
    finally:
        conn.close()


@pytest.mark.slow
@pytest.mark.parametrize("origin", [date(2026, 3, 2), date(2026, 5, 25)])
def test_rewriting_every_later_row_changes_no_expected_number(
    synthetic_db: Path, tmp_path: Path, origin: date
) -> None:  # fmt: skip
    """Rule zero: what was expected comes from rows posted by the origin. (At the second origin
    a hold has its reversal after the origin: using later rows would net it and change the
    spending stream; breaking the rule on purpose makes this test fail.)"""
    copy = tmp_path / "later.db"
    shutil.copy(synthetic_db, copy)
    raw = sqlite3.connect(copy)
    raw.execute(
        "UPDATE transactions SET amount_cents = amount_cents * 3 WHERE posted_date > ?",
        (origin.isoformat(),),
    )
    raw.commit()
    raw.close()
    a, b = connect_readonly(synthetic_db), connect_readonly(copy)
    try:
        before = origin_record(a, "acct", origin, 60)
        after = origin_record(b, "acct", origin, 60)
    finally:
        a.close()
        b.close()
    assert before["expected_cum"] == after["expected_cum"]
    assert before["mean_path"] == after["mean_path"]
    assert before["happened_cum"] != after["happened_cum"]  # the check can tell the difference


@pytest.mark.slow
def test_the_irregular_deposit_counts_add_up(synthetic_db: Path) -> None:
    conn = connect_readonly(synthetic_db)
    try:
        ev = deposit_evidence(conn, "acct", [date(2026, 3, 2), date(2026, 6, 1)])
    finally:
        conn.close()
    for window in ev["windows"].values():
        assert sum(b["count"] for b in window["bands"]) == window["rows"]
        assert sum(window["by_type"].values()) == window["rows"]


@pytest.mark.slow
def test_the_command_prints_summaries_and_no_dates_or_names(
    synthetic_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # fmt: skip
    assert main(["--db", str(synthetic_db), "--limit", "12", "--futures", "30"]) == 0
    out = capsys.readouterr().out
    assert "account-1" in out and "acct" not in out
    assert "Parts of the 30-day error" in out and "Parts of the lowest-balance error" in out
    assert "Irregular deposits" in out
    assert not re.search(r"\d{4}-\d{2}-\d{2}", out)  # no date
    assert "Free Interac" not in out and "credit memo" in out  # types only, no row text


@pytest.mark.slow
def test_with_keeps_coming_on_the_pieces_still_add_up_and_the_new_parts_are_used(
    synthetic_db: Path,
) -> None:  # fmt: skip
    from lookahead.forecast.spending import KeepsComing

    conn = connect_readonly(synthetic_db)
    seen = {"keeps_in": 0.0, "keeps_out": 0.0}
    try:
        for own in (True, False):
            for origin in (date(2026, 3, 2), date(2026, 6, 1), date(2026, 8, 3)):
                r = origin_record(conn, "acct", origin, 60, KeepsComing(own_account=own))
                assert sum(r["error_30"].values()) == pytest.approx(r["total_30"])
                assert sum(r["error_low"].values()) == pytest.approx(r["lowest_error"])
                assert all(abs(v) < 1 for v in r["happened_cum"]["unexplained"])
                for part in seen:
                    seen[part] += abs(r["expected_cum"][part][-1]) + abs(
                        r["happened_cum"][part][-1]
                    )
    finally:
        conn.close()
    assert seen["keeps_in"] > 0  # this persona has big deposits that keep coming
