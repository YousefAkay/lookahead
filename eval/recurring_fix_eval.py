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
from eval.fix_variants import fix_options
from eval.keeps_coming_eval import _d, _pct, archetype_lines, verdict
from eval.metrics import (
    PROVISIONAL_THRESHOLD,
    alert_metrics,
    forecast_metrics,
    group_origins,
    load_records,
)
from lookahead.db import connect
from lookahead.forecast.spending import DEFAULT_KEEPS
from lookahead.generator.archetypes import END, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents

Record = dict[str, Any]
VARIANTS = ("off", "b", "a", "both")
NAMES = {
    "off": "no fix",
    "b": "(b) possibly ended not placed (the default)",
    "a": "(a) weekly card series not placed",
    "both": "(a) and (b)",
}
DEFAULT_OUT_FILE = Path("docs/results/recurring_fix_a_2026-10-12.md")
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
                                    options=fix_options(variant))  # fmt: skip
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


LF = chr(10)


def _ordered(rows: list[Record]) -> list[Record]:
    return sorted(rows, key=lambda r: (r["persona"], r["origin"]))


def compare_lines(records: dict[str, list[Record]], base: str, new: str, label: str) -> list[str]:
    """`new` against `base`: identical personas, origins placing fewer payments, the drop rule."""
    same, differing = compare_personas(records[base], records[new])
    fewer = sum(
        1 for a, b in zip(_ordered(records[base]), _ordered(records[new]), strict=True)
        if b["n_items"] < a["n_items"]
    )  # fmt: skip
    got = verdict(records[base], records[new])
    changed = ", ".join(f"{p} ({n})" for p, n in sorted(differing.items())) or "-"
    return [
        f"## {label}",
        f"- origins where it placed fewer payments: {fewer} of {len(records[new])}",
        f"- personas identical to {NAMES[base]} in every forecast field: {len(same)} "
        f"({', '.join(same) or '-'})",
        f"- personas that differ (origins differing): {changed}",
        f"- archetypes worse on the 30-day error (more than 1 percent): "
        f"{len(got['worse']['mae30'])} of {got['n_archetypes']} "
        f"({', '.join(got['worse']['mae30']) or '-'})",
        f"- archetypes worse on the lowest-balance bias: {len(got['worse']['bias'])} of "
        f"{got['n_archetypes']} ({', '.join(got['worse']['bias']) or '-'})",
        f"- **drop rule: {'DROP' if got['drop'] else 'not triggered'}**",
        "",
    ]


def report(records: dict[str, list[Record]]) -> str:
    cols = []
    for v in VARIANTS:
        recs = records[v]
        cols.append((NAMES[v], column(recs, alert_metrics(recs, PROVISIONAL_THRESHOLD, True),
                                      "provisional (20% or median)")))  # fmt: skip
    lines = ["# The recurring-placement fix, (a) and (b): synthetic development personas", "",
             "20 development personas, 40 weekly origins, 500 futures, seed 42, raw chances, the "
             "original episode definition (the provisional rule). Held-out personas not run.", "",
             *table([(c[0], c[1], [], []) for c in cols]), ""]  # fmt: skip
    lines += [*archetype_lines([(NAMES[v], records[v]) for v in VARIANTS]), ""]
    lines += compare_lines(records, "off", "b", "(b) against no fix")
    lines += compare_lines(records, "b", "both", "(a) added to (b), against the default")
    lines += compare_lines(records, "off", "a", "(a) alone against no fix (for information)")
    m = {v: forecast_metrics(group_origins(records[v])["ordinary"]) for v in VARIANTS}
    lines.append(f"Ordinary origins: {m['b']['n_origins']}, {m['b']['n_personas']} personas.")
    for v in VARIANTS:
        lines.append(
            f"- {NAMES[v]}: lowest-balance bias {_d(m[v]['lowest']['bias'])}, Brier "
            f"{m[v]['brier']['brier']:.3f}, coverage over all days "
            f"{_pct(m[v]['coverage']['all'])}"
        )
    return LF.join(lines)


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
