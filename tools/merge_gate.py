"""The merge gate: nothing lands on `main` without a full-suite pass on that exact commit.

- `python -m tools.merge_gate run` runs the full suite in the current folder. Only if pytest's
  own exit code is 0, the worktree has no uncommitted or untracked (non-ignored) file, and HEAD
  did not move, it writes a pass marker `logs/suite-pass/<sha>` (main folder) naming its log,
  which ends with `PYTEST_EXIT=<code>`.
- `python -m tools.merge_gate install` copies this file into the shared `.git/hooks/` and adds a
  check to `pre-push`, so the gate does not change with the branch that is checked out.
- `pre_push(remote, lines)`: git's own pre-push hook. Every push whose destination is
  `refs/heads/main` needs a pass for the pushed commit, whatever the command form or client.
- `gate_reason(command, cwd)`: a Claude Code hook calls it before every shell command. It refuses
  a merge or pull into main without a pass, a merge chained after `cd`/`switch`/`checkout`, a
  `fetch X:main`, a push to main without a pass, and any command that touches the markers.

A pass also covers a later commit when everything changed after the marked one is in
`DOC_ONLY` (Markdown no code or test reads). Renames are listed as a delete plus an add.
"""

import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

DOC_ONLY = re.compile(r"^(DECISIONS\.md|README\.md|docs/HANDOFF\.md|docs/results/[^/]+\.md)$")
SHA = re.compile(r"^[0-9a-f]{40}$")
GIT = re.compile(r"(?:^|[\s;&|(`])git((?:\s+(?:-C|-c)\s+\S+)*)\s+(merge|pull|push|fetch)(?![-\w])"
                 r"([^;&|\n`]*)")  # fmt: skip
MOVES = re.compile(r"(?:^|[\s;&|(])(cd|pushd)\s|git\s+(?:-C\s+\S+\s+)?(switch|checkout)\b")
MAIN_REFS = {"main", "refs/heads/main"}


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def main_folder(cwd: Path) -> Path:
    out = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    common = Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    return common.parent if common is not None and common.name == ".git" else cwd


def marker_dir(cwd: Path) -> Path:
    return main_folder(cwd) / "logs" / "suite-pass"


def _sha(ref: str, cwd: Path) -> str | None:
    out = _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd)
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _branch(cwd: Path) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd).stdout.strip()


def _valid_marker(marker: Path) -> bool:
    """A marker counts only if it names a full sha and its log ends with PYTEST_EXIT=0."""
    if not SHA.match(marker.name):
        return False
    log = marker.parent.parent / marker.read_text(encoding="utf-8").split()[-1]
    if not log.is_file():
        return False
    lines = log.read_text(encoding="utf-8").rstrip().splitlines()
    return bool(lines) and lines[-1] == "PYTEST_EXIT=0"


def passed(sha: str, cwd: Path) -> bool:
    """A valid pass for this commit, or for an ancestor with only DOC_ONLY files changed since."""
    folder = marker_dir(cwd)
    if not folder.is_dir():
        return False
    for marker in folder.iterdir():
        if not _valid_marker(marker):
            continue
        if marker.name == sha:
            return True
        if _git(["merge-base", "--is-ancestor", marker.name, sha], cwd).returncode != 0:
            continue
        changed = _git(["diff", "--no-renames", "--name-only", marker.name, sha], cwd)
        names = [n for n in changed.stdout.splitlines() if n]
        if changed.returncode == 0 and all(DOC_ONLY.match(n) for n in names):
            return True
    return False


def _refuse(sha: str) -> str:
    return (f"merge gate: commit {sha[:10]} has no full-suite pass. Run `python -m "
            "tools.merge_gate run` on it first (pytest must exit 0), then merge in its own "
            "command.")  # fmt: skip


def gate_reason(command: str, cwd: Path) -> str | None:
    """Why the command must not run, or None."""
    if "suite-pass" in command and "tools.merge_gate run" not in command:
        return "merge gate: the pass markers are written only by `python -m tools.merge_gate run`"
    found = list(GIT.finditer(command))
    if not found:
        return None
    for m in found:
        verb, tail = m.group(2), m.group(3)
        where = cwd
        opts = m.group(1).split()
        if "-C" in opts:
            where = (cwd / opts[opts.index("-C") + 1]).resolve()
        if verb in ("merge", "pull") and MOVES.search(command[: m.start()]):
            return "merge gate: run a merge or pull on its own, not after cd/switch/checkout"
        try:
            args = [a for a in shlex.split(tail) if not a.startswith("-")]
        except ValueError:
            return "merge gate: cannot read this git command; run it on its own, simply quoted"
        if verb in ("merge", "pull"):
            if _branch(where) != "main":
                continue
            refs = args if verb == "merge" else args[1:]
            if verb == "pull" or not refs:
                return f"merge gate: no `git {verb}` into main; merge a tested branch instead"
            for ref in refs:
                sha = _sha(ref, where)
                if sha is None:
                    return f"merge gate: cannot resolve {ref!r}; run the merge on its own"
                if not passed(sha, where):
                    return _refuse(sha)
        elif verb == "fetch":
            for spec in args[1:]:
                src, _, dst = spec.lstrip("+").partition(":")
                if dst in MAIN_REFS:
                    if args[0] != ".":
                        return "merge gate: main is updated only from a local tested commit"
                    sha = _sha(src, where)
                    if sha is None or not passed(sha, where):
                        return _refuse(sha or src)
        # pushes are checked by git's own pre-push hook, which sees every form exactly
    return None


def pre_push(remote: str, lines: Iterable[str], cwd: Path) -> str | None:
    """git pre-push: refuse a push to refs/heads/main of a commit without a pass."""
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        _, local_sha, remote_ref, _ = parts
        if remote_ref == "refs/heads/main" and set(local_sha) != {"0"}:
            if not passed(local_sha, cwd):
                return _refuse(local_sha)
    return None


def run(cwd: Path) -> int:
    """The full suite, gated on pytest's own exit code."""

    def dirty() -> bool:
        return bool(_git(["status", "--porcelain", "--untracked-files=normal"], cwd).stdout.strip())

    head = _sha("HEAD", cwd)
    if head is None or dirty():
        print("merge gate: commit (or remove) every changed and untracked file first: the "
              "marker names a commit, and the suite must test exactly that commit")  # fmt: skip
        return 2
    logs = main_folder(cwd) / "logs"
    logs.mkdir(exist_ok=True)
    log = logs / f"suite-{head[:10]}-{datetime.now():%Y%m%d-%H%M%S}.log"
    with log.open("w", encoding="utf-8") as f:
        proc = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                                cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)  # fmt: skip
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            f.write(line)
        code = proc.wait()
        f.write(f"PYTEST_EXIT={code}\n")
    if code != 0:
        print(f"merge gate: NO PASS (pytest exit {code}); nothing may land (log {log.name})")
        return code
    if dirty() or _sha("HEAD", cwd) != head:
        print(f"merge gate: NO PASS (the worktree changed during the run; log {log.name})")
        return 3
    folder = marker_dir(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / head).write_text(f"{datetime.now().isoformat(timespec='seconds')} {log.name}\n",
                               encoding="utf-8")  # fmt: skip
    print(f"merge gate: PASS, commit {head[:10]} may land on main (log {log.name})")
    return 0


PRE_PUSH = """#!/bin/sh
# Written by `python -m tools.merge_gate install`. Each check reads its own copy of git's input
# and any refusal stops the push.
common="$(git rev-parse --path-format=absolute --git-common-dir)"
input="$(cat)"
printf '%s\\n' "$input" | "$common/../.venv/bin/python" "$common/hooks/merge_gate.py" \\
  pre-push "$@" || exit 1
if [ -f "$common/hooks/pre-push-history" ]; then
  printf '%s\\n' "$input" | sh "$common/hooks/pre-push-history" "$@" || exit 1
fi
exit 0
"""


def install(cwd: Path) -> int:
    """Copy this file into the shared hooks folder and make pre-push call it. An existing
    pre-push that is not ours is kept, unchanged, as pre-push-history and still runs."""
    common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    hooks = Path(common.stdout.strip()) / "hooks"
    shutil.copyfile(Path(__file__), hooks / "merge_gate.py")
    pre_push_file, history = hooks / "pre-push", hooks / "pre-push-history"
    if pre_push_file.exists() and "merge_gate.py" not in pre_push_file.read_text("utf-8"):
        if history.exists():
            print("merge gate: pre-push-history already exists; nothing changed")
            return 2
        pre_push_file.rename(history)
    pre_push_file.write_text(PRE_PUSH, encoding="utf-8")
    pre_push_file.chmod(0o755)
    print(f"merge gate: installed in {hooks}")
    return 0


if __name__ == "__main__":
    command = sys.argv[1:2]
    if command == ["run"]:
        sys.exit(run(Path.cwd()))
    if command == ["install"]:
        sys.exit(install(Path.cwd()))
    if command == ["pre-push"]:
        reason = pre_push(sys.argv[2] if len(sys.argv) > 2 else "", sys.stdin, Path.cwd())
        if reason:
            print(reason, file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    print(__doc__)
    sys.exit(2)
