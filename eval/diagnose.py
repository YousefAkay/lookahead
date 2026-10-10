"""Where the 30-day error comes from (diagnosis, development personas only; nothing is fixed here).

Method in DECISIONS.md (2026-10-10, "Where the 30-day error comes from"). For each origin the
mean future's 30-day change is split into parts by the generator's answer key, next to what
really happened in each part, and every irregular-income row of the 13 weeks before the origin is
counted by what the forecaster did with it (placed as a series, resampled by the weekly draw,
set aside as a large deposit or a pass-through).

    python -m eval.diagnose                        # writes docs/results/diagnosis_2026-10-10.md
    python -m eval.diagnose --only v2-FREE-01 --origins 4 --futures 100   # a quick look
"""

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from eval.backtest import HORIZON, METHOD, origins, realized_balances
from eval.personas_db import PersonaDb, persona_db
from lookahead.flows import Txn, flow_types
from lookahead.forecast.path import Forecast, build_forecast
from lookahead.forecast.simulate import BLOCK, pool_week_starts
from lookahead.forecast.spending import BigEvent, _streams
from lookahead.generator.schedules import GeneratedTxn
from lookahead.resolve import load_user_decisions

PERSONAS = (
    "v2-ITIGHT-01", "v2-ITIGHT-02", "v2-ITIGHT-03", "v2-IFAM-01", "v2-IFAM-02", "v2-IFAM-03",
    "v2-FREE-01", "v2-SAL-01", "v2-SAL-02", "v2-SAL-03", "v2-TIGHT-01", "v2-TIGHT-02",
    "v2-TIGHT-03",
)  # fmt: skip
PARTS = ("tuition", "family", "other_irregular", "regular_income", "bills", "one_time", "fees",
         "everyday")  # fmt: skip
PART_NAMES = {
    "tuition": "Tuition payments",
    "family": "Family deposits and top-ups",
    "other_irregular": "Other irregular income",
    "regular_income": "Regular income (pay, allowance)",
    "bills": "Recurring bills (rent, bills, subscriptions)",
    "one_time": "Big one-time events",
    "fees": "Fees",
    "everyday": "Everyday spending",
}
IRREGULAR = ("family", "other_irregular")
WATCHED = ("tuition", "family", "other_irregular", "one_time")  # treatment is counted for these
STATUSES = ("placed as a series", "resampled (weekly draw)", "set aside: large deposit",
            "set aside: large payment", "set aside: pass-through", "not used")  # fmt: skip
POOL_DAYS = 91
DEFAULT_OUT = Path("docs/results/diagnosis_2026-10-10.md")
RAW_OUT = Path("data/backtest/diagnosis")


def part_of(t: GeneratedTxn) -> str:
    """The part a generated row belongs to (DECISIONS.md, 2026-10-10)."""
    sid = t.series_id or ""
    if sid.endswith("-tuition"):
        return "tuition"
    if sid.endswith(("-tuition-funding", "-tuition-topup", "-family")):
        return "family"
    if sid.endswith(("-gigs", "-aid")):
        return "other_irregular"
    if sid.endswith(("-pay", "-allowance")):
        return "regular_income"
    if sid:
        return "bills"
    if t.one_off:
        return "one_time"
    if t.category == "Fees & interest":
        return "fees"
    return "everyday"


def _majority(parts: list[str]) -> str:
    return Counter(parts).most_common(1)[0][0] if parts else "everyday"


def expected_from_pool(
    values_by_day: Mapping[date, float], week_starts: list[date], horizon: int = HORIZON
) -> float:  # fmt: skip
    """The expected total over `horizon` days of a stream drawn week by week from these weeks:
    day k of the horizon takes day k % 7 of a week drawn at random, so its expectation is the
    average of that day over the weeks."""
    if not week_starts:
        return 0.0
    total = 0.0
    for k in range(horizon):
        d = k % BLOCK
        total += sum(values_by_day.get(w + timedelta(days=d), 0) for w in week_starts) / len(
            week_starts
        )
    return total


def _event_status(event: BigEvent) -> str:
    if event.group == "Pass-through":
        return "set aside: pass-through"
    return "set aside: large deposit" if event.cents > 0 else "set aside: large payment"


def origin_parts(db: PersonaDb, origin: date, futures: int) -> dict[str, Any]:
    """Expected and happened, by part, for one origin; and the treatment of watched rows."""
    conn, pid, labels = db.conn, db.persona.persona.persona_id, db.labels
    forecast: Forecast | None = build_forecast(conn, pid, METHOD, origin, None, HORIZON, futures)
    if forecast is None or forecast.simulation is None or forecast.inputs is None:
        raise ValueError(f"no forecast for {pid} as of {origin}")
    start = forecast.start
    as_of = start.data_last_updated
    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ? AND posted_date <= ?", (pid, as_of.isoformat()),
    ).fetchall()  # fmt: skip
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    row_of = {r[5]: r[0] for r in raw}
    flows = flow_types(txns, load_user_decisions(conn, origin))
    series = [s for s in forecast.series if s.status != "rejected"]
    in_series = {row_of[fp] for s in series for fp in s.evidence if fp in row_of}
    streams = _streams(txns, flows, in_series, as_of)
    expected: dict[str, float] = defaultdict(float)
    by_key = {s.key: s for s in series}
    for item in forecast.items:
        s = by_key.get(item.series_key)
        parts = [part_of(labels[row_of[fp]]) for fp in (s.evidence if s else ()) if fp in row_of]
        expected[_majority(parts)] += item.cents
    weeks = pool_week_starts(start, forecast.inputs.history_start)
    by_part_day: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for t, _, cents in streams.spending:
        by_part_day[part_of(labels[t.row_id])][t.posted_date] -= cents
    for t, _, cents in streams.deposits:
        by_part_day[part_of(labels[t.row_id])][t.posted_date] += cents
    for part, values in by_part_day.items():
        expected[part] += expected_from_pool(values, weeks)
    expected["fees"] -= forecast.fees_per_day * HORIZON
    happened: dict[str, float] = defaultdict(float)
    last = origin + timedelta(days=HORIZON)
    later = conn.execute(
        "SELECT id, amount_cents FROM transactions WHERE account = ? AND posted_date > ?"
        " AND posted_date <= ?", (pid, origin.isoformat(), last.isoformat()),
    ).fetchall()  # fmt: skip
    events_in_window: dict[str, list[int]] = defaultdict(list)
    for row_id, cents in later:
        part = part_of(labels[row_id])
        happened[part] += cents
        events_in_window[part].append(cents)
    realized = realized_balances(conn, pid, origin)
    if realized[-1] - start.balance_cents != round(sum(happened.values())):
        raise ValueError("the parts do not add up to the observed change")
    # Treatment of the watched rows of the 13 weeks before the origin.
    spending_ids = {t.row_id for t, _, _ in streams.spending}
    deposit_ids = {t.row_id for t, _, _ in streams.deposits}
    events = {(e.day, abs(e.cents), e.cents > 0): e for e in streams.events}
    treatment: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    first = as_of - timedelta(days=POOL_DAYS - 1)
    for t in txns:
        part = part_of(labels[t.row_id])
        if part not in WATCHED or t.posted_date < first:
            continue
        if t.row_id in in_series:
            status = "placed as a series"
        elif t.row_id in spending_ids or t.row_id in deposit_ids:
            status = "resampled (weekly draw)"
        else:
            event = events.get((t.posted_date, abs(t.amount_cents), t.amount_cents > 0))
            status = _event_status(event) if event else "not used"
        treatment[part][status].append(t.amount_cents)
    sim = forecast.simulation
    return {
        "persona": pid, "archetype": db.persona.archetype, "origin": origin.isoformat(),
        "buffer_cents": forecast.buffer_cents, "start_cents": start.balance_cents,
        "in_shortfall_at_origin": start.balance_cents < forecast.buffer_cents,
        "expected": {p: expected.get(p, 0.0) for p in PARTS},
        "happened": {p: happened.get(p, 0.0) for p in PARTS},
        "events": {p: events_in_window.get(p, []) for p in PARTS},
        "treatment": {p: dict(v) for p, v in treatment.items()},
        "p50_30": sim.p50[HORIZON - 1], "realized_30": realized[-1],
        "median_lowest_cents": sim.median_lowest_cents, "realized_lowest_cents": min(realized),
        "has_income_series": forecast.has_income,
    }  # fmt: skip


def run(personas: tuple[str, ...], days: list[date], futures: int, work: Path,
        raw_out: Path | None) -> list[dict[str, Any]]:  # fmt: skip
    records: list[dict[str, Any]] = []
    for n, pid in enumerate(personas, start=1):
        with persona_db(pid, work) as db:
            rows = [origin_parts(db, origin, futures) for origin in days]
        records += rows
        if raw_out is not None:
            raw_out.mkdir(parents=True, exist_ok=True)
            (raw_out / f"{pid}.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
            )
        print(f"[{n}/{len(personas)}] {pid}: {len(rows)} origins", flush=True)
    return records


# --- The report ---


def _d(cents: float) -> str:
    x = cents / 100
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _group(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        out[r["archetype"]].append(r)
    order = ["ITIGHT", "IFAM", "FREE", "SAL", "TIGHT"]
    return {k: out[k] for k in order if k in out}


def _n(rows: list[dict[str, Any]]) -> str:
    return f"{len({r['persona'] for r in rows})} personas, {len(rows)} origins"


def error_table(records: list[dict[str, Any]]) -> list[str]:
    """Mean signed error by part (expected minus happened; + is optimistic), then the mean
    absolute error by part, per archetype."""
    groups = _group(records)
    head = "| Part | " + " | ".join(f"{k} ({_n(v)})" for k, v in groups.items()) + " |"
    sep = "|---|" + "---|" * len(groups)
    lines = ["Mean signed 30-day error by part (expected minus happened; positive = the forecast "
             "was too optimistic about that part), then the mean absolute error in brackets.",
             "", head, sep]  # fmt: skip
    for p in PARTS:
        cells = []
        for rows in groups.values():
            e = [r["expected"][p] - r["happened"][p] for r in rows]
            cells.append(f"{_d(_mean(e))} ({_d(_mean([abs(x) for x in e]))})")
        lines.append(f"| {PART_NAMES[p]} | " + " | ".join(cells) + " |")
    totals, p50s = [], []
    for rows in groups.values():
        e = [sum(r["expected"].values()) + r["start_cents"] - r["realized_30"] for r in rows]
        totals.append(f"{_d(_mean(e))} ({_d(_mean([abs(x) for x in e]))})")
        q = [r["p50_30"] - r["realized_30"] for r in rows]
        p50s.append(f"{_d(_mean(q))} ({_d(_mean([abs(x) for x in q]))})")
    lines.append("| **Mean future, all parts** | " + " | ".join(totals) + " |")
    lines.append("| Middle future (p50), for comparison | " + " | ".join(p50s) + " |")
    return lines


def events_table(records: list[dict[str, Any]]) -> list[str]:
    """How often each part happens in a 30-day window, and how big it is."""
    groups = _group(records)
    lines = ["Rows in the 30 days after each origin, by part: share of windows with at least one "
             "row, rows per window, and the median and largest row size (absolute).", "",
             "| Part | " + " | ".join(groups) + " |", "|---|" + "---|" * len(groups)]  # fmt: skip
    for p in PARTS:
        if p == "everyday":
            continue
        cells = []
        for rows in groups.values():
            having = [r for r in rows if r["events"][p]]
            sizes = [abs(c) for r in rows for c in r["events"][p]]
            if not sizes:
                cells.append("none")
                continue
            per = _mean([len(r["events"][p]) for r in rows])
            cells.append(f"{len(having)}/{len(rows)} windows, {per:.1f} rows, median "
                         f"{_d(statistics.median(sizes))}, max {_d(max(sizes))}")  # fmt: skip
        lines.append(f"| {PART_NAMES[p]} | " + " | ".join(cells) + " |")
    return lines


def treatment_table(records: list[dict[str, Any]]) -> list[str]:
    """What the forecaster did with each watched row of the 13 weeks before an origin. A row is
    counted once per origin that sees it (an origin sees about 13 weeks), so these are
    row-origin pairs; the dollars are their absolute sum."""
    groups = _group(records)
    lines = ["What the forecaster did with the rows of the 13 weeks before each origin "
             "(row-origin pairs, then their absolute dollars). Each row is seen by about 13 "
             "origins.", ""]  # fmt: skip
    for arch, rows in groups.items():
        lines.append(f"**{arch}** ({_n(rows)})")
        lines.append("")
        lines.append("| Part | " + " | ".join(STATUSES) + " |")
        lines.append("|---|" + "---|" * len(STATUSES))
        for p in WATCHED:
            counts: Counter[str] = Counter()
            dollars: Counter[str] = Counter()
            for r in rows:
                for status, amounts in r["treatment"].get(p, {}).items():
                    counts[status] += len(amounts)
                    dollars[status] += sum(abs(a) for a in amounts)
            if not counts:
                continue
            cells = [f"{counts[s]} ({_d(dollars[s])})" if counts[s] else "0" for s in STATUSES]
            lines.append(f"| {PART_NAMES[p]} | " + " | ".join(cells) + " |")
        lines.append("")
    return lines


def lowest_table(records: list[dict[str, Any]]) -> list[str]:
    """Lowest-balance bias (forecast median lowest minus observed lowest; positive = optimistic)
    by what the next 30 days held."""

    def kind(r: dict[str, Any]) -> str:
        has_t = bool(r["events"]["tuition"])
        has_f = bool(r["events"]["family"]) or bool(r["events"]["other_irregular"])
        if has_t and has_f:
            return "tuition and irregular income"
        if has_t:
            return "tuition, no irregular income"
        if has_f:
            return "irregular income, no tuition"
        return "neither"

    groups = _group(records)
    kinds = ("tuition, no irregular income", "tuition and irregular income",
             "irregular income, no tuition", "neither")  # fmt: skip
    lines = ["Lowest-balance bias (forecast median lowest minus observed lowest; positive = too "
             "optimistic), by what the 30 days after the origin held. Cell: bias (origins).", "",
             "| Window held | " + " | ".join(groups) + " |",
             "|---|" + "---|" * len(groups)]  # fmt: skip
    for k in kinds:
        cells = []
        for rows in groups.values():
            sel = [r["median_lowest_cents"] - r["realized_lowest_cents"] for r in rows
                   if kind(r) == k]  # fmt: skip
            cells.append(f"{_d(_mean(sel))} ({len(sel)})" if sel else "-")
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    cells = []
    for rows in groups.values():
        sel = [r["median_lowest_cents"] - r["realized_lowest_cents"] for r in rows]
        cells.append(f"{_d(_mean(sel))} ({len(sel)})")
    lines.append("| all | " + " | ".join(cells) + " |")
    return lines


def per_persona_table(records: list[dict[str, Any]]) -> list[str]:
    """The three biggest parts of the error per persona (mean signed, dollars)."""
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by[r["persona"]].append(r)
    lines = ["Per persona: mean signed 30-day error of the mean future, and its three largest "
             "parts (mean signed).", "", "| Persona | Origins | Error | Largest parts |",
             "|---|---|---|---|"]  # fmt: skip
    for pid, rows in by.items():
        total = _mean([sum(r["expected"].values()) + r["start_cents"] - r["realized_30"]
                       for r in rows])  # fmt: skip
        parts = {p: _mean([r["expected"][p] - r["happened"][p] for r in rows]) for p in PARTS}
        top = sorted(parts.items(), key=lambda kv: -abs(kv[1]))[:3]
        lines.append(
            f"| {pid} | {len(rows)} | {_d(total)} | "
            + "; ".join(f"{PART_NAMES[p]} {_d(v)}" for p, v in top)
            + " |"
        )
    return lines


def report(records: list[dict[str, Any]], futures: int) -> str:
    out = [
        "# Diagnosis: where the 30-day error comes from (development personas, synthetic)",
        "",
        f"Made with `python -m eval.diagnose` (seed 42, {futures} futures, weekday method, "
        f"{_n(records)}). Method: DECISIONS.md, 2026-10-10. All origins, ordinary and already "
        "in shortfall together. Dollars are rounded. No change was made to the forecaster.",
        "",
    ]
    for section in (error_table, events_table, lowest_table, treatment_table, per_persona_table):
        out += section(records)
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.diagnose",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--origins", type=int, default=None, help="use every n-th origin")
    parser.add_argument("--futures", type=int, default=500)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--work", type=Path, default=RAW_OUT / "work")
    args = parser.parse_args(argv)
    personas = tuple(args.only) if args.only else PERSONAS
    days = origins()[:: args.origins] if args.origins else origins()
    records = run(personas, days, args.futures, args.work, RAW_OUT)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report(records, args.futures), encoding="utf-8")
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
