"""lookahead.ask_check on synthetic demo persona A (never held-out), its data cut mid-month:
every fixed question agrees with the figure worked out another way, a wrong figure shows "no",
the output holds ids, outcomes and yes/no only, and the database is never written."""

import hashlib
import re
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead import ask_check
from lookahead.budgets import set_limit
from lookahead.db import connect, connect_readonly
from lookahead.demo import build_demo_db
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.pseudonymize import KEY_NAME
from lookahead.views import transactions

ACCOUNT = "synthetic-A"
KEY = bytes(range(32))
DATA_END = date(2026, 9, 14)
TODAY = DATA_END + timedelta(days=1)
LINE = re.compile(
    r"^Q\d\d (en|fr)  (ran|refused|asked back|not found|error) +matches: (yes|no|n/a)$"
)
HEADER = f"{len(ask_check.QUESTIONS)} fixed questions, asked as the Ask page asks them (offline)."
SUMMARY = re.compile(
    r"^Summary: ran \d+, refused \d+, asked back \d+, not found \d+, error \d+; "
    r"matches: yes \d+, no \d+, n/a \d+$"
)

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("ask_check")
    for pid in "ABC":  # the demo database holds the three demo personas
        write_persona(PERSONAS[pid], tmp / "synthetic", seed=42, start=date(2026, 3, 1),
                      export_date=date(2026, 9, 30))  # fmt: skip
    build_demo_db(tmp / "demo.db", tmp / "synthetic")
    conn = connect(tmp / "demo.db")
    with conn:
        conn.execute("DELETE FROM transactions WHERE posted_date > ?", (DATA_END.isoformat(),))
    conn.close()
    return tmp / "demo.db"


@pytest.fixture
def db(built: Path, tmp_path: Path) -> Path:
    shutil.copy(built, tmp_path / "a.db")
    return tmp_path / "a.db"


def lines_for(db: Path, today: date = TODAY) -> list[str]:
    conn = connect_readonly(db)
    try:
        return ask_check.report(conn, ACCOUNT, today, KEY)
    finally:
        conn.close()


def by_id(lines: list[str]) -> dict[str, str]:
    return {line[:3]: line for line in lines if LINE.match(line)}


def test_every_question_runs_and_agrees_with_the_independent_figure(db: Path) -> None:
    lines = lines_for(db)
    rows = by_id(lines)
    assert len(rows) == len(ask_check.QUESTIONS) == 24
    assert not [line for line in rows.values() if "matches: no" in line]
    assert not [line for line in rows.values() if " error " in line]
    for qid in [f"Q{i:02d}" for i in range(1, 23)]:  # the lookups and the forecast figures
        assert " ran " in rows[qid] and rows[qid].endswith("matches: yes"), rows[qid]
    assert rows["Q23"].endswith("refused     matches: yes")  # no budget saved: refused
    assert rows["Q24"].endswith("refused     matches: yes")  # advice: refused
    assert sum(" fr " in line for line in rows.values()) >= 5


def test_a_saved_budget_is_answered_and_its_spent_so_far_agrees(db: Path) -> None:
    conn = connect(db)
    set_limit(conn, ACCOUNT, "Groceries", 40_000)
    set_limit(conn, ACCOUNT, "Restaurants & cafés", 15_000)
    conn.close()
    row = by_id(lines_for(db))["Q23"]
    assert " ran " in row and row.endswith("matches: yes"), row


def test_a_wrong_figure_shows_no(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_total = ask_check.Facts.total
    monkeypatch.setattr(
        ask_check.Facts, "total", lambda self, span, **kw: real_total(self, span, **kw) + 1
    )
    rows = by_id(lines_for(db))
    assert rows["Q01"].endswith("matches: no")
    assert rows["Q14"].endswith("matches: no")
    assert rows["Q07"].endswith("matches: yes")  # the largest purchase does not use the total


def test_the_output_holds_ids_outcomes_and_yes_no_only(db: Path) -> None:
    lines = lines_for(db)
    assert lines[0] == HEADER
    assert SUMMARY.match(lines[-1])
    assert all(LINE.match(line) for line in lines[1:-1])
    text = "\n".join(lines).casefold()
    conn = connect_readonly(db)
    rows = transactions(conn, ACCOUNT)
    conn.close()
    merchants = {m.casefold() for m in rows["merchant"] if isinstance(m, str) and len(m) >= 4}
    assert merchants and not [m for m in merchants if m in text]
    assert not re.search(r"\d{3,}|\$|\d+[.,]\d\d|\d{4}-\d\d-\d\d", text)  # amounts, dates


def test_the_command_reads_only_and_refuses_a_day_past_the_forecast(
    db: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(KEY_NAME, KEY.hex())  # never the .env file
    monkeypatch.setattr(ask_check.demo_mode, "today", lambda last: date(2026, 11, 26))
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    assert ask_check.main(["--db", str(db)]) == 0
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Account 1 of 3"
    rows = by_id(out)
    assert rows["Q18"].endswith("refused     matches: yes")  # 14 days on is past the forecast
    assert not [line for line in rows.values() if "matches: no" in line]


def test_a_missing_database_or_account_is_refused(tmp_path: Path, db: Path) -> None:
    assert ask_check.main(["--db", str(tmp_path / "none.db")]) == 2
    assert ask_check.main(["--db", str(db), "--account", "4"]) == 2


def test_the_questions_are_fixed_and_cover_both_languages() -> None:
    ids = [q.id for q in ask_check.QUESTIONS]
    assert ids == [f"Q{i:02d}" for i in range(1, 25)]
    assert {q.lang for q in ask_check.QUESTIONS} == {"en", "fr"}
    assert sum(ask_check.MERCHANT in q.text for q in ask_check.QUESTIONS) == 1


def test_no_categories_says_so(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE transactions (account TEXT, posted_date TEXT)")
    assert ask_check.report(conn, ACCOUNT, TODAY, KEY) == [
        "No categories yet: run the categorizer first (Ask needs them)."
    ]
