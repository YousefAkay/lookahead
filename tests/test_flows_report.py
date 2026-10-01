"""Tests for the counts-only flow report. Temporary databases only: never data/private/."""

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.flows import Txn, count_flows, main
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

UBER = "Opos Uber Canada/Ubertriptoron "
D = date(2026, 9, 1)


def test_hold_to_correction_gaps_are_bucketed() -> None:
    rows: list[Txn] = []
    for n, gap in enumerate([0, 0, 1, 2, 3, 5, 7]):
        amount = 1_000 + n  # a different amount per pair, so each pairs with its own hold
        rows.append(Txn(2 * n, D, -amount, "pos purchase", UBER))
        rows.append(Txn(2 * n + 1, D + timedelta(days=gap), amount, "correction", UBER))
    c = count_flows(rows)
    assert c.pairs["pre-auth correction"] == 7
    assert dict(c.preauth_gaps) == {"0": 2, "1": 1, "2-3": 2, "4-7": 2}


def test_rows_without_a_rule_fail_the_check() -> None:
    c = count_flows([Txn(1, D, -100, "mystery debit", " ")])
    assert not c.ok and c.no_rule == {("mystery debit", "out"): 1}


@pytest.fixture
def db(tmp_path: Path) -> tuple[Path, Path]:
    export, _ = write_persona(
        PERSONAS["C"], tmp_path / "s", 42, date(2026, 6, 1), date(2026, 9, 30)
    )
    path = tmp_path / "db" / "t.db"
    conn = connect(path)
    store_exports(conn, "synthetic-C", [(export.name, parse_export(export))], datetime(2026, 10, 2))
    conn.commit()
    conn.close()
    return path, export


def test_report_on_a_synthetic_account(
    db: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    path, export = db
    code = main(["report", "--db", str(path), "--account", "synthetic-C"])
    out = capsys.readouterr().out
    assert code == 0 and out.rstrip().endswith("RESULT: PASS")
    assert re.search(r"pre-auth corrections paired with their hold: (\d+) of \1\b", out)  # all
    assert re.search(r"returned e-Transfers paired with the e-Transfer: 1 of 1", out)
    assert "hold to correction: 0 days" in out
    assert "rows without a rule: 0" in out
    # Counts only: no money, no merchant text.
    assert not re.search(r"\d+\.\d{2}", out) and "$" not in out
    for row in parse_export(export):
        text = row.sub_description.strip()
        if text:
            assert text not in out


def test_report_on_a_missing_database(tmp_path: Path) -> None:
    assert main(["report", "--db", str(tmp_path / "none.db")]) == 2
    assert not (tmp_path / "none.db").exists()


@pytest.mark.parametrize("pid", ["A", "B", "C"])
def test_only_the_week3_gap_is_missed_on_synthetic_data(pid: str, tmp_path: Path) -> None:
    from eval.flow_typing import FlowScore, score

    export, key = write_persona(PERSONAS[pid], tmp_path, 42, date(2026, 6, 1), date(2026, 9, 30))
    s = FlowScore()
    score(export, key, s)
    assert s.correct + s.week3_gap == s.rows
    assert s.paired == s.to_pair == s.paired_right


# --- Counts-only reasons ---

SHOP = "Apos Winners #9074       Montr "
OTHER = "Apos Dollarama #8138     Montr "
ETRANSFER = "Free Interac E-Transfer "
RTN = "Interac E-Transfer Rtn "


def reasons(rows: list[Txn]) -> dict[tuple[str, str], int]:
    return dict(count_flows(rows).unpaired)


def test_no_rule_rows_are_counted_with_their_direction() -> None:
    c = count_flows([Txn(1, D, 100, "mystery credit", " "), Txn(2, D, -100, "mystery debit", " ")])
    assert c.no_rule == {("mystery credit", "in"): 1, ("mystery debit", "out"): 1}


def test_return_with_no_purchase_from_that_merchant_at_all() -> None:
    rows = [Txn(1, D, 2_000, "pos return", SHOP)]
    assert reasons(rows) == {("card return", "no earlier purchase from that merchant"): 1}


def test_return_whose_purchase_is_over_90_days_old() -> None:
    rows = [
        Txn(1, D - timedelta(days=120), -2_000, "pos purchase", SHOP),
        Txn(2, D, 2_000, "pos return", SHOP),
    ]
    assert reasons(rows) == {("card return", "nearest purchase over 90 days earlier"): 1}


def test_return_larger_than_every_recent_purchase() -> None:
    rows = [Txn(1, D, -1_000, "pos purchase", SHOP), Txn(2, D, 2_000, "pos return", SHOP)]
    assert reasons(rows) == {("card return", "purchases too small"): 1}


def test_return_whose_purchase_has_different_merchant_text() -> None:
    rows = [Txn(1, D, -2_000, "pos purchase", OTHER), Txn(2, D, 2_000, "pos return", SHOP)]
    assert reasons(rows) == {("card return", "same amount, different merchant group"): 1}


def test_return_whose_purchase_is_taken_by_another_return() -> None:
    rows = [
        Txn(1, D, -2_000, "pos purchase", SHOP),
        Txn(2, D, 2_000, "pos return", SHOP),
        Txn(3, D, 2_000, "pos return", SHOP),
    ]
    assert reasons(rows) == {("card return", "matching purchases already paired"): 1}


def test_returned_etransfer_with_no_etransfer_within_30_days() -> None:
    rows = [
        Txn(1, D - timedelta(days=45), -12_000, "withdrawal", ETRANSFER),
        Txn(2, D, 12_000, "deposit", RTN),
    ]
    assert reasons(rows) == {("returned e-Transfer", "same amount, over 30 days earlier"): 1}


@pytest.mark.parametrize(
    ("sent", "reason"),
    [
        (-12_500, "nearest amount within 30 days differs by 5% or less"),
        (-20_000, "nearest amount within 30 days differs by more than 5%"),
    ],
)
def test_returned_etransfer_with_a_different_amount(sent: int, reason: str) -> None:
    rows = [Txn(1, D, sent, "withdrawal", ETRANSFER), Txn(2, D, 12_000, "deposit", RTN)]
    assert reasons(rows) == {("returned e-Transfer", reason): 1}


def test_returned_etransfer_with_no_outgoing_etransfer_at_all() -> None:
    rows = [Txn(1, D, 12_000, "deposit", RTN)]
    assert reasons(rows) == {("returned e-Transfer", "no outgoing e-Transfer within 30 days"): 1}


def test_paired_rows_give_no_reasons() -> None:
    rows = [Txn(1, D, -2_000, "pos purchase", SHOP), Txn(2, D, 2_000, "pos return", SHOP)]
    assert reasons(rows) == {}


def test_returned_etransfer_whose_original_was_not_an_etransfer_withdrawal() -> None:
    rows = [
        Txn(1, D, -12_000, "customer transfer dr.", "Mb-Transfer "),
        Txn(2, D, 12_000, "deposit", RTN),
    ]
    assert reasons(rows) == {
        ("returned e-Transfer", "same amount within 30 days, not an e-Transfer withdrawal"): 1
    }
