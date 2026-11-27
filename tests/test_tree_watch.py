"""The output-folder snapshot that fails a test session which wrote into the checkout."""

import os
from pathlib import Path

from tools import tree_watch as tw


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_added_changed_and_removed_files_are_listed(tmp_path: Path) -> None:
    _write(tmp_path / "data" / "ask_eval" / "p1.db", "a")
    _write(tmp_path / "lookahead" / "x.py", "b")
    before = tw.snapshot(tmp_path)
    _write(tmp_path / "data" / "ask_eval" / "p2.db", "new")
    _write(tmp_path / "lookahead" / "x.py", "bb")
    (tmp_path / "data" / "ask_eval" / "p1.db").unlink()
    assert tw.changes(before, tw.snapshot(tmp_path)) == [
        "removed  data/ask_eval/p1.db", "added    data/ask_eval/p2.db", "changed  lookahead/x.py",
    ]  # fmt: skip


def test_a_rewrite_with_the_same_size_is_seen_by_its_time(tmp_path: Path) -> None:
    f = tmp_path / "data" / "demo" / "demo.db"
    _write(f, "aaaa")
    before = tw.snapshot(tmp_path)
    os.utime(f, ns=(1, 1))
    assert tw.changes(before, tw.snapshot(tmp_path)) == ["changed  data/demo/demo.db"]


def test_stray_files_at_the_top_and_in_data_are_seen_but_not_deeper(tmp_path: Path) -> None:
    before = tw.snapshot(tmp_path)
    _write(tmp_path / "stray.db", "x")
    _write(tmp_path / "data" / "stray.db", "x")
    _write(tmp_path / "data" / "unwatched" / "deep.db", "x")  # folders not listed are not walked
    assert tw.changes(before, tw.snapshot(tmp_path)) == ["added    data/stray.db",
                                                          "added    stray.db"]  # fmt: skip


def test_caches_are_ignored(tmp_path: Path) -> None:
    before = tw.snapshot(tmp_path)
    _write(tmp_path / "lookahead" / "__pycache__" / "x.pyc", "x")
    _write(tmp_path / "tests" / ".DS_Store", "x")
    assert tw.changes(before, tw.snapshot(tmp_path)) == []


def test_private_and_external_data_folders_are_never_walked() -> None:
    for folder in tw.WATCHED:
        assert folder.split("/")[0] != "data" or folder.split("/")[1] in {
            "ask_eval", "synthetic", "synthetic_v1", "backtest", "timemachine", "demo",
            "reference"}, folder  # fmt: skip
    assert tw.TOP_LEVEL == (".", "data")  # the files directly in them, never their subfolders


def test_files_in_private_external_and_secret_places_never_enter_the_snapshot(
    tmp_path: Path,
) -> None:
    for name in ("data/private/x.csv", "data/berka/x.csv", "data/public/x.csv", ".env",
                 "DECISIONS-private.md"):  # fmt: skip
        _write(tmp_path / name, "x")
    assert tw.snapshot(tmp_path) == {}
