"""Build my scrub patterns from my own bank files, then run the scrub scan with them.

Run by me only, on my private files (every path is given on the command line; there is no
default). It never prints or writes a pattern: it prints how many it built per category, then the
scan's section headings and the section 7 counts. Spec: docs/specs/2026-11-17-scrub-scan.md.

What it builds:
- etransfer: every e-Transfer counterpart name (the sender on a credit memo; a person's name on a
  transfer-type row or a mobile-banking payee), the known businesses of the allowlist left out;
- amount: every transaction amount over $100, from the exports and the database;
- merchant: every card merchant (to tell brand names from my own shops apart);
- account: every run of 7 or more digits in the exports' text and file names, and every
  mobile-banking reference;
and adds the few lines I type myself (--extra: my name spellings, address, building), in the
format of `tools/scrub_scan.py`.
"""

import argparse
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from lookahead.ingest.scotiabank import ExportFormatError, parse_export, read_export_text
from lookahead.merchants import extract_merchant
from lookahead.pseudonymize import (
    _CAPITALIZED_WORD,
    _FIXED_TEXTS,
    _MEMO,
    _MOBILE_BILL,
    _NAME_TYPES,
    _normalized,
    load_allowlist,
)
from tools.scrub_scan import Patterns, load_patterns, scan

OVER_CENTS = 10_000  # amounts over $100
MIN_NAME = 4  # shorter names would match too much
DIGITS_IN_TEXT = re.compile(r"(?<![\d.,-])\d(?: ?\d){6,}(?![\d.,])")  # 7+ digits, maybe spaced
DIGITS_IN_NAME = re.compile(r"\d(?:[ _-]?\d){6,}")
_LONG_DIGITS = re.compile(r"\d{7,}")


def names_in(description: str, sub: str, known: set[str]) -> tuple[list[str], list[str]]:
    """(person-like names, reference numbers) in one row's text."""
    names: list[str] = []
    numbers: list[str] = []
    if description == "credit memo":
        match = _MEMO.fullmatch(sub)
        if match and match.group(2):
            names.append(match.group(2))
    bill = _MOBILE_BILL.fullmatch(sub.strip())
    if bill:
        payee, reference = bill.groups()
        numbers.append(reference)
        if len(_CAPITALIZED_WORD.findall(payee)) >= 2 and _normalized(payee) not in known:
            names.append(payee)
    elif (
        description in _NAME_TYPES
        and sub.strip() not in _FIXED_TEXTS
        and len(_CAPITALIZED_WORD.findall(sub)) >= 2
        and _normalized(sub) not in known
    ):
        names.append(_LONG_DIGITS.sub(" ", sub))
    clean = [" ".join(n.split()) for n in names]
    return [n for n in clean if len(n) >= MIN_NAME and _normalized(n) not in known], numbers


def _rows_from_db(db: Path) -> Iterable[tuple[str, str, int]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        yield from conn.execute(
            "SELECT description, sub_description, amount_cents FROM transactions"
        )
    finally:
        conn.close()


def build_patterns(exports: Path | None, db: Path | None, allowlist: Path | None) -> Patterns:
    """Patterns from every export under `exports` and every row of `db`."""
    known = load_allowlist(allowlist) if allowlist is not None else set()
    names: set[str] = set()
    merchants: set[str] = set()
    accounts: set[str] = set()
    amounts: set[int] = set()
    rows: list[tuple[str, str, int]] = []
    skipped = 0
    for path in sorted(exports.rglob("*.csv")) if exports is not None else []:
        accounts |= {re.sub(r"\D", "", m) for m in DIGITS_IN_NAME.findall(path.name)}
        try:
            text, _ = read_export_text(path)
            parsed = parse_export(path)
        except ExportFormatError:
            skipped += 1  # not a bank export: counted, never printed
            continue
        accounts |= {m.replace(" ", "") for m in DIGITS_IN_TEXT.findall(text)}
        rows += [(r.description, r.sub_description, r.amount_cents) for r in parsed]
    if db is not None:
        rows += list(_rows_from_db(db))
    for description, sub, cents in rows:
        found, numbers = names_in(description, sub, known)
        names |= {n.casefold() for n in found}
        accounts |= set(numbers)
        found_merchant = extract_merchant(sub)
        if found_merchant.kind == "card":
            for name in (found_merchant.merchant, found_merchant.via_restaurant):
                if name and len(name) >= MIN_NAME - 1:
                    merchants.add(" ".join(name.split()).casefold())
        if abs(cents) > OVER_CENTS:
            amounts.add(abs(cents))
    if skipped:
        print(f"files skipped (not a bank export): {skipped}")
    text = {
        "etransfer": tuple(sorted(names)),
        "account": tuple(sorted(accounts)),
        "merchant": tuple(sorted(merchants)),
    }
    return Patterns(text, frozenset(amounts))


def out_is_private(repo: Path, out: Path) -> bool:
    """True if the report path is outside the repository or one git ignores."""
    root, target = repo.resolve(), out.resolve()
    if root not in target.parents:
        return True
    done = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", str(target)])
    return done.returncode == 0


def merged(built: Patterns, extra: Patterns | None) -> Patterns:
    if extra is None:
        return built
    text = {
        k: tuple(sorted({*built.text.get(k, ()), *extra.text.get(k, ())}))
        for k in {*built.text, *extra.text}
    }
    return Patterns(text, built.amounts | extra.amounts)


def main_folder(start: Path) -> Path:
    """The main repository folder (also when run from a worktree): where my private files are."""
    common = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(common.stdout.strip()).parent


def main(argv: list[str] | None = None) -> int:
    root = main_folder(Path.cwd())
    private = root / "data" / "private"
    parser = argparse.ArgumentParser(
        prog="python -m tools.scrub_patterns",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--exports", type=Path, default=private / "raw")
    parser.add_argument("--db", type=Path, default=private / "db" / "lookahead.db")
    parser.add_argument("--allowlist", type=Path, default=private / "pseudonymize_allowlist.txt")
    parser.add_argument("--extra", type=Path, default=private / "scrub_extra.txt")
    parser.add_argument("--repo", type=Path, default=root)
    parser.add_argument("--tip", default="prepublic-prep")
    parser.add_argument("--public-email", default=None, help="default: git config user.email")
    parser.add_argument("--out", type=Path, default=None, help="default: in data/private/scrub")
    args = parser.parse_args(argv)
    if not args.exports.is_dir():
        print(f"no exports folder at {args.exports}; nothing was run")
        return 2
    missing = [name for name in ("db", "allowlist", "extra") if not getattr(args, name).exists()]
    for name in missing:
        setattr(args, name, None)
    if missing:
        print("skipped (not found): " + ", ".join(missing))
    if args.public_email is None:
        email = subprocess.run(
            ["git", "-C", str(args.repo), "config", "user.email"], capture_output=True, text=True
        )
        args.public_email = email.stdout.strip() or None
    if args.out is None:
        args.out = private / "scrub" / f"scrub-report-{date.today().isoformat()}.md"

    if not out_is_private(args.repo, args.out):
        print("--out must be a path git ignores (or outside the repository); nothing was run")
        return 2
    built = build_patterns(args.exports, args.db, args.allowlist)
    patterns = merged(built, load_patterns(args.extra) if args.extra else None)
    print("patterns built: " + ", ".join(f"{k} {v}" for k, v in patterns.counts().items() if v))
    report = scan(args.repo, args.tip, patterns, args.public_email)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    lines = report.splitlines()
    for k, line in enumerate(lines):
        if line.startswith("## "):
            print(line)
        if line.startswith("## 7."):
            for row in lines[k + 1 :]:
                if row.startswith("| commit messages"):
                    print(row.split("|")[1].strip() + ": " + row.split("|")[2].strip())
                    break
                if row.startswith("|"):
                    print(row)
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
