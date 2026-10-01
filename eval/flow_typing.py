"""Score flow typing and pairing against the generator's answer keys (synthetic only).

    python -m eval.flow_typing

Flow type: accuracy and a confusion table (rows = answer key, columns = rules). Rows that
need the Week 3 recurring rules (card subscriptions, e-Transfer rent: answer key
recurring_outflow, rules everyday / transfer) are counted as a known gap and also shown
excluded. Pairing: every refund row the rules try to pair (correction, return, returned
e-Transfer) is correct when its partner has the same answer-key link_id.
Optimistic: the rules and the generator come from the same notes about the bank format.
"""

import argparse
import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lookahead.flows import Txn, flow_types
from lookahead.ingest.scotiabank import parse_export

FLOWS = ("income", "recurring_outflow", "everyday", "transfer", "refund")
_PAIRED_REASONS = {"pre-auth correction", "card return", "returned e-Transfer"}


@dataclass
class FlowScore:
    rows: int = 0
    correct: int = 0
    week3_gap: int = 0  # recurring_outflow rows that only the recurring rules can find
    confusion: Counter[tuple[str, str]] = field(default_factory=Counter)  # (truth, rules)
    to_pair: int = 0
    paired: int = 0
    paired_right: int = 0


def _read(export: Path, key: Path) -> tuple[list[Txn], list[dict[str, str]]]:
    """Export rows (parsed by the importer: exact integer cents) and answer-key rows, both in
    the export's order, so row_id i is answer-key row i."""
    rows = parse_export(export)
    with key.open(encoding="utf-8", newline="") as f:
        truth = list(csv.DictReader(line for line in f if not line.startswith("#")))
    txns = [
        Txn(i, r.posted_date, r.amount_cents, r.description, r.sub_description)
        for i, r in enumerate(rows)
    ]
    return txns, truth


def score(export: Path, key: Path, total: FlowScore) -> None:
    txns, truth = _read(export, key)
    flows = flow_types(txns)
    for t, k in zip(txns, truth, strict=True):
        f = flows[t.row_id]
        total.rows += 1
        total.confusion[(k["flow_type"], f.flow_type)] += 1
        if f.flow_type == k["flow_type"]:
            total.correct += 1
        elif k["flow_type"] == "recurring_outflow" and f.flow_type in ("everyday", "transfer"):
            total.week3_gap += 1
        if f.reason in _PAIRED_REASONS:
            total.to_pair += 1
            if f.link is not None:
                total.paired += 1
                link = k["link_id"]
                total.paired_right += bool(link) and truth[f.link]["link_id"] == link


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.flow_typing")
    parser.add_argument("--dir", type=Path, default=Path("data/synthetic"))
    args = parser.parse_args(argv)
    s = FlowScore()
    for pid in "ABC":
        score(args.dir / f"persona_{pid}.csv", args.dir / f"persona_{pid}_answer_key.csv", s)

    without_gap = s.rows - s.week3_gap
    print(f"Flow typing vs answer keys (synthetic, {s.rows} rows)")
    print(f"  accuracy: {s.correct}/{s.rows} = {s.correct / s.rows:.1%}")
    print(
        f"  known Week 3 gap (recurring card subscriptions / e-Transfer rent): {s.week3_gap} rows"
    )
    print(f"  accuracy without the gap: {s.correct}/{without_gap} = {s.correct / without_gap:.1%}")
    print("  confusion (rows = answer key, columns = rules):")
    print("    " + " " * 18 + "".join(f"{f[:10]:>11}" for f in FLOWS))
    for truth in FLOWS:
        cells = "".join(f"{s.confusion[(truth, got)]:>11}" for got in FLOWS)
        print(f"    {truth:<18}{cells}")
    print(
        f"  pairing: {s.paired} of {s.to_pair} refund rows paired; "
        f"{s.paired_right} with the right partner (same answer-key link)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
