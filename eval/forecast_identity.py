"""Forecast identity check: does a code change leave every forecast number as it was?

Fingerprints the default forecast at the 40 recorded backtest origins of the synthetic
development personas (never held-out ones: every id passes assert_development_only): every
future's daily balances, the simulation numbers, the dip chances, the point forecast, the placed
items, the alert with its evidence, and safe-to-spend. Used for the e8f1073 port (DECISIONS,
2026-11-17: 800 of 800 origins identical, 20 personas).

Run it once per code version, each from a checkout of that version (a detached worktree), then
compare:

    git worktree add --detach /tmp/before <commit>
    cd /tmp/before && PYTHONPATH=. python -m eval.forecast_identity fingerprint before.json work
    cd <repo> && python -m eval.forecast_identity fingerprint after.json work
    python -m eval.forecast_identity compare before.json after.json
"""

import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from eval.backtest import origins
from eval.personas_db import persona_db
from lookahead.alerts import build_evidence, snapshot_of
from lookahead.forecast.path import build_forecast
from lookahead.generator.archetypes import assert_development_only, development_ids
from lookahead.safe_to_spend import safe_to_spend_from


def h(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def one(args: tuple[str, str]) -> list[dict[str, Any]]:
    pid, work = args
    assert_development_only([pid])
    out = []
    with persona_db(pid, Path(work)) as db:
        for origin in origins():
            f = build_forecast(db.conn, pid, "weekday", origin, None, keep_paths=True)
            assert f is not None and f.simulation is not None
            sim = f.simulation
            assert len(sim.paths) == sim.futures > 0  # every future's daily balance is kept
            path_hash = h(sim.paths)
            scalars = {k: v for k, v in vars(sim).items() if k != "paths"}
            snap = snapshot_of(f)
            sts = safe_to_spend_from(f)
            out.append(
                {
                    "persona": pid,
                    "origin": origin.isoformat(),
                    "paths_hash": path_hash,
                    "n_paths": len(sim.paths),
                    "sim": h(scalars),
                    "dip": sim.shortfall_probability,
                    "dip_without": sim.shortfall_probability_without,
                    "point": h(
                        [(d.day, d.balance_cents, d.spending_cents, d.fees_cents) for d in f.days]
                    ),
                    "items": h([vars(i) for i in f.items]),
                    "alert": None if snap is None else h(asdict(snap)),
                    "alerted": snap is not None,
                    "evidence": h(build_evidence(f)),
                    "safe": h(asdict(sts)),
                    "safe_cents": sts.cents_per_day,
                    "has_income": f.has_income,
                    "not_placed": list(getattr(f, "not_placed", ())),
                }
            )
    return out


def fingerprint_all(out_path: str, work: str, jobs: int = 8) -> None:
    ids = development_ids()
    assert_development_only(ids)
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        rows = [r for part in pool.map(one, [(p, work) for p in ids]) for r in part]
    Path(out_path).write_text(json.dumps(rows), encoding="utf-8")
    print(f"{len(ids)} personas, {len(rows)} origins -> {out_path}")


def compare(before_path: str, after_path: str) -> None:
    before = {(r["persona"], r["origin"]): r for r in json.loads(Path(before_path).read_text())}
    after = {(r["persona"], r["origin"]): r for r in json.loads(Path(after_path).read_text())}
    assert before.keys() == after.keys(), "different origins"
    SAME = [
        "paths_hash",
        "n_paths",
        "sim",
        "dip",
        "dip_without",
        "point",
        "items",
        "alert",
        "evidence",
        "safe",
    ]
    n = len(before)
    print(
        f"personas: {len({p for p, _ in before})}, origins: {n}, "
        f"futures per origin: {sorted({r['n_paths'] for r in after.values()})}"
    )
    for field in SAME:
        same = sum(before[k][field] == after[k][field] for k in before)
        print(f"{field:12s} identical at {same} of {n}")
    all_same = sum(all(before[k][f] == after[k][f] for f in SAME) for k in before)
    print(f"ALL fields   identical at {all_same} of {n}")
    print(
        f"alerting origins: {sum(r['alerted'] for r in after.values())} of {n}; "
        f"with a safe amount: {sum(r['safe_cents'] is not None for r in after.values())} of {n}"
    )
    changed = [k for k in before if before[k]["has_income"] != after[k]["has_income"]]
    print(
        f"has_income changed at {len(changed)} of {n} origins "
        f"({len({p for p, _ in changed})} personas); "
        f"True->False: {sum(before[k]['has_income'] > after[k]['has_income'] for k in changed)}"
    )
    print(
        f"origins with something not placed (after): "
        f"{sum(bool(r['not_placed']) for r in after.values())} of {n}"
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["fingerprint"] and len(args) in (3, 4):
        fingerprint_all(args[1], args[2], int(args[3]) if len(args) == 4 else 8)
    elif args[:1] == ["compare"] and len(args) == 3:
        compare(args[1], args[2])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
