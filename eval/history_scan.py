"""Scan the whole git history for things that should not be public. Report only: changes nothing.

Every file ever committed, on every branch, is read once per distinct content (blob), and the
first commit and path where that content appeared is reported. Commit author and committer
emails are listed too. Findings are masked: the report never prints a match in full.

What it looks for:
- email addresses, phone numbers (North American shapes);
- account-number-like digit runs (8 to 19 digits, not part of a decimal number, a date or a hex
  hash) and card-number-like groups;
- secret-like strings (API keys, private keys, a filled-in PSEUDONYM_KEY or API key line);
- possible people's names: a common first name followed by a capitalized word, when neither word
  is in the synthetic vocabulary (every capitalized word in any version of the generator code);
- dollar amounts added to DECISIONS.md, commit by commit, flagged when the line talks about my
  own data;
- files under data/private, .env files, databases, model files and keys ever committed;
- large files (1 MB and up);
- mentions of an AI assistant or its vendor, in files and in commit messages.
Commit messages are checked for emails, phone numbers, names and secrets too.

    python -m eval.history_scan                # writes docs/results/history_scan.md
    python -m eval.history_scan --repo <path> --out <file>
"""

import argparse
import difflib
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

LARGE_BYTES = 1_000_000
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<![\d$.])(?:\+?1[ .-]?)?\(?[2-9]\d{2}\)?[ .-]\d{3}[ .-]\d{4}(?![\d.])")
DIGITS = re.compile(r"(?<![\w.$-])\d{8,19}(?![\w.])")
CARD = re.compile(r"(?<!\d)\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,4}(?!\d)")
SECRETS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?:PSEUDONYM_KEY|API_KEY|SECRET|TOKEN)\s*=\s*['\"]?[A-Za-z0-9+/_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
)
# Assistant and vendor names, stored reversed so this file is not itself a mention.
_AI_NAMES = [name[::-1] for name in ("edoC edualC", "edualC", "ciporhtnA", "TPGtahC", "IAnepO",
                                     "tolipoC", "inimeG")]  # fmt: skip
AI_MENTION = re.compile(r"\b(" + "|".join(_AI_NAMES) + r")\b")
DOLLARS = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
REAL_WORDS = re.compile(r"\b(my data|my account|my own|real data|real account|my export|"
                        r"on my|from my|private log)\b", re.IGNORECASE)  # fmt: skip
CAPITALIZED = re.compile(r"\b[A-Z][a-z]{1,15}\b")
NAME_PAIR = re.compile(r"\b([A-Z][a-z]{1,15})[ ]+([A-Z][a-z]{1,15})\b")
RISKY_PATH = (
    (re.compile(r"(^|/)data/private/"), "under data/private"),
    (re.compile(r"(^|/)\.env$"), ".env file"),
    (re.compile(r"\.(db|sqlite3?)$"), "database file"),
    (re.compile(r"\.(joblib|pkl|pickle)$"), "model file"),
    (re.compile(r"\.(pem|key|p12|pfx)$"), "key file"),
)
BINARY_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".woff", ".woff2", ".ico", ".pdf", ".npz")
SYNTHETIC_SOURCES = ("lookahead/generator/", "lookahead/lexicon.py", "lookahead/merchants.py",
                     "tests/")  # fmt: skip
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "users.noreply.github.com")

# Common first names (several languages). A capitalized word after one of these is a possible
# surname. A heuristic, not proof.
FIRST_NAMES = frozenset("""
Aaron Abdul Adam Adrien Ahmed Aisha Alain Alex Alexandre Alexis Ali Alice Amanda Amelie Amir Amy
Ana Andre Andrea Andrew Angela Anna Anne Anthony Antoine Arnaud Arthur Ashley Benjamin Bernard
Brian Bruno Camille Carlos Caroline Catherine Charles Charlotte Chen Chloe Chris Christian
Christine Christopher Claire Clement Daniel David Denis Diana Diego Dominique Elena Elias
Elie Elizabeth Emily Emma Eric Etienne Fatima Felix Fernando Francis Francois Frederic Gabriel
George Georges Guillaume Hadi Hamza Hana Hannah Hassan Helene Henri Hugo Ibrahim Isabelle Jack
Jacques James Jasmine Jason Jean Jennifer Jessica Joao John Jonathan Jose Joseph Joshua Julia
Julie Julien Karim Karine Kevin Khalid Laura Laurent Layla Leila Lina Linda Lisa Louis Lucas
Lucie Luis Mahmoud Manon Marc Marco Maria Marie Mark Martin Mary Mathieu Matthew Maxime Mehdi
Melanie Michael Michel Michelle Mohamed Mohammed Mona Mustafa Nadia Natalie Nathalie Nicolas
Nicole Nour Olivier Omar Pablo Patrick Paul Pedro Philippe Pierre Priya Rachel Rahul Rami Rana
Raphael Rebecca Richard Robert Sami Samir Samuel Sandra Sara Sarah Sebastien Simon Sofia Sophie
Stephane Stephanie Steven Sylvie Thomas Valerie Victor Vincent Wei William Xavier Yann Yasmine
Yousef Youssef Yusuf Zainab Zhang Ziad
""".split())  # fmt: skip


@dataclass
class Finding:
    kind: str
    masked: str
    commit: str
    path: str
    line: int | None = None
    note: str = ""


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    blobs_scanned: int = 0
    commits: int = 0
    branches: list[str] = field(default_factory=list)
    authors: dict[str, int] = field(default_factory=dict)


def mask(text: str) -> str:
    """Keep the first character and the shape: letters become x, digits #."""
    if len(text) <= 2:
        return "*" * len(text)
    head = text[0]
    rest = re.sub(r"[A-Za-z]", "x", re.sub(r"\d", "#", text[1:]))
    return head + rest


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    parts = domain.split(".")
    return f"{local[:1]}***@{parts[0][:1]}***.{parts[-1]}"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True)  # fmt: skip
    return done.stdout


def _read_blobs(repo: Path, shas: list[str]) -> dict[str, bytes]:
    """The contents of these blobs, through one `git cat-file --batch`."""
    proc = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"],
                          input="\n".join(shas).encode() + b"\n", capture_output=True,
                          check=True)  # fmt: skip
    out, data, pos = {}, proc.stdout, 0
    for sha in shas:
        end = data.index(b"\n", pos)
        header = data[pos:end].split()
        size = int(header[2])
        out[sha] = data[end + 1 : end + 1 + size]
        pos = end + 1 + size + 1
    return out


def synthetic_vocabulary(repo: Path, blobs: dict[str, tuple[str, str]],
                         contents: dict[str, bytes]) -> set[str]:  # fmt: skip
    """Every capitalized word in any version of the generator and test code."""
    words: set[str] = set()
    for sha, (_, path) in blobs.items():
        if path.startswith(SYNTHETIC_SOURCES) and path.endswith(".py"):
            words |= set(CAPITALIZED.findall(contents[sha].decode("utf-8", "replace")))
    return words


def scan_text(text: str, commit: str, path: str, vocabulary: set[str]) -> list[Finding]:
    out: list[Finding] = []
    for n, line in enumerate(text.splitlines(), start=1):
        for m in EMAIL.findall(line):
            if not m.lower().endswith(ALLOWED_EMAIL_DOMAINS):
                out.append(Finding("email address", mask_email(m), commit, path, n))
        for m in PHONE.findall(line):
            out.append(Finding("phone number", mask(m), commit, path, n))
        for m in CARD.findall(line):
            out.append(Finding("card-number-like", mask(m), commit, path, n))
        for m in DIGITS.findall(line):
            if not re.fullmatch(r"20\d{6}", m):  # a compact date
                out.append(Finding("account-number-like digits", mask(m), commit, path, n,
                                   f"{len(m)} digits"))  # fmt: skip
        for pattern in SECRETS:
            for m in pattern.findall(line):
                out.append(Finding("secret-like string", mask(m)[:12] + "...", commit, path, n))
        for m in AI_MENTION.findall(line):
            out.append(Finding("mention of an AI assistant or vendor", mask(m), commit, path, n))
        for first, last in NAME_PAIR.findall(line):
            if first in FIRST_NAMES and first not in vocabulary and last not in vocabulary:
                out.append(Finding("possible person's name", f"{mask(first)} {mask(last)}",
                                   commit, path, n))  # fmt: skip
    return out


def decisions_amounts(repo: Path, history: list[tuple[str, str]],
                      contents: dict[str, bytes]) -> list[Finding]:  # fmt: skip
    """Dollar amounts on lines added to DECISIONS.md, commit by commit (oldest first)."""
    out: list[Finding] = []
    previous: list[str] = []
    for commit, sha in history:
        lines = contents[sha].decode("utf-8", "replace").splitlines()
        matcher = difflib.SequenceMatcher(a=previous, b=lines, autojunk=False)
        for tag, _, _, j1, j2 in matcher.get_opcodes():
            if tag not in ("insert", "replace"):
                continue
            for n in range(j1, j2):
                amounts = DOLLARS.findall(lines[n])
                if not amounts:
                    continue
                flag = "line talks about my own data" if REAL_WORDS.search(lines[n]) else ""
                out.append(Finding("dollar amounts added to DECISIONS.md",
                                   f"{len(amounts)} amount(s)", commit, "DECISIONS.md", n + 1,
                                   flag))  # fmt: skip
        previous = lines
    return out


def scan(repo: Path) -> Report:
    report = Report()
    report.branches = [b.strip() for b in _git(repo, "for-each-ref", "--format=%(refname:short)",
                                               "refs/heads", "refs/remotes").splitlines()
                       if b.strip()]  # fmt: skip
    for ref in report.branches:
        if re.search(AI_MENTION.pattern, ref, re.IGNORECASE):
            report.findings.append(Finding("mention of an AI assistant or vendor", mask(ref), "-",
                                           "(branch name)"))  # fmt: skip
    commits = _git(repo, "rev-list", "--all", "--date-order", "--reverse").split()
    report.commits = len(commits)
    for line in _git(repo, "log", "--all", "--format=%ae%n%ce").splitlines():
        if line.strip():
            report.authors[line.strip()] = report.authors.get(line.strip(), 0) + 1
    first_seen: dict[str, tuple[str, str]] = {}  # blob -> (commit, path), oldest first
    sizes: dict[str, int] = {}
    decisions: list[tuple[str, str]] = []
    paths_seen: dict[str, str] = {}
    for commit in commits:
        for row in _git(repo, "ls-tree", "-r", "-l", commit).splitlines():
            meta, path = row.split("\t", 1)
            _, kind, sha, size = meta.split()
            if kind != "blob":
                continue
            paths_seen.setdefault(path, commit)
            if sha not in first_seen:
                first_seen[sha] = (commit, path)
                sizes[sha] = int(size) if size != "-" else 0
            if path == "DECISIONS.md" and (not decisions or decisions[-1][1] != sha):
                decisions.append((commit, sha))
    for path, commit in sorted(paths_seen.items()):
        for pattern, why in RISKY_PATH:
            if pattern.search(path):
                report.findings.append(Finding("file that should not be committed", why,
                                               commit[:10], path))  # fmt: skip
    for sha, size in sizes.items():
        if size >= LARGE_BYTES:
            commit, path = first_seen[sha]
            report.findings.append(Finding("large file", f"{size / 1e6:.1f} MB", commit[:10],
                                           path))  # fmt: skip
    contents = _read_blobs(repo, list(first_seen))
    report.blobs_scanned = len(contents)
    vocabulary = synthetic_vocabulary(repo, first_seen, contents)
    for sha, (commit, path) in first_seen.items():
        data = contents[sha]
        if path.lower().endswith(BINARY_SUFFIXES) or b"\0" in data[:8000]:
            continue
        report.findings += scan_text(data.decode("utf-8", "replace"), commit[:10], path,
                                     vocabulary)  # fmt: skip
    report.findings += [Finding(f.kind, f.masked, f.commit[:10], f.path, f.line, f.note)
                        for f in decisions_amounts(repo, decisions, contents)]  # fmt: skip
    for commit in commits:
        message = _git(repo, "log", "-1", "--format=%B", commit)
        report.findings += scan_text(message, commit[:10], "(commit message)", vocabulary)
    return report


KIND_ORDER = (
    "file that should not be committed", "secret-like string", "email address", "phone number",
    "card-number-like", "account-number-like digits", "possible person's name", "large file",
    "mention of an AI assistant or vendor", "dollar amounts added to DECISIONS.md",
)  # fmt: skip


def render(report: Report, limit: int = 60) -> str:
    by_kind: dict[str, list[Finding]] = defaultdict(list)
    for f in report.findings:
        by_kind[f.kind].append(f)
    out = [
        "# Pre-public history scan",
        "",
        f"Made with `python -m eval.history_scan`. Report only: nothing in the history was "
        f"changed. {report.commits} commits on {len(report.branches)} branch refs, "
        f"{report.blobs_scanned} distinct file contents. Each finding names the first commit and "
        "path where that content appeared (a later commit may carry the same content). Matches "
        "are masked: the first character is kept, letters become x and digits #.",
        "",
        "## Summary",
        "",
        "| What | Found |",
        "|---|---|",
        *[f"| {k} | {len(by_kind.get(k, []))} |" for k in KIND_ORDER],
        "",
        "## Commit author and committer emails",
        "",
        "These are in every commit's metadata and become public with the repository.",
        "",
        *[f"- {mask_email(e)}: {n} commit fields" for e, n in sorted(report.authors.items())],
        "",
    ]
    for kind in KIND_ORDER:
        rows = by_kind.get(kind, [])
        out += [f"## {kind[0].upper() + kind[1:]} ({len(rows)})", ""]
        if not rows:
            out += ["None found.", ""]
            continue
        if kind == "dollar amounts added to DECISIONS.md":
            flagged = [f for f in rows if f.note]
            per_commit: dict[str, int] = defaultdict(int)
            for f in rows:
                per_commit[f.commit] += 1
            out += [
                f"{len(rows)} added lines with dollar amounts over {len(per_commit)} versions; "
                f"{len(flagged)} of those lines also talk about my own data (listed first; worth "
                "reading before going public). Amounts are not printed. To read a line: "
                "`git show <commit>:DECISIONS.md | sed -n '<line>p'`.",
                "",
                "| Commit | Line (in that version) | Amounts | Note |",
                "|---|---|---|---|",
                *[f"| {f.commit} | {f.line} | {f.masked} | {f.note} |"
                  for f in (flagged + [f for f in rows if not f.note])[:limit]],
                "",
            ]  # fmt: skip
            if len(rows) > limit:
                out += [f"... and {len(rows) - limit} more lines.", ""]
            continue
        by_path: dict[str, list[Finding]] = defaultdict(list)
        for f in rows:
            by_path[f.path].append(f)
        out += ["| Path | Commit | Lines | Masked examples |", "|---|---|---|---|"]
        for path, items in sorted(by_path.items(), key=lambda kv: -len(kv[1]))[:limit]:
            lines = ", ".join(str(f.line) for f in items[:5] if f.line) or "-"
            examples = "; ".join(sorted({f.masked + (f" ({f.note})" if f.note else "")
                                         for f in items})[:3])  # fmt: skip
            more = f" (+{len(items) - 5})" if len(items) > 5 else ""
            out.append(f"| {path} | {items[0].commit} | {lines}{more} | {examples} |")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.history_scan",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, default=Path("docs/results/history_scan.md"))
    args = parser.parse_args(argv)
    report = scan(args.repo)
    args.out.write_text(render(report), encoding="utf-8")
    counts = defaultdict(int)
    for f in report.findings:
        counts[f.kind] += 1
    print(f"written: {args.out} ({report.commits} commits, {report.blobs_scanned} contents)")
    for kind in KIND_ORDER:
        print(f"  {kind}: {counts.get(kind, 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
