"""Tests for the runners of the recurring-placement fix (DECISIONS.md, 2026-10-12). Made-up and
synthetic data only; nothing here touches a real folder."""

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from eval.diagnose_real import main as diagnose_main
from eval.recurring_fix_eval import compare_personas, report, run_all
from eval.recurring_fix_real import main as real_main
from eval.recurring_fix_real import run_real_fix
from lookahead.db import connect
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents


@pytest.fixture(scope="module")
def private_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A made-up "real" database inside a folder called data/private (in a temporary place)."""
    folder = tmp_path_factory.mktemp("root") / "data" / "private"
    folder.mkdir(parents=True)
    export, _ = write_persona(build_persona("v2-TIGHT-01").persona, folder, 42, START, END)
    conn = connect(folder / "made_up.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    set_safety_buffer_cents(conn, 10_000)
    conn.close()
    return folder / "made_up.db"


def test_real_results_are_refused_outside_a_private_folder(
    private_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # fmt: skip
    with pytest.raises(ValueError, match="data/private"):
        run_real_fix(private_db, tmp_path / "out", 20, "off", quick=True)
    assert not (tmp_path / "out").exists()  # nothing was created
    assert real_main(["--fix", "off", "--db", str(private_db), "--out", str(tmp_path / "o")]) == 1
    assert "output folder must be inside" in capsys.readouterr().out
    assert real_main(["--fix", "off", "--db", str(tmp_path / "x.db")]) == 1  # no database there


@pytest.mark.slow
def test_each_variant_has_its_own_folder_and_they_never_mix(private_db: Path) -> None:
    root = private_db.parent / "backtest"
    off, b = root / "recurring-fix-off" / "quick", root / "recurring-fix-b" / "quick"
    assert run_real_fix(private_db, off, 20, "off", quick=True)["new_origins"] == 4
    assert run_real_fix(private_db, b, 20, "b", quick=True)["new_origins"] == 4
    (saved,) = list(off.glob("account-*.jsonl"))  # named by a salted key, not a list position
    record = json.loads(saved.read_text(encoding="utf-8").splitlines()[0])
    assert record["recurring_fix"] == "off" and record["persona"] == saved.stem
    assert not re.fullmatch(r"account-\d{1,6}", saved.stem)  # not the old numbered name
    assert "acct" not in json.dumps(record)  # the account's name stays out of the files
    assert (private_db.parent / "backtest_account_salt.txt").exists()
    with pytest.raises(ValueError, match="different settings"):
        run_real_fix(private_db, off, 20, "b", quick=True)  # the other variant's settings


@pytest.mark.slow
def test_the_breakdown_and_the_check_take_the_flag(
    private_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # fmt: skip
    args = ["--db", str(private_db), "--limit", "6", "--futures", "20", "--recurring-fix", "b"]
    assert diagnose_main(args) == 0
    out = capsys.readouterr().out
    assert "Parts of the 30-day error" in out and not re.search(r"\d{4}-\d{2}-\d{2}", out)


def rec(persona: str, origin: str, items: int = 5, p50: int = 100) -> dict:  # type: ignore[type-arg]
    return {
        "persona": persona, "origin": origin, "n_items": items, "p10": [p50] * 30,
        "p50": [p50] * 30, "p90": [p50] * 30, "point": [p50] * 30, "median_lowest_cents": p50,
        "shortfall_probability": 0.0, "lowest_cents": [p50],
    }  # fmt: skip


def test_a_persona_is_identical_only_if_every_forecast_field_is() -> None:
    off = [rec("a", "2026-01-05"), rec("b", "2026-01-05"), rec("c", "2026-01-05")]
    on = [rec("a", "2026-01-05"), rec("b", "2026-01-05", items=4), rec("c", "2026-01-05", p50=99)]
    same, differing = compare_personas(off, on)
    assert same == ["a"] and differing == {"b": 1, "c": 1}


@pytest.mark.slow
def test_the_synthetic_comparison_runs_and_a_persona_without_stopped_series_is_identical(
    tmp_path: Path,
) -> None:  # fmt: skip
    from eval.metrics import load_records
    from eval.recurring_fix_eval import VARIANTS, folders

    run_all(["v2-SAL-01"], tmp_path / "work", tmp_path, futures=20)
    where = folders(tmp_path)
    records = {v: load_records(where[v]) for v in VARIANTS}
    assert all(len(records[v]) == 40 for v in VARIANTS)
    text = report(records)
    assert text.count("drop rule") == 3 and "personas identical to no fix" in text
    assert "(a) added to (b), against the default" in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)
