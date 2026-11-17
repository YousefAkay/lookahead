"""Honesty check of safe-to-spend on the synthetic development personas (never held-out ones).

Method in DECISIONS.md (2026-10-11, "Safe-to-spend", the honesty check). At each of the 40 weekly
backtest origins of the 20 development personas of the 12-month set: the safe amount as of the
origin (rule zero: rows posted by then only); the actual everyday spending a day over its window
(the stream the forecaster calls everyday spending, read from the rows after the origin and
classified with the rows up to the window's end); whether the observed closing balance stayed at
or above the buffer on every day of the window. If the amount means what it says, about 80 percent
or more of the origins that spent no more than it stayed above the buffer.

    python -m eval.safe_to_spend_check     # writes docs/results/safe_to_spend_check_<date>.md
"""

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from eval.backtest import origins, realized_balances
from eval.personas_db import persona_db
from lookahead.flows import Txn, flow_types
from lookahead.forecast.spending import spending_by_day
from lookahead.generator.archetypes import assert_development_only, development_ids
from lookahead.recurring_store import series_as_of
from lookahead.resolve import load_user_decisions
from lookahead.safe_to_spend import safe_to_spend

WORK = Path("data/backtest/safe_to_spend")
# Each run writes a new dated file (the first, 2026-10-11, is safe_to_spend_check.md).
RESULTS = Path("docs/results")
# Near the edge: a safe amount at most this many times the usual (DECISIONS, 2026-11-17).
NEAR_EDGE_TIMES = 2

Record = dict[str, Any]


def actual_everyday(conn: Any, account: str, first: date, last: date) -> int:
    """The everyday spending in cents a day over first..last, as the forecaster's stream would
    classify it with the rows up to `last` (what happened, never fed back into a forecast)."""
    rows = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ? AND posted_date <= ?", (account, last.isoformat()),
    ).fetchall()  # fmt: skip
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in rows]
    row_of = {r[5]: r[0] for r in rows}
    series, _ = series_as_of(conn, account, last, None)
    flows = flow_types(txns, load_user_decisions(conn, last))
    in_series = {row_of[fp] for s in series if s.status != "rejected"
                 for fp in s.evidence if fp in row_of}  # fmt: skip
    daily = spending_by_day(txns, flows, in_series, last)
    days = (last - first).days + 1
    return round(sum(daily.get(first + timedelta(days=k), 0) for k in range(days)) / days)


def check_persona(persona_id: str) -> list[Record]:
    assert_development_only([persona_id])
    out = []
    with persona_db(persona_id, WORK / "work") as db:
        for origin in origins():
            s = safe_to_spend(db.conn, persona_id, as_of=origin)
            assert s is not None
            realized = realized_balances(db.conn, persona_id, origin, s.days)
            out.append({
                "persona": persona_id, "archetype": db.persona.archetype,
                "origin": origin.isoformat(), "days": s.days, "safe": s.cents_per_day,
                "usual": s.usual_cents_per_day,
                "actual": actual_everyday(db.conn, persona_id, s.first_day, s.last_day),
                "stayed": all(b >= s.buffer_cents for b in realized),
                "below_at_origin": s.start_balance_cents < s.buffer_cents,
            })  # fmt: skip
    return out


def _share(rows: list[Record]) -> str:
    if not rows:
        return "-"
    n = sum(r["stayed"] for r in rows)
    return f"{n / len(rows):.0%} ({n} of {len(rows)}, {len({r['persona'] for r in rows})} personas)"


def near_edge(records: list[Record]) -> list[Record]:
    """Origins with a safe amount at most NEAR_EDGE_TIMES the usual everyday spending."""
    return [r for r in records if r["safe"] is not None and r["usual"] > 0
            and r["safe"] <= NEAR_EDGE_TIMES * r["usual"]]  # fmt: skip


def reading(records: list[Record]) -> str:
    """The reading rule written before the run (DECISIONS, 2026-11-17), on the near-the-edge
    origins that spent at or below the safe amount."""
    rows = [r for r in near_edge(records) if r["actual"] <= r["safe"]]
    personas = len({r["persona"] for r in rows})
    if len(rows) < 30 or personas < 5:
        return (f"too few origins near the edge to say ({len(rows)} origins, "
                f"{personas} personas)")  # fmt: skip
    share = sum(r["stayed"] for r in rows) / len(rows)
    if share >= 0.8:
        return "the 80 percent holds near the edge"
    if share >= 0.7:
        return "slightly optimistic near the edge"
    return "optimistic near the edge: the 80 percent does not hold there"


def report(records: list[Record]) -> str:
    with_amount = [r for r in records if r["safe"] is not None]
    within = [r for r in with_amount if r["actual"] <= r["safe"]]
    over = [r for r in with_amount if r["actual"] > r["safe"]]
    none = [r for r in records if r["safe"] is None]
    edge = near_edge(records)
    edge_within = [r for r in edge if r["actual"] <= r["safe"]]
    edge_over = [r for r in edge if r["actual"] > r["safe"]]
    lines = [
        "# Safe-to-spend honesty check (synthetic development personas)", "",
        "Made with `python -m eval.safe_to_spend_check`. Method in DECISIONS.md (2026-10-11, "
        "safe-to-spend). 12-month set, development personas only, the 40 weekly backtest "
        f"origins: {len(records)} origins, {len({r['persona'] for r in records})} personas. "
        "Stayed above = the observed closing balance was at or above the buffer on every day of "
        "the window.", "",
        f"Near the edge = a safe amount at most {NEAR_EDGE_TIMES} times the usual everyday "
        "spending (DECISIONS, 2026-11-17, written before the run).", "",
        "| Origins | Stayed above the buffer |", "|---|---|",
        f"| a safe amount, actual everyday spending at or below it | {_share(within)} |",
        f"| a safe amount, actual everyday spending above it | {_share(over)} |",
        f"| no safe amount | {_share(none)} |",
        f"| near the edge, actual everyday spending at or below it | {_share(edge_within)} |",
        f"| near the edge, actual everyday spending above it | {_share(edge_over)} |",
        f"| all | {_share(records)} |", "",
        "## By archetype (a safe amount, spending at or below it)", "",
        "| Archetype | Stayed above |", "|---|---|",
    ]  # fmt: skip
    for code in sorted({r["archetype"] for r in records}):
        lines.append(f"| {code} | {_share([r for r in within if r['archetype'] == code])} |")
    ratio = [r["safe"] / r["usual"] for r in with_amount if r["usual"] > 0]
    if ratio:
        ratio.sort()
        lines += ["", f"Safe amount against the usual everyday spending (origins with both): "
                  f"median {ratio[len(ratio) // 2]:.1f} times (n = {len(ratio)})."]  # fmt: skip
    lines += ["", f"Reading (rule written before the run): {reading(records)}."]
    lines += ["", "Origins overlap within a persona; the persona counts are the honest n."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.safe_to_spend_check", description=__doc__)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args(argv)
    ids = args.only or development_ids()
    assert_development_only(ids)
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        records = [r for rows in pool.map(check_persona, ids) for r in rows]
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "records.json").write_text(json.dumps(records), encoding="utf-8")
    out = RESULTS / f"safe_to_spend_check_{date.today().isoformat()}.md"
    out.write_text(report(records), encoding="utf-8")
    print(report(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
