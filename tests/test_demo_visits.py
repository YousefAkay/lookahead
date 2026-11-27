"""The public demo gives every visitor a copy of their own: nothing one visitor does reaches
another, and the base database stays as it was built. Temporary folders only."""

import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from lookahead import demo


def _fake_base(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE category_predictions (x INTEGER)")
    conn.execute("CREATE TABLE notes (text TEXT)")
    conn.execute("INSERT INTO category_predictions VALUES (1)")
    conn.commit()
    conn.close()
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _notes(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT text FROM notes")]
    finally:
        conn.close()


def _write_note(path: Path, text: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO notes VALUES (?)", (text,))
    conn.commit()
    conn.close()


def test_a_change_in_one_visit_is_not_seen_in_another_and_the_base_is_unchanged(
    tmp_path: Path,
) -> None:
    base = _fake_base(tmp_path / "demo.db")
    before = _digest(base)
    one, two = demo.new_visit_id(), demo.new_visit_id()
    assert one != two
    _write_note(demo.visit_copy(base, one), "moved a row")
    assert _notes(demo.visit_copy(base, one)) == ["moved a row"]  # kept on the next page
    assert _notes(demo.visit_copy(base, two)) == []
    assert _digest(base) == before


def test_start_again_gives_a_fresh_copy(tmp_path: Path) -> None:
    base = _fake_base(tmp_path / "demo.db")
    visit = demo.new_visit_id()
    _write_note(demo.visit_copy(base, visit), "x")
    assert _notes(demo.visit_copy(base, visit, fresh=True)) == []


@pytest.mark.parametrize("visit", ["../../etc/x", "short", "a" * 41, "abc/defghijklmno", ""])
def test_only_well_formed_visit_ids_are_used(tmp_path: Path, visit: str) -> None:
    base = _fake_base(tmp_path / "demo.db")
    with pytest.raises(ValueError):
        demo.visit_copy(base, visit)


def test_a_copy_is_never_made_inside_a_private_folder(tmp_path: Path) -> None:
    base = _fake_base(tmp_path / "data" / "private" / "demo.db")  # a stand-in, in tmp only
    with pytest.raises(ValueError, match="private"):
        demo.visit_copy(base, demo.new_visit_id())
    assert not demo.visits_dir(base).exists()
    with pytest.raises(ValueError, match="private"):
        demo.ensure_base(tmp_path / "data" / "private" / "new.db", tmp_path)


def test_copies_unused_for_six_hours_are_deleted_and_used_ones_kept(tmp_path: Path) -> None:
    base = _fake_base(tmp_path / "demo.db")
    old, recent = demo.new_visit_id(), demo.new_visit_id()
    old_copy, recent_copy = demo.visit_copy(base, old), demo.visit_copy(base, recent)
    seven_hours_ago = time.time() - 7 * 3600
    os.utime(old_copy, (seven_hours_ago, seven_hours_ago))
    half_made = demo.visits_dir(base) / "abandoned1234.part"
    half_made.write_bytes(b"")
    os.utime(half_made, (seven_hours_ago, seven_hours_ago))
    assert demo.forget_old_visits(base) == 2
    assert not old_copy.exists() and not half_made.exists()
    assert recent_copy.exists() and base.exists()


def test_using_a_copy_marks_it_as_used(tmp_path: Path) -> None:
    base = _fake_base(tmp_path / "demo.db")
    visit = demo.new_visit_id()
    copy = demo.visit_copy(base, visit)
    os.utime(copy, (0, 0))
    demo.visit_copy(base, visit)
    assert copy.stat().st_mtime > time.time() - 60


def test_the_base_is_built_once_even_when_two_visitors_arrive_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def slow_build(db_path: Path, synthetic_dir: Path) -> None:
        calls.append(db_path)
        time.sleep(0.2)
        _fake_base(db_path)

    monkeypatch.setattr(demo, "build_demo_db", slow_build)
    base = tmp_path / "demo.db"
    threads = [threading.Thread(target=demo.ensure_base, args=(base, tmp_path)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and calls[0] != base  # built beside it, then moved into place
    assert demo.base_ready(base)
    assert demo.ensure_base(base, tmp_path) is False


def test_beyond_the_cap_the_least_recently_used_copies_go(tmp_path: Path) -> None:
    base = _fake_base(tmp_path / "demo.db")
    visits = [demo.new_visit_id() for _ in range(4)]
    for age, visit in enumerate(visits):  # the first is the most recently used
        copy = demo.visit_copy(base, visit)
        os.utime(copy, (time.time() - 60 * age, time.time() - 60 * age))
    assert demo.forget_old_visits(base, keep=2) == 2
    left = sorted(p.stem for p in demo.visits_dir(base).glob("*.db"))
    assert left == sorted(visits[:2])


def test_a_file_that_vanishes_during_the_sweep_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _fake_base(tmp_path / "demo.db")
    demo.visit_copy(base, demo.new_visit_id())
    real_stat = Path.stat

    def gone_meanwhile(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self.suffix == ".db" and self.parent == demo.visits_dir(base):
            raise FileNotFoundError(self)  # another visitor's copy moved or deleted meanwhile
        return real_stat(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", gone_meanwhile)
    assert demo.forget_old_visits(base) == 0


def test_two_tabs_making_the_same_copy_at_once_both_get_a_whole_copy(tmp_path: Path) -> None:
    base = _fake_base(tmp_path / "demo.db")
    visit = demo.new_visit_id()
    errors: list[BaseException] = []

    def open_tab() -> None:
        try:
            for _ in range(20):
                assert _notes(demo.visit_copy(base, visit, fresh=True)) == []
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    tabs = [threading.Thread(target=open_tab) for _ in range(2)]
    for t in tabs:
        t.start()
    for t in tabs:
        t.join()
    assert errors == []
    assert not list(demo.visits_dir(base).glob("*.part"))  # nothing half-made left
