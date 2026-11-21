"""The guard on Ask's held-out folder (spec docs/specs/2026-11-20-ask.md, definition of done 10):
every way in that the review listed is refused for the coding session and for every agent but
the writers, each writer reaches only its own files and its own check command, the printout of
questions and plans is refused for everyone, and the code that works on the folder can still be
edited, tested and searched."""

import re
from pathlib import Path

import pytest

from tools.heldout_guard import checkout_is_clean, decide, reaches, run_folder

ROOT = Path(__file__).resolve().parents[1]
NAME = "ask_" + "heldout"  # built, so this file does not name the folder in one piece
FOLDER = f"data/{NAME}"
ABS = f"/Users/someone/lookahead/{FOLDER}"
PY = "../lookahead/.venv/bin/python"
OTHERS = (None, "reviewer", "general-purpose", "Explore", "heldout-gold-a")


def allowed(kind: str, text: str, agent: str | None = None) -> bool:
    return decide(kind, text, agent).allowed


SPELLINGS = [
    f"{FOLDER}/gold_a.jsonl",
    f"{ABS}/questions_agent.jsonl",
    "data/Ask_HeldOut/gold_a.jsonl",
    "data/ask-heldout/gold_a.jsonl",
    "data/ask heldout/gold_a.jsonl",
    "data/askheldout/gold_a.jsonl",
    "data/a''sk_he\"\"ldout/gold_a.jsonl",
    "data/ask\\_heldout/gold_a.jsonl",
    "data/ASK.HELD" + "OUT/x",
    "data/ask__held/x",
]


@pytest.mark.parametrize("path", SPELLINGS)
def test_any_spelling_of_the_folder_is_refused(path: str) -> None:
    for agent in (None, "reviewer", "general-purpose"):
        assert not allowed("read", path, agent)
        assert not allowed("write", path, agent)
        assert not allowed("command", f"cat {path}", agent)
        assert not allowed("other", f'{{"path": "{path}"}}', agent)


@pytest.mark.parametrize(
    "command",
    [
        "cat data/*/gold_a.jsonl",
        "cat data/ask_*/gold_a.jsonl",
        "cat data/a*/x",
        "ls data/**",
        "cat data/ask_h?ldout/x",
        "cat data/[a]sk_x/y",
        "cat data/{a,b}/x",
        "cat data/$D/x",
        "head data//*",
        "cat ../lookahead/data/*/x",
    ],
)
def test_wildcards_and_variables_over_data_are_refused(command: str) -> None:
    for agent in OTHERS:
        assert not allowed("command", command, agent)


@pytest.mark.parametrize(
    "command",
    [
        "cd data",
        "cd ./data && cat x",
        "cd ../lookahead/data",
        "cd 'da'ta",
        "pushd data",
        "ls; cd data; cat x",
    ],
)
def test_cd_into_data_is_refused(command: str) -> None:
    for agent in OTHERS:
        assert not allowed("command", command, agent)


@pytest.mark.parametrize(
    "command",
    [
        "grep -r groceries .",
        "grep -r groceries",  # no folder: the current one
        "grep -rn groceries /",
        "grep -r tests",  # the search text looks like a code folder, but there is no folder
        "grep -r x tests /Users/someone",  # one code folder is not enough
        "grep -r x ~",
        "grep -r x $HOME",
        "grep -r x ../lookahead",
        "grep -r x tests/../data",
        "grep -R x lookahead data",
        "egrep -r x .",
        "grep --recursive x .",
        "find .",
        "find",
        "find tests /Users/someone -name x",
        "find . -name '*.jsonl'",
        "find data -name '*.jsonl'",
        "rg groceries",
        "rg groceries data",
        "ag x",
        "ls -R",
        "ls -laR .",
        "tree",
        "du -a",
        "tar czf out.tgz .",
        "zip -r out.zip .",
        "cp -r data /tmp/x",
        "rsync -a data/ /tmp/x",
        "echo / | xargs grep -rl x",
        "cd tests && grep -r x ..",
        "git status; find . -type f",
        "sudo find /",
        "FOO=1 rg x",
        "grep -r -e x",  # text given by -e, still no folder
        "grep -r x /Users/someone/lookahead/data/lookahead-x/tests",  # nested under data
    ],
)
def test_walks_from_the_root_or_with_no_folder_are_refused(command: str) -> None:
    assert reaches("command", command)
    for agent in OTHERS:
        assert not allowed("command", command, agent)


@pytest.mark.parametrize(
    "folder",
    ["", ".", "..", "data", "data/", "/", "~", "/Users/someone", "/Users/someone/lookahead",
     "../lookahead", "tests/../data", "$HOME"],
)  # fmt: skip
def test_searches_outside_the_code_folders_are_refused(folder: str) -> None:
    for agent in OTHERS:
        assert not allowed("search", folder, agent)


@pytest.mark.parametrize("pattern", ["../data/**", "/abs/**", "~/x", "../*/*", "data/*", "$D/*"])
def test_search_patterns_that_climb_out_are_refused(pattern: str) -> None:
    for agent in OTHERS:
        assert not allowed("pattern", pattern, agent)


def test_the_printout_is_refused_for_everyone() -> None:
    for agent in (None, "reviewer", "heldout-questions", "heldout-gold-a", "heldout-gold-b"):
        assert not allowed("command", f"{PY} -m eval.ask_heldout disagreements", agent)
        assert not allowed("command", f"cd /x/lookahead && {PY} -m eval.ask_heldout disagreements",
                           agent)  # fmt: skip


def test_the_session_may_run_only_the_fingerprint() -> None:
    assert allowed("command", f"{PY} -m eval.ask_heldout fingerprint")
    assert allowed("command", f"cd /x/lookahead && {PY} -m eval.ask_heldout fingerprint")
    for sub in ("check-questions", "check-gold --which a", "check-gold --which b"):
        assert not allowed("command", f"{PY} -m eval.ask_heldout {sub}")
    assert not allowed("command", f"{PY} -m eval.ask_heldout fingerprint && cat x")
    assert not allowed("command", f"{PY} eval/{NAME}.py fingerprint")  # only the -m form
    assert not allowed("command", f'{PY} -c "import eval.{NAME}"')
    assert not allowed("command", f"{PY} -m eval.ask_heldout fingerprint", "reviewer")


def test_each_writer_reaches_only_its_own_files() -> None:
    q, a, b, y = (f"{ABS}/{n}" for n in ("questions_agent.jsonl", "gold_a.jsonl",
                                         "gold_b.jsonl", "questions_agent_b.jsonl"))  # fmt: skip
    assert allowed("write", q, "heldout-questions") and allowed("read", q, "heldout-questions")
    assert not allowed("read", a, "heldout-questions")
    # The two question writers never see each other's questions.
    assert not allowed("read", y, "heldout-questions")
    assert allowed("write", y, "heldout-questions-b") and allowed("read", y, "heldout-questions-b")
    assert not allowed("read", q, "heldout-questions-b")
    assert not allowed("write", q, "heldout-questions-b")
    assert not allowed("read", a, "heldout-questions-b")
    for who, own, other in (("heldout-gold-a", a, b), ("heldout-gold-b", b, a)):
        own_name, other_name = own.rsplit("/", 1)[1], other.rsplit("/", 1)[1]
        assert allowed("read", q, who) and allowed("read", y, who)
        assert allowed("write", own, who) and allowed("read", own, who)
        assert not allowed("read", other, who)  # never the other gold set
        assert not allowed("write", other, who)
        assert not allowed("write", q, who)  # never the questions
        assert not allowed("read", f"{ABS}/sub/{own_name}", who)  # only directly in the folder
        assert not allowed("read", f"{ABS}/../{NAME}/{other_name}", who)
        assert not allowed("command", f"cat {own}", who)  # files are read only by path
        assert not allowed("search", ABS, who)
    assert not allowed("read", f"{ABS}/notes.txt", "heldout-gold-a")
    # The gold writers never see the review; the fixer sees it, writes only the fixes.
    for who in ("heldout-gold-a", "heldout-gold-b", "heldout-questions", "heldout-questions-b"):
        assert not allowed("read", f"{ABS}/answers.txt", who)
    fixer = "heldout-fixer"
    for name in ("questions_agent.jsonl", "questions_agent_b.jsonl", "gold_a.jsonl",
                 "gold_b.jsonl", "answers.txt", "review_cases.json",
                 "gold_fixes.jsonl"):  # fmt: skip
        assert allowed("read", f"{ABS}/{name}", fixer)
    assert allowed("write", f"{ABS}/gold_fixes.jsonl", fixer)
    for name in (
        "gold_a.jsonl",
        "gold_b.jsonl",
        "gold_final.jsonl",
        "answers.txt",
        "review.txt",
        "review_cases.json",
    ):
        assert not allowed("write", f"{ABS}/{name}", fixer)
    assert not allowed("read", f"{ABS}/review.txt", fixer)  # the plans' text, for Yousef only


def test_each_writer_runs_only_its_own_check() -> None:
    cd = "cd /Users/someone/lookahead && "
    own = {"heldout-questions": "check-questions --file a",
           "heldout-questions-b": "check-questions --file b",
           "heldout-gold-a": "check-gold --which a", "heldout-gold-b": "check-gold --which b",
           "heldout-fixer": "check-fixes"}  # fmt: skip
    every = {*own.values(), "apply-review", "check-final", "disagreements", "check-questions",
             "fingerprint"}  # fmt: skip
    for who, sub in own.items():
        assert allowed("command", f"{cd}{PY} -m eval.ask_heldout {sub}", who)
        if not who.startswith("heldout-questions"):  # question writers: their own counts only
            assert allowed("command", f"{cd}{PY} -m eval.ask_heldout fingerprint", who)
        for other in every - {sub, "fingerprint"}:
            assert not allowed("command", f"{cd}{PY} -m eval.ask_heldout {other}", who)
    for who in ("heldout-questions", "heldout-questions-b"):
        assert not allowed("command", f"{cd}{PY} -m eval.ask_heldout fingerprint", who)
    # The session: counts, ids and hashes only; never a writer's check or the review text.
    for sub in ("fingerprint", "apply-review", "check-final"):
        assert allowed("command", f"{cd}{PY} -m eval.ask_heldout {sub}")
    for sub in ("disagreements", "check-fixes", "check-questions --file a", "check-gold --which a"):
        assert not allowed("command", f"{cd}{PY} -m eval.ask_heldout {sub}")


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("write", f"/repo/eval/{NAME}.py"),
        ("read", f"/repo/tests/test_{NAME}.py"),
        ("read", "/repo/data/ask_eval/v2-SAL-01.db"),
        ("command", f"ruff check eval/{NAME}.py tests/test_{NAME}.py"),
        ("command", f"pytest -q tests/test_{NAME}.py"),
        ("command", "rg foo -g '*.py' lookahead"),
        ("command", "grep -r -A 3 foo lookahead"),
        ("search", "DECISIONS.md"),
        ("search", f"eval/{NAME}.py"),
        ("search", "/Users/someone/lookahead-x/README.md"),
        ("command", "python -m eval.ask_score --planner offline"),
        ("command", 'python -m pytest -m "not slow" -q'),
        ("command", "git status --short && git log --oneline -3"),
        ("command", "grep -rn groceries lookahead tests"),
        ("command", "grep -rn -e groceries ../lookahead-ask-llm/eval"),
        ("command", "rg groceries app eval"),
        ("command", "find tests -name '*.py'"),
        ("command", "ls -R docs"),
        ("command", "ls data"),
        ("command", "echo find this"),
        ("search", "lookahead"),
        ("search", "/Users/someone/lookahead-ask-llm/tests"),
        ("search", "lookahead/ask"),
        ("pattern", "**/*.py"),
    ],
)
def test_the_code_and_other_data_are_not_blocked(kind: str, text: str) -> None:
    assert allowed(kind, text)


def test_the_guard_names_no_assistant_tools() -> None:
    """The guard works on kinds of access; the hook outside the repository maps tools to kinds."""
    words = ("read", "write", "edit", "multiedit", "grep", "glob", "bash", "monitor", "agent",
             "notebookedit", "claude", "task")  # fmt: skip
    source = (ROOT / "tools" / "heldout_guard.py").read_text(encoding="utf-8")
    for w in words:  # a tool's name is capitalized; the plain words (read, write) are fine
        assert not re.search(rf"\b{w[0].upper()}{w[1:]}\b", source), w
        assert not re.search(rf"\b{w}\b", source, re.IGNORECASE) or w in (
            "read", "write", "edit", "grep", "glob", "task", "agent", "monitor"
        ), w  # fmt: skip


@pytest.mark.parametrize(
    "command",
    [
        f"PYTHONPATH=. {PY} -u eval/{NAME}.py disagreements",
        f"{PY} -c \"import runpy,sys; sys.argv=['x','disagreements']; "
        f"runpy.run_path('eval/{NAME}.py', run_name='__main__')\"",
        f"python3.13 eval/{NAME}.py disagreements",
        f"python eval/{NAME}.py fingerprint",
        f"{PY} -m pytest -q tests/test_{NAME}.py",  # an interpreter: the file name stays in
    ],
)
def test_the_module_runs_only_in_the_m_form(command: str) -> None:
    """Review B1 (2026-11-21): an interpreter option before the file, runpy, or python3.13 ran
    the printout. A command that runs an interpreter keeps the file name in the check."""
    for agent in (None, "reviewer", "heldout-gold-a"):
        assert not allowed("command", command, agent)


@pytest.mark.parametrize(
    "command",
    ["cd da* && cat */q*", "cd $D", "mv data tests/d", "ln -s ../data tests/d"],
)
def test_cd_to_a_wildcard_and_moving_data_are_refused(command: str) -> None:
    for agent in OTHERS:
        assert not allowed("command", command, agent)


@pytest.mark.parametrize(
    "prefix",
    ["{cat,x};", "cat x;", "$(cat x)", "`cat x`", "a|", "x&", "{a,b}/"],
)
def test_nothing_rides_in_front_of_an_allowed_command(prefix: str) -> None:
    """Review B1a (2026-11-21): the allowed command is matched on the text as typed, with plain
    path characters only, so no shell text can be attached to it."""
    module = "eval.ask_" + "heldout"
    for agent, sub in ((None, "fingerprint"), ("heldout-gold-a", "check-gold --which a")):
        assert not allowed("command", f"{prefix}python -m {module} {sub}", agent)
        assert not allowed("command", f"cd /x;{prefix} && python -m {module} {sub}", agent)
        assert not allowed("command", f"cd {prefix}x && python -m {module} {sub}", agent)
        assert not allowed("command", f"python -m {module} {sub};{prefix}", agent)


def test_harmless_cd_and_moves_are_allowed() -> None:
    assert allowed("command", "cd ~/Documents/PersonalProjects/lookahead-x && git status")
    assert allowed("command", "git mv lookahead/data.py lookahead/rows.py")
    assert allowed("command", "mv x.txt data.csv")


def test_a_cd_into_data_refuses_even_an_allowed_command() -> None:
    """Review round 3, N1: the allowed command cannot start inside the folder."""
    module = "eval.ask_" + "heldout"
    for agent, sub in ((None, "fingerprint"), ("heldout-gold-a", "check-gold --which a")):
        assert not allowed("command", f"cd {FOLDER} && python -m {module} {sub}", agent)
        assert not allowed("command", f"cd data && python -m {module} {sub}", agent)


def test_the_check_commands_run_only_on_committed_code(tmp_path: Path) -> None:
    """Review round 3, N2: the hook asks `checkout_is_clean` for the folder an allowed command
    runs in; an uncommitted edit under eval/ (or untracked code) makes it refuse."""
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "m.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("n\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "c")
    assert checkout_is_clean(tmp_path)
    (tmp_path / "notes.md").write_text("changed\n", encoding="utf-8")  # not code
    assert checkout_is_clean(tmp_path)
    (tmp_path / "eval" / "m.py").write_text("x = 2\n", encoding="utf-8")
    assert not checkout_is_clean(tmp_path)
    git("checkout", "-q", "--", "eval")
    (tmp_path / "eval" / "new.py").write_text("y = 1\n", encoding="utf-8")
    assert not checkout_is_clean(tmp_path)
    assert not checkout_is_clean(tmp_path / "missing")  # cannot check: not clean


def test_the_run_folder_is_the_cd_target_or_the_calls_folder() -> None:
    module = "eval.ask_" + "heldout"
    assert run_folder(f"cd /a/b && python -m {module} fingerprint", "/x") == Path("/a/b")
    assert run_folder(f"cd ../w && python -m {module} fingerprint", "/x/y") == Path("/x/y/../w")
    assert run_folder(f"python -m {module} fingerprint", "/x") == Path("/x")
