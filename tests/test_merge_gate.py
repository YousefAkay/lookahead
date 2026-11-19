"""The merge gate: main moves only to a commit with a full-suite pass (or Markdown after one)."""

import subprocess
from pathlib import Path

import pytest

from tools import merge_gate as g


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "a.py").write_text("x = 1\n")
    _git(r, "add", "a.py")
    _git(r, "commit", "-q", "-m", "one")
    _git(r, "switch", "-q", "-c", "feature")
    (r / "a.py").write_text("x = 2\n")
    _git(r, "commit", "-q", "-am", "two")
    _git(r, "switch", "-q", "main")
    return r


def _mark(repo: Path, ref: str) -> None:
    folder = g.marker_dir(repo)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / _git(repo, "rev-parse", ref)).write_text("pass\n")


def test_a_merge_without_a_pass_is_refused_and_allowed_after_one(repo: Path) -> None:
    assert g.gate_reason("git merge --ff-only -q feature", repo) is not None
    _mark(repo, "feature")
    assert g.gate_reason("git merge --ff-only -q feature", repo) is None


def test_the_suite_and_the_merge_in_one_command_are_refused(repo: Path) -> None:
    cmd = ".venv/bin/python -m pytest -q | tee log && git merge --ff-only feature"
    assert g.gate_reason(cmd, repo) is not None  # the marker cannot exist yet


def test_markdown_after_a_pass_may_land_code_may_not(repo: Path) -> None:
    _mark(repo, "feature")
    _git(repo, "switch", "-q", "feature")
    (repo / "DECISIONS.md").write_text("note\n")
    _git(repo, "add", "DECISIONS.md")
    _git(repo, "commit", "-q", "-m", "docs")
    _git(repo, "switch", "-q", "main")
    assert g.gate_reason("git merge feature", repo) is None
    _git(repo, "switch", "-q", "feature")
    (repo / "b.py").write_text("y = 1\n")
    _git(repo, "add", "b.py")
    _git(repo, "commit", "-q", "-m", "code")
    _git(repo, "switch", "-q", "main")
    assert g.gate_reason("git merge feature", repo) is not None


def test_push_and_fetch_into_main_are_gated(repo: Path) -> None:
    assert g.gate_reason("git push -q origin main", repo) is not None
    assert g.gate_reason("git fetch . feature:main", repo) is not None
    _mark(repo, "main")
    assert g.gate_reason("git push -q origin main", repo) is None
    assert g.gate_reason("git fetch . feature:main", repo) is not None  # feature is not marked


def test_other_commands_and_other_branches_pass(repo: Path) -> None:
    assert g.gate_reason("git status --short", repo) is None
    assert g.gate_reason("git push origin feature", repo) is None
    _git(repo, "switch", "-q", "feature")
    assert g.gate_reason("git merge main", repo) is None  # main does not move


def test_an_unknown_ref_is_refused(repo: Path) -> None:
    assert g.gate_reason("git merge nosuchbranch", repo) is not None


def test_run_refuses_uncommitted_changes(repo: Path) -> None:
    (repo / "a.py").write_text("x = 3\n")
    assert g.run(repo) == 2
    assert not g.marker_dir(repo).exists()


@pytest.mark.parametrize(("body", "code"), [("assert True", 0), ("assert False", 1)])
def test_run_writes_a_marker_only_when_pytest_exits_0(repo: Path, body: str, code: int) -> None:
    (repo / "test_x.py").write_text(f"def test_x() -> None:\n    {body}\n")
    _git(repo, "add", "test_x.py")
    _git(repo, "commit", "-q", "-m", "a test")
    _git(repo, "rm", "-q", "--cached", "a.py")  # keep the run to the one test file
    _git(repo, "commit", "-q", "-m", "only the test")
    head = _git(repo, "rev-parse", "HEAD")
    assert g.run(repo) == code
    assert (g.marker_dir(repo) / head).exists() is (code == 0)
    log = next((repo / "logs").glob("suite-*.log")).read_text()
    assert log.rstrip().endswith(f"PYTEST_EXIT={code}")
