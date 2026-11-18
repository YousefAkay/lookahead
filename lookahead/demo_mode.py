"""Demo mode: the app as a public demo on synthetic data (DECISIONS.md, 2026-10-11).

Switched on by the environment variable LOOKAHEAD_DEMO=1 (on Streamlit Community Cloud, a
root-level secret, which Streamlit makes an environment variable when the server starts). In demo
mode only the synthetic personas are offered, `.env` is never read, no API key is needed, the
LLM fallback and CSV import are off, and every page says "Demo with made-up data".

The guard: an audit hook that refuses to open, connect to, list or glob anything inside a
`data/private` folder. It checks the switch at every event, so it does nothing when demo mode is
off, and once installed it stays for the life of the process (audit hooks cannot be removed).
"""

import hashlib
import os
import sys
from typing import Any

ENV = "LOOKAHEAD_DEMO"
ON = ("1", "true", "yes", "on")
BANNER = "Demo with made-up data"
# A fixed, public, made-up key: in demo mode it stands in for PSEUDONYM_KEY so "Pseudonymize text"
# keeps working on the synthetic personas without any secret.
DEMO_KEY = hashlib.sha256(b"lookahead synthetic demo: not a secret").digest()

_GUARDED_EVENTS = ("open", "sqlite3.connect", "os.listdir", "os.scandir", "glob.glob",
                   "glob.glob/2", "pathlib.Path.glob", "pathlib.Path.rglob", "shutil.copyfile",
                   "shutil.copytree")  # fmt: skip
_installed = False


def is_demo() -> bool:
    """True when demo mode is switched on."""
    return os.environ.get(ENV, "").strip().casefold() in ON


def _as_path(value: Any) -> str | None:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    if isinstance(value, str):
        return value
    if hasattr(value, "__fspath__"):
        return str(os.fspath(value))
    return None  # a file descriptor or something that is not a path


def is_private(value: Any) -> bool:
    """True if `value` names something inside a `data/private` folder. No file system call: the
    path is made absolute against the working directory and compared by its parts."""
    text = _as_path(value)
    if not text:
        return False
    if text.startswith("file:"):  # an SQLite URI, such as the read-only connections use
        text = text[5:].split("?", 1)[0]
    parts = [p.casefold() for p in os.path.normpath(os.path.abspath(text)).replace("\\", "/")
             .split("/")]  # fmt: skip
    return any(a == "data" and b == "private" for a, b in zip(parts, parts[1:], strict=False))


def _hook(event: str, args: tuple[Any, ...]) -> None:
    if event in _GUARDED_EVENTS and args and is_demo() and is_private(args[0]):
        raise PermissionError("demo mode never opens anything under data/private")


def install_guard() -> None:
    """Install the audit hook once per process (it acts only while demo mode is on)."""
    global _installed
    if not _installed:
        sys.addaudithook(_hook)
        _installed = True


def enable_if_demo() -> bool:
    """Called by every page and on import of the package: install the guard when demo mode is on.
    Returns whether demo mode is on."""
    if is_demo():
        install_guard()
        return True
    return False
