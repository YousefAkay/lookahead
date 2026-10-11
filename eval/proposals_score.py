"""Step 2 of the 2026-10-11 plan: a) lump proposals and b) repeated large outflows, scored on
the generator's answer key (development personas only; held-out ids are refused).

Method, answer key and gates in DECISIONS.md (2026-10-11, "Step 2"). At each of the 40 weekly
origins the inputs are read exactly as the forecaster reads them (rows posted by the origin, the
recurring series as of the origin, flow types), and the same functions the forecaster calls make
the proposals and placements; the answer key is only used afterwards, to score them.

    python -m eval.proposals_score                 # both sets, writes the results file
    python -m eval.proposals_score --set 12 --only v2-IFAM-02 --origins 4   # a quick look
    python -m eval.proposals_score --calendar      # b) by calendar position (2026-10-12, step 1)
"""

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from eval.backtest import HORIZON, origins
from eval.personas_db import PersonaDb, persona_db
from lookahead.flows import Txn, flow_types
from lookahead.forecast.start import forecast_start
from lookahead.generator.archetypes import assert_development_only, development_ids
from lookahead.generator.schedules import GeneratedTxn
from lookahead.proposals import (
    LumpProposal,
    big_outflows,
    lump_proposals,
    repeated_outflows,
    sender_of,
)
from lookahead.recurring_store import series_as_of
from lookahead.resolve import load_user_decisions

KEY = b"synthetic personas only: a made-up pseudonym key"
INCOME_KINDS = ("-tuition-funding", "-tuition-topup", "-allowance", "-aid", "-family")
RULES = ("earliest", "year", "gap", "calendar")
# b) by calendar position (DECISIONS.md, 2026-10-12, step 1): the personas that pay tuition from
# chequing, by how many terms a year (summer term or not).
TWO_TERMS = ("IFAM-01", "ITIGHT-01", "DRENT-03")
THREE_TERMS = ("IFAM-02", "ITIGHT-03", "DHOME-02", "DRENT-01")
CALENDAR_OUT = Path("docs/results/proposals_scores_2026-10-12.md")
MATCH_DAYS = 10  # b) a placement is right within 10 days ...
MATCH_SHARE = 0.20  # ... and 20 percent of the amount
OUT = Path("docs/results/proposals_scores_2026-10-11.md")
RAW = Path("data/backtest/proposals")

Record = dict[str, Any]


def right_lump(
    p: LumpProposal, origin: date, labels: dict[int, GeneratedTxn], fp_row: dict[str, int],
    by_series: dict[str, list[tuple[date, int]]],
) -> tuple[list[str], tuple[str, tuple[date, int]] | None]:  # fmt: skip
    """The answer key for a lump proposal: its planted income series, and the nearest of their
    next rows (after the origin) that falls inside the window, if any (then it is right)."""
    planted = sorted({labels[fp_row[fp]].series_id or "" for fp in p.evidence}
                     & {s for s in by_series if s.endswith(INCOME_KINDS)})  # fmt: skip
    hits = []
    for s in planted:
        nxt = next(((d, c) for d, c in by_series[s] if d > origin), None)
        if nxt is not None and p.first <= nxt[0] <= p.last:
            hits.append((s, nxt))
    best = min(hits, key=lambda h: abs((h[1][0] - p.expected).days)) if hits else None
    return planted, best


def series_rows(labels: dict[int, GeneratedTxn]) -> dict[str, list[tuple[date, int]]]:
    """Each planted series' rows as (posted date, cents), oldest first."""
    by_series: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for t in labels.values():
        if t.series_id:
            by_series[t.series_id].append((t.posted_date, t.amount_cents))
    for rows in by_series.values():
        rows.sort()
    return by_series


def _inputs(db: PersonaDb, origin: date) -> tuple[list[Txn], dict, set[int], dict[int, str], Any]:
    """What the forecaster reads at `origin`."""
    conn, pid = db.conn, db.persona.persona.persona_id
    start = forecast_start(conn, pid, origin)
    assert start is not None
    rows = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ? AND posted_date <= ?",
        (pid, start.data_last_updated.isoformat()),
    ).fetchall()
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in rows]
    row_of = {r[5]: r[0] for r in rows}
    series, _ = series_as_of(conn, pid, origin, None)
    user = load_user_decisions(conn, origin)
    flows = flow_types(txns, user)
    in_series = {row_of[fp] for s in series if s.status != "rejected"
                 for fp in s.evidence if fp in row_of}  # fmt: skip
    return txns, flows, in_series, {r[0]: r[5] for r in rows}, (start, user)


def score_persona(persona_id: str, n_origins: int | None = None) -> Record:
    """Every proposal, placement and answer-key event of one persona, as plain records."""
    assert_development_only([persona_id])
    out: Record = {"persona": persona_id, "lumps": [], "a": [], "a_findable": [], "b": [],
                   "b_events": []}  # fmt: skip
    days = origins()[:: max(1, 40 // n_origins)][:n_origins] if n_origins else origins()
    with persona_db(persona_id, RAW / "work") as db:
        out["archetype"] = db.persona.archetype
        labels = db.labels
        by_series = series_rows(labels)
        stored = db.conn.execute(
            "SELECT id, fingerprint FROM transactions WHERE account = ?", (persona_id,)
        )
        fp_row = {fp: rid for rid, fp in stored}

        def next_row(series_id: str, after: date) -> tuple[date, int] | None:
            return next(((d, c) for d, c in by_series[series_id] if d > after), None)

        for origin in days:
            txns, flows, in_series, fps, (start, user) = _inputs(db, origin)
            as_of, last = start.data_last_updated, start.last_day
            horizon_end = origin + timedelta(days=HORIZON)
            # --- a) ---
            proposals, lumps = lump_proposals(txns, flows, in_series, as_of, start.first_day,
                                              last, KEY, fps)  # fmt: skip
            for t, _, _ in lumps:
                out["lumps"].append({"origin": origin.isoformat(),
                                     "named": bool(sender_of(t.text))})  # fmt: skip
            right_series: set[str] = set()
            for p in proposals:
                planted, best = right_lump(p, origin, labels, fp_row, by_series)
                for s in planted:
                    nxt = next_row(s, origin)
                    if nxt is not None and p.first <= nxt[0] <= p.last:
                        right_series.add(s)
                out["a"].append({
                    "origin": origin.isoformat(), "key": p.key, "lumps": len(p.evidence),
                    "right": best is not None, "planted": planted,
                    "date_error": abs((best[1][0] - p.expected).days) if best else None,
                    "size_error": abs(p.typical_cents - best[1][1]) / best[1][1] if best else None,
                })  # fmt: skip
            lump_series = Counter(labels[t.row_id].series_id for t, _, _ in lumps)
            for s, n in lump_series.items():
                if not s or not s.endswith(INCOME_KINDS) or n < 2:
                    continue
                nxt = next_row(s, origin)
                if nxt is not None and nxt[0] <= horizon_end:
                    out["a_findable"].append({"origin": origin.isoformat(), "series": s,
                                              "hit": s in right_series})  # fmt: skip
            # --- b) ---
            groups = big_outflows(txns, flows, in_series, as_of, KEY, user)
            tuition_payments_by: dict[str, int] = {}
            for rows in groups.values():
                for t, _ in rows:
                    sid = labels[t.row_id].series_id or ""
                    if sid.endswith("-tuition"):
                        tuition_payments_by[sid] = len(rows)
            matched: dict[str, set[tuple[str, str]]] = {rule: set() for rule in RULES}
            for rule in RULES:
                placed, offers = repeated_outflows(txns, flows, in_series, as_of, start.first_day,
                                                   last, KEY, fps, rule, user)  # type: ignore[arg-type]  # fmt: skip
                for p in placed:
                    sids = {labels[fp_row[fp]].series_id or "" for fp in p.evidence}
                    tuition = sorted(s for s in sids if s.endswith("-tuition"))
                    match = None
                    for s in tuition:
                        for d, c in by_series[s]:
                            near = abs((d - p.day).days) <= MATCH_DAYS
                            close = abs(c - p.cents) <= MATCH_SHARE * abs(p.cents)
                            if d > origin and near and close:
                                if match is None or abs((d - p.day).days) < abs(
                                    (match[1] - p.day).days
                                ):
                                    match = (s, d)
                    if match is not None:
                        matched[rule].add((match[0], match[1].isoformat()))
                    out["b"].append({
                        "origin": origin.isoformat(), "rule": rule, "key": p.key,
                        "right": match is not None, "tuition": bool(tuition),
                        "date_error": abs((match[1] - p.day).days) if match else None,
                        "which": p.rule,
                    })  # fmt: skip
                if rule == "earliest":
                    out.setdefault("b_offers", []).append(
                        {"origin": origin.isoformat(), "offers": len(offers),
                         "tuition_offers": sum(any((labels[fp_row[fp]].series_id or "")
                                                   .endswith("-tuition") for fp in o.evidence)
                                               for o in offers)})  # fmt: skip
            for s in (s for s in by_series if s.endswith("-tuition")):
                for d, _ in by_series[s]:
                    if origin < d <= horizon_end:
                        out["b_events"].append({
                            "origin": origin.isoformat(), "series": s, "day": d.isoformat(),
                            "payments_by_origin": tuition_payments_by.get(s, 0),
                            **{f"hit_{rule}": (s, d.isoformat()) in matched[rule]
                               for rule in RULES},
                        })  # fmt: skip
    return out


# --- Summaries ---


def _pct(num: int, den: int) -> str:
    return "-" if den == 0 else f"{num / den:.0%} ({num}/{den})"


def _med(values: list[float]) -> str:
    return "-" if not values else f"{statistics.median(values):.1f}"


def summarize_a(results: list[Record]) -> Record:
    props = [p for r in results for p in r["a"]]
    findable = [f for r in results for f in r["a_findable"]]
    lumps = [x for r in results for x in r["lumps"]]
    right = [p for p in props if p["right"]]
    false_keys = {(r["persona"], p["key"]) for r in results for p in r["a"] if not p["right"]}
    years = sum(r.get("n_origins", 40) for r in results) * 7 / 365.25
    by_lumps = {k: [p for p in props if min(p["lumps"], 4) == k] for k in (2, 3, 4)}
    return {
        "proposals": len(props), "right": len(right),
        "precision": len(right) / len(props) if props else None,
        "findable": len(findable), "hit": sum(f["hit"] for f in findable),
        "recall": sum(f["hit"] for f in findable) / len(findable) if findable else None,
        "findable_series": len({f["series"] for f in findable}),
        "date_error_median": statistics.median([p["date_error"] for p in right]) if right else None,
        "date_error_mean": statistics.mean([p["date_error"] for p in right]) if right else None,
        "size_error_median": statistics.median([p["size_error"] for p in right]) if right else None,
        "by_lumps": {k: (len(v), sum(p["right"] for p in v)) for k, v in by_lumps.items()},
        "false_distinct": len(false_keys), "persona_years": years,
        "false_per_persona_year": len(false_keys) / years if years else None,
        "lump_pairs": len(lumps), "unnamed": sum(not x["named"] for x in lumps),
        "personas": len(results),
    }  # fmt: skip


def summarize_b(results: list[Record], rule: str = "earliest") -> Record:
    placed = [p for r in results for p in r["b"] if p["rule"] == rule]
    right = [p for p in placed if p["right"]]
    events = [e for r in results for e in r["b_events"]]
    eligible = [e for e in events if e["payments_by_origin"] >= 2]
    hit = f"hit_{rule}"
    return {
        "placements": len(placed), "right": len(right),
        "tuition_placements": sum(p["tuition"] for p in placed),
        "precision": len(right) / len(placed) if placed else None,
        "events": len(events), "events_hit": sum(e[hit] for e in events),
        "eligible": len(eligible), "eligible_hit": sum(e[hit] for e in eligible),
        "recall": sum(e[hit] for e in eligible) / len(eligible) if eligible else None,
        "first_time_events": sum(e["payments_by_origin"] == 1 for e in events),
        "no_payment_events": sum(e["payments_by_origin"] == 0 for e in events),
        "date_error_median": statistics.median([p["date_error"] for p in right]) if right else None,
        "personas": len(results),
    }  # fmt: skip


def _gate_a(s12: Record, sl: Record) -> bool:
    return any(s["precision"] is not None and s["precision"] >= 0.70 and s["recall"] is not None
               and s["recall"] >= 0.50 for s in (s12, sl))  # fmt: skip


def _gate_b(sl: Record) -> bool:
    return (sl["recall"] is not None and sl["recall"] >= 0.50 and sl["precision"] is not None
            and sl["precision"] >= 0.70)  # fmt: skip


def _a_table(title: str, groups: dict[str, list[Record]]) -> list[str]:
    lines = [f"#### {title}", "",
             "| Group | Personas | Proposals | Precision | Findable (origin, series) | Recall | "
             "Date error, median days | Size error, median | False proposals per persona-year "
             "| Lumps without a sender |", "|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for name, rows in groups.items():
        s = summarize_a(rows)
        size = s["size_error_median"]
        false = s["false_per_persona_year"]
        lines.append(
            f"| {name} | {s['personas']} | {s['proposals']} | {_pct(s['right'], s['proposals'])} | "
            f"{s['findable']} ({s['findable_series']} series) | {_pct(s['hit'], s['findable'])} | "
            f"{'-' if s['date_error_median'] is None else s['date_error_median']} | "
            f"{'-' if size is None else f'{size:.0%}'} | "
            f"{'-' if false is None else f'{false:.1f}'} | {_pct(s['unnamed'], s['lump_pairs'])} |"
        )
    return lines + [""]


def _b_table(title: str, groups: dict[str, list[Record]]) -> list[str]:
    lines = [f"#### {title}", "",
             "| Group | Personas | Placements (tuition) | Precision | Recall, payee with 2+ "
             "payments | Recall, all tuition payments in a horizon | First-time cases | Date "
             "error, median days |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for name, rows in groups.items():
        s = summarize_b(rows)
        lines.append(
            f"| {name} | {s['personas']} | {s['placements']} ({s['tuition_placements']}) | "
            f"{_pct(s['right'], s['placements'])} | {_pct(s['eligible_hit'], s['eligible'])} | "
            f"{_pct(s['events_hit'], s['events'])} | {s['first_time_events']} | "
            f"{'-' if s['date_error_median'] is None else s['date_error_median']} |"
        )  # fmt: skip
    return lines + [""]


def _groups(results: list[Record]) -> dict[str, list[Record]]:
    out: dict[str, list[Record]] = {"all": results}
    for r in sorted(results, key=lambda x: x["archetype"]):
        out.setdefault(r["archetype"], []).append(r)
    return out


def report(by_set: dict[str, list[Record]]) -> str:
    lines = ["# Step 2: proposals scored on the answer key (synthetic, development personas)", "",
             "Made with `python -m eval.proposals_score`. Method, answer key and gates in "
             "DECISIONS.md (2026-10-11, step 2). 40 weekly origins per persona; every count is "
             "of (origin, proposal), (origin, series) or (origin, payment) pairs, since origins "
             "within a persona overlap. Each set on its own; never pooled.", ""]  # fmt: skip
    summaries = {}
    for name, results in by_set.items():
        lines += [f"## {name}", ""]
        lines += _a_table("a) Lump proposals", _groups(results))
        lines += _b_table("b) Repeated large outflows (default rule: earliest of a year after "
                          "the latest and the typical gap)", _groups(results))  # fmt: skip
        lines += ["#### b) The two next-date rules alone and the default, all personas", "",
                  "| Rule | Placements | Precision | Recall, payee with 2+ payments |",
                  "|---|---|---|---|"]  # fmt: skip
        for rule in RULES:
            b = summarize_b(results, rule)
            lines.append(f"| {rule} | {b['placements']} | {_pct(b['right'], b['placements'])} | "
                         f"{_pct(b['eligible_hit'], b['eligible'])} |")  # fmt: skip
        lines.append("")
        a = summarize_a(results)
        lines += ["a) proposals by the number of lumps behind them (proposals, right): "
                  + ", ".join(f"{'4+' if k == 4 else k}: {n}, {r}" for k, (n, r)
                              in a["by_lumps"].items()), ""]  # fmt: skip
        summaries[name] = (a, summarize_b(results))
    s12a, s12b = summaries.get("12-month set", (None, None))
    sla, slb = summaries.get("24-month set", (None, None))
    lines += ["## Gates (fixed before computing)", ""]
    if s12a is not None and sla is not None:
        ok_a = _gate_a(s12a, sla)
        lines.append(
            "- a) precision >= 70% and recall >= 50% on at least one set: "
            f"**{'pass' if ok_a else 'fail'}** (precision / recall: 12-month "
            f"{_pct(s12a['right'], s12a['proposals'])} / {_pct(s12a['hit'], s12a['findable'])}; "
            f"24-month {_pct(sla['right'], sla['proposals'])} / "
            f"{_pct(sla['hit'], sla['findable'])})."
        )
    if slb is not None:
        ok_b = _gate_b(slb)
        lines.append(
            "- b) on the 24-month set, recall >= 50% among origins where the payee had 2+ "
            "payments and precision >= 70% (Yousef's addition): "
            f"**{'pass' if ok_b else 'fail'}** (recall "
            f"{_pct(slb['eligible_hit'], slb['eligible'])}, precision "
            f"{_pct(slb['right'], slb['placements'])})."
        )
    return "\n".join(lines) + "\n"


def _terms(results: list[Record]) -> dict[str, list[Record]]:
    """All personas, the two-term and three-term tuition payers, and each archetype."""
    out: dict[str, list[Record]] = {"all": results}
    for name, codes in (("two terms (January, September)", TWO_TERMS),
                        ("three terms (with summer)", THREE_TERMS)):  # fmt: skip
        out[name] = [r for r in results if r["persona"].split("-", 1)[1] in codes]
    for r in sorted(results, key=lambda x: x["archetype"]):
        out.setdefault(r["archetype"], []).append(r)
    return out


def _rule_table(groups: dict[str, list[Record]], rules: tuple[str, ...]) -> list[str]:
    lines = ["| Group | Personas | " + " | ".join(
        f"{rule}: placements (tuition) / precision / recall, payee with 2+ payments / date error"
        for rule in rules) + " |", "|---|---|" + "---|" * len(rules)]  # fmt: skip
    for name, rows in groups.items():
        cells = []
        for rule in rules:
            b = summarize_b(rows, rule)
            date_error = "-" if b["date_error_median"] is None else b["date_error_median"]
            cells.append(f"{b['placements']} ({b['tuition_placements']}) / "
                         f"{_pct(b['right'], b['placements'])} / "
                         f"{_pct(b['eligible_hit'], b['eligible'])} / {date_error}")  # fmt: skip
        lines.append(f"| {name} | {len(rows)} | " + " | ".join(cells) + " |")
    return lines + [""]


def calendar_report(by_set: dict[str, list[Record]]) -> str:
    """Step 1 of the 2026-10-12 plan: b) by calendar position beside the earlier default rule,
    by set, by number of terms and by archetype, with the gate (on the 24-month set)."""
    lines = ["# Step 1: b) by calendar position, scored on the answer key (synthetic, development "
             "personas)", "",
             "Made with `python -m eval.proposals_score --calendar`. Method, answer key and gate "
             "in DECISIONS.md (2026-10-12, step 1). 40 weekly origins per persona; counts are of "
             "(origin, placement) and (origin, tuition payment) pairs, since origins within a "
             "persona overlap. Recall is among the origins where the payee had 2 or more big "
             "payments by the origin. Date error: median days, right placements. Each set on its "
             "own; never pooled. `earliest` is the earlier default (the proposals run), rerun "
             "here as a check that it reproduces.", ""]  # fmt: skip
    for name, results in by_set.items():
        lines += [f"## {name}", ""]
        lines += _rule_table(_terms(results), ("calendar", "earliest"))
        offers = sum(o["tuition_offers"] for r in results for o in r.get("b_offers", []))
        events = [e for r in results for e in r["b_events"]]
        lines += [f"Tuition payments inside a horizon (origin, payment): {len(events)}; of them "
                  f"with the payee seen once by the origin (first-time cases, no placement "
                  f"possible): {sum(e['payments_by_origin'] == 1 for e in events)}; never seen: "
                  f"{sum(e['payments_by_origin'] == 0 for e in events)}. First-time offers for a "
                  f"tuition payee (origin, offer): {offers}.", ""]  # fmt: skip
    if "24-month set" in by_set:
        b = summarize_b(by_set["24-month set"], "calendar")
        ok = _gate_b(b)
        lines += ["## Gate (fixed before computing)", "",
                  "- b) by calendar position, on the 24-month set: recall >= 50% among origins "
                  "where the payee had 2+ payments, and precision >= 70%: "
                  f"**{'pass' if ok else 'fail'}** (recall "
                  f"{_pct(b['eligible_hit'], b['eligible'])}, precision "
                  f"{_pct(b['right'], b['placements'])}).", ""]  # fmt: skip
    return "\n".join(lines)


def run_set(long: bool, only: list[str] | None, n_origins: int | None, jobs: int) -> list[Record]:
    ids = only or development_ids(long=long)
    assert_development_only(ids)
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(score_persona, ids, [n_origins] * len(ids)))
    for r in results:
        r["n_origins"] = n_origins or 40
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.proposals_score", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("--set", choices=("12", "long", "both"), default="both")
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--origins", type=int, default=None)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--calendar", action="store_true",
                        help="b) by calendar position (2026-10-12, step 1)")  # fmt: skip
    args = parser.parse_args(argv)
    by_set: dict[str, list[Record]] = {}
    if args.set in ("12", "both"):
        by_set["12-month set"] = run_set(False, args.only, args.origins, args.jobs)
    if args.set in ("long", "both"):
        by_set["24-month set"] = run_set(True, args.only, args.origins, args.jobs)
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / ("scores_calendar.json" if args.calendar else "scores.json")).write_text(
        json.dumps(by_set), encoding="utf-8")  # fmt: skip
    text = calendar_report(by_set) if args.calendar else report(by_set)
    (args.out or (CALENDAR_OUT if args.calendar else OUT)).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
