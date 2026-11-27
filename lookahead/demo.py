"""The synthetic demo database: each v0 persona imported as its own account.

On the public demo every visitor gets a copy of their own (`visit_copy`): the base database is
built once per server (`ensure_base`) and never written after that, so nothing one visitor does
reaches another."""

import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from itertools import pairwise
from pathlib import Path

from lookahead.categorize import Settings, categorize, train
from lookahead.db import connect, connect_readonly
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.labels import freeze_splits
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key
from lookahead.recurring_store import refresh

DEMO_ACCOUNTS = ("synthetic-A", "synthetic-B", "synthetic-C")
DEMO_SETTINGS = Settings(threshold=0.5, synthetic_months=4)
FIRST_ACCOUNT = "synthetic-C"  # the public demo opens on it: it has a fresh low-balance warning
VISIT_ID = re.compile(r"[A-Za-z0-9_-]{12,40}")  # what a visit id in the address may look like
VISIT_HOURS = 6  # a copy nobody used for this long is deleted
MAX_VISITS = 300  # at most this many copies (about 200 MB); the least recently used go first
_BUILD = threading.Lock()  # Streamlit runs every visitor's session in one process


def has_categories(db_path: Path) -> bool:
    """False for a demo database built before step 8 (the page rebuilds it then)."""
    conn = connect_readonly(db_path)
    try:
        return bool(conn.execute("SELECT 1 FROM category_predictions LIMIT 1").fetchone())
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def build_demo_db(db_path: Path, synthetic_dir: Path) -> None:
    """Import persona_A/B/C.csv from `synthetic_dir`. Safe to run again (dedupe)."""
    conn = connect(db_path)
    for account in DEMO_ACCOUNTS:
        export = synthetic_dir / f"persona_{account.removeprefix('synthetic-')}.csv"
        store_exports(conn, account, [(export.name, parse_export(export))], datetime.now())
    # Categories for the demo: one model trained on the generator's answer keys (the demo
    # has no labels of its own), with a fixed flag threshold. Demo only: it knows every
    # synthetic merchant, so it says nothing about real accuracy.
    model, _ = train(conn, DEMO_ACCOUNTS[0], DEMO_SETTINGS)
    for account in DEMO_ACCOUNTS:
        categorize(conn, account, DEMO_SETTINGS, model=model)
    conn.close()


def base_ready(base: Path) -> bool:
    return base.exists() and has_categories(base)


def ensure_base(base: Path, synthetic_dir: Path) -> bool:
    """Build the base demo database if it is missing (or from before categories). Built under a
    lock into a side file that then replaces the base in one step, so a second visitor never
    reads a half-built one. Returns True if it built."""
    if base_ready(base):
        return False
    with _BUILD:
        if base_ready(base):
            return False
        _refuse_private(base)
        building = base.with_name(base.stem + "-building.db")
        _remove(building)
        build_demo_db(building, synthetic_dir)
        os.replace(building, base)
        return True


def new_visit_id() -> str:
    return secrets.token_urlsafe(12)  # 16 letters, digits, - or _


def visits_dir(base: Path) -> Path:
    return base.with_name(base.stem + "-visits")


def visit_copy(base: Path, visit_id: str, *, fresh: bool = False) -> Path:
    """The visitor's own copy of the base, made on first use (or again when `fresh`: "Start the
    demo again"). Each use marks it as used now, so only abandoned copies are cleaned up."""
    if not VISIT_ID.fullmatch(visit_id):
        raise ValueError("not a visit id")
    folder = visits_dir(base)
    _refuse_private(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{visit_id}.db"
    if fresh or not path.exists():
        part = folder / f"{visit_id}.{secrets.token_hex(4)}.part"  # its own, even for two tabs
        source, target = connect_readonly(base), sqlite3.connect(part)
        try:
            source.backup(target)  # a consistent copy even while another visitor reads the base
        finally:
            source.close()
            target.close()
        for suffix in ("-wal", "-shm", "-journal"):  # the old copy's leftovers, never the copy:
            Path(f"{path}{suffix}").unlink(missing_ok=True)  # a reader sees old or new, whole
        os.replace(part, path)
    os.utime(path)
    return path


def forget_old_visits(base: Path, hours: float = VISIT_HOURS, now: float | None = None,
                      keep: int = MAX_VISITS) -> int:  # fmt: skip
    """Delete the visitor copies (and half-made ones) unused for `hours`, then the least recently
    used copies beyond `keep`. A file another visitor moves or deletes meanwhile is skipped.
    Returns how many were deleted."""
    folder = visits_dir(base)
    if not folder.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - hours * 3600
    gone = 0
    copies: list[tuple[float, Path]] = []
    for f in folder.iterdir():
        if f.suffix not in (".db", ".part"):
            continue
        try:
            used = f.stat().st_mtime
        except FileNotFoundError:
            continue
        if used < cutoff:
            _remove(f)
            gone += 1
        elif f.suffix == ".db":
            copies.append((used, f))
    for _, f in sorted(copies)[: max(0, len(copies) - keep)]:
        _remove(f)
        gone += 1
    return gone


def _remove(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _refuse_private(path: Path) -> None:
    if is_in_data_private(path.resolve()):
        raise ValueError("the demo never writes inside a private folder")


def is_in_data_private(path: Path) -> bool:
    """True for a path inside a data/private folder (where real bank data lives), at any depth.
    Only "data" directly followed by "private" counts: macOS keeps its temporary folders under
    /private/var, and those must pass. Compared without case, as macOS file names are."""
    parts = [part.lower() for part in path.parts]
    return any(a == "data" and b == "private" for a, b in pairwise(parts))


def reset_demo_db(db_path: Path, synthetic_dir: Path) -> None:
    """Throw the synthetic demo database away and build it again from the persona files: no
    alerts, planned items, decisions, corrections or changed buffer, every recurring series
    waiting to be reviewed, and the label-review splits frozen so that page opens on a merchant.

    It refuses a path inside a data/private folder and anything that is not a .db file, so it
    can never be pointed at real data by accident."""
    resolved = db_path.resolve()
    if is_in_data_private(resolved):
        raise ValueError(f"refusing to reset a database in a private folder: {resolved.name}")
    if resolved.suffix != ".db":
        raise ValueError("a demo database is a .db file")
    try:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(f"{resolved}{suffix}").unlink(missing_ok=True)
    except PermissionError:  # Windows: the running app holds the file open, so empty it instead
        _empty(resolved)
    build_demo_db(db_path, synthetic_dir)
    conn = connect(db_path)
    try:
        key: bytes | None = pseudonym_key()  # the pages detect with it when it exists
    except PseudonymKeyError:
        key = None
    for account in DEMO_ACCOUNTS:
        freeze_splits(conn, account, seed=7)
        refresh(conn, account, key)  # so Home counts the series before Recurring is opened
    conn.close()


def _empty(path: Path) -> None:
    """Drop every table and view in place (for a database file that cannot be deleted)."""
    conn = sqlite3.connect(path)
    objects = conn.execute(
        "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'view')"
        " AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for kind, name in objects:
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    conn.commit()
    conn.close()


def main() -> None:
    """`python -m lookahead.demo reset`: start the synthetic demo again from a clean state."""
    import argparse

    from lookahead.ui import demo_db_path, synthetic_dir

    parser = argparse.ArgumentParser(prog="python -m lookahead.demo")
    parser.add_argument("command", choices=["reset"])
    parser.parse_args()
    db_path = demo_db_path()
    reset_demo_db(db_path, synthetic_dir())
    print(f"Synthetic demo reset: {db_path}")
    print("Recurring starts with every series to review; Forecast on synthetic-C shows a fresh "
          "alert the first time it opens.")  # fmt: skip


if __name__ == "__main__":
    main()
