"""Score the category rules against the generator's answer keys (synthetic only).

    python -m eval.category_rules

Coverage = rows the rules decide; accuracy = correct among those, per source. e-Transfer
rent (answer key Rent & housing, rules Transfers until Week 3 confirms it) is shown as a
known gap. Optimistic: the defaults are the generator's own choices for those merchants.
"""

import argparse
import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lookahead.categories import category_by_rules
from lookahead.flows import Txn
from lookahead.ingest.scotiabank import parse_export


@dataclass
class RuleScore:
    rows: int = 0
    decided: Counter[str] = field(default_factory=Counter)  # source -> rows
    correct: Counter[str] = field(default_factory=Counter)  # source -> correct rows
    rent_gap: int = 0
    waiting: int = 0
    netted: int = 0  # fully reversed pairs: no category needed
    misses: Counter[tuple[str, str, str]] = field(default_factory=Counter)


def score(export: Path, key: Path, s: RuleScore) -> None:
    rows = parse_export(export)
    with key.open(encoding="utf-8", newline="") as f:
        truth = list(csv.DictReader(line for line in f if not line.startswith("#")))
    txns = [
        Txn(i, r.posted_date, r.amount_cents, r.description, r.sub_description)
        for i, r in enumerate(rows)
    ]
    decisions = category_by_rules(txns)
    for t, k in zip(txns, truth, strict=True):
        d = decisions[t.row_id]
        s.rows += 1
        s.waiting += d.source.startswith("inherits;")
        s.netted += d.source.startswith("netted")
        if d.category is None:
            continue
        source = d.source.split(":")[0]
        s.decided[source] += 1
        if d.category == k["category"]:
            s.correct[source] += 1
        elif k["category"] == "Rent & housing" and d.category == "Transfers":
            s.rent_gap += 1
        else:
            s.misses[(source, k["category"], d.category)] += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.category_rules")
    parser.add_argument("--dir", type=Path, default=Path("data/synthetic"))
    args = parser.parse_args(argv)
    s = RuleScore()
    for pid in "ABC":
        score(args.dir / f"persona_{pid}.csv", args.dir / f"persona_{pid}_answer_key.csv", s)
    decided = sum(s.decided.values())
    correct = sum(s.correct.values())
    print(f"Category rules vs answer keys (synthetic, {s.rows} rows)")
    print(f"  decided by rules: {decided}/{s.rows} = {decided / s.rows:.1%}")
    print(f"  correct among decided: {correct}/{decided} = {correct / decided:.1%}")
    print(f"  known Week 3 gap (e-Transfer rent typed Transfers): {s.rent_gap} rows")
    print(f"  inherited, waiting for the categorizer: {s.waiting} rows")
    print(f"  netted pairs (no category needed): {s.netted} rows")
    print(f"  left for the categorizer: {s.rows - decided - s.netted} rows")
    for source, n in s.decided.most_common():
        print(f"    {source:<36} {s.correct[source]}/{n}")
    for (source, truth, got), n in s.misses.most_common():
        print(f"  miss: {source}: answer key {truth!r}, rules {got!r}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
