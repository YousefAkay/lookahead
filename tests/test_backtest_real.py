"""The real-data backtest script: one the account holder runs. Tested here only on a made-up
database; it must leave the database untouched and print counts, never rows or names."""

import hashlib
import json
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from eval.backtest_real import main, real_origins, run_real
from lookahead.db import connect
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents

ACCOUNT = "my-private-chequing"  # a name that must never be printed


@pytest.fixture(scope="module")
def made_up_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("real")
    persona = build_persona("v2-TIGHT-01")
    export, _ = write_persona(persona.persona, folder, 42, START, END)
    conn = connect(folder / "made_up.db")
    store_exports(conn, ACCOUNT, [(export.name, parse_export(export))], datetime(2026, 10, 1))
    set_safety_buffer_cents(conn, 0)
    conn.close()
    return folder / "made_up.db"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_origins_leave_eight_weeks_before_and_thirty_days_after() -> None:
    days = real_origins(date(2024, 10, 3), date(2026, 10, 1))
    assert days[0] >= date(2024, 10, 3) + timedelta(weeks=8)
    assert days[-1] <= date(2026, 10, 1) - timedelta(days=30)
    assert all(d.weekday() == 0 for d in days)
    assert days[1] - days[0] == timedelta(days=7)


def test_a_short_history_has_no_origins() -> None:
    assert real_origins(date(2026, 8, 1), date(2026, 9, 1)) == []


def test_the_run_saves_records_labelled_real_and_leaves_the_database_alone(
    made_up_db: Path, tmp_path: Path
) -> None:
    db = tmp_path / "copy.db"
    shutil.copy(made_up_db, db)
    before = digest(db)
    counts = run_real(db, tmp_path / "out", futures=40, quick=True)
    assert digest(db) == before  # read-only: not a byte changed
    assert counts == {"accounts": 1, "new_origins": 4, "origins_saved": 4}
    [path] = list((tmp_path / "out").glob("*.jsonl"))
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 4
    assert {r["dataset"] for r in records} == {"real"}  # never mixed with synthetic
    assert all(r["held_out"] is None and r["archetype"] is None for r in records)
    assert all(r["buffer_cents"] == 0 for r in records)  # the account's own saved buffer
    assert all(r["persona"] == "account-1" for r in records)  # the name stays out of the files


def test_the_output_files_do_not_carry_the_account_name_in_their_names(
    made_up_db: Path, tmp_path: Path
) -> None:
    run_real(made_up_db, tmp_path / "out", futures=40, quick=True)
    names = [p.name for p in (tmp_path / "out").iterdir()]
    assert not any(ACCOUNT in n for n in names)  # account-1, account-2, ...


def test_a_second_run_does_nothing_new(made_up_db: Path, tmp_path: Path) -> None:
    run_real(made_up_db, tmp_path / "out", futures=40, quick=True)
    again = run_real(made_up_db, tmp_path / "out", futures=40, quick=True)
    assert again["new_origins"] == 0 and again["origins_saved"] == 4


def test_the_command_prints_only_counts(
    made_up_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--db", str(made_up_db), "--out", str(tmp_path / "out"), "--quick", "--futures", "40"])
    text = capsys.readouterr().out
    assert ACCOUNT not in text and "$" not in text and "cents" not in text
    assert "1 account" in text and "4 origins" in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)  # no date of any transaction or origin
    assert "tight-budget" not in text  # nothing about the content of the data


def test_a_missing_database_says_so_without_a_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--db", str(tmp_path / "nope.db"), "--out", str(tmp_path / "out")]) == 1
    assert "nope.db" not in capsys.readouterr().out
