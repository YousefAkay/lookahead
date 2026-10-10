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
    assert counts == {"accounts": 1, "new_origins": 4, "origins_saved": 4, "archived_older": 0}
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


# --- Saved results from an older version are never mixed with new ones ---


def old_format_results(out: Path) -> None:
    """What the earlier --quick run left: records without the newer fields, a meta file without
    a format."""
    out.mkdir(parents=True)
    record = {"dataset": "real", "persona": "account-1", "origin": "2026-01-05", "buffer_cents": 0,
              "realized": [1, 2], "realized_below_zero": False,
              "realized_first_below_buffer": None}  # fmt: skip
    (out / "account-1.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    (out / "account-1.meta.json").write_text(
        json.dumps({"futures": 40, "method": "weekday"}), encoding="utf-8"
    )


def test_results_from_an_older_version_are_set_aside_and_the_run_starts_fresh(
    made_up_db: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    old_format_results(out)
    counts = run_real(made_up_db, out, futures=40, quick=True)
    assert counts["archived_older"] == 1  # one batch of old results was set aside
    assert counts["new_origins"] == 4 and counts["origins_saved"] == 4  # not 5: no mixing
    older = list((out / "older").rglob("*.jsonl"))
    assert len(older) == 1 and "2026-01-05" in older[0].read_text(
        encoding="utf-8"
    )  # kept, not lost
    fresh = [json.loads(x) for x in (out / "account-1.jsonl").read_text().splitlines()]
    assert all(r["format"] == 2 and "history_days" in r for r in fresh)


def test_the_command_says_so_in_one_line_and_prints_counts_only(
    made_up_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    old_format_results(out)
    main(["--db", str(made_up_db), "--out", str(out), "--quick", "--futures", "40"])
    text = capsys.readouterr().out
    lines = [x for x in text.splitlines() if "older version" in x]
    assert len(lines) == 1 and "starting fresh" in lines[0]
    assert ACCOUNT not in text and "$" not in text and not re.search(r"\d{4}-\d{2}-\d{2}", text)


def test_current_results_are_not_set_aside(made_up_db: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    run_real(made_up_db, out, futures=40, quick=True)
    again = run_real(made_up_db, out, futures=40, quick=True)
    assert again["archived_older"] == 0 and not (out / "older").exists()


def test_a_logic_version_change_also_counts_as_older(
    made_up_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import eval.backtest as backtest

    out = tmp_path / "out"
    run_real(made_up_db, out, futures=40, quick=True)
    monkeypatch.setattr(backtest, "LOGIC", backtest.LOGIC + 1)  # the forecast logic changed
    counts = run_real(made_up_db, out, futures=40, quick=True)
    assert counts["archived_older"] == 1 and counts["origins_saved"] == 4


def test_the_metrics_flag_prints_summary_numbers_for_the_real_column_only(
    made_up_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--db", str(made_up_db), "--out", str(tmp_path / "out"), "--quick", "--futures", "40",
          "--metrics"])  # fmt: skip
    text = capsys.readouterr().out
    assert "BACKTEST SUMMARY" in text and "Real" in text and "Real, buffer $0" in text
    assert "Synthetic (development): none" in text  # no synthetic numbers here
    assert ACCOUNT not in text and "account-1" not in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)  # no date of any transaction or origin


# --- The quick and the full run never share a folder ---


def test_quick_and_full_runs_save_to_their_own_folders_and_do_not_collide(
    made_up_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import eval.backtest_real as backtest_real

    monkeypatch.setattr(backtest_real, "DEFAULT_OUT", tmp_path / "backtest")
    off = ["--keeps-coming", "off"]  # the plain folders are the old forecaster's
    main(["--db", str(made_up_db), "--quick", "--futures", "40", *off])
    main(["--db", str(made_up_db), "--futures", "20", *off])  # other settings: used to raise
    quick = list((tmp_path / "backtest" / "quick").glob("*.jsonl"))
    full = list((tmp_path / "backtest" / "full").glob("*.jsonl"))
    assert len(quick) == 1 and len(full) == 1
    assert not list((tmp_path / "backtest").glob("*.jsonl"))  # nothing in the shared folder
    n_quick = len(quick[0].read_text(encoding="utf-8").splitlines())
    n_full = len(full[0].read_text(encoding="utf-8").splitlines())
    assert n_quick == 4 and n_full > n_quick


def test_an_explicit_out_folder_is_used_as_given(made_up_db: Path, tmp_path: Path) -> None:
    main(["--db", str(made_up_db), "--out", str(tmp_path / "mine"), "--quick", "--futures", "40"])
    assert list((tmp_path / "mine").glob("*.jsonl"))


def test_the_default_forecaster_has_its_own_folder_apart_from_the_old_one(
    made_up_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import eval.backtest_real as backtest_real

    monkeypatch.setattr(backtest_real, "DEFAULT_OUT", tmp_path / "backtest")
    main(["--db", str(made_up_db), "--quick", "--futures", "40"])  # the default: keeps coming on
    main(["--db", str(made_up_db), "--quick", "--futures", "40", "--keeps-coming", "off"])
    main(["--db", str(made_up_db), "--quick", "--futures", "40", "--keeps-coming", "without-own"])
    root = tmp_path / "backtest"
    assert len(list((root / "keeps-coming-with-own" / "quick").glob("*.jsonl"))) == 1
    assert len(list((root / "keeps-coming-without-own" / "quick").glob("*.jsonl"))) == 1
    assert len(list((root / "quick").glob("*.jsonl"))) == 1  # the old forecaster, untouched
