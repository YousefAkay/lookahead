"""Tests for the counts-only lump sender report. Made-up rows and synthetic personas only."""

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from eval.lump_senders import count_account, count_lumps, main, sender_of
from lookahead.db import connect, connect_readonly
from lookahead.flows import Txn, flow_types
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

AS_OF = date(2026, 9, 30)
CARD = "Opos {:<20}toron "


def background() -> list[Txn]:
    """Small everyday rows, so that the typical row is small."""
    return [
        Txn(100 + i, date(2026, 9, 1) + timedelta(days=i), -2000, "pos purchase",
            CARD.format("Shop"))
        for i in range(12)
    ]  # fmt: skip


def test_a_sender_is_the_text_without_the_bank_words_and_slots() -> None:
    assert sender_of("1/Some Person ") == "some person"
    assert sender_of("Free Interac E-Transfer ") == ""  # an e-Transfer names nobody
    assert sender_of("1/ ") == "" and sender_of(" ") == ""
    assert sender_of("0012345 ") == ""  # a number alone is no name


def test_lumps_are_counted_named_unnamed_and_by_sender() -> None:
    rows = background() + [
        Txn(1, date(2026, 2, 3), 1500000, "credit memo", "1/Person A "),
        Txn(2, date(2026, 5, 4), 1400000, "credit memo", "1/Person A "),
        Txn(3, date(2026, 6, 4), 1400000, "credit memo", "1/Person B "),
        Txn(4, date(2026, 7, 6), 900000, "deposit", "Free Interac E-Transfer "),
        Txn(5, date(2026, 8, 1), 30000, "credit memo", "1/Person A "),  # small: not a lump
    ]  # fmt: skip
    counts = count_lumps(rows, flow_types(rows), set(), AS_OF)
    assert counts == {
        "lumps": 4, "named": 3, "unnamed": 1, "senders": 2, "senders_2_plus": 1,
        "senders_3_plus": 0, "lumps_from_senders_2_plus": 2,
    }  # fmt: skip


def test_a_row_posted_after_the_date_is_not_counted() -> None:
    rows = background() + [Txn(1, date(2026, 10, 5), 1500000, "credit memo", "1/Person A ")]
    assert count_lumps(rows, flow_types(rows), set(), AS_OF)["lumps"] == 0


@pytest.fixture(scope="module")
def synthetic_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("lumps")
    export, _ = write_persona(build_persona("v2-ITIGHT-01").persona, folder, 42, START, END)
    conn = connect(folder / "made_up.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    conn.close()
    return folder / "made_up.db"


@pytest.mark.slow
def test_the_command_prints_counts_only(
    synthetic_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # fmt: skip
    assert main(["--db", str(synthetic_db)]) == 0
    out = capsys.readouterr().out
    assert "account-1" in out and "acct" not in out
    assert "lump deposits in the last 365 days" in out
    conn = connect_readonly(synthetic_db)
    try:
        counts = count_account(conn, "acct")
        assert counts is not None and counts["lumps"] == counts["named"] + counts["unnamed"]
    finally:
        conn.close()
