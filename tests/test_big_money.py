"""Tests for the big-money diagnostic. Made-up rows and a synthetic persona only."""

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from eval.big_money import band_of, history_lines, history_rows, main, origin_facts, similar_before
from lookahead.db import connect
from lookahead.flows import Txn, flow_types
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

CARD = "Opos {:<20}toron "
ORIGIN = date(2026, 9, 30)


def small_rows(start_id: int = 100) -> list[Txn]:
    """Small purchases and small deposits, so that the typical deposit is small."""
    rows = [
        Txn(start_id + i, date(2026, 6, 1) + timedelta(days=3 * i), -2000, "pos purchase",
            CARD.format("Shop"))
        for i in range(30)
    ]  # fmt: skip
    rows += [
        Txn(start_id + 50 + i, date(2026, 6, 2) + timedelta(days=5 * i), 3000, "deposit", "Friend ")
        for i in range(18)
    ]  # fmt: skip
    return rows


def test_the_bands_and_similar_size_rows() -> None:
    assert [band_of(c) for c in (50000, 99999, 100000, 250000, 500000, 9_000_000)] == [
        0,
        0,
        1,
        2,
        3,
        3,
    ]
    day = date(2026, 5, 1)
    others = [(date(2026, 4, 1), 60000), (date(2026, 3, 1), 150000), (date(2025, 1, 1), 60000),
              (date(2026, 5, 2), 60000)]  # fmt: skip
    # 600 is between half (300) and double (1,200) of 700; 1,500 is not; one is over a year back;
    # one is after the day
    assert similar_before(70000, day, others) == 1


def test_what_the_forecaster_set_aside_matches_the_cutoffs_printed() -> None:
    """A deposit at the cutoff is set aside, one under it is not; the counts follow."""
    rows = small_rows()
    rows += [
        Txn(1, date(2026, 8, 3), 50000, "credit memo", "1/Person A "),  # at the $500 floor
        Txn(2, date(2026, 9, 3), 49900, "credit memo", "1/Person A "),  # a cent-ish under
        Txn(3, date(2026, 9, 10), -1600000, "bill payment", "City University "),
    ]  # fmt: skip
    flows = flow_types(rows)
    facts = origin_facts(rows, flows, set(), ORIGIN)
    assert facts["cut_in"] == 50000 and facts["own_yardstick"]
    assert facts["deposits_large"] == 1 and facts["payments_large"] == 1
    assert facts["deposits_pass"] == facts["payments_pass"] == 0


def test_a_monthly_deposit_is_big_on_arrival_and_has_earlier_rows_of_similar_size() -> None:
    rows = small_rows()
    rows += [
        Txn(10 + i, date(2026, 3, 5) + timedelta(days=30 * i), 60000 + 1000 * i, "credit memo",
            "1/Person A ")
        for i in range(6)
    ]  # fmt: skip
    rows.append(Txn(30, date(2026, 8, 20), -1600000, "bill payment", "City University "))
    flows = flow_types(rows)
    got = history_rows(rows, flows, set())
    # the first three deposits come while the typical deposit is not yet defined by 5 rows: the
    # point is the shape: later ones have earlier similar rows
    assert [r["similar"] for r in got["deposits"]] == [0, 1, 2, 3, 4, 5]
    assert all(r["band"] == 0 for r in got["deposits"])
    (payment,) = got["payments"]
    assert payment["big"] and payment["similar"] == 0 and payment["band"] == 3
    text = "\n".join(history_lines(got))
    assert "payments: 1 rows" in text and not re.search(r"\d{4}-\d{2}-\d{2}", text)


@pytest.mark.slow
def test_the_command_prints_counts_and_no_dates_or_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # fmt: skip
    export, _ = write_persona(build_persona("v2-ITIGHT-01").persona, tmp_path, 42, START, END)
    conn = connect(tmp_path / "d.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    conn.close()
    assert main(["--db", str(tmp_path / "d.db")]) == 0
    out = capsys.readouterr().out
    assert "account-1" in out and "acct" not in out
    assert "The forecaster at each of" in out and "big-deposit cutoff" in out
    assert "were big on the day they arrived" in out
    assert not re.search(r"\d{4}-\d{2}-\d{2}", out)


def test_deposits_by_type_split_the_recurring_size_money() -> None:
    from eval.big_money import type_lines

    def dep(kind: str, cents: int, similar: int, big: bool = True) -> dict:
        return {"type": kind, "cents": cents, "similar": similar, "big": big}

    deposits = [
        dep("own-account transfer", 100000, 2), dep("own-account transfer", 100000, 3),
        dep("credit memo", 200000, 2), dep("incoming e-Transfer", 80000, 0),
        dep("deposit", 60000, 5, big=False),
    ]  # fmt: skip
    text = "\n".join(type_lines(deposits))
    assert "own-account transfer: 2 rows of $500 or more, 2 big on arrival, 2 of those" in text
    assert "= 50% of the recurring-size money" in text  # 2 x 1,000 of 4,000
    assert "credit memo: 1 rows of $500 or more, 1 big on arrival, 1 of those" in text
    assert "incoming e-Transfer: 1 rows of $500 or more, 1 big on arrival, 0 of those" in text
    assert "deposit: 1 rows of $500 or more, 0 big on arrival, 0 of those" in text
    assert type_lines([dep("deposit", 60000, 0)])[-1].startswith("      no big deposit has")
