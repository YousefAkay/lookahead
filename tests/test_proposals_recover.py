"""The real-account veto script (eval.proposals_recover), on a made-up database: counts only."""

import hashlib
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from eval import proposals_recover as pr
from lookahead import recurring_store as rs
from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect
from lookahead.rent import RentRule
from lookahead.setting_proposals import accept, current_proposals

ACCOUNT = "a"
RENT_DAYS = [
    date(2025, 1, 2), date(2025, 2, 3), date(2025, 3, 3), date(2025, 3, 31), date(2025, 5, 2),
    date(2025, 6, 4), date(2025, 7, 1), date(2025, 8, 2), date(2025, 9, 3), date(2025, 9, 30),
    date(2025, 11, 3), date(2025, 12, 1),
]  # fmt: skip
RENT_SIZES = [90000] * 8 + [95000] * 4


def month_day(month: int) -> date:
    year, m = 2025 + (month - 1) // 12, (month - 1) % 12 + 1
    return shift_to_business_day(monthly_date(year, m, 8), "later")


def build(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    n = 0

    def insert(day: date, cents: int, kind: str, text: str) -> None:
        nonlocal n
        n += 1
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, f"fp{n:04d}"),
        )

    for day, cents in zip(RENT_DAYS, RENT_SIZES, strict=True):
        insert(day, -cents, "withdrawal", "Free Interac E-Transfer ")
    for month in range(1, 9):
        insert(month_day(month), -2500, "bill payment", "Fitplace ")
    for month in range(9, 13):
        insert(month_day(month), -2500, "bill payment", "Fitplace Online ")
    for month in (11, 12):
        insert(month_day(month), -1299, "bill payment", "Zenstudio ")
    conn.commit()
    return conn


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    build(path).close()
    return path


def accept_everything(path: Path) -> None:
    conn = connect(path)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    for proposal in current_proposals(conn, ACCOUNT):
        accept(conn, ACCOUNT, proposal, series, date(2026, 1, 5))
    conn.close()


def copy_of(path: Path) -> sqlite3.Connection:
    return pr._in_memory_copy(path)


def test_hand_entries_that_equal_the_proposals_are_all_recovered(db: Path) -> None:
    accept_everything(db)  # what a click would have written: the entries to recover
    assert pr.recover(copy_of(db)) == {"rent": (1, 0), "link": (1, 0), "hand-added": (1, 0)}


def test_a_rent_rule_with_other_amounts_is_not_recovered(db: Path) -> None:
    conn = connect(db)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    rent = next(s for s in series if s.kind == "e-transfer")
    rs.set_rent_rule(conn, ACCOUNT, rent, RentRule((120000,), 1, 4), date(2026, 1, 5))
    conn.close()
    assert pr.recover(copy_of(db))["rent"] == (0, 1)


def test_a_rent_rule_on_other_days_is_not_recovered(
    db: Path,
) -> None:
    conn = connect(db)
    (proposal,) = [p for p in current_proposals(conn, ACCOUNT) if p.kind == "rent"]
    series, _ = rs.series_as_of(conn, ACCOUNT)
    mine = RentRule(proposal.rent.amounts_cents, 1, 4)  # type: ignore[union-attr]
    far = RentRule(proposal.rent.amounts_cents, 15, 19)  # type: ignore[union-attr]
    target = next(s for s in series if s.id == proposal.series_id)
    rs.set_rent_rule(conn, ACCOUNT, target, far, date(2026, 1, 5))
    conn.close()
    assert pr.recover(copy_of(db))["rent"] == (0, 1)
    assert pr.rent_recovered(mine, proposal)
    assert not pr.rent_recovered(far, proposal)


def test_with_no_hand_entries_there_is_nothing_to_check(db: Path) -> None:
    assert pr.recover(copy_of(db)) == {"rent": (0, 0), "link": (0, 0), "hand-added": (0, 0)}


def test_the_script_prints_counts_only_and_leaves_the_database_untouched(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    accept_everything(db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    assert pr.main(["--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before  # read-only
    assert "rent: yes 1, no 0 -> ok" in out and "link: yes 1, no 0 -> ok" in out
    assert "hand-added: yes 1, no 0 -> ok" in out
    for leak in ("Zenstudio", "Fitplace", "sid|", "$", "2025", "2026", "900", "950"):
        assert leak not in out
    assert not re.search(r"\d{4}-\d{2}-\d{2}", out)


def test_a_no_prints_a_veto(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    conn = connect(db)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    rent = next(s for s in series if s.kind == "e-transfer")
    rs.set_rent_rule(conn, ACCOUNT, rent, RentRule((120000,), 1, 4), date(2026, 1, 5))
    conn.close()
    pr.main(["--db", str(db)])
    assert "rent: yes 0, no 1 -> VETO" in capsys.readouterr().out


def test_a_missing_database_is_reported_not_created(tmp_path: Path) -> None:
    assert pr.main(["--db", str(tmp_path / "nope.db")]) == 2
    assert not (tmp_path / "nope.db").exists()


def test_the_hand_entries_are_hidden_from_the_copy_before_the_proposers_run(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accept_everything(db)  # the entries exist in the database
    real = connect(db)
    present = {r[0] for r in real.execute("SELECT DISTINCT action FROM recurring_decisions")}
    assert {"rent", "continue", "add"} <= present
    assert current_proposals(real, ACCOUNT) == []  # with them there, nothing is proposed
    real.close()
    seen: list[set[str]] = []
    original = pr.current_proposals

    def spy(conn: sqlite3.Connection, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        seen.append({r[0] for r in conn.execute("SELECT DISTINCT action FROM recurring_decisions")})
        return original(conn, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pr, "current_proposals", spy)
    result = pr.recover(copy_of(db))
    assert seen and all(not ({"rent", "continue", "add"} & actions) for actions in seen)
    assert result == {"rent": (1, 0), "link": (1, 0), "hand-added": (1, 0)}  # so it can say yes
    after = connect(db)  # the real database still has them
    assert {"rent", "continue", "add"} <= {
        r[0] for r in after.execute("SELECT DISTINCT action FROM recurring_decisions")
    }
    after.close()
