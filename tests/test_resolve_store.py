"""Tests for stored decisions (merchants tables) and the counts-only resolution commands.

All databases and files are in a temporary folder: never data/private/.
"""

import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.resolve import (
    apply_decisions,
    load_user_decisions,
    main,
    name_infos,
    name_key,
    read_decisions,
)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "t.db")


def merchant_of(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute(
        "SELECT merchant_id FROM merchant_aliases WHERE alias_key = ?", (name_key(name),)
    ).fetchone()
    return None if row is None else row[0]


# --- Storing your decisions ---


def test_merge_creates_a_merchant_with_both_aliases(conn: sqlite3.Connection) -> None:
    counts = apply_decisions(conn, [("merge", "Uber Canada/Ubereats", "UE Delivery")])
    assert counts == {"merges": 1, "splits": 0, "skipped": 0}
    a, b = merchant_of(conn, "Uber Canada/Ubereats"), merchant_of(conn, "UE Delivery")
    assert a is not None and a == b
    row = conn.execute(
        "SELECT canonical_name, source FROM merchants WHERE merchant_id = ?", (a,)
    ).fetchone()
    assert tuple(row) == ("Uber Canada/Ubereats", "user")
    assert load_user_decisions(conn).merges == {
        "Uber Canada/Ubereats": {"uber canada ubereats", "ue delivery"}
    }


def test_merge_into_an_existing_merchant(conn: sqlite3.Connection) -> None:
    apply_decisions(conn, [("merge", "Maxi", "Maxi Verdun")])
    apply_decisions(conn, [("merge", "Maxi Lasalle", "Maxi")])
    assert merchant_of(conn, "Maxi Lasalle") == merchant_of(conn, "Maxi Verdun")
    assert conn.execute("SELECT COUNT(*) FROM merchants").fetchone()[0] == 1


def test_merging_two_merchants_joins_them(conn: sqlite3.Connection) -> None:
    apply_decisions(conn, [("merge", "Maxi", "Maxi Verdun"), ("merge", "Iga", "Iga Extra")])
    apply_decisions(conn, [("merge", "Maxi Verdun", "Iga Extra")])
    assert merchant_of(conn, "Maxi") == merchant_of(conn, "Iga")
    assert conn.execute("SELECT COUNT(*) FROM merchants").fetchone()[0] == 1


def test_split_is_stored_and_undoes_your_earlier_merge(conn: sqlite3.Connection) -> None:
    apply_decisions(conn, [("merge", "Jean Coutu", "Jean Coutou")])
    counts = apply_decisions(conn, [("split", "Jean Coutu", "Jean Coutou")])
    assert counts["splits"] == 1
    # Their merchant had only these two names, so it is removed entirely.
    assert merchant_of(conn, "Jean Coutu") is None and merchant_of(conn, "Jean Coutou") is None
    assert load_user_decisions(conn).splits == {frozenset(("jean coutu", "jean coutou"))}


def test_merge_after_a_split_removes_the_split(conn: sqlite3.Connection) -> None:
    apply_decisions(conn, [("split", "Jean Coutu", "Jean Coutou")])
    apply_decisions(conn, [("merge", "Jean Coutu", "Jean Coutou")])
    assert load_user_decisions(conn).splits == set()


def test_blank_and_unknown_decisions_are_skipped(conn: sqlite3.Connection) -> None:
    counts = apply_decisions(conn, [("", "A Shop", "B Shop"), ("maybe", "A Shop", "B Shop")])
    assert counts == {"merges": 0, "splits": 0, "skipped": 2}


def test_applying_twice_changes_nothing(conn: sqlite3.Connection) -> None:
    rows = [("merge", "Maxi", "Maxi Verdun"), ("split", "Maxi", "Maxi Dollar")]
    apply_decisions(conn, rows)
    before = conn.execute("SELECT * FROM merchant_aliases ORDER BY alias_key").fetchall()
    apply_decisions(conn, rows)
    after = conn.execute("SELECT * FROM merchant_aliases ORDER BY alias_key").fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]


def test_decisions_from_an_old_database_without_the_tables(tmp_path: Path) -> None:
    plain = sqlite3.connect(tmp_path / "old.db")
    assert load_user_decisions(plain).merges == {} and load_user_decisions(plain).splits == set()


# --- The commands (synthetic database) ---


@pytest.fixture
def db(tmp_path: Path) -> Path:
    export, _ = write_persona(
        PERSONAS["C"], tmp_path / "synthetic", 42, date(2026, 6, 1), date(2026, 9, 30)
    )
    path = tmp_path / "db" / "t.db"
    c = connect(path)
    store_exports(c, "synthetic-C", [(export.name, parse_export(export))], datetime(2026, 10, 2))
    c.commit()
    c.close()
    return path


def names_in(db: Path) -> set[str]:
    c = sqlite3.connect(db)
    texts = [r[0] for r in c.execute("SELECT sub_description FROM transactions")]
    c.close()
    return {i.name for i in name_infos(texts)}


def private_file(tmp_path: Path) -> Path:
    return tmp_path / "data" / "private" / "eval" / "proposals.tsv"


def test_report_is_counts_only(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["report", "--db", str(db), "--account", "synthetic-C"])
    out = capsys.readouterr().out
    assert code == 0
    assert re.search(r"names before resolution: \d+", out)
    assert re.search(r"merchant groups after resolution: \d+", out)
    assert re.search(r"pairs waiting for your confirmation: \d+", out)
    assert not re.search(r"\d+\.\d{2}", out) and "$" not in out
    for name in names_in(db):
        assert name not in out, name


def test_write_proposals_puts_names_in_the_file_only(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = private_file(tmp_path)
    main(["report", "--db", str(db), "--account", "synthetic-C", "--write-proposals", str(target)])
    out = capsys.readouterr().out
    assert "wrote" in out and "(not shown here)" in out
    text = target.read_text(encoding="utf-8")
    assert "Uber Canada/Ubereats" in text  # proposed next to "Uber Canada/Ubertrip"
    assert "Uber Canada/Ubereats" not in out


def test_write_proposals_refuses_a_path_outside_data_private(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "proposals.tsv"
    args = ["report", "--db", str(db), "--account", "synthetic-C", "--write-proposals"]
    assert main([*args, str(target)]) == 2 and not target.exists()


def fill_first_decision(path: Path, decision: str) -> tuple[str, str]:
    rows = read_decisions(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#") and not line.startswith("decision\t"):
            lines[i] = decision + line  # first column is the (empty) decision
            break
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    first = rows[0]
    return first[1], first[2]


def test_apply_stores_decisions_and_prints_counts_only(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = private_file(tmp_path)
    main(["report", "--db", str(db), "--account", "synthetic-C", "--write-proposals", str(target)])
    a, b = fill_first_decision(target, "split")
    capsys.readouterr()
    assert main(["apply", "--db", str(db), "--decisions", str(target)]) == 0
    out = capsys.readouterr().out
    assert "stored: merge decisions 0, split decisions 1" in out
    assert a not in out and b not in out
    c = sqlite3.connect(db)
    assert load_user_decisions(c).splits == {frozenset((name_key(a), name_key(b)))}


def test_rewriting_the_file_refuses_while_decisions_are_unstored(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = private_file(tmp_path)
    args = ["report", "--db", str(db), "--account", "synthetic-C", "--write-proposals", str(target)]
    main(args)
    fill_first_decision(target, "split")
    before = target.read_text(encoding="utf-8")
    assert main(args) == 2  # your review work would be lost
    assert target.read_text(encoding="utf-8") == before
    main(["apply", "--db", str(db), "--decisions", str(target)])
    assert main(args) == 0  # stored now, so the file can be rewritten


def waiting(out: str) -> int:
    match = re.search(r"pairs waiting for your confirmation: (\d+)", out)
    assert match, out
    return int(match[1])


def test_report_uses_your_stored_split(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = private_file(tmp_path)
    args = ["report", "--db", str(db), "--account", "synthetic-C"]
    main([*args, "--write-proposals", str(target)])
    first = capsys.readouterr().out
    assert waiting(first) > 0  # e.g. the two Uber names look alike
    fill_first_decision(target, "split")
    main(["apply", "--db", str(db), "--decisions", str(target)])
    capsys.readouterr()
    main(args)
    second = capsys.readouterr().out
    assert "1 pairs kept apart" in second
    assert waiting(second) == waiting(first) - 1  # a pair you decided is not asked again


# --- Merge rows vs merchants (the owner's "10 merges stored, report says 9") ---


def test_two_merge_rows_sharing_a_name_build_one_merchant(conn: sqlite3.Connection) -> None:
    counts = apply_decisions(
        conn, [("merge", "Maxi", "Maxi Verdun"), ("merge", "Maxi Verdun", "Maxi Lasalle")]
    )
    assert counts["merges"] == 2  # two rows processed...
    merges = load_user_decisions(conn).merges
    assert len(merges) == 1  # ...one merchant...
    assert sum(len(keys) for keys in merges.values()) == 3  # ...holding three names


def test_split_of_a_two_name_merge_leaves_no_one_name_merchant(conn: sqlite3.Connection) -> None:
    apply_decisions(conn, [("merge", "Jean Coutu", "Jean Coutou")])
    apply_decisions(conn, [("split", "Jean Coutu", "Jean Coutou")])
    assert load_user_decisions(conn).merges == {}
    assert conn.execute("SELECT COUNT(*) FROM merchants").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM merchant_aliases").fetchone()[0] == 0


def test_split_inside_a_bigger_merge_keeps_the_rest(conn: sqlite3.Connection) -> None:
    apply_decisions(conn, [("merge", "Maxi", "Maxi Verdun"), ("merge", "Maxi", "Maxi Lasalle")])
    apply_decisions(conn, [("split", "Maxi", "Maxi Lasalle")])
    assert load_user_decisions(conn).merges == {"Maxi": {"maxi", "maxi verdun"}}


def test_report_and_apply_name_their_units(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    c = connect(db)
    apply_decisions(c, [("merge", "Maxi", "Maxi Verdun"), ("merge", "Maxi Verdun", "Maxi Lasalle")])
    c.close()
    main(["report", "--db", str(db), "--account", "synthetic-C"])
    out = capsys.readouterr().out
    assert (
        "your decisions stored: 1 merchants built from merges (3 names), 0 pairs kept apart" in out
    )


def test_a_one_name_merchant_from_an_older_database_is_not_a_merge(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        "INSERT INTO merchants (merchant_id, canonical_name, source) VALUES (7, 'Maxi', 'user')"
    )
    conn.execute("INSERT INTO merchant_aliases VALUES ('maxi', 7, 'user')")
    assert load_user_decisions(conn).merges == {}
