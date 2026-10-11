"""`is_private_path` is anchored to the repository's own data/private folder (DECISIONS.md,
2026-10-12, the review of 2026-10-11), and the Time Machine decides what is private by what is in
the database, not by where its file happens to be."""

import shutil
import sqlite3
from pathlib import Path, PurePosixPath

import pytest

from lookahead import merchants
from lookahead.merchants import REPO_ROOT, is_private_path


@pytest.fixture
def strict(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test sandbox: only the repository's own data/private folder counts."""
    monkeypatch.setattr(merchants, "SANDBOXES", [])


def test_only_the_repository_data_private_folder_counts(strict: None) -> None:
    assert is_private_path(REPO_ROOT / "data" / "private" / "x.db")
    assert is_private_path(REPO_ROOT / "data" / "private")
    assert not is_private_path(REPO_ROOT / "data" / "synthetic" / "x.db")
    assert not is_private_path(REPO_ROOT / "tmp" / "data" / "private" / "x.db")  # not anchored
    assert not is_private_path(REPO_ROOT / "data" / "privateer" / "x.db")  # not a prefix match


def test_a_data_private_folder_somewhere_else_is_not_private(
    strict: None, tmp_path: Path
) -> None:  # fmt: skip
    elsewhere = tmp_path / "data" / "private"
    elsewhere.mkdir(parents=True)
    assert not is_private_path(elsewhere / "copy.db")
    assert not is_private_path(Path("private/data/x.txt"))


def test_the_comparison_is_case_sensitive_and_anchored_part_by_part() -> None:
    inside = merchants._inside
    root = PurePosixPath("/repo/data/private").parts
    assert inside(PurePosixPath("/repo/data/private/a/b.db").parts, root)
    assert not inside(PurePosixPath("/repo/Data/Private/a.db").parts, root)
    assert not inside(PurePosixPath("/repo/data/PRIVATE/a.db").parts, root)
    assert not inside(PurePosixPath("/other/repo/data/private/a.db").parts, root)


def test_in_the_test_suite_a_data_private_folder_in_the_temporary_folder_counts(
    tmp_path: Path,
) -> None:  # fmt: skip
    assert is_private_path(tmp_path / "data" / "private" / "x.db")  # the conftest sandbox
    assert not is_private_path(tmp_path / "private" / "x.db")


def test_the_time_machine_does_not_take_a_copy_of_a_private_database_for_synthetic(
    tmp_path: Path,
) -> None:  # fmt: skip
    from lookahead.db import connect
    from lookahead.timemachine import precompute

    folder = tmp_path / "data" / "private"
    folder.mkdir(parents=True)
    conn = connect(folder / "mine.db")
    conn.execute(
        "INSERT INTO transactions (account, posted_date, amount_cents, description,"
        " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
        " VALUES ('my-chequing', '2026-01-02', -500, 'x', 'y', 1000, 'fp1', 'f', '2026-10-01')"
    )
    conn.commit()
    conn.close()
    copy = tmp_path / "somewhere-else.db"  # a copy outside data/private
    shutil.copy(folder / "mine.db", copy)
    c = sqlite3.connect(copy)
    try:
        with pytest.raises(ValueError, match="data/private"):
            precompute(c, "my-chequing", [], tmp_path / "out" / "snap.jsonl")
    finally:
        c.close()
    assert not (tmp_path / "out").exists()
