"""Demo mode (DECISIONS.md, 2026-10-11): synthetic data only, nothing under data/private can be
opened, no .env, no keys, no import, no LLM, a banner on every page.

The guard is an audit hook, which stays for the life of a process, so the tests that switch demo
mode on for real run in a separate Python process. Temporary folders only."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.navigation import APP_PAGES
from lookahead import demo_mode
from lookahead.demo_mode import DEMO_KEY, is_demo, is_private

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOOKAHEAD_DEMO", "1")


def test_the_switch_is_an_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOOKAHEAD_DEMO", raising=False)
    assert not is_demo()
    for value in ("1", "true", "YES", " on "):
        monkeypatch.setenv("LOOKAHEAD_DEMO", value)
        assert is_demo()
    monkeypatch.setenv("LOOKAHEAD_DEMO", "0")
    assert not is_demo()


@pytest.mark.parametrize("path", [
    "data/private/db/lookahead.db", "./data/private", "/x/data/private/a.csv",
    b"data/private/key", Path("data") / "private" / "models", "DATA/Private/x",
    "file:data/private/db/lookahead.db?mode=ro", "data/synthetic/../private/x",
])  # fmt: skip
def test_paths_inside_data_private_are_private(path: object) -> None:
    assert is_private(path)


@pytest.mark.parametrize("path", ["data/synthetic/demo.db", "data/privates/x", "private/data/x",
                                  "docs/private.md", 3, None, ""])  # fmt: skip
def test_other_paths_are_not(path: object) -> None:
    assert not is_private(path)


def test_in_demo_mode_no_env_file_is_read_and_the_key_is_the_demo_key(
    demo: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lookahead.config import load_env
    from lookahead.pseudonymize import pseudonym_key

    env = tmp_path / ".env"
    env.write_text("PSEUDONYM_KEY=" + "ab" * 32 + "\nANTHROPIC_API_KEY=sk-test\n", encoding="utf-8")
    monkeypatch.setenv("PSEUDONYM_KEY", "cd" * 32)
    assert load_env(env) == {}
    assert pseudonym_key(env) == DEMO_KEY  # neither the file nor the variable is used


def test_in_demo_mode_the_llm_import_and_private_data_are_off(
    demo: None, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    from lookahead import llm_categorizer, llm_fallback
    from lookahead.ingest.__main__ import main as ingest
    from lookahead.ui import private_db_path

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert not llm_fallback.has_api_key()
    with pytest.raises(RuntimeError, match="demo mode"):
        llm_categorizer.make_client()
    with pytest.raises(PermissionError):
        private_db_path()
    (tmp_path / "in").mkdir()
    assert ingest([str(tmp_path / "in"), "--db", str(tmp_path / "x.db")]) == 2
    assert "disabled in demo mode" in capsys.readouterr().out
    assert not (tmp_path / "x.db").exists()


def test_in_demo_mode_the_sources_are_synthetic_and_the_snapshots_committed(demo: None) -> None:
    from app.theme import data_sources
    from eval.report_data import real_summary
    from lookahead.timemachine_snapshots import DEMO_SNAPSHOTS, snapshot_dir

    assert data_sources() == ["Synthetic demo"]
    assert snapshot_dir() == DEMO_SNAPSHOTS
    assert real_summary() is None


def test_outside_demo_mode_nothing_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.theme import data_sources

    monkeypatch.delenv("LOOKAHEAD_DEMO", raising=False)
    assert data_sources() == ["Synthetic demo", "My data"]
    demo_mode._hook("open", ("data/private/x",))  # the hook does nothing when the switch is off


# --- The committed Time Machine snapshots ---


def test_the_committed_snapshots_are_synthetic_and_hold_no_names() -> None:
    from lookahead.generator.archetypes import ALL_HELD_OUT
    from lookahead.timemachine_snapshots import (
        DEMO_PERSONAS,
        DEMO_SNAPSHOTS,
        MIX_PERSONAS,
        available_personas,
    )

    found = available_personas(DEMO_SNAPSHOTS)
    assert set(found) == set(DEMO_PERSONAS) | set(MIX_PERSONAS)
    assert not set(found) & ALL_HELD_OUT
    for pid in found:
        text = (DEMO_SNAPSHOTS / f"{pid}.jsonl").read_text(encoding="utf-8")
        for field in ('"name"', '"series_key"', '"group"', '"account"'):
            assert field not in text
        assert (DEMO_SNAPSHOTS / f"{pid}.db").exists()  # for the what-if
        assert (DEMO_SNAPSHOTS / f"{pid}.jsonl").stat().st_size < 1_000_000
        conn = sqlite3.connect(f"file:{DEMO_SNAPSHOTS / f'{pid}.db'}?mode=ro", uri=True)
        accounts = {r[0] for r in conn.execute("SELECT DISTINCT account FROM transactions")}
        conn.close()
        assert accounts == {pid}  # nothing but the synthetic persona itself


# --- The guard, for real, in its own process ---

GUARD_SCRIPT = r"""
import glob, json, os, sqlite3, sys
from pathlib import Path
import lookahead  # installs the guard when LOOKAHEAD_DEMO is set
from lookahead.db import connect, connect_readonly

tries = {
    "open for writing": lambda: open("data/private/new.txt", "w"),
    "open for reading": lambda: open(os.path.abspath("data/private/db/lookahead.db"), "rb"),
    "pathlib read": lambda: Path("data/private/db/lookahead.db").read_bytes(),
    "sqlite3": lambda: sqlite3.connect("data/private/db/lookahead.db"),
    "sqlite3 uri": lambda: sqlite3.connect("file:data/private/db/lookahead.db?mode=ro", uri=True),
    "lookahead connect": lambda: connect(Path("data/private/db/lookahead.db")),
    "lookahead read-only": lambda: connect_readonly(Path("data/private/db/lookahead.db")),
    "listdir": lambda: os.listdir("data/private"),
    "scandir": lambda: list(os.scandir("data/private/db")),
    "glob": lambda: glob.glob("data/private/*"),
    "pathlib glob": lambda: list(Path("data/private").glob("*")),
    "env file": lambda: lookahead.config.load_env(Path(".env")),
}
import lookahead.config
out = {}
for name, attempt in tries.items():
    try:
        result = attempt()
        out[name] = "refused" if name == "env file" and result == {} else "opened"
    except PermissionError:
        out[name] = "refused"
    except Exception as e:
        out[name] = type(e).__name__
out["synthetic file"] = Path("data/synthetic/ok.txt").read_text()
print(json.dumps(out))
"""


def run_python(code: str, cwd: Path, demo_on: bool = True, timeout: int = 600
               ) -> subprocess.CompletedProcess[str]:  # fmt: skip
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LOOKAHEAD_", "PSEUDONYM"))}
    env["PYTHONPATH"] = str(ROOT)
    if demo_on:
        env["LOOKAHEAD_DEMO"] = "1"
    return subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout)  # fmt: skip


def plant_private(root: Path) -> None:
    """A private database and an .env where demo mode must never look."""
    (root / "data" / "private" / "db").mkdir(parents=True)
    sqlite3.connect(root / "data" / "private" / "db" / "lookahead.db").close()
    (root / "data" / "synthetic").mkdir(parents=True, exist_ok=True)
    (root / "data" / "synthetic" / "ok.txt").write_text("fine", encoding="utf-8")
    (root / ".env").write_text("PSEUDONYM_KEY=" + "ab" * 32 + "\n", encoding="utf-8")


def test_demo_mode_can_never_open_anything_under_data_private(tmp_path: Path) -> None:
    plant_private(tmp_path)
    done = run_python(GUARD_SCRIPT, tmp_path)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out.pop("synthetic file") == "fine"  # other files open as usual
    assert set(out.values()) == {"refused"}, out
    assert not (tmp_path / "data" / "private" / "new.txt").exists()


def test_without_demo_mode_the_guard_does_nothing(tmp_path: Path) -> None:
    plant_private(tmp_path)
    done = run_python(GUARD_SCRIPT, tmp_path, demo_on=False)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["sqlite3"] == "opened" and out["listdir"] == "opened"


PAGES_SCRIPT = r"""
import json, sys
from pathlib import Path
opened = []
sys.addaudithook(lambda e, a: opened.append(str(a[0])) if e in ("open", "sqlite3.connect")
                 and a and isinstance(a[0], (str, bytes, Path)) else None)
from streamlit.testing.v1 import AppTest
from lookahead.demo_mode import BANNER, is_private
pages = ["app/Home.py"] + sorted(str(p) for p in Path("app/pages").glob("*.py"))
out = {}
for page in pages:
    at = AppTest.from_file(page, default_timeout=300).run()
    text = "\n".join(str(m.value) for m in at.markdown)
    out[page] = {"exception": [str(e.value) for e in at.exception],
                 "banner": BANNER in text, "safe to spend": "Safe to spend" in text,
                 "intro": "MADE-UP DATA" in text,
                 "this computer": "worked out on this computer" in text,
                 "time machine dates": "As of " in text,
                 "real date": "Today is " in text or "but before today" in text,
                 "my data offered": any("My data" in str(r.options) for r in at.sidebar.radio)}
out["private opened"] = sorted({p for p in opened if is_private(p)})
out["env opened"] = sorted({p for p in opened if p.endswith(".env")})
print(json.dumps(out))
"""


@pytest.mark.slow
def test_every_page_runs_in_demo_mode_without_env_and_never_opens_private_data(
    tmp_path: Path,
) -> None:
    plant_private(tmp_path)
    (tmp_path / ".env").unlink()  # it starts with no .env present
    for name in ("app", "lookahead", "eval", "docs", ".streamlit"):
        (tmp_path / name).symlink_to(ROOT / name)
    for f in (ROOT / "data" / "synthetic").glob("persona_*.csv"):
        (tmp_path / "data" / "synthetic" / f.name).symlink_to(f)
    (tmp_path / "data" / "demo").symlink_to(ROOT / "data" / "demo")
    done = run_python(PAGES_SCRIPT, tmp_path, timeout=1800)
    assert done.returncode == 0, done.stderr[-3000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out.pop("private opened") == []
    assert out.pop("env opened") == []
    # The router (app/Home.py, which opens Home) and every other page it lists: a page added to
    # the app without being run here fails this line.
    assert set(out) == {"app/Home.py"} | {f"app/{p.file}" for p in APP_PAGES if p.url_path}
    for page, result in out.items():
        assert result["exception"] == [], (page, result["exception"])
        assert result["banner"], page
        assert not result["my data offered"], page
        assert not result["real date"], page  # "today" is the day after the demo data ends
    assert out["app/Home.py"]["safe to spend"]
    assert [page for page, result in out.items() if result["intro"]] == ["app/Home.py"]
    assert not out["app/pages/11_Ask.py"]["this computer"]  # the server works it out
    assert out["app/pages/5_Forecast.py"]["safe to spend"]
    assert out["app/pages/6_Time_machine.py"]["time machine dates"]  # the committed snapshots
    assert not (tmp_path / ".env").exists()


def test_today_is_the_day_after_the_data_in_demo_mode_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import date

    end = date(2026, 10, 1)
    monkeypatch.setenv("LOOKAHEAD_DEMO", "1")
    assert demo_mode.today(end) == date(2026, 10, 2)
    assert demo_mode.today(None) == date.today()  # no data: the real date
    monkeypatch.delenv("LOOKAHEAD_DEMO")
    assert demo_mode.today(end) == date.today()  # my real data: the real clock, unchanged


VISITS_SCRIPT = r"""
import hashlib, json, sqlite3
from pathlib import Path
from streamlit.testing.v1 import AppTest

base = Path("data/synthetic/demo.db")
visits = Path("data/synthetic/demo-visits")

def page(file, visit=None):
    at = AppTest.from_file(file, default_timeout=300)
    if visit:
        at.query_params["visit"] = visit
    return at.run()

def marked(visit):
    conn = sqlite3.connect(visits / f"{visit}.db")
    try:
        return bool(conn.execute("SELECT name FROM sqlite_master WHERE name = 'visitor_mark'")
                    .fetchone())
    finally:
        conn.close()

out = {}
first = page("app/Home.py")
a = first.query_params["visit"]
a = a[0] if isinstance(a, list) else a
out["exceptions"] = [str(e.value) for e in first.exception]
out["account"] = first.sidebar.selectbox[0].value
out["reset button"] = [b.label for b in first.sidebar.button]
digest = hashlib.sha256(base.read_bytes()).hexdigest()
conn = sqlite3.connect(visits / f"{a}.db")  # a change made by visitor A
conn.execute("CREATE TABLE visitor_mark (x INTEGER)")
conn.commit()
conn.close()
again = page("app/pages/5_Forecast.py", a)  # A opens another page: the same copy
out["A keeps its change"] = marked(a)
second = page("app/Home.py")
b = second.query_params["visit"]
b = b[0] if isinstance(b, list) else b
out["B differs"] = b != a
out["B sees A's change"] = marked(b)
out["base unchanged"] = hashlib.sha256(base.read_bytes()).hexdigest() == digest
reset = page("app/Home.py", a)
reset.sidebar.button[0].click().run()
out["A after reset"] = marked(a)
for run in (again, second, reset):
    out["exceptions"] += [str(e.value) for e in run.exception]
print(json.dumps(out))
"""


@pytest.mark.slow
def test_each_visitor_has_their_own_copy_and_can_start_again(tmp_path: Path) -> None:
    plant_private(tmp_path)
    for name in ("app", "lookahead", "eval", "docs", ".streamlit"):
        (tmp_path / name).symlink_to(ROOT / name)
    for f in (ROOT / "data" / "synthetic").glob("persona_*.csv"):
        (tmp_path / "data" / "synthetic" / f.name).symlink_to(f)
    (tmp_path / "data" / "demo").symlink_to(ROOT / "data" / "demo")
    done = run_python(VISITS_SCRIPT, tmp_path, timeout=1800)
    assert done.returncode == 0, done.stderr[-3000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["exceptions"] == []
    assert out["account"] == "synthetic-C"  # the demo opens on the account with a warning
    assert out["reset button"] == ["Start the demo again"]
    assert out["A keeps its change"] and out["B differs"]
    assert not out["B sees A's change"]
    assert out["base unchanged"]
    assert not out["A after reset"]
