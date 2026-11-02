"""Where do the "?" in merchant text come from? A counts-only check of exports and stored rows.

    python -m lookahead.ingest.textcheck data/private/raw --db data/private/db/lookahead.db

For each export: the encoding it is read in, its rows, and how many rows have a letter outside
plain ASCII (an accented letter: é, è, ç...), a "?" touching a letter (a letter that was lost
before or during the export), or the Unicode replacement character. Then the same counts for
the stored rows, by import day. It prints counts only, never the text, so the output is safe to
share; it changes nothing.

A "?" counted in the export itself was already in the file the bank gave: no importer setting
can bring that letter back.
"""

import argparse
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from lookahead.ingest.scotiabank import ExportFormatError, export_encoding, parse_export

MARKS = ("accented letter", "? touching a letter", "replacement character")
_LOST = re.compile(r"[^\W\d_]\?|\?[^\W\d_]")  # a "?" right before or after a letter


def marks(text: str) -> set[str]:
    """Which of MARKS a piece of text shows."""
    found = set()
    if any(ord(c) > 127 and c.isalpha() for c in text):
        found.add("accented letter")
    if _LOST.search(text):
        found.add("? touching a letter")
    if "�" in text:
        found.add("replacement character")
    return found


def count_marks(texts: Iterable[str]) -> Counter[str]:
    """Rows showing each mark (a row counts once per mark), and the rows in all."""
    counts: Counter[str] = Counter()
    for text in texts:
        counts["rows"] += 1
        counts.update(marks(text))
    return counts


def _line(label: str, counts: Counter[str]) -> str:
    return f"{label}: {counts['rows']} rows; " + ", ".join(f"{m} {counts[m]}" for m in MARKS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.ingest.textcheck",
                                     description=__doc__)  # fmt: skip
    parser.add_argument("folder", type=Path, help="folder with .csv exports")
    parser.add_argument("--db", type=Path, help="also count the stored rows")
    parser.add_argument("--glob", default="*.csv")
    args = parser.parse_args(argv)
    for i, path in enumerate(sorted(args.folder.glob(args.glob)), start=1):
        try:
            rows = parse_export(path)
        except ExportFormatError as e:
            print(f"file {i}: not read ({e.kind})")
            continue
        texts = (f"{r.description} {r.sub_description}" for r in rows)
        print(_line(f"file {i} ({export_encoding(path)})", count_marks(texts)))
    if args.db is not None:
        if not args.db.exists():
            print("database not found")
            return 2
        conn = sqlite3.connect(f"{args.db.resolve().as_uri()}?mode=ro", uri=True)
        by_day: dict[str, list[str]] = {}
        for imported, description, sub in conn.execute(
            "SELECT imported_at, description, sub_description FROM transactions"
        ):
            by_day.setdefault(str(imported)[:10], []).append(f"{description} {sub}")
        conn.close()
        for day in sorted(by_day):
            print(_line(f"stored, imported {day}", count_marks(by_day[day])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
