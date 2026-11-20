"""The synthetic development personas Ask's test set runs on (spec docs/specs/2026-11-20-ask.md).

Six development personas of the 12-month set (never held-out: `assert_development_only`), each
built into its own database from the generator's export, with categories, flow types and merchant
names taken from the generator's answer key (so the test measures Ask, not the categorizer). The
data ends on DATA_END and every question is asked on TODAY, the day after. Each persona gets
budgets for the default budget categories, set to the Budgets tab's own proposals, so budget
questions have something to answer.

Built on demand into data/ask_eval/ (git-ignored), rebuilt when missing:
    python -m eval.ask_personas            # build all six, print each one's sheet
"""

import argparse
import csv
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from lookahead.budgets import (
    DEFAULT_CATEGORIES,
    budget_forecast,
    category_rows,
    proposals_for,
    set_limit,
)
from lookahead.db import connect
from lookahead.forecast.path import build_forecast
from lookahead.generator.archetypes import END, assert_development_only, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

PERSONAS: tuple[str, ...] = (
    "v2-SAL-01", "v2-FREE-01", "v2-TIGHT-01", "v2-DRENT-01", "v2-DHOME-01", "v2-IFAM-01",
)  # fmt: skip
SEED = 42
DATA_END = date(2026, 9, 14)  # mid-month: "this month" has rows and days left
TODAY = DATA_END + timedelta(days=1)  # Tuesday 2026-09-15
FOLDER = Path("data/ask_eval")
ACCOUNT = "ask-eval"
KEY = bytes(range(32))  # a made-up pseudonym key (the series need one; nothing is shown)

assert END >= DATA_END


def db_path(persona_id: str, folder: Path = FOLDER) -> Path:
    return folder / f"{persona_id}.db"


def build(persona_id: str, folder: Path = FOLDER) -> Path:
    """The persona's database (built once; reused while it exists)."""
    assert_development_only([persona_id])
    path = db_path(persona_id, folder)
    if path.exists():
        return path
    folder.mkdir(parents=True, exist_ok=True)
    p = build_persona(persona_id)
    export, key = write_persona(p.persona, folder / persona_id, SEED, p.start, END)
    parsed = parse_export(export)
    with key.open(encoding="utf-8", newline="") as f:
        truth = list(csv.DictReader(line for line in f if not line.startswith("#")))
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    conn = connect(tmp)
    keep = [(r, t) for r, t in zip(parsed, truth, strict=True) if r.posted_date <= DATA_END]
    store_exports(conn, ACCOUNT, [(export.name, [r for r, _ in keep])], datetime(2026, 9, 15))
    by_row = {(r.posted_date.isoformat(), r.amount_cents, r.sub_description): t for r, t in keep}
    with conn:
        for fp, day, cents, text in conn.execute(
            "SELECT fingerprint, posted_date, amount_cents, sub_description FROM transactions"
        ).fetchall():
            t = by_row[(day, cents, text)]
            conn.execute(
                "INSERT INTO category_predictions (fingerprint, account, merchant, flow_type,"
                " category, source, detail, flagged, confidence, model_version, predicted_at)"
                " VALUES (?, ?, ?, ?, ?, 'rule', 'answer key', 0, NULL, 'ask-eval', ?)",
                (fp, ACCOUNT, t["merchant"] or None, t["flow_type"], t["category"] or None,
                 TODAY.isoformat()),
            )  # fmt: skip
    _set_budgets(conn)
    conn.close()
    tmp.rename(path)
    return path


def _set_budgets(conn: sqlite3.Connection) -> None:
    fc = budget_forecast(conn, ACCOUNT, KEY)
    assert fc is not None
    rows = category_rows(conn, ACCOUNT, fc.start.data_last_updated)
    proposals = proposals_for(conn, ACCOUNT, rows, fc)
    for category in DEFAULT_CATEGORIES:
        proposal = proposals.get(category)
        cents = proposal.limit_cents if proposal is not None else None
        if cents:
            set_limit(conn, ACCOUNT, category, cents, TODAY)


def sheet(persona_id: str, folder: Path = FOLDER) -> str:
    """What a question writer needs: categories with spending, merchants, recurring payments and
    budgets of one persona. Made-up data only."""
    conn = connect(build(persona_id, folder))
    lines = [f"## {persona_id}", "", f"Data from {_first(conn)} to {DATA_END}; today is {TODAY}."]
    cats = conn.execute(
        "SELECT category, COUNT(*) FROM category_predictions WHERE category IS NOT NULL"
        " GROUP BY category ORDER BY COUNT(*) DESC"
    ).fetchall()
    lines.append("- Categories: " + ", ".join(f"{c} ({n})" for c, n in cats))
    merchants = conn.execute(
        "SELECT merchant, COUNT(*) FROM category_predictions WHERE merchant IS NOT NULL"
        " GROUP BY merchant ORDER BY COUNT(*) DESC, merchant"
    ).fetchall()
    lines.append("- Merchants: " + ", ".join(f"{m} ({n})" for m, n in merchants))
    fc = build_forecast(conn, ACCOUNT, "weekday", None, KEY, futures=0)
    if fc is not None:
        named = sorted({s.name for s in fc.series if s.name and s.active
                        and s.status != "rejected"})  # fmt: skip
        lines.append("- Recurring payments: " + ", ".join(named))
    limits = conn.execute("SELECT category, limit_cents FROM budgets").fetchall()
    budgets = ", ".join(f"{c} ${v / 100:,.0f}" for c, v in limits if v)
    lines.append("- Budgets this month: " + budgets)
    conn.close()
    return "\n".join(lines)


def _first(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT MIN(posted_date) FROM transactions").fetchone()[0])


SHEET_HEAD = f"""# Ask: persona sheet for writing held-out questions

Six made-up development personas (synthetic data only). Every question is asked on {TODAY}
(a Tuesday); each persona's data ends on {DATA_END}. The forecast covers {TODAY} to
{DATA_END + timedelta(days=30)}.

How to write them (spec docs/specs/2026-11-20-ask.md, step 9):
- Write 50 questions the way you would really type them, in English or French (about 1 in 7 in
  French), each for one persona below. Put the persona id before each question.
- Mix roughly as the spec says: half lookups (totals, averages, counts, top N, comparisons, a few
  typos or casual wording), some forecasts and what-ifs, a few budget, safe-to-spend and
  recurring-payment questions, a few ambiguous ones, and a few it should refuse (advice,
  opinions, things not in the data, past the 30-day forecast).
- Do not read eval/ask_dev_set.py first, so your questions are your own wording.
- Write only the questions; the gold plans are written separately (two subagents), and you
  review where they disagree.
- Counts in brackets are the number of rows, so you can tell what is common.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", type=Path, default=FOLDER)
    parser.add_argument("--write", type=Path, help="write the sheet as Markdown to this file")
    args = parser.parse_args(argv)
    text = SHEET_HEAD + "\n" + "\n\n".join(sheet(pid, args.folder) for pid in PERSONAS) + "\n"
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
