"""Tests for the counts-only category report and the synthetic evaluation.

Temporary databases only: never data/private/.
"""

import re
from datetime import date, datetime
from pathlib import Path

import pytest

from lookahead.categories import main, set_category_preference
from lookahead.db import connect
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

START, EXPORT = date(2026, 6, 1), date(2026, 9, 30)


@pytest.fixture
def db(tmp_path: Path) -> tuple[Path, Path]:
    export, _ = write_persona(PERSONAS["C"], tmp_path / "s", 42, START, EXPORT)
    path = tmp_path / "db" / "t.db"
    conn = connect(path)
    store_exports(conn, "synthetic-C", [(export.name, parse_export(export))], datetime(2026, 10, 2))
    conn.commit()
    conn.close()
    return path, export


def report(path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = main(["report", "--db", str(path), "--account", "synthetic-C"])
    return code, capsys.readouterr().out


def test_report_counts_add_up_and_pass(
    db: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    path, export = db
    code, out = report(path, capsys)
    assert code == 0 and out.rstrip().endswith("RESULT: PASS")
    assert "flow/category conflicts: 0" in out
    total = int(re.search(r"Category rules: (\d+) rows", out)[1])  # type: ignore[index]
    parts = [
        int(re.search(rf"{label}: (\d+)", out)[1])  # type: ignore[index]
        for label in (
            "decided by transaction type",
            "inherited from the paired purchase",
            "your preferences",
            "delivery platforms",
            "merchant defaults",
            r"netted pairs \(no category needed\)",
            "left for the categorizer",
        )
    ]
    assert sum(parts) == total == len(parse_export(export))


def test_report_prints_no_names_or_amounts(
    db: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    path, export = db
    _, out = report(path, capsys)
    assert not re.search(r"\d+\.\d{2}", out) and "$" not in out
    for row in parse_export(export):
        if row.sub_description.strip():
            assert row.sub_description.strip() not in out


def test_report_uses_your_stored_preferences(
    db: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    path, _ = db
    conn = connect(path)
    set_category_preference(conn, "Tim Hortons", "Restaurants & cafés")
    conn.close()
    _, out = report(path, capsys)
    assert re.search(r"your preferences: [1-9]\d*", out)


@pytest.mark.parametrize("pid", ["A", "B", "C"])
def test_rules_miss_only_the_week3_rent_gap(pid: str, tmp_path: Path) -> None:
    from eval.category_rules import RuleScore, score

    export, key = write_persona(PERSONAS[pid], tmp_path, 42, START, EXPORT)
    s = RuleScore()
    score(export, key, s)
    assert not s.misses
    assert sum(s.correct.values()) + s.rent_gap == sum(s.decided.values())
