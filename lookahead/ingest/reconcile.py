"""Reconciliation: check that stored transactions explain every balance anchor.

For consecutive anchor dates d1 < d2:
    anchor(d1) + sum of stored amounts on days after d1 up to d2 == anchor(d2)
A mismatch is a Break (rows missing, duplicated or wrong between d1 and d2). Two anchors
for the same date with different balances are a Conflict; that date is left out of the
chain check so one bad anchor does not also produce misleading breaks.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Break:
    from_date: date
    to_date: date
    expected_cents: int  # anchor(from_date) + stored amounts in (from_date, to_date]
    anchor_cents: int  # what the anchor on to_date says


@dataclass(frozen=True)
class Conflict:
    date: date
    balances: dict[str, int]  # "source:file" -> balance


@dataclass
class ReconcileReport:
    anchors_checked: int = 0
    breaks: list[Break] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.breaks and not self.conflicts


def reconcile(conn: sqlite3.Connection, account: str) -> ReconcileReport:
    """Check all anchors of `account` against its stored transactions."""
    by_date: dict[date, dict[str, int]] = {}
    for d, balance, source, source_file in conn.execute(
        "SELECT date, balance_cents, source, source_file FROM balance_anchors WHERE account = ?",
        (account,),
    ):
        by_date.setdefault(date.fromisoformat(d), {})[f"{source}:{source_file}"] = balance

    report = ReconcileReport()
    agreed: list[tuple[date, int]] = []
    for d in sorted(by_date):
        balances = by_date[d]
        if len(set(balances.values())) > 1:
            report.conflicts.append(Conflict(d, balances))
        else:
            agreed.append((d, next(iter(balances.values()))))
    report.anchors_checked = len(agreed)

    daily = {
        date.fromisoformat(d): total
        for d, total in conn.execute(
            "SELECT posted_date, SUM(amount_cents) FROM transactions WHERE account = ?"
            " GROUP BY posted_date",
            (account,),
        )
    }
    for (d1, b1), (d2, b2) in zip(agreed, agreed[1:], strict=False):
        expected = b1 + sum(total for d, total in daily.items() if d1 < d <= d2)
        if expected != b2:
            report.breaks.append(Break(d1, d2, expected, b2))
    return report
