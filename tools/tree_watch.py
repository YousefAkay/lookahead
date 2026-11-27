"""Which files a test session added, changed or removed in the checkout's output folders.

The merge gate's clean-tree check cannot see git-ignored files, so a test that writes a database
or a build into an ignored folder (`data/ask_eval/`, a stray `*.db`) would pass unnoticed and race
other workers in a parallel run. `tests/conftest.py` takes a snapshot when the session starts and
fails the session if the snapshot differs when it ends.

Only the folders in WATCHED are walked (each fully), plus the files directly in the checkout root
and in `data/`. The real-data, held-out and external-dataset folders are never walked.
"""

import os
from pathlib import Path

WATCHED = ("lookahead", "app", "eval", "tools", "tests", "docs", "models",
           "data/ask_eval", "data/synthetic", "data/synthetic_v1", "data/backtest",
           "data/timemachine", "data/demo", "data/reference")  # fmt: skip
TOP_LEVEL = (".", "data")  # only the files directly in these folders
SKIP_NAMES = {"__pycache__", ".pytest_cache", ".ruff_cache", ".DS_Store"}

Snapshot = dict[str, tuple[int, int]]  # relative path -> (size, modification time in ns)


def _add(snap: Snapshot, root: Path, path: str) -> None:
    try:
        st = os.stat(path)
    except FileNotFoundError:  # removed while we looked
        return
    snap[Path(path).relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)


def snapshot(root: Path) -> Snapshot:
    """Size and modification time of every file in the watched places."""
    snap: Snapshot = {}
    for folder in TOP_LEVEL:
        base = root / folder
        if base.is_dir():
            for entry in os.scandir(base):
                if entry.is_file(follow_symlinks=False) and entry.name not in SKIP_NAMES:
                    _add(snap, root, entry.path)
    for folder in WATCHED:
        for here, dirs, files in os.walk(root / folder):
            dirs[:] = [d for d in dirs if d not in SKIP_NAMES]
            for name in files:
                if name not in SKIP_NAMES:
                    _add(snap, root, os.path.join(here, name))
    return snap


def changes(before: Snapshot, after: Snapshot) -> list[str]:
    """One line per added, changed or removed file, sorted by path."""
    lines = []
    for path in sorted(before.keys() | after.keys()):
        if path not in before:
            lines.append(f"added    {path}")
        elif path not in after:
            lines.append(f"removed  {path}")
        elif before[path] != after[path]:
            lines.append(f"changed  {path}")
    return lines
