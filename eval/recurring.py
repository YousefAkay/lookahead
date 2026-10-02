"""Recurring detection against the generator's planted schedules (step 3a-ii).

    python -m eval.recurring                # 12 months of each synthetic persona

The answer is the generator's schedule objects (which rows each schedule produced, its
cadence, its next due date), not the detection rules. A detected series counts as true if
most of its rows come from one planted schedule.
Reported per persona and pooled, with n: series found (precision, recall), cadence right,
next expected date within tolerance, and rows (precision, recall).

A PIPELINE CHECK, NOT EVIDENCE: the generator shifts due dates with the same calendar rules
the detector uses, its schedules are clean, and the two "habit" rules of the detector were
tuned while looking at these three personas. The meaningful check is the list the owner
confirms on real data.
"""

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np

from lookahead.flows import Txn
from lookahead.generator.personas import PERSONAS
from lookahead.generator.schedules import RecurringSchedule
from lookahead.generator.writer import export_rows
from lookahead.recurring import TOLERANCE, Series, business_days_apart, detect
from lookahead.synthetic_training import EXPORT_DATE, _start


@dataclass
class Result:
    planted: int = 0  # schedules with at least one row in the window
    planted_fixed: int = 0  # ... that follow a fixed cadence (the detector's target)
    detected: int = 0
    true: int = 0  # detected series that match a planted schedule
    found: int = 0  # planted schedules matched by a detected series
    found_fixed: int = 0
    cadence_right: int = 0  # of the found fixed-cadence schedules
    next_date_right: int = 0  # of the found fixed-cadence schedules still running
    next_date_checked: int = 0
    rows_detected: int = 0  # rows inside detected series
    rows_right: int = 0  # ... that belong to the matched schedule
    rows_planted_fixed: int = 0  # rows of fixed-cadence schedules
    missed: Counter[str] = field(default_factory=Counter)  # kind of schedule -> not found

    def add(self, other: "Result") -> None:
        for name in vars(self):
            if name == "missed":
                self.missed += other.missed
            else:
                setattr(self, name, getattr(self, name) + getattr(other, name))


def _owner(series: Series, truth: dict[int, str | None]) -> str | None:
    """The planted schedule most of this series' rows come from, if more than half do."""
    counts = Counter(truth[r] for r in series.rows)
    top, n = counts.most_common(1)[0]
    return top if top is not None and n * 2 > len(series.rows) else None


def evaluate(pid: str, months: int = 12, seed: int = 42) -> Result:
    persona = PERSONAS[pid]
    rows = export_rows(persona, _start(months), EXPORT_DATE, seed)
    txns = [
        Txn(i, t.posted_date, t.amount_cents, t.description, t.sub_description)
        for i, t in enumerate(rows)
    ]
    truth = {i: t.series_id for i, t in enumerate(rows)}
    planted: dict[str, list[int]] = {}
    for i, series_id in truth.items():
        if series_id:
            planted.setdefault(series_id, []).append(i)
    schedules = {
        s.series_id: s for s in persona.sources if isinstance(s, RecurringSchedule)
    }  # the fixed-cadence ones; other planted series are irregular by design
    as_of = max(t.posted_date for t in txns)
    result = Result(planted=len(planted), planted_fixed=sum(s in schedules for s in planted))
    result.rows_planted_fixed = sum(len(ids) for s, ids in planted.items() if s in schedules)
    found: dict[str, Series] = {}
    for series in detect(txns, as_of=as_of):
        result.detected += 1
        result.rows_detected += len(series.rows)
        owner = _owner(series, truth)
        if owner is None:
            continue
        result.true += 1
        result.rows_right += sum(truth[r] == owner for r in series.rows)
        found.setdefault(owner, series)
    result.found = len(found)
    for series_id in planted:
        schedule = schedules.get(series_id)
        if series_id not in found:
            result.missed["fixed cadence" if schedule else "irregular (not targeted)"] += 1
            continue
        if schedule is None:
            continue
        result.found_fixed += 1
        result.cadence_right += found[series_id].cadence == schedule.cadence
        last_seen = max(txns[i].posted_date for i in planted[series_id])
        coming = schedule.generate(  # the first planted payment after the last one in the data
            last_seen + timedelta(days=1), as_of + timedelta(days=400), np.random.default_rng(0)
        )
        if coming:  # the schedule is still running: is the next date right?
            result.next_date_checked += 1
            gap = business_days_apart(coming[0].posted_date, found[series_id].next_expected)
            result.next_date_right += gap <= TOLERANCE
    return result


def _share(a: int, b: int) -> str:
    return f"{a}/{b}" + (f" ({a / b:.0%})" if b else "")


def _print(name: str, r: Result) -> None:
    print(f"{name}: planted {r.planted} series ({r.planted_fixed} fixed cadence), "
          f"detected {r.detected}")  # fmt: skip
    print(f"   precision (detected series that are planted): {_share(r.true, r.detected)}")
    print(f"   recall, fixed-cadence series: {_share(r.found_fixed, r.planted_fixed)}; "
          f"all planted series: {_share(r.found, r.planted)}")  # fmt: skip
    next_date = _share(r.next_date_right, r.next_date_checked)
    print(f"   cadence right: {_share(r.cadence_right, r.found_fixed)}; next expected date "
          f"within {TOLERANCE} business days: {next_date}")  # fmt: skip
    print(f"   rows: precision {_share(r.rows_right, r.rows_detected)}, recall of "
          f"fixed-cadence rows {_share(r.rows_right, r.rows_planted_fixed)}")  # fmt: skip
    if r.missed:
        print("   not found: " + ", ".join(f"{k} {n}" for k, n in sorted(r.missed.items())))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.recurring")
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    print(f"Recurring detection vs the generator's planted schedules: {args.months} months, "
          f"seed {args.seed}, export date {EXPORT_DATE}")  # fmt: skip
    print("PIPELINE CHECK, NOT EVIDENCE: clean schedules, same calendar rules, rules tuned on")
    print("these personas. The schedules start in June 2026, so a longer window only adds")
    print("everyday spending before that (more chances for a false series, no more payments).")
    total = Result()
    for pid in "ABC":
        r = evaluate(pid, args.months, args.seed)
        _print(f"persona {pid}", r)
        total.add(r)
    _print("all 3 personas", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
