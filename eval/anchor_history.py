"""Experiment 3 (net-flow anchoring): how many origins meet the minimum history. Counts only.

Method in DECISIONS.md (2026-10-17, "Experiment 3"). The trailing figure is used only when the
account has at least 91 days of history before the origin and at least 20 unscheduled rows in the
91 days ending on it. *Unscheduled* means all rows except those in a recurring series and those
the forecaster sets aside as big one-time money or as a pass-through. This counts, and scores
nothing: no forecast is compared with what happened.

    python -m eval.anchor_history berka               # the 200 Berka primary development accounts
    python -m eval.anchor_history synthetic --set 12  # the 20 development personas, 12-month set
    python -m eval.anchor_history synthetic --set long
    python -m eval.anchor_history report              # writes the report in docs/results
"""

import argparse
import json
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from eval.backtest import origins
from eval.berka import DATA, build_db, dip_line_cents, origin_dates
from eval.berka_gate import load
from eval.berka_split import assert_development_only as berka_development_only
from eval.berka_split import development_accounts
from eval.personas_db import persona_db
from lookahead.forecast.path import DEFAULT_OPTIONS, Forecast, build_forecast
from lookahead.generator.archetypes import assert_development_only, development_ids

WINDOW_DAYS = 91
MIN_HISTORY_DAYS = 91
MIN_ROWS = 8  # was 20; changed on 2026-10-17 (DECISIONS.md) after counting, before scoring
CANDIDATE_MINIMA = (5, 8, 10, 12, 15, 20)  # shares at other row minima, for the proposal only
SET_ASIDE = ("Large payment", "Large deposit", "Pass-through")
OUT = DATA / "runs" / "anchor-history"
REPORT = Path("docs/results/anchor_history_2026-10-17.md")


def unscheduled_count(conn: sqlite3.Connection, account: str, origin: date) -> dict[str, int]:
    """History in days before the origin and the unscheduled rows of the last 91 days."""
    f = build_forecast(conn, account, "weekday", origin, None, 30, 10, options=DEFAULT_OPTIONS)
    return count_from_forecast(conn, account, origin, f)


def count_from_forecast(
    conn: sqlite3.Connection, account: str, origin: date, f: Forecast | None
) -> dict[str, int]:
    """The same counts from a forecast already made at `origin`: its series and the rows it sets
    aside. `unscheduled_cents` is the sum of the unscheduled rows (signed cents)."""
    first = conn.execute(
        "SELECT MIN(posted_date) FROM transactions WHERE account = ? AND posted_date <= ?",
        (account, origin.isoformat()),
    ).fetchone()[0]
    history = (origin - date.fromisoformat(first)).days + 1 if first else 0
    start = origin - timedelta(days=WINDOW_DAYS - 1)
    rows = conn.execute(
        "SELECT posted_date, amount_cents, fingerprint FROM transactions WHERE account = ?"
        " AND posted_date BETWEEN ? AND ?",
        (account, start.isoformat(), origin.isoformat()),
    ).fetchall()
    series = f.series if f else ()
    in_series = {fp for s in series if s.status != "rejected" for fp in s.evidence}
    aside = Counter((e.day, e.cents) for e in (f.big_events if f else ()) if e.group in SET_ASIDE)
    n_series = n_aside = n_unscheduled = total = 0
    for day, cents, fingerprint in rows:
        key = (date.fromisoformat(day), cents)
        if fingerprint in in_series:
            n_series += 1
        elif aside[key] > 0:
            aside[key] -= 1
            n_aside += 1
        else:
            n_unscheduled += 1
            total += cents
    return {"history_days": history, "rows": len(rows), "series": n_series,
            "set_aside": n_aside, "unscheduled": n_unscheduled,
            "unscheduled_cents": total}  # fmt: skip


def berka_account(args: tuple[int, pd.DataFrame]) -> dict[str, Any]:
    account, frame = args
    berka_development_only([account])
    path = OUT / f"{account}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    days = origin_dates(frame)
    db = OUT / "work" / f"{account}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = build_db(frame, account, db, dip_line_cents(frame, days[0]))
    try:
        rows = [{"origin": d.isoformat(), **unscheduled_count(conn, str(account), d)}
                for d in days]  # fmt: skip
    finally:
        conn.close()
        db.unlink(missing_ok=True)
    result = {"account": account, "origins": rows}
    path.write_text(json.dumps(result), encoding="utf-8")
    return result


def synthetic_persona(args: tuple[str, bool]) -> dict[str, Any]:
    pid, long = args
    assert_development_only([pid])
    path = OUT / "synthetic" / ("long" if long else "12") / f"{pid}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with persona_db(pid, OUT / "work", 42) as db:
        rows = [{"origin": d.isoformat(), **unscheduled_count(db.conn, pid, d)} for d in origins()]
        archetype = db.persona.archetype
    result = {"persona": pid, "archetype": archetype, "origins": rows}
    path.write_text(json.dumps(result), encoding="utf-8")
    return result


def qualifies(o: dict[str, int], minimum_rows: int = MIN_ROWS) -> bool:
    return o["history_days"] >= MIN_HISTORY_DAYS and o["unscheduled"] >= minimum_rows


def summarize(title: str, results: list[dict[str, Any]], unit: str) -> list[str]:
    origins_all = [o for r in results for o in r["origins"]]
    n = len(origins_all)
    ok = [o for o in origins_all if qualifies(o)]
    short = [o for o in origins_all if o["history_days"] < MIN_HISTORY_DAYS]
    few = [o for o in origins_all if o["history_days"] >= MIN_HISTORY_DAYS
           and o["unscheduled"] < MIN_ROWS]  # fmt: skip
    per_unit = [sum(qualifies(o) for o in r["origins"]) for r in results]
    rows = [float(o["unscheduled"]) for o in origins_all if o["history_days"] >= MIN_HISTORY_DAYS]
    q = np.quantile(rows, [0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0]) if rows else []
    lines = [
        f"## {title}", "",
        f"- Origins: {n:,}. Meeting the minimum history (at least {MIN_HISTORY_DAYS} days before "
        f"the origin and at least {MIN_ROWS} unscheduled rows in the {WINDOW_DAYS} days): "
        f"{len(ok):,} ({len(ok) / n:.1%}).",
        f"- Below it: {len(short):,} with under {MIN_HISTORY_DAYS} days of history, "
        f"{len(few):,} with enough history but under {MIN_ROWS} unscheduled rows.",
        f"- {unit}: {len(results)}; with every origin qualifying: "
        f"{sum(c == len(r['origins']) for c, r in zip(per_unit, results, strict=True))}; "
        f"with no origin qualifying: {sum(c == 0 for c in per_unit)}; with at least half of "
        f"their origins qualifying: "
        f"{sum(c >= len(r['origins']) / 2 for c, r in zip(per_unit, results, strict=True))}.",
        "- Unscheduled rows in the 91 days, origins with the history (min / 10% / 25% / median / "
        "75% / 90% / max): " + (" / ".join(f"{x:.0f}" for x in q) if rows else "-") + ".", "",
        "| minimum unscheduled rows | origins qualifying | " + unit + " with every origin |",
        "|---|---|---|",
    ]  # fmt: skip
    for m in CANDIDATE_MINIMA:
        count = sum(qualifies(o, m) for o in origins_all)
        every = sum(all(qualifies(o, m) for o in r["origins"]) for r in results)
        lines.append(f"| {m}{' (the rule)' if m == MIN_ROWS else ''} | {count:,} "
                     f"({count / n:.1%}) | {every} |")  # fmt: skip
    lines.append("")
    return lines


def report() -> str:
    ids = development_accounts("primary")
    berka_results = [json.loads((OUT / f"{a}.json").read_text("utf-8")) for a in ids]
    lines = ["# Experiment 3: how many origins meet the minimum history (counts only)", "",
             "Made with `python -m eval.anchor_history report`. Method in DECISIONS.md "
             "(2026-10-17). Nothing is scored; no held-out account is read.", ""]  # fmt: skip
    lines += summarize("Berka, primary development accounts", berka_results, "accounts")
    for long, name in ((False, "12-month"), (True, "24-month")):
        folder = OUT / "synthetic" / ("long" if long else "12")
        res = [json.loads(p.read_text("utf-8")) for p in sorted(folder.glob("*.json"))]
        lines += summarize(f"Synthetic, {name} set (20 development personas)", res, "personas")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.anchor_history",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=["berka", "synthetic", "report"])
    parser.add_argument("--set", choices=["12", "long"], default="12")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args(argv)
    if args.command == "report":
        text = report()
        REPORT.write_text(text, encoding="utf-8")
        print(text)
        return 0
    if args.command == "synthetic":
        long = args.set == "long"
        jobs = [(pid, long) for pid in development_ids(long=long)]
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for n, r in enumerate(pool.map(synthetic_persona, jobs), 1):
                print(f"[{n}/{len(jobs)}] {r['persona']}", flush=True)
        return 0
    ids = development_accounts("primary")
    berka_development_only(ids)
    t = load(DATA)
    t = t[t["account_id"].isin(ids)].reset_index(drop=True)  # only development rows are kept
    frames = {a: g.reset_index(drop=True) for a, g in t.groupby("account_id")}
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for n, _ in enumerate(pool.map(berka_account, [(a, frames[a]) for a in ids]), 1):
            if n % 20 == 0:
                print(f"[{n}/{len(ids)}] accounts", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
