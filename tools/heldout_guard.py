"""The guard on Ask's held-out folder (spec docs/specs/2026-11-20-ask.md, step 9).

A local command hook runs `main` before every tool call of the coding session and of every
subagent, with the call as JSON on stdin; exit code 2 blocks it. It blocks any call that names
the held-out folder, or that could reach it through a wildcard or a recursive search of data/,
unless the caller is one of the named writer subagents, and then only for its own files:

- ask-heldout-questions: writes and reads questions_agent.jsonl;
- ask-gold-a / ask-gold-b: read the question files, write and read their own gold file
  (gold_a.jsonl / gold_b.jsonl), never the other one;
- each may also run `python -m eval.ask_heldout` (counts and fingerprints only).

Everything else, the main session and the reviewer included, is blocked. Anything that cannot
be read is blocked (fail closed). The log line keeps only the tool name, the caller's type and
the decision, never what the call contained.
"""

import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

FOLDER = "data/ask_heldout"
NAMES = re.compile(r"ask[\W_]*held", re.IGNORECASE)
# The code that works on the folder is not the folder: its module and test file may be named.
CODE_NAMES = re.compile(r"eval[./\\]ask_heldout(\.py)?\b|tests[/\\]test_ask_heldout\.py",
                        re.IGNORECASE)  # fmt: skip
# A wildcard over data/ (data/*, data/ask_*, data/**), or a recursive look through data/.
WILDCARD = re.compile(r"data[\\/]+(\*|ask_?[*?\[])|data[\\/]+\*\*", re.IGNORECASE)
RECURSIVE = re.compile(r"\b(grep|rg|ag)\b[^|;&]*\s-\w*[rR]|\bfind\b|\bls\b[^|;&]*\s-\w*R|\btar\b|"
                       r"\bcp\b[^|;&]*\s-\w*[rR]|\brsync\b|\bzip\b|\bdu\b")  # fmt: skip
DATA = re.compile(r"(^|[\s\"'=/])data([\\/]|\s|$|[\"'])")
QUESTION_FILES = ("questions_agent.jsonl", "questions_yousef.txt")
WRITERS: dict[str, dict[str, tuple[str, ...]]] = {
    "ask-heldout-questions": {"read": ("questions_agent.jsonl",),
                              "write": ("questions_agent.jsonl",)},
    "ask-gold-a": {"read": (*QUESTION_FILES, "gold_a.jsonl"), "write": ("gold_a.jsonl",)},
    "ask-gold-b": {"read": (*QUESTION_FILES, "gold_b.jsonl"), "write": ("gold_b.jsonl",)},
}  # fmt: skip
ALLOWED_COMMAND = re.compile(r"^\S*python3?\s+-m\s+eval\.ask_heldout(\s+[\w=.\-]+)*\s*$")
# The main repository folder's logs/ (the hook runs a copy from .claude/hooks/).
LOG = (
    Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[1])
    / "logs"
    / ("heldout-guard.log")
)


def _texts(tool: str, args: Mapping[str, Any]) -> list[str]:
    keys = {"Read": ("file_path",), "Write": ("file_path",), "Edit": ("file_path",),
            "MultiEdit": ("file_path",), "NotebookEdit": ("notebook_path",),
            "Grep": ("path", "glob"), "Glob": ("path", "pattern"),
            "Bash": ("command",), "Monitor": ("command",)}  # fmt: skip
    if tool in keys:
        return [str(args.get(k) or "") for k in keys[tool]]
    return [json.dumps(args, default=str)]  # any other tool: everything it was given


# Tools that open no file and run no command: starting or messaging an agent, loading tools.
# An agent's instructions may name the folder; the agent's own calls are checked one by one.
NO_ACCESS = frozenset({"Agent", "Task", "SendMessage", "ListAgents", "TaskStop", "ToolSearch",
                       "Skill", "ScheduleWakeup", "AskUserQuestion", "ReportFindings",
                       "SubagentHandback"})  # fmt: skip


def touches(tool: str, args: Mapping[str, Any]) -> bool:
    """True when the call names the folder or could reach it (wildcards, recursive search of
    data/, a search tool pointed at data/)."""
    if tool in NO_ACCESS:
        return False
    texts = _texts(tool, args)
    joined = CODE_NAMES.sub(" ", " ".join(texts))
    if NAMES.search(joined) or WILDCARD.search(joined):
        return True
    if tool in ("Bash", "Monitor") and RECURSIVE.search(joined) and DATA.search(joined):
        return True
    if tool in ("Grep", "Glob"):
        path = str(args.get("path") or "")
        return bool(re.match(r"^(\./)?data([\\/]|$)", path) or path.rstrip("/").endswith("/data"))
    return False


def decide(tool: str, args: Mapping[str, Any], agent: str | None) -> tuple[bool, str]:
    """(allowed, reason)."""
    if not touches(tool, args):
        return True, "does not reach the held-out folder"
    rights = WRITERS.get(agent or "")
    if rights is None:
        return False, "the held-out folder is blocked for this session and every other agent"
    if tool in ("Bash", "Monitor"):
        command = str(args.get("command") or "").strip()
        if ALLOWED_COMMAND.match(command):
            return True, "the held-out check command"
        return False, "writer agents may only run python -m eval.ask_heldout"
    if tool not in ("Read", "Write", "Edit", "MultiEdit"):
        return False, f"writer agents may not use {tool} on the held-out folder"
    path = str(args.get("file_path") or "").replace("\\", "/")
    name = path.rsplit("/", 1)[-1]
    if not path.endswith(f"{FOLDER}/{name}"):
        return False, "not a file directly in the held-out folder"
    allowed = rights["read"] if tool == "Read" else rights["write"]
    if name in allowed:
        return True, f"{agent} may {'read' if tool == 'Read' else 'write'} {name}"
    return False, f"{agent} may not touch {name}"


def main() -> None:
    try:
        call = json.load(sys.stdin)
        tool = str(call["tool_name"])
        args = call.get("tool_input") or {}
        agent = call.get("agent_type")
    except Exception:
        print("Blocked by heldout_guard: could not read the tool call", file=sys.stderr)
        sys.exit(2)
    allowed, reason = decide(tool, args, agent)
    if touches(tool, args):
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"tool": tool, "agent_type": agent, "allowed": allowed}) + "\n")
        except OSError:
            pass
    if not allowed:
        print(f"Blocked by heldout_guard: {reason}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
