"""Read-only scan of the whole git history for what must be cleaned before going public.

Spec: docs/specs/2026-11-17-scrub-scan.md. Every commit in this clone is read: those on any
branch, remote-tracking branch or tag, those only in a reflog, and unreachable ones (deleted
branches). The report gives counts, file paths, commit ids and ref names only: it never quotes an
amount, a matched line, a pattern or an email.

This file holds generic rules only. Personal patterns (names, amounts, e-Transfer names,
merchants, exact wordings) are read from a file given with --patterns, which is never opened
unless that option is given:

    python -m tools.scrub_scan                                  # generic rules only
    python -m tools.scrub_scan --patterns data/private/scrub_patterns.txt   # mine, run by me

The contents of anything under data/private, any .env file (not .env.example) and
DECISIONS-private.md are never read, in any commit: only their paths are counted.
"""

import argparse
import hashlib
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from lookahead.generator.descriptions import _TEMPLATES

OLD_REPORT = "docs/prepublic-scrub-report.md"
DEMO_DBS = "data/demo/timemachine/"
SYNTHETIC = "data/synthetic/persona_"
CATEGORIES = ("name", "address", "etransfer", "merchant", "account", "amount", "phrase")
# How section 7 counts them: a bare number (no $, no cents) is counted apart, it is noisier.
REPORTED = (*CATEGORIES, "amount (bare number)")

AI_WORDS = re.compile(
    r"\b(claude|anthropic|chatgpt|gpt-?\d+|openai|copilot|cursor|gemini|codex|llm|haiku|ai|"
    r"co-authored-by|generated with)\b",
    re.IGNORECASE,
)
# In file lines, a narrower list: an AI assistant or tool named (llm, ai or haiku alone are too
# common in the app's own LLM-fallback code to be worth listing).
FILE_AI_WORDS = re.compile(
    r"\b(claude|anthropic|chatgpt|openai|copilot|co-authored-by|generated with)\b", re.I
)
DOLLARS = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?")
FIRST_PERSON = re.compile(r"\b(?:my|i|me|mine|personal|own)\b|real account|real data", re.I)
OWN_ACCOUNT = re.compile(
    r"\bmy (?:own |real )?(?:account|accounts|data|bank|income|pay|paycheque|paycheck|salary|"
    r"rent|savings|balance|deposits?|spending|transactions?|exports?|chequing|lump sum|"
    r"transfers?|family)\b",
    re.IGNORECASE,
)
# The brand names the synthetic generator already uses (public code): whole words, with case.
BRANDS = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(b) for b in _TEMPLATES) + r")(?!\w)")
AMOUNT_TOKEN = re.compile(r"(?<![\w.])(\$\s?)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d{2})?(?![\d])")
BINARY_SUFFIXES = (
    ".db",
    ".sqlite",
    ".sqlite3",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".pdf",
    ".zip",
    ".gz",
    ".pkl",
    ".joblib",
    ".npz",
    ".npy",
    ".parquet",
)
MAX_BYTES = 20_000_000


@dataclass(frozen=True)
class Patterns:
    """Personal patterns: text by category (compared without case) and amounts in cents."""

    text: dict[str, tuple[str, ...]]
    amounts: frozenset[int]
    compiled: dict[str, re.Pattern[str]] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        # whole words, without case: a short name never matches inside a longer word
        for category, values in self.text.items():
            if values:
                alternation = "|".join(re.escape(v) for v in sorted(set(values), key=len)[::-1])
                self.compiled[category] = re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.I)

    def is_mine(self, brand: str) -> bool:
        """Whether a brand name is also one of my own card merchants."""
        b = brand.casefold()
        return any(b == m or b in m or m in b for m in self.text.get("merchant", ()))

    def counts(self) -> dict[str, int]:
        out = {c: len(self.text.get(c, ())) for c in CATEGORIES if c != "amount"}
        out["amount"] = len(self.amounts)
        return out


def to_cents(text: str) -> int:
    """'$1,234.56', '1234.56' or '$40' as cents."""
    value = Decimal(text.replace("$", "").replace(",", "").strip())
    return int((value * 100).to_integral_value())


def load_patterns(path: Path) -> Patterns:
    """Read a patterns file (format in the spec). An error names the line number and the
    category, never the pattern itself."""
    text: dict[str, list[str]] = defaultdict(list)
    amounts: set[int] = set()
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        category, sep, value = line.partition(":")
        category, value = category.strip().casefold(), value.strip()
        if not sep or category not in CATEGORIES:
            raise ValueError(f"line {n}: the category must be one of {', '.join(CATEGORIES)}")
        if not value:
            raise ValueError(f"line {n} ({category}): nothing after the colon")
        if category == "amount":
            try:
                amounts.add(abs(to_cents(value)))
            except InvalidOperation:
                raise ValueError(f"line {n} (amount): not an amount") from None
        else:
            text[category].append(value.casefold())
    return Patterns({k: tuple(v) for k, v in text.items()}, frozenset(amounts))


def never_read(path: str) -> str | None:
    """Why a path's contents must never be read (None: it may be read)."""
    parts = [p.casefold() for p in path.split("/")]
    if any(a == "data" and b == "private" for a, b in zip(parts, parts[1:], strict=False)):
        return "a path under data/private"
    if ".env" in parts[:-1]:
        return "a .env file"  # inside a folder named .env
    name = parts[-1]
    if name != ".env.example" and (
        name in (".env", ".envrc") or name.startswith(".env.") or name.endswith(".env")
    ):
        return "a .env file"
    if name == "decisions-private.md":
        return "DECISIONS-private.md"
    return None


def must_leave(path: str) -> str | None:
    """Why a whole file must leave the history (None: it may stay)."""
    why = never_read(path)
    if why is not None:
        return why
    if path == OLD_REPORT:
        return "the old scrub report (it quotes amounts)"
    if path.casefold().endswith((".db", ".sqlite", ".sqlite3")) and not path.startswith(DEMO_DBS):
        return "a database outside data/demo/timemachine"
    return None


def mask_email(email: str) -> str:
    """'someone@example.org' -> 's***@example.org'."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def mask_name(name: str) -> str:
    return f"{name[:1]}***" if name else "(none)"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


class BlobReader:
    """Reads objects through one `git cat-file --batch` process."""

    def __init__(self, repo: Path) -> None:
        self.proc = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def read(self, oid: str) -> bytes:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(oid.encode() + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        if len(header) < 3:
            raise KeyError(oid)
        data = self.proc.stdout.read(int(header[2]))
        self.proc.stdout.read(1)  # the newline after the object
        return data

    def close(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        self.proc.wait()


@dataclass
class Commit:
    oid: str
    reach: str  # "on a ref", "reflog only" or "unreachable"
    author: tuple[str, str] = ("", "")
    committer: tuple[str, str] = ("", "")
    ai_words: tuple[str, ...] = ()
    mine: tuple[str, ...] = ()  # categories of my patterns its message matches
    refs: set[str] = field(default_factory=set)


def parse_commit(raw: bytes) -> tuple[dict[str, tuple[str, str]], str]:
    head, _, message = raw.decode("utf-8", "replace").partition("\n\n")
    people: dict[str, tuple[str, str]] = {}
    for line in head.splitlines():
        key, _, rest = line.partition(" ")
        if key in ("author", "committer"):
            m = re.match(r"(.*) <([^>]*)>", rest)
            if m:
                people[key] = (m.group(1).strip(), m.group(2).strip())
    return people, message


def all_commits(repo: Path) -> dict[str, str]:
    """Every commit in the clone, with how it is reached."""
    on_ref = set(git(repo, "rev-list", "--all").split())
    with_reflog = set(git(repo, "rev-list", "--all", "--reflog").split())
    fsck = subprocess.run(
        ["git", "-C", str(repo), "fsck", "--unreachable", "--no-reflogs", "--no-progress"],
        capture_output=True,
        text=True,
    ).stdout
    lost = {line.split()[2] for line in fsck.splitlines() if line.startswith("unreachable commit")}
    reach = dict.fromkeys(lost, "unreachable")
    reach.update(dict.fromkeys(with_reflog - on_ref, "reflog only"))
    reach.update(dict.fromkeys(on_ref, "on a ref"))
    return reach


def ref_names(repo: Path) -> list[str]:
    out = git(
        repo, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes", "refs/tags"
    ).split()
    return [r for r in out if not r.endswith("/HEAD")]


def short_ref(ref: str) -> str:
    for prefix in ("refs/heads/", "refs/remotes/", "refs/tags/"):
        if ref.startswith(prefix):
            return ref[len(prefix) :] + (" (tag)" if prefix == "refs/tags/" else "")
    return ref


def tree_of(repo: Path, commit: str) -> list[tuple[str, str]]:
    """(path, blob id) of every file in a commit."""
    out = []
    for entry in git(repo, "ls-tree", "-r", "-z", "--full-tree", commit).split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        _mode, kind, oid = meta.split()
        if kind == "blob":
            out.append((path, oid))
    return out


def line_rules(line: str, patterns: Patterns | None) -> set[str]:
    """The rules a line matches (names of rules, never what matched)."""
    hits = set()
    if DOLLARS.search(line) and FIRST_PERSON.search(line):
        hits.add("amount")
    if OWN_ACCOUNT.search(line):
        hits.add("own account")
    for m in BRANDS.finditer(line):
        mine = patterns is not None and patterns.is_mine(m.group(0))
        hits.add("brand (also mine)" if mine else "brand")
    if FILE_AI_WORDS.search(line):
        hits.add("ai tool")
    if patterns is not None:
        for category, regex in patterns.compiled.items():
            if regex.search(line):
                hits.add(f"mine: {category}")
        if patterns.amounts:
            for m in AMOUNT_TOKEN.finditer(line):
                if (m.group(1) or m.group(3)) and to_cents(m.group(0)) in patterns.amounts:
                    hits.add("mine: amount")
                    break
                # a bare number: whole cents (the project's convention, -123456) or whole
                # dollars ("rent 1450"); both are tried, over-flagging is the safe side
                if not (m.group(1) or m.group(3)):
                    whole = int(m.group(2).replace(",", ""))
                    if whole in patterns.amounts or whole * 100 in patterns.amounts:
                        hits.add("mine: amount (bare number)")
    return hits


def scan_blob(data: bytes, patterns: Patterns | None) -> dict[str, set[str]]:
    """Rule -> hashes of the distinct lines that match it (the lines themselves are not kept)."""
    out: dict[str, set[str]] = defaultdict(set)
    for line in data.decode("utf-8", "replace").splitlines():
        rules = line_rules(line, patterns)
        if rules:
            digest = hashlib.sha256(line.strip().encode()).hexdigest()
            for rule in rules:
                out[rule].add(digest)
    return out


def is_binary(path: str, data: bytes) -> bool:
    return path.casefold().endswith(BINARY_SUFFIXES) or b"\0" in data[:8000]


@dataclass
class PathHits:
    lines: set[str] = field(default_factory=set)
    commits: set[str] = field(default_factory=set)
    at_tip: bool = False


def scan(repo: Path, tip: str, patterns: Patterns | None, public_email: str | None = None) -> str:
    """The report, as Markdown. Reads git objects only; writes nothing."""
    reach = all_commits(repo)
    commits = {oid: Commit(oid, how) for oid, how in reach.items()}
    refs = ref_names(repo)
    for ref in refs:
        for oid in git(repo, "rev-list", ref).split():
            if oid in commits:
                commits[oid].refs.add(short_ref(ref))
    tip_oid = git(repo, "rev-parse", f"{tip}^{{commit}}").strip()
    tip_files = dict(tree_of(repo, tip_oid))

    reader = BlobReader(repo)
    try:
        for c in commits.values():
            people, message = parse_commit(reader.read(c.oid))
            c.author = people.get("author", ("", ""))
            c.committer = people.get("committer", ("", ""))
            c.ai_words = tuple(sorted({m.group(1).casefold() for m in AI_WORDS.finditer(message)}))
            if patterns is not None:
                c.mine = tuple(sorted({rule.removeprefix("mine: ")
                                       for line in message.splitlines()
                                       for rule in line_rules(line, patterns)
                                       if rule.startswith("mine: ")}))  # fmt: skip

        where: dict[tuple[str, str], set[str]] = defaultdict(set)  # (path, blob) -> commits
        for c in commits.values():
            for path, oid in tree_of(repo, c.oid):
                where[(path, oid)].add(c.oid)

        by_rule: dict[str, dict[str, PathHits]] = defaultdict(lambda: defaultdict(PathHits))
        leaving: dict[str, PathHits] = defaultdict(PathHits)
        cache: dict[str, dict[str, set[str]]] = {}
        skipped = 0
        for (path, oid), holders in sorted(where.items()):
            why = must_leave(path)
            if why is not None:
                hit = leaving[path]
                hit.commits |= holders
                hit.at_tip = hit.at_tip or path in tip_files
            if never_read(path) is not None:
                continue  # contents never read, in any commit
            if oid not in cache:
                data = reader.read(oid)
                if len(data) > MAX_BYTES or is_binary(path, data):
                    cache[oid] = {}
                    skipped += 1
                else:
                    cache[oid] = scan_blob(data, patterns)
            for rule, lines in cache[oid].items():
                hit = by_rule[rule][path]
                hit.lines |= lines
                hit.commits |= holders
                hit.at_tip = hit.at_tip or tip_files.get(path) == oid
    finally:
        reader.close()

    return render(tip, refs, commits, by_rule, leaving, patterns, skipped, public_email)


def _reach_of(oids: Iterable[str], commits: dict[str, Commit]) -> str:
    kinds = sorted({commits[o].reach for o in oids})
    return ", ".join(kinds)


def _refs_of(oids: Iterable[str], commits: dict[str, Commit]) -> str:
    names = sorted({r for o in oids for r in commits[o].refs})
    return ", ".join(names) if names else "no ref"


def _path_table(hits: dict[str, PathHits], commits: dict[str, Commit]) -> list[str]:
    if not hits:
        return ["None found.", ""]
    lines = [
        "| Path | Distinct lines | Commits | Where | Reached | Refs |",
        "|---|---|---|---|---|---|",
    ]
    for path, h in sorted(hits.items()):
        where = "at the tip" if h.at_tip else "history only"
        lines.append(
            f"| `{path}` | {len(h.lines)} | {len(h.commits)} | {where} | "
            f"{_reach_of(h.commits, commits)} | {_refs_of(h.commits, commits)} |"
        )
    return [*lines, ""]


def render(
    tip: str,
    refs: list[str],
    commits: dict[str, Commit],
    by_rule: dict[str, dict[str, PathHits]],
    leaving: dict[str, PathHits],
    patterns: Patterns | None,
    skipped: int,
    public_email: str | None = None,
) -> str:
    kinds = defaultdict(int)
    for c in commits.values():
        kinds[c.reach] += 1
    out = [
        f"# Scrub scan, {date.today().isoformat()}",
        "",
        "Read-only. Counts, file paths, commit ids and ref names only: no amount, matched line, "
        "pattern or email is quoted (emails are masked, except the public one given with "
        "--public-email). Spec: "
        "docs/specs/2026-11-17-scrub-scan.md.",
        "",
        f"- Refs scanned ({len(refs)}): {', '.join(short_ref(r) for r in refs)}.",
        f"- Commits: {len(commits)} ({kinds['on a ref']} on a ref, {kinds['reflog only']} in a "
        f"reflog only, {kinds['unreachable']} unreachable).",
        f'- "At the tip" means at the tip of `{tip}`.',
        f"- Binary or very large files not scanned: {skipped} file versions.",
        "- Personal patterns: "
        + (
            "not used (generic rules only)."
            if patterns is None
            else "used (" + ", ".join(f"{k} {v}" for k, v in patterns.counts().items()) + ")."
        ),
        "",
    ]

    ai = sorted((c for c in commits.values() if c.ai_words), key=lambda c: (c.reach, c.oid))
    out += [f"## 1. Commit messages that mention an AI tool ({len(ai)} commits)", ""]
    out += [
        f"- `{c.oid[:10]}` matched: {', '.join(c.ai_words)}; {c.reach}; "
        f"{', '.join(sorted(c.refs)) or 'no ref'}"
        for c in ai
    ] or ["None found."]
    out.append("")

    public = (public_email or "").casefold()
    people: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for c in commits.values():
        for role, (name, email) in (("author", c.author), ("committer", c.committer)):
            if public and email.casefold() == public:
                shown, status = email, "kept (public)"
            else:
                shown, status = mask_email(email), "to rewrite" if public else "-"
            people[(role, mask_name(name), shown, status)] += 1
    out += [
        "## 2. Author and committer identities",
        "",
        "| Role | Name | Email | Status | Commits |",
        "|---|---|---|---|---|",
    ]
    out += [f"| {r} | {n} | {e} | {st} | {k} |" for (r, n, e, st), k in sorted(people.items())]
    out.append("")

    sections = (
        (
            "3. Lines that look like a personal dollar amount",
            "amount",
            "A dollar amount on a line that also says my, I, me, personal, own, real account or "
            "real data. Rough: it finds first-person lines about money.",
        ),
        (
            "4. Lines that speak of my own account",
            "own account",
            "Wording such as my account, my data, my income, my pay, my rent, my savings "
            "(it covers the income-timing entries).",
        ),
    )
    for title, rule, rule_text in sections:
        out += [f"## {title} ({len(by_rule.get(rule, {}))} paths)", "", rule_text, ""]
        out += _path_table(by_rule.get(rule, {}), commits)

    brands = by_rule.get("brand", {})
    mine = by_rule.get("brand (also mine)", {})
    elsewhere = {p: h for p, h in brands.items() if not p.startswith(SYNTHETIC)}
    mine_elsewhere = {p: h for p, h in mine.items() if not p.startswith(SYNTHETIC)}
    synthetic = {p: h for p, h in [*brands.items(), *mine.items()] if p.startswith(SYNTHETIC)}
    out += [
        f"## 5. Brand names outside the synthetic persona files ({len(elsewhere)} paths)",
        "",
        "The brand names the synthetic generator uses, as whole words.",
        "",
    ]
    out += _path_table(elsewhere, commits)
    if patterns is not None:
        out += [f"### Also among my own merchants ({len(mine_elsewhere)} paths)", "",
                "The same brand names, where the brand is also one of my own card merchants.",
                ""]  # fmt: skip
        out += _path_table(mine_elsewhere, commits)
    out += [f"## 6. Brand names in the synthetic persona files ({len(synthetic)} paths)", ""]
    out += _path_table(synthetic, commits)

    out += ["## 7. My personal patterns", ""]
    if patterns is None:
        out += ["Not run: no patterns file was given.", ""]
    else:
        out += ["| Category | Paths | Distinct lines | Commits |", "|---|---|---|---|"]
        for category in REPORTED:
            hits = by_rule.get(f"mine: {category}", {})
            n_commits = len({o for h in hits.values() for o in h.commits})
            out.append(
                f"| {category} | {len(hits)} | {sum(len(h.lines) for h in hits.values())} "
                f"| {n_commits} |"
            )
        out.append("")
        flagged = sorted((c for c in commits.values() if c.mine), key=lambda c: c.oid)
        ids = "; ".join(f"`{c.oid[:10]}` ({', '.join(c.mine)}; {c.reach})" for c in flagged)
        out += ["| In | Commits | Which (categories only) |", "|---|---|---|",
                f"| commit messages | {len(flagged)} | {ids or 'none'} |", ""]  # fmt: skip
        for category in REPORTED:
            hits = by_rule.get(f"mine: {category}", {})
            if hits:
                out += [f"### {category}", ""]
                out += _path_table(hits, commits)

    out += [f"## 8. Files that must leave the history whole ({len(leaving)} paths)", ""]
    named = {p: h for p, h in leaving.items() if never_read(p) is None}
    hidden: dict[str, set[str]] = defaultdict(set)
    for p, h in leaving.items():
        why = never_read(p)
        if why is not None:
            hidden[why] |= h.commits
    if named:
        out += [
            "| Path | Why | Commits | Where | Reached | Refs | Commit ids |",
            "|---|---|---|---|---|---|---|",
        ]
        for p, h in sorted(named.items()):
            where = "at the tip" if h.at_tip else "history only"
            ids = ", ".join(f"`{o[:10]}`" for o in sorted(h.commits))
            out.append(
                f"| `{p}` | {must_leave(p)} | {len(h.commits)} | {where} | "
                f"{_reach_of(h.commits, commits)} | {_refs_of(h.commits, commits)} | "
                f"{ids} |"
            )
        out.append("")
    for why in ("a path under data/private", "a .env file", "DECISIONS-private.md"):
        n_paths = sum(1 for p in leaving if never_read(p) == why)
        out.append(
            f"- {why}: {n_paths} paths in {len(hidden.get(why, set()))} commits "
            "(names not listed, contents never read)."
        )
    ai_lines = by_rule.get("ai tool", {})
    out += [
        "",
        f"## 9. File lines that name an AI tool or assistant ({len(ai_lines)} paths)",
        "",
        'Claude, Anthropic, ChatGPT, OpenAI, Copilot, Co-Authored-By or "Generated with". Many '
        "are the app's own optional LLM fallback (keep); a line about who wrote the code is not.",
        "",
    ]
    out += _path_table(ai_lines, commits)
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.scrub_scan",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--tip", default="prepublic-prep", help="the branch to call 'the tip'")
    parser.add_argument(
        "--public-email", default=None, help="the author email that stays public (shown as is)"
    )
    parser.add_argument(
        "--patterns",
        type=Path,
        default=None,
        help="my personal patterns file (never opened unless given)",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="report path (default: logs/scrub-report-<date>.md)"
    )
    args = parser.parse_args(argv)
    patterns = load_patterns(args.patterns) if args.patterns is not None else None
    report = scan(args.repo, args.tip, patterns, args.public_email)
    out = args.out or args.repo / "logs" / f"scrub-report-{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    counts = [line for line in report.splitlines() if line.startswith("## ")]
    print("\n".join(counts))
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
