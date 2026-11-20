"""The guard on Ask's held-out folder (spec docs/specs/2026-11-20-ask.md, definition of done 10):
the coding session and every agent but the writers are refused, each writer reaches only its own
files, and the code that works on the folder can still be edited."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.heldout_guard import decide, touches

ROOT = Path(__file__).resolve().parents[1]
FOLDER = "/repo/data/" + "ask_" + "heldout"  # built, so this file does not name it in one piece


def call(tool: str, agent: str | None = None, **args: str) -> bool:
    return decide(tool, args, agent)[0]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("Read", {"file_path": f"{FOLDER}/gold_a.jsonl"}),
        ("Write", {"file_path": f"{FOLDER}/questions_agent.jsonl"}),
        ("Edit", {"file_path": f"{FOLDER}/questions_yousef.txt"}),
        ("Bash", {"command": f"cat {FOLDER}/gold_b.jsonl"}),
        ("Bash", {"command": "cat data/ask_*/gold_a.jsonl"}),
        ("Bash", {"command": "cat data/*/gold_a.jsonl"}),
        ("Bash", {"command": "grep -r groceries data/"}),
        ("Bash", {"command": "find data -name '*.jsonl'"}),
        ("Grep", {"pattern": "groceries", "path": "data"}),
        ("Glob", {"pattern": "**/*.jsonl", "path": "data/"}),
        ("Monitor", {"command": f"tail -f {FOLDER}/gold_a.jsonl"}),
    ],
)
def test_the_session_is_refused_every_way_in(tool: str, args: dict[str, str]) -> None:
    assert touches(tool, args)
    assert not decide(tool, args, None)[0]  # the coding session
    assert not decide(tool, args, "reviewer")[0]
    assert not decide(tool, args, "general-purpose")[0]


def test_each_writer_reaches_only_its_own_files() -> None:
    q, a, b = (f"{FOLDER}/{n}" for n in ("questions_agent.jsonl", "gold_a.jsonl", "gold_b.jsonl"))
    assert call("Write", "ask-heldout-questions", file_path=q)
    assert not call("Read", "ask-heldout-questions", file_path=a)
    assert call("Read", "ask-gold-a", file_path=q) and call("Write", "ask-gold-a", file_path=a)
    assert not call("Read", "ask-gold-a", file_path=b)  # never the other gold set
    assert not call("Write", "ask-gold-a", file_path=q)  # never the questions
    assert call("Read", "ask-gold-b", file_path=b) and not call("Read", "ask-gold-b", file_path=a)
    assert call("Bash", "ask-gold-a", command="../lookahead/.venv/bin/python -m eval.ask_heldout "
                                              "check-gold --which a")  # fmt: skip
    assert not call("Bash", "ask-gold-a", command=f"cat {b}")
    assert not call("Grep", "ask-gold-a", pattern="x", path=FOLDER)


def test_the_code_and_other_data_are_not_blocked() -> None:
    assert call("Write", None, file_path="/repo/eval/ask_heldout.py")
    assert call("Read", None, file_path="/repo/tests/test_ask_heldout.py")
    assert call("Bash", None, command="python -m eval.ask_score --planner offline")
    assert call("Read", None, file_path="/repo/data/ask_eval/v2-SAL-01.db")
    assert call("Agent", None, prompt=f"write questions into {FOLDER}")  # starting an agent


def test_the_hook_fails_closed_and_logs_no_content(tmp_path: Path) -> None:
    script = ROOT / "tools" / "heldout_guard.py"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_path)}
    run = subprocess.run([sys.executable, str(script)], input="not json", text=True,
                         capture_output=True, cwd=ROOT, env=env)  # fmt: skip
    assert run.returncode == 2
    payload = {"tool_name": "Read", "tool_input": {"file_path": f"{FOLDER}/secret.jsonl"}}
    run = subprocess.run([sys.executable, str(script)], input=json.dumps(payload), text=True,
                         capture_output=True, cwd=ROOT, env=env)  # fmt: skip
    assert run.returncode == 2
    logged = (tmp_path / "logs" / "heldout-guard.log").read_text()
    assert "secret" not in logged and json.loads(logged)["allowed"] is False
