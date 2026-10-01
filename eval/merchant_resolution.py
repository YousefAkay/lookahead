"""Pair precision and recall of merchant resolution on the synthetic variant set.

    python -m eval.merchant_resolution                 # the reported numbers (test half)
    python -m eval.merchant_resolution --sweep         # choose SIMILAR_AT (dev half only)

A pair = two different names. It is a true pair when the answer key gives both names the
same merchant, and a predicted pair when resolution puts them in one group.
- precision = predicted pairs that are true / predicted pairs
- recall    = true pairs that were predicted / true pairs
Reported for the automatic rules alone, and for "rules + every proposal accepted" (what you
would get by saying yes to every waiting pair: an upper bound for recall).
Optimistic by construction: the rules and the variant types were written by the same
person, and the alias list covers several generator spellings (shown with and without it).
"""

import argparse
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from eval.resolution_variants import Variant, build_variants
from lookahead.resolve import SIMILAR_AT, NameInfo, Resolution, resolve


@dataclass
class PairScore:
    true_pairs: int
    predicted: int
    correct: int

    @property
    def precision(self) -> float:
        return self.correct / self.predicted if self.predicted else 1.0

    @property
    def recall(self) -> float:
        return self.correct / self.true_pairs if self.true_pairs else 1.0


def _groups_with_proposals(result: Resolution, names: list[str]) -> dict[str, str]:
    """Group of each name if every proposal were accepted."""
    parent = {result.group_of(n): result.group_of(n) for n in names}

    def find(g: str) -> str:
        while parent[g] != g:
            g = parent[g]
        return g

    for p in result.proposals:
        ga, gb = find(result.group_of(p.a)), find(result.group_of(p.b))
        if ga != gb:
            parent[gb] = ga
    return {n: find(result.group_of(n)) for n in names}


def score_pairs(variants: list[Variant], predicted_group: dict[str, str]) -> PairScore:
    true_pairs = predicted = correct = 0
    for a, b in combinations(variants, 2):
        same_truth = a.merchant == b.merchant
        same_group = predicted_group[a.name] == predicted_group[b.name]
        true_pairs += same_truth
        predicted += same_group
        correct += same_truth and same_group
    return PairScore(true_pairs, predicted, correct)


@dataclass
class Evaluation:
    auto: PairScore
    with_proposals: PairScore
    proposals: int
    true_proposals: int
    recall_by_kind: dict[str, tuple[int, int]]  # kind -> (found, true pairs) for auto rules


def evaluate(
    variants: list[Variant], similar_at: float = SIMILAR_AT, aliases: bool = True
) -> Evaluation:
    infos = [NameInfo(v.name, 1, v.cut) for v in variants]
    result = resolve(infos, similar_at=similar_at, aliases=None if aliases else {})
    names = [v.name for v in variants]
    auto_group = {n: result.group_of(n) for n in names}
    truth = {v.name: v.merchant for v in variants}
    true_proposals = sum(truth[p.a] == truth[p.b] for p in result.proposals)
    by_kind: dict[str, list[int]] = {}
    for a, b in combinations(variants, 2):
        if a.merchant != b.merchant:
            continue
        for kind in {a.kind, b.kind}:
            found_total = by_kind.setdefault(kind, [0, 0])
            found_total[1] += 1
            found_total[0] += auto_group[a.name] == auto_group[b.name]
    return Evaluation(
        auto=score_pairs(variants, auto_group),
        with_proposals=score_pairs(variants, _groups_with_proposals(result, names)),
        proposals=len(result.proposals),
        true_proposals=true_proposals,
        recall_by_kind={k: (v[0], v[1]) for k, v in sorted(by_kind.items())},
    )


def _line(label: str, s: PairScore) -> str:
    return (
        f"  {label:<34} precision {s.precision:6.1%} ({s.correct}/{s.predicted})   "
        f"recall {s.recall:6.1%} ({s.correct}/{s.true_pairs})"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.merchant_resolution")
    parser.add_argument("--dir", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--sweep", action="store_true", help="threshold table on the dev half")
    args = parser.parse_args(argv)
    sets = build_variants(args.dir)

    if args.sweep:
        print("SIMILAR_AT sweep on the DEV half (choose here, never on test)")
        print("  threshold  proposals  true  recall(rules+proposals)  precision(rules+proposals)")
        for t in [0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8]:
            e = evaluate(sets.dev, similar_at=t)
            print(
                f"  {t:9.2f}  {e.proposals:9d}  {e.true_proposals:4d}  "
                f"{e.with_proposals.recall:23.1%}  {e.with_proposals.precision:26.1%}"
            )
        return 0

    for label, variants in (("dev", sets.dev), ("test", sets.test)):
        merchants = len({v.merchant for v in variants})
        print(f"{label.upper()} half: {len(variants)} names, {merchants} merchants "
              f"(SIMILAR_AT = {SIMILAR_AT})")  # fmt: skip
        for aliases in (True, False):
            e = evaluate(variants, aliases=aliases)
            tag = "with alias list" if aliases else "WITHOUT alias list"
            print(f" {tag}")
            print(_line("rules only", e.auto))
            print(_line("rules + every proposal accepted", e.with_proposals))
            print(f"  proposals waiting: {e.proposals} ({e.true_proposals} are true pairs)")
            if aliases:
                kinds = ", ".join(f"{k} {f}/{n}" for k, (f, n) in e.recall_by_kind.items())
                print(f"  rules-only recall by variant type: {kinds}")
    print(f"names dropped because two merchants share them: {sets.dropped}")
    counts = Counter(v.kind for v in sets.test)
    print("test variant types: " + ", ".join(f"{k} {n}" for k, n in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
