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
    folder = tmp_path_factory.mktemp("real") / "data" / "private"
    folder.mkdir(parents=True)
    persona = build_persona("v2-TIGHT-01")
    export, _ = write_persona(persona.persona, folder, 42, START, END)
    conn = connect(folder / "made_up.db")
    store_exports(conn, ACCOUNT, [(export.name, parse_export(export))], datetime(2026, 10, 1))
    set_safety_buffer_cents(conn, 0)
    conn.close()
    return folder / "made_up.db"


def private(tmp_path: Path) -> Path:
    """A folder inside data/private, where the script may write."""
    folder = tmp_path / "data" / "private"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


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
    db = private(tmp_path) / "copy.db"
    shutil.copy(made_up_db, db)
    before = digest(db)
    counts = run_real(db, private(tmp_path) / "out", futures=40, quick=True)
    assert digest(db) == before  # read-only: not a byte changed
    assert counts == {"accounts": 1, "new_origins": 4, "origins_saved": 4, "archived_older": 0}
    [path] = list((private(tmp_path) / "out").glob("*.jsonl"))
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 4
    assert {r["dataset"] for r in records} == {"real"}  # never mixed with synthetic
    assert all(r["held_out"] is None and r["archetype"] is None for r in records)
    assert all(r["buffer_cents"] == 0 for r in records)  # the account's own saved buffer
    assert len({r["persona"] for r in records}) == 1  # the name stays out of the files:
    assert re.fullmatch(r"account-[0-9a-f]{10}", records[0]["persona"])  # a salted key


def test_the_output_files_do_not_carry_the_account_name_in_their_names(
    made_up_db: Path, tmp_path: Path
) -> None:
    run_real(made_up_db, private(tmp_path) / "out", futures=40, quick=True)
    names = [p.name for p in (private(tmp_path) / "out").iterdir()]
    assert not any(ACCOUNT in n for n in names)  # account-<salted key>


def test_the_settings_of_the_forecaster_are_in_the_meta_of_a_real_run(
    made_up_db: Path, tmp_path: Path
) -> None:  # fmt: skip
    out = private(tmp_path) / "out"
    run_real(made_up_db, out, futures=40, quick=True)
    [meta] = list(out.glob("*.meta.json"))
    saved = json.loads(meta.read_text(encoding="utf-8"))
    assert saved["forecaster"]["recurring_fix"] == {"possibly_ended": True, "weekly_card": True}


def test_a_second_run_does_nothing_new(made_up_db: Path, tmp_path: Path) -> None:
    run_real(made_up_db, private(tmp_path) / "out", futures=40, quick=True)
    again = run_real(made_up_db, private(tmp_path) / "out", futures=40, quick=True)
    assert again["new_origins"] == 0 and again["origins_saved"] == 4


def test_the_command_prints_only_counts(
    made_up_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = private(tmp_path) / "out"
    main(["--db", str(made_up_db), "--out", str(out), "--quick", "--futures", "40"])
    text = capsys.readouterr().out
    assert ACCOUNT not in text and "$" not in text and "cents" not in text
    assert "1 account" in text and "4 origins" in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)  # no date of any transaction or origin
    assert "tight-budget" not in text  # nothing about the content of the data


def test_a_missing_database_says_so_without_a_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--db", str(tmp_path / "nope.db"), "--out", str(private(tmp_path) / "out")]) == 1
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
    out = private(tmp_path) / "out"
    old_format_results(out)
    counts = run_real(made_up_db, out, futures=40, quick=True)
    assert counts["archived_older"] == 1  # one batch of old results was set aside
    assert counts["new_origins"] == 4 and counts["origins_saved"] == 4  # not 5: no mixing
    older = list((out / "older").rglob("*.jsonl"))
    assert len(older) == 1 and "2026-01-05" in older[0].read_text(
        encoding="utf-8"
    )  # kept, not lost
    [path] = list(out.glob("account-*.jsonl"))
    fresh = [json.loads(x) for x in path.read_text().splitlines()]
    assert all(r["format"] == 2 and "history_days" in r for r in fresh)


def test_the_command_says_so_in_one_line_and_prints_counts_only(
    made_up_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = private(tmp_path) / "out"
    old_format_results(out)
    main(["--db", str(made_up_db), "--out", str(out), "--quick", "--futures", "40"])
    text = capsys.readouterr().out
    lines = [x for x in text.splitlines() if "older version" in x]
    assert len(lines) == 1 and "starting fresh" in lines[0]
    assert ACCOUNT not in text and "$" not in text and not re.search(r"\d{4}-\d{2}-\d{2}", text)


def test_current_results_are_not_set_aside(made_up_db: Path, tmp_path: Path) -> None:
    out = private(tmp_path) / "out"
    run_real(made_up_db, out, futures=40, quick=True)
    again = run_real(made_up_db, out, futures=40, quick=True)
    assert again["archived_older"] == 0 and not (out / "older").exists()


def test_a_logic_version_change_also_counts_as_older(
    made_up_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import eval.backtest as backtest

    out = private(tmp_path) / "out"
    run_real(made_up_db, out, futures=40, quick=True)
    monkeypatch.setattr(backtest, "LOGIC", backtest.LOGIC + 1)  # the forecast logic changed
    counts = run_real(made_up_db, out, futures=40, quick=True)
    assert counts["archived_older"] == 1 and counts["origins_saved"] == 4


def test_the_metrics_flag_prints_summary_numbers_for_the_real_column_only(
    made_up_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = private(tmp_path) / "out"
    main(["--db", str(made_up_db), "--out", str(out), "--quick", "--futures", "40",
          "--metrics"])  # fmt: skip
    text = capsys.readouterr().out
    assert "BACKTEST SUMMARY" in text and "Real" in text and "Real, buffer $0" in text
    assert "Synthetic (development): none" in text  # no synthetic numbers here
    assert ACCOUNT not in text and "account-" not in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)  # no date of any transaction or origin


# --- The quick and the full run never share a folder ---


def test_quick_and_full_runs_save_to_their_own_folders_and_do_not_collide(
    made_up_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import eval.backtest_real as backtest_real

    monkeypatch.setattr(backtest_real, "DEFAULT_OUT", private(tmp_path) / "backtest")
    off = ["--keeps-coming", "off"]  # the plain folders are the old forecaster's
    main(["--db", str(made_up_db), "--quick", "--futures", "40", *off])
    main(["--db", str(made_up_db), "--futures", "20", *off])  # other settings: used to raise
    quick = list((private(tmp_path) / "backtest" / "quick").glob("*.jsonl"))
    full = list((private(tmp_path) / "backtest" / "full").glob("*.jsonl"))
    assert len(quick) == 1 and len(full) == 1
    shared = private(tmp_path) / "backtest"
    assert not list(shared.glob("*.jsonl"))  # nothing in the shared folder
    n_quick = len(quick[0].read_text(encoding="utf-8").splitlines())
    n_full = len(full[0].read_text(encoding="utf-8").splitlines())
    assert n_quick == 4 and n_full > n_quick


def test_an_explicit_private_out_folder_is_used_as_given(made_up_db: Path, tmp_path: Path) -> None:
    out = private(tmp_path) / "mine"
    assert main(["--db", str(made_up_db), "--out", str(out), "--quick", "--futures", "40"]) == 0
    assert list(out.glob("*.jsonl"))


# --- Real data is read from and written to data/private only (review item R1) ---


def test_an_out_folder_outside_data_private_is_refused_before_anything_is_made(
    made_up_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "mine"
    assert main(["--db", str(made_up_db), "--out", str(out), "--quick", "--futures", "40"]) == 1
    assert "outside data/private" in capsys.readouterr().out
    assert not out.exists()  # nothing created
    with pytest.raises(ValueError, match="outside data/private"):
        run_real(made_up_db, out, futures=40, quick=True)
    assert not out.exists()


def test_a_database_outside_data_private_is_refused(
    made_up_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "elsewhere.db"
    shutil.copy(made_up_db, db)
    out = private(tmp_path) / "out"
    assert main(["--db", str(db), "--out", str(out), "--quick", "--futures", "40"]) == 1
    assert "database outside data/private" in capsys.readouterr().out
    assert not out.exists()
    with pytest.raises(ValueError, match="database outside data/private"):
        run_real(db, out, futures=40, quick=True)


@pytest.mark.parametrize("extra", [["--arm", "3"], ["--keeps-coming", "off"], ["--paths"]])
def test_the_default_folders_of_arms_and_switches_must_be_private(
    made_up_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], extra: list[str],
) -> None:  # fmt: skip
    import eval.backtest_real as backtest_real

    monkeypatch.setattr(backtest_real, "DEFAULT_OUT", tmp_path / "backtest")  # not private
    assert main(["--db", str(made_up_db), "--quick", "--futures", "40", *extra]) == 1
    assert "outside data/private" in capsys.readouterr().out
    assert not (tmp_path / "backtest").exists()


def test_the_real_default_folder_is_private() -> None:
    from eval.backtest_real import DEFAULT_OUT
    from lookahead.merchants import is_private_path

    assert is_private_path(DEFAULT_OUT / "arms" / "arm1" / "keeps-coming-off" / "quick")


def test_the_default_forecaster_has_its_own_folder_apart_from_the_old_one(
    made_up_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import eval.backtest_real as backtest_real

    monkeypatch.setattr(backtest_real, "DEFAULT_OUT", private(tmp_path) / "backtest")
    main(["--db", str(made_up_db), "--quick", "--futures", "40"])  # the default: keeps coming on
    main(["--db", str(made_up_db), "--quick", "--futures", "40", "--keeps-coming", "off"])
    main(["--db", str(made_up_db), "--quick", "--futures", "40", "--keeps-coming", "without-own"])
    root = private(tmp_path) / "backtest"
    assert len(list((root / "keeps-coming-with-own" / "quick").glob("*.jsonl"))) == 1
    assert len(list((root / "keeps-coming-without-own" / "quick").glob("*.jsonl"))) == 1
    assert len(list((root / "quick").glob("*.jsonl"))) == 1  # the old forecaster, untouched


# --- Result files are named by a stable key, not by list position (review item R3) ---


def add_account(db: Path, name: str, folder: Path) -> None:
    """A second account in the database, with another persona's rows."""
    export, _ = write_persona(build_persona("v2-SAL-01").persona, folder, 42, START, END)
    c = connect(db)
    store_exports(c, name, [(export.name, parse_export(export))], datetime(2026, 10, 1))
    c.close()


def test_adding_an_account_that_sorts_first_keeps_every_file_with_its_account(
    made_up_db: Path, tmp_path: Path
) -> None:
    db = private(tmp_path) / "two.db"
    shutil.copy(made_up_db, db)
    out = private(tmp_path) / "out"
    run_real(db, out, futures=40, quick=True)
    [first] = list(out.glob("*.jsonl"))
    before = first.read_text(encoding="utf-8")
    add_account(db, "aaa-sorts-first", tmp_path / "export")  # sorts before the first account
    counts = run_real(db, out, futures=40, quick=True)
    assert counts["accounts"] == 2 and counts["new_origins"] == 4  # only the new account's
    assert first.read_text(encoding="utf-8") == before  # the old file is still that account's
    [second] = [p for p in out.glob("*.jsonl") if p != first]

    def starts(path: Path) -> list[int]:
        return [json.loads(x)["start_balance_cents"] for x in path.read_text().splitlines()]

    assert starts(second) != starts(first)  # the new account's own forecasts, not a copy
    assert not list(out.rglob("*aaa-sorts-first*")) and not list(out.rglob(f"*{ACCOUNT}*"))
    personas = {json.loads(line)["persona"] for p in out.glob("*.jsonl")
                for line in p.read_text(encoding="utf-8").splitlines()}  # fmt: skip
    assert len(personas) == 2 and not [p for p in personas if "aaa" in p or ACCOUNT in p]


def test_the_salt_is_kept_in_data_private_and_the_key_is_stable(
    made_up_db: Path, tmp_path: Path
) -> None:
    from eval.backtest_real import SALT_FILE, account_key

    root = private(tmp_path)
    key = account_key(ACCOUNT, root / "backtest" / "full")
    assert (root / SALT_FILE).exists()  # made on first use, in the data/private folder
    assert account_key(ACCOUNT, root / "backtest" / "quick") == key  # same account, same key
    assert account_key("another", root / "backtest" / "full") != key
    assert ACCOUNT not in key and re.fullmatch(r"account-[0-9a-f]{10}", key)


def test_files_named_by_list_position_are_set_aside_not_read_as_another_account(
    made_up_db: Path, tmp_path: Path
) -> None:
    out = private(tmp_path) / "out"
    run_real(made_up_db, out, futures=40, quick=True)
    [current] = list(out.glob("*.jsonl"))
    numbered = out / "account-1.jsonl"  # what an older version wrote
    numbered.write_text(current.read_text(encoding="utf-8"), encoding="utf-8")
    counts = run_real(made_up_db, out, futures=40, quick=True)
    assert counts["archived_older"] == 1 and counts["origins_saved"] == 4  # not 8
    assert not numbered.exists() and list((out / "older").rglob("account-1.jsonl"))


# --- No empty files (review item R6) ---


def test_an_account_with_too_short_a_history_leaves_no_files(tmp_path: Path) -> None:
    folder = private(tmp_path)
    export, _ = write_persona(build_persona("v2-SAL-01").persona, folder / "x", 42, START, END)
    db = folder / "short.db"
    c = connect(db)
    rows = [r for r in parse_export(export) if r.posted_date >= date(2026, 8, 15)]  # six weeks
    store_exports(c, ACCOUNT, [(export.name, rows)], datetime(2026, 10, 1))
    c.close()
    out = folder / "out"
    counts = run_real(db, out, futures=40, quick=True)
    assert counts["new_origins"] == 0 and counts["origins_saved"] == 0
    assert not list(out.glob("*.jsonl")) and not list(out.glob("*.meta.json"))


def test_a_salted_key_that_happens_to_be_all_digits_is_not_taken_for_a_numbered_file(
    tmp_path: Path,
) -> None:
    from eval.backtest_real import archive_numbered

    out = private(tmp_path) / "out"
    out.mkdir()
    (out / "account-1234567890.jsonl").write_text("{}\n", encoding="utf-8")  # a key, all digits
    (out / "account-3.jsonl").write_text("{}\n", encoding="utf-8")  # an older numbered file
    assert archive_numbered(out)
    assert (out / "account-1234567890.jsonl").exists() and not (out / "account-3.jsonl").exists()
