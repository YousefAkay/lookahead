"""The merge gate: nothing lands on `main` without a full-suite pass on that exact commit.

Two halves:
- `python -m tools.merge_gate run` runs the full suite (pytest) in the current folder. Only if
  pytest's own exit code is 0, the worktree has no uncommitted tracked change, and HEAD did not
  move during the run, it writes a pass marker named after the tested commit
  (`logs/suite-pass/<sha>` in the main folder). The log ends with `PYTEST_EXIT=<code>`.
- `gate_reason(command, cwd)` is called by a Claude Code hook before every shell command. It
  refuses a command that would move or push `main` (`git merge` while on main,
  `git fetch . X:main`, `git push ... main`) unless the commit about to land has a pass marker,
  or every change after a marked ancestor is a Markdown file (DECISIONS, README, specs).

The hook sees the command before it runs, so `pytest ... && git merge ...` in one line is refused:
its marker cannot exist yet.
"""

import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

GIT = re.compile(r"(?:^|[\s;&|(])git\s+((?:-C\s+\S+\s+)?)(merge|push|fetch)\b([^;&|\n]*)")


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


def passed(sha: str, cwd: Path) -> bool:
    """A pass marker for this commit, or for an ancestor with only Markdown changed since."""
    folder = marker_dir(cwd)
    if (folder / sha).exists():
        return True
    for marker in folder.glob("*") if folder.exists() else []:
        if _git(["merge-base", "--is-ancestor", marker.name, sha], cwd).returncode != 0:
            continue
        changed = _git(["diff", "--name-only", marker.name, sha], cwd)
        names = [n for n in changed.stdout.splitlines() if n]
        if changed.returncode == 0 and all(n.endswith(".md") for n in names):
            return True
    return False


def landing_commits(command: str, cwd: Path) -> list[str] | None:
    """The commits a command would put on main, or None when it cannot tell (refused)."""
    out: list[str] = []
    for m in GIT.finditer(command):
        where = cwd
        if m.group(1):
            where = (cwd / shlex.split(m.group(1))[1]).resolve()
        verb = m.group(2)
        try:
            args = [a for a in shlex.split(m.group(3)) if not a.startswith("-")]
        except ValueError:
            return None
        if verb == "merge":
            if _branch(where) != "main" or not args:
                continue
            sha = _sha(args[-1], where)
            if sha is None:
                return None
            out.append(sha)
        elif verb == "fetch":
            for spec in args:
                if spec.lstrip("+").endswith(":main") or spec.endswith(":refs/heads/main"):
                    sha = _sha(spec.lstrip("+").split(":")[0], where)
                    if sha is None:
                        return None
                    out.append(sha)
        else:  # push
            specs = args[1:] if args else []
            mains = [s for s in specs if s == "main" or s.endswith(":main")]
            if not specs and _branch(where) == "main":
                mains = ["main"]
            if "--all" in m.group(3) or "--mirror" in m.group(3):
                mains = ["main"]
            for spec in mains:
                sha = _sha(spec.split(":")[0] or "main", where)
                if sha is None:
                    return None
                out.append(sha)
    return out


def gate_reason(command: str, cwd: Path) -> str | None:
    """Why the command must not run, or None."""
    if not GIT.search(command):
        return None
    commits = landing_commits(command, cwd)
    if commits is None:
        return "merge gate: cannot tell which commit would land on main; run it on its own"
    for sha in commits:
        if not passed(sha, cwd):
            return (f"merge gate: commit {sha[:10]} has no full-suite pass. Run "
                    "`python -m tools.merge_gate run` on it first (pytest must exit 0); a merge "
                    "never follows the suite in the same command.")  # fmt: skip
    return None


def run(cwd: Path) -> int:
    """The full suite, gated on pytest's own exit code."""

    def dirty() -> bool:
        return bool(_git(["status", "--porcelain", "--untracked-files=no"], cwd).stdout.strip())

    head = _sha("HEAD", cwd)
    if head is None or dirty():
        print("merge gate: commit or stash tracked changes first (the marker names a commit)")
        return 2
    logs = main_folder(cwd) / "logs"
    logs.mkdir(exist_ok=True)
    log = logs / f"suite-{head[:10]}-{datetime.now():%Y%m%d-%H%M}.log"
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
    if code == 0 and not dirty() and _sha("HEAD", cwd) == head:
        folder = marker_dir(cwd)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / head).write_text(f"{datetime.now().isoformat(timespec='seconds')} {log.name}\n",
                                   encoding="utf-8")  # fmt: skip
        print(f"merge gate: PASS, commit {head[:10]} may land on main (log {log.name})")
    else:
        print(f"merge gate: NO PASS (pytest exit {code}); nothing may land (log {log.name})")
    return code


if __name__ == "__main__":
    if sys.argv[1:] != ["run"]:
        print(__doc__)
        sys.exit(2)
    sys.exit(run(Path.cwd()))
