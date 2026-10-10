"""The recurring-placement fix (b), on the synthetic development personas (DECISIONS.md,
2026-10-12). Two runs of the development backtest (20 personas, 40 weekly origins, 500 futures,
seed 42, raw chances, the original and the new "caught"): the forecaster as merged (keeps-coming
on, the fix off) and the same with (b), series whose last payment is more than 1.5 periods back
are not placed. Written to docs/results/recurring_fix_2026-10-12.md. Development personas only.

    python -m eval.recurring_fix_eval                  # runs what is missing, then compares
    python -m eval.recurring_fix_eval --compare-only
"""

import argparse
import gc
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from eval.backtest import (
    DEFAULT_OUT,
    FUTURES,
    METHOD,
    _check_settings,
    _saved_origins,
    keeps_name,
    origins,
    run_origin,
    select_personas,
    version_settings,
)
from eval.compare_runs import column, table
from eval.keeps_coming_eval import _d, _pct, archetype_lines, verdict
from eval.metrics import (
    PROVISIONAL_THRESHOLD,
    alert_metrics,
    forecast_metrics,
    group_origins,
    load_records,
)
from lookahead.db import connect
from lookahead.forecast.path import ForecastOptions
from lookahead.forecast.recurring_filter import RecurringFix
from lookahead.forecast.spending import DEFAULT_KEEPS
from lookahead.generator.archetypes import END, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents

Record = dict[str, Any]
VARIANTS = ("off", "b")
OPTIONS = {"off": ForecastOptions(), "b": ForecastOptions(recurring_fix=RecurringFix())}
NAMES = {"off": "current forecaster (keeps-coming on)", "b": "with (b): possibly ended not placed"}
DEFAULT_OUT_FILE = Path("docs/results/recurring_fix_2026-10-12.md")
SAME_FIELDS = ("p10", "p50", "p90", "point", "median_lowest_cents", "shortfall_probability",
               "lowest_cents", "n_items")  # fmt: skip


def folders(root: Path) -> dict[str, Path]:
    return {v: root / f"recurring-fix-{v}" / "full" for v in VARIANTS}


def run_persona_with(
    persona_id: str, out_dir: Path, days: list[date], futures: int, seed: int, work_dir: Path,
    variant: str,
) -> int:  # fmt: skip
    """`eval.backtest.run_persona` with forecaster options (resumable, one persona at a time)."""
    persona = build_persona(persona_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / f"{persona_id}.jsonl"
    _check_settings(
        out_dir / f"{persona_id}.meta.json",
        {"futures": futures, "seed": seed, "method": METHOD, **version_settings(persona_id),
         "keeps_coming": keeps_name(DEFAULT_KEEPS), "recurring_fix": variant},
    )  # fmt: skip
    todo = [d for d in days if d.isoformat() not in _saved_origins(result_path)]
    if not todo:
        return 0
    folder = work_dir / persona_id
    folder.mkdir(parents=True, exist_ok=True)
    conn = None
    try:
        export, _ = write_persona(persona.persona, folder, seed, persona.start, END)
        conn = connect(folder / "run.db")
        store_exports(
            conn, persona_id, [(export.name, parse_export(export))], datetime(2026, 10, 1)
        )
        set_safety_buffer_cents(conn, persona.buffer_cents)
        with result_path.open("a", encoding="utf-8") as out:
            for origin in todo:
                record = run_origin(conn, persona_id, "synthetic", origin, futures,
                                    persona.archetype, persona.held_out,
                                    options=OPTIONS[variant])  # fmt: skip
                record["recurring_fix"] = variant
                out.write(json.dumps(record) + "\n")
                out.flush()
    finally:
        if conn is not None:
            conn.close()
        gc.collect()
        shutil.rmtree(folder, ignore_errors=True)
    return len(todo)


def run_all(personas: list[str], work: Path, root: Path, futures: int = FUTURES) -> None:
    where = folders(root)
    for variant in VARIANTS:
        for n, pid in enumerate(personas, start=1):
            done = run_persona_with(pid, where[variant], origins(), futures, 42, work, variant)
            print(f"[{variant}] [{n}/{len(personas)}] {pid}: {done} new origins", flush=True)


def compare_personas(off: list[Record], on: list[Record]) -> tuple[list[str], dict[str, int]]:
    """(personas identical to the current forecaster in every forecast field, and for the others
    the number of origins that differ)."""
    by_off = {(r["persona"], r["origin"]): r for r in off}
    differing: dict[str, int] = {}
    personas = sorted({r["persona"] for r in on})
    for r in on:
        base = by_off[(r["persona"], r["origin"])]
        if any(r[f] != base[f] for f in SAME_FIELDS):
            differing[r["persona"]] = differing.get(r["persona"], 0) + 1
    return [p for p in personas if p not in differing], differing


def report(records: dict[str, list[Record]]) -> str:
    cols = []
    for v in VARIANTS:
        recs = records[v]
        cols.append((NAMES[v], column(recs, alert_metrics(recs, PROVISIONAL_THRESHOLD, True),
                                      "provisional (20% or median)")))  # fmt: skip
    lines = ["# The recurring-placement fix (b): synthetic development personas", "",
             "20 development personas, 40 weekly origins, 500 futures, seed 42, raw chances, the "
             "original episode definition (the table is the provisional rule; the metric counts "
             "caught under both definitions elsewhere). Held-out personas not run.", "",
             *table([(c[0], c[1], [], []) for c in cols]), ""]  # fmt: skip
    lines += [*archetype_lines([(NAMES[v], records[v]) for v in VARIANTS]), ""]
    same, differing = compare_personas(records["off"], records["b"])
    fewer = sum(
        1 for a, b in zip(sorted(records["off"], key=lambda r: (r["persona"], r["origin"])),
                          sorted(records["b"], key=lambda r: (r["persona"], r["origin"])),
                          strict=True)
        if b["n_items"] < a["n_items"]
    )  # fmt: skip
    lines.append(f"- origins where (b) placed fewer payments: {fewer} of {len(records['b'])}")
    lines.append(f"- personas identical to the current forecaster in every forecast field: "
                 f"{len(same)} ({', '.join(same) or '-'})")  # fmt: skip
    changed = ", ".join(f"{p} ({n})" for p, n in sorted(differing.items())) or "-"
    lines.append(f"- personas that differ (origins differing): {changed}")
    got = verdict(records["off"], records["b"])
    lines.append(f"- archetypes worse on the 30-day error (more than 1 percent): "
                 f"{len(got['worse']['mae30'])} of {got['n_archetypes']} "
                 f"({', '.join(got['worse']['mae30']) or '-'})")  # fmt: skip
    lines.append(f"- archetypes worse on the lowest-balance bias: {len(got['worse']['bias'])} of "
                 f"{got['n_archetypes']} ({', '.join(got['worse']['bias']) or '-'})")  # fmt: skip
    lines.append(f"- **drop rule: {'DROP' if got['drop'] else 'not triggered'}**")
    lines.append("")
    m = {v: forecast_metrics(group_origins(records[v])["ordinary"]) for v in VARIANTS}
    lo, lb = m["off"], m["b"]
    lines.append(
        f"Ordinary origins: {lo['n_origins']}, {lo['n_personas']} personas. Lowest balance, bias: "
        f"{_d(lo['lowest']['bias'])} to {_d(lb['lowest']['bias'])}; Brier "
        f"{lo['brier']['brier']:.3f} to {lb['brier']['brier']:.3f}; coverage over all days "
        f"{_pct(lo['coverage']['all'])} to {_pct(lb['coverage']['all'])}."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.recurring_fix_eval", description=__doc__)
    parser.add_argument("--compare-only", action="store_true")
    parser.add_argument("--only", nargs="*", default=None, help="persona ids (a quick look)")
    parser.add_argument("--futures", type=int, default=FUTURES)
    parser.add_argument("--work", type=Path, default=DEFAULT_OUT / "recurring-fix-work")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_FILE)
    parser.add_argument("--root", type=Path, default=DEFAULT_OUT, help="where the two runs go")
    args = parser.parse_args(argv)
    personas = select_personas(args.only, False)
    if not args.compare_only:
        run_all(personas, args.work, args.root, args.futures)
    where = folders(args.root)
    records = {v: load_records(where[v]) for v in VARIANTS}
    text = report(records)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
