"""Big money that keeps coming, on the synthetic development personas (DECISIONS.md, 2026-10-11).

Three runs of the development backtest (20 personas, 40 weekly origins, 500 futures, seed 42, raw
chances, the original episode definition): the current forecaster, and the forecaster with big
money that keeps coming drawn as random arrivals, with and without transfers in from my own
accounts. Written to docs/results/keeps_coming_2026-10-11.md. Development personas only.

    python -m eval.keeps_coming_eval                  # runs what is missing, then compares
    python -m eval.keeps_coming_eval --compare-only   # only reads the saved runs
"""

import argparse
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from eval.backtest import (
    DEFAULT_OUT,
    FUTURES,
    KEEPS_NAMES,
    origins,
    run_persona,
    select_personas,
)
from eval.compare_runs import column, table
from eval.metrics import (
    PROVISIONAL_THRESHOLD,
    alert_metrics,
    by_archetype,
    forecast_metrics,
    group_origins,
    load_records,
)
from eval.personas_db import persona_db

Record = dict[str, Any]
VARIANTS = ("off", "with-own", "without-own")
NOISE = 0.01  # "worse" means more than 1 percent above the current forecaster (DECISIONS.md)
DEFAULT_OUT_FILE = Path("docs/results/keeps_coming_2026-10-11.md")
SAME_FIELDS = ("p10", "p50", "p90", "point", "median_lowest_cents", "shortfall_probability",
               "lowest_cents")  # fmt: skip


def _d(x: float | None) -> str:
    if x is None:
        return "-"
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def _head(first: str, names: list[str]) -> list[str]:
    return [f"| {first} | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def folders(root: Path) -> dict[str, Path]:
    return {v: root / f"keeps-coming-{v}" / "full" for v in VARIANTS}


def run_all(personas: list[str], work: Path, root: Path, futures: int = FUTURES) -> None:
    days = origins()
    where = folders(root)
    for variant in VARIANTS:
        keeps = KEEPS_NAMES.get(variant)
        for n, pid in enumerate(personas, start=1):
            done = run_persona(pid, where[variant], days, futures, 42, work, keeps)
            print(f"[{variant}] [{n}/{len(personas)}] {pid}: {done} new origins", flush=True)


# --- Which windows contain what (from the generator's answer key) ---


def window_kinds(personas: list[str], work: Path) -> dict[tuple[str, str], str]:
    """(persona, origin) -> "tuition", "family" (family money and no tuition) or "neither", from
    the generator's answer key for the 30 days after the origin."""
    kinds: dict[tuple[str, str], str] = {}
    for pid in personas:
        with persona_db(pid, work) as db:
            rows = [(t.posted_date, t.series_id or "") for t in db.labels.values()]
        for origin in origins():
            end = origin + timedelta(days=30)
            ids = [s for d, s in rows if origin < d <= end]
            tuition = any(s.endswith("-tuition") for s in ids)
            family = any(s.endswith(("-tuition-funding", "-tuition-topup", "-family")) for s in ids)
            kinds[(pid, origin.isoformat())] = (
                "tuition" if tuition else "family" if family else "neither"
            )
    return kinds


# --- Checks and the verdict ---


def identical_personas(off: list[Record], on: list[Record]) -> tuple[list[str], list[str]]:
    """(personas with no arrival at any origin whose records equal the current forecaster's,
    those with no arrival whose records differ). A persona with an arrival is not in either."""
    by_off = {(r["persona"], r["origin"]): r for r in off}
    arrivals: dict[str, int] = defaultdict(int)
    for r in on:
        arrivals[r["persona"]] += r["arrivals_in"] + r["arrivals_out"]
    same, differ = [], []
    for pid in sorted(arrivals):
        if arrivals[pid]:
            continue
        rows = [r for r in on if r["persona"] == pid]
        ok = all(
            all(r[f] == by_off[(pid, r["origin"])][f] for f in SAME_FIELDS) for r in rows
        )  # fmt: skip
        (same if ok else differ).append(pid)
    return same, differ


def archetype_measures(records: list[Record]) -> dict[str, dict[str, float]]:
    out = {}
    for arch, rows in by_archetype(group_origins(records)["ordinary"]).items():
        m = forecast_metrics(rows)
        out[arch] = {"mae30": m["error"][30]["mean_p50"], "bias": abs(m["lowest"]["bias"]),
                     "n": float(m["n_origins"])}  # fmt: skip
    return out


def verdict(off: list[Record], on: list[Record]) -> dict[str, Any]:
    """How many archetypes are worse (by more than 1 percent) on each measure, and whether the
    drop rule (more than half of the archetypes on either measure) says to drop."""
    a, b = archetype_measures(off), archetype_measures(on)
    worse = {"mae30": [], "bias": []}
    for arch in a:
        for key in worse:
            if b[arch][key] > a[arch][key] * (1 + NOISE):
                worse[key].append(arch)
    n = len(a)
    drop = any(len(v) > n / 2 for v in worse.values())
    return {"n_archetypes": n, "worse": worse, "drop": drop}


# --- The report ---


def window_table(
    cols: list[tuple[str, list[Record]]], kinds: dict[tuple[str, str], str]
) -> list[str]:  # fmt: skip
    lines = [
        "Lowest-balance bias / typical miss by kind of window (ordinary origins; negative = too "
        "pessimistic), for all personas and for the two with family money:",
        "",
        *_head("Windows", [c[0] for c in cols]),
    ]
    for label, only in (("all personas", None), ("ITIGHT and IFAM", ("ITIGHT", "IFAM"))):
        for kind in ("tuition", "family", "neither"):
            cells, n = [], 0
            for _, recs in cols:
                rows = [
                    r for r in group_origins(recs)["ordinary"]
                    if kinds[(r["persona"], r["origin"])] == kind
                    and (only is None or r["archetype"] in only)
                ]  # fmt: skip
                n = len(rows)
                m = forecast_metrics(rows)
                cells.append(
                    "-" if not n else f"{_d(m['lowest']['bias'])} / {_d(m['lowest']['mae'])}"
                )
            name = {"tuition": "a tuition payment", "family": "family money, no tuition",
                    "neither": "neither"}[kind]  # fmt: skip
            lines.append(f"| {label}: {name} (n={n}) | " + " | ".join(cells) + " |")
    return lines


def archetype_lines(cols: list[tuple[str, list[Record]]]) -> list[str]:
    lines = [
        "Per archetype (ordinary origins): 30-day error / lowest-balance bias.",
        "",
        *_head("Archetype", [c[0] for c in cols]),
    ]
    groups = [archetype_measures(r) for _, r in cols]
    full = [by_archetype(group_origins(r)["ordinary"]) for _, r in cols]
    for arch in groups[0]:
        cells = []
        for g, f in zip(groups, full, strict=True):
            m = forecast_metrics(f[arch])
            cells.append(f"{_d(g[arch]['mae30'])} / {_d(m['lowest']['bias'])}")
        n = forecast_metrics(full[0][arch])
        lines.append(f"| {arch} ({n['n_origins']} origins, {n['n_personas']} personas) | "
                     + " | ".join(cells) + " |")  # fmt: skip
    return lines


def report(records: dict[str, list[Record]], kinds: dict[tuple[str, str], str]) -> str:
    names = {"off": "current forecaster", "with-own": "keeps coming, with own-account",
             "without-own": "keeps coming, without own-account"}  # fmt: skip
    cols = []
    for v in VARIANTS:
        recs = records[v]
        cols.append((names[v], column(recs, alert_metrics(recs, PROVISIONAL_THRESHOLD, True),
                                      "provisional (20% or median)")))  # fmt: skip
    lines = ["# Big money that keeps coming: synthetic development personas", "",
             "20 development personas, 40 weekly origins, 500 futures, seed 42, raw chances, the "
             "original episode definition. Held-out personas not run.", "",
             *table([(c[0], c[1], [], []) for c in cols]), ""]  # fmt: skip
    lines += [*archetype_lines([(names[v], records[v]) for v in VARIANTS]), ""]
    lines += [*window_table([(names[v], records[v]) for v in VARIANTS], kinds), ""]
    for v in ("with-own", "without-own"):
        same, differ = identical_personas(records["off"], records[v])
        arrived = sum(r["arrivals_in"] + r["arrivals_out"] > 0 for r in records[v])
        lines.append(f"## {names[v]}")
        lines.append(f"- origins with at least one arrival stream: {arrived} of {len(records[v])}")
        listed = ", ".join(same) or "-"
        lines.append(
            "- personas with no arrival at any origin, identical to the current forecaster in "
            f"every forecast field: {len(same)} ({listed})"
        )
        if differ:
            lines.append(f"- **no arrival, but different: {', '.join(differ)} (a bug)**")
        got = verdict(records["off"], records[v])
        lines.append(f"- archetypes worse on the 30-day error (more than 1 percent): "
                     f"{len(got['worse']['mae30'])} of {got['n_archetypes']} "
                     f"({', '.join(got['worse']['mae30']) or '-'})")  # fmt: skip
        lines.append(f"- archetypes worse on the lowest-balance bias: "
                     f"{len(got['worse']['bias'])} of {got['n_archetypes']} "
                     f"({', '.join(got['worse']['bias']) or '-'})")  # fmt: skip
        lines.append(f"- **drop rule: {'DROP' if got['drop'] else 'not triggered'}**")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.keeps_coming_eval", description=__doc__)
    parser.add_argument("--compare-only", action="store_true")
    parser.add_argument("--only", nargs="*", default=None, help="persona ids (a quick look)")
    parser.add_argument("--futures", type=int, default=FUTURES)
    parser.add_argument("--work", type=Path, default=DEFAULT_OUT / "keeps-coming-work")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_FILE)
    parser.add_argument("--root", type=Path, default=DEFAULT_OUT, help="where the three runs go")
    args = parser.parse_args(argv)
    personas = select_personas(args.only)
    if not args.compare_only:
        run_all(personas, args.work, args.root, args.futures)
    where = folders(args.root)
    records = {v: load_records(where[v]) for v in VARIANTS}
    kinds = window_kinds(personas, args.work)
    text = report(records, kinds)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
