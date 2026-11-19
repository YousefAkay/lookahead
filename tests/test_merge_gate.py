"""The merge gate: main moves only to a commit with a full-suite pass (or docs after one)."""

import subprocess
from pathlib import Path

import pytest

from tools import merge_gate as g


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=check)


def _sha(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", ref).stdout.strip()


def _commit(repo: Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / ".gitignore").write_text("logs/\n__pycache__/\n.venv/\n")
    _git(r, "add", ".gitignore")
    _commit(r, "a.py", "x = 1\n")
    _git(r, "switch", "-q", "-c", "feature")
    _commit(r, "a.py", "x = 2\n")
    _git(r, "switch", "-q", "main")
    return r


def _mark(repo: Path, ref: str, exit_line: str = "PYTEST_EXIT=0") -> None:
    sha = _sha(repo, ref)
    logs = repo / "logs"
    (logs / "suite-pass").mkdir(parents=True, exist_ok=True)
    (logs / f"suite-{sha[:10]}.log").write_text(f"1 passed\n{exit_line}\n")
    (logs / "suite-pass" / sha).write_text(f"2026-11-19T00:00:00 suite-{sha[:10]}.log\n")


# --- Local moves of main (the Claude Code hook) ---


def test_a_merge_without_a_pass_is_refused_and_allowed_after_one(repo: Path) -> None:
    assert g.gate_reason("git merge --ff-only -q feature", repo) is not None
    _mark(repo, "feature")
    assert g.gate_reason("git merge --ff-only -q feature", repo) is None


def test_a_marker_whose_log_did_not_pass_does_not_count(repo: Path) -> None:
    _mark(repo, "feature", exit_line="PYTEST_EXIT=1")
    assert g.gate_reason("git merge feature", repo) is not None
    (repo / "logs" / "suite-pass" / "HEAD").write_text("x suite-x.log\n")  # not a sha
    assert not g.passed(_sha(repo, "feature"), repo)


def test_the_suite_and_the_merge_in_one_command_are_refused(repo: Path) -> None:
    cmd = ".venv/bin/python -m pytest -q | tee log && git merge --ff-only feature"
    assert g.gate_reason(cmd, repo) is not None  # the marker cannot exist yet


def test_a_merge_after_cd_or_switch_is_refused(repo: Path) -> None:
    _git(repo, "switch", "-q", "feature")
    assert g.gate_reason("git switch main && git merge other", repo) is not None
    assert g.gate_reason("cd ../x && git merge other", repo) is not None


def test_pull_into_main_and_fetch_from_a_remote_into_main_are_refused(repo: Path) -> None:
    assert g.gate_reason("git pull origin feature", repo) is not None
    assert g.gate_reason("git fetch origin feature:main", repo) is not None
    assert g.gate_reason("git fetch . feature:main", repo) is not None
    _mark(repo, "feature")
    assert g.gate_reason("git fetch . feature:main", repo) is None


def test_reading_commands_and_other_branches_pass(repo: Path) -> None:
    for cmd in ("git status --short", "git merge-base --is-ancestor main feature",
                "git merge-tree --write-tree main feature", "git push origin feature"):  # fmt: skip
        assert g.gate_reason(cmd, repo) is None, cmd
    _git(repo, "switch", "-q", "feature")
    assert g.gate_reason("git merge main", repo) is None  # main does not move


def test_the_markers_are_only_written_by_the_gate(repo: Path) -> None:
    assert g.gate_reason("touch logs/suite-pass/abc", repo) is not None
    assert g.gate_reason("python -m tools.merge_gate run", repo) is None


def test_an_unknown_ref_is_refused(repo: Path) -> None:
    assert g.gate_reason("git merge nosuchbranch", repo) is not None


# --- Which later commits a pass covers ---


def test_allowed_docs_after_a_pass_may_land_code_or_rules_may_not(repo: Path) -> None:
    _mark(repo, "feature")
    _git(repo, "switch", "-q", "feature")
    _commit(repo, "DECISIONS.md", "note\n")
    _commit(repo, "docs/results/x.md", "report\n")
    assert g.passed(_sha(repo, "feature"), repo)
    _commit(repo, "docs/specs/rule.md", "a rule the harness reads\n")
    assert not g.passed(_sha(repo, "feature"), repo)


def test_a_code_file_renamed_to_markdown_is_not_docs(repo: Path) -> None:
    _mark(repo, "feature")
    _git(repo, "switch", "-q", "feature")
    _git(repo, "mv", "a.py", "README.md")
    _git(repo, "commit", "-q", "-m", "rename")
    assert not g.passed(_sha(repo, "feature"), repo)


# --- The push (git's own pre-push hook) ---


def test_pre_push_refuses_main_without_a_pass_in_any_form(repo: Path) -> None:
    sha = _sha(repo, "main")
    z = "0" * 40
    line = f"refs/heads/x {sha} refs/heads/main {z}\n"
    assert g.pre_push("origin", [line], repo) is not None
    assert g.pre_push("origin", [f"refs/heads/main {sha} refs/heads/other {z}\n"], repo) is None
    _mark(repo, "main")
    assert g.pre_push("origin", [line], repo) is None


def test_a_real_push_goes_through_the_installed_hook(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    hooks = repo / ".git" / "hooks"
    (hooks / "pre-push").write_text("#!/bin/sh\nexit 0\n")  # an older hook, kept and still run
    (hooks / "pre-push").chmod(0o755)
    venv = repo / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(Path(__file__).resolve().parents[1] / ".venv/bin/python")
    assert g.install(repo) == 0
    assert (hooks / "pre-push-history").exists()
    refused = _git(repo, "push", "-q", "origin", "HEAD:refs/heads/main", check=False)
    assert refused.returncode != 0 and "merge gate" in refused.stderr
    _mark(repo, "main")
    assert _git(repo, "push", "-q", "origin", "main", check=False).returncode == 0


# --- The run ---


def test_run_refuses_changed_or_untracked_files(repo: Path) -> None:
    (repo / "a.py").write_text("x = 3\n")
    assert g.run(repo) == 2
    _git(repo, "checkout", "-q", "a.py")
    (repo / "new_module.py").write_text("y = 1\n")  # never added: the commit would not have it
    assert g.run(repo) == 2
    assert not (repo / "logs" / "suite-pass").exists()


@pytest.mark.parametrize(("body", "code"), [("assert True", 0), ("assert False", 1)])
def test_run_writes_a_marker_only_when_pytest_exits_0(repo: Path, body: str, code: int) -> None:
    _git(repo, "rm", "-q", "a.py")
    _commit(repo, "test_x.py", f"def test_x() -> None:\n    {body}\n")
    head = _sha(repo, "HEAD")
    assert g.run(repo) == code
    assert ((repo / "logs" / "suite-pass" / head).exists()) is (code == 0)
    assert g.passed(head, repo) is (code == 0)
