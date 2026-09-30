"""Fingerprints and duplicate removal for overlapping exports.

Fingerprint = hash of (account, date, amount, transaction type, normalized merchant
text, balance after the row, occurrence number). The balance separates two real
identical purchases (different balances); the same row in two overlapping exports has
the same balance (SUPPORTED by real overlapping exports, see DECISIONS.md).

The occurrence number counts identical keys within a file, oldest first. It only
matters for $0.00 rows, which leave the balance unchanged. Counting oldest first keeps
it stable when a later export adds rows at the end of a day the earlier export cut off.
Known limit: if a file's oldest day starts partway through a run of identical $0.00
rows, those rows can get new numbers and be imported twice.
"""

import hashlib
from collections import Counter
from dataclasses import dataclass, field

from lookahead.ingest.scotiabank import ParsedRow


@dataclass(frozen=True)
class KeptRow:
    """A row accepted by the merge, with its fingerprint and the file it came from."""

    fingerprint: str
    row: ParsedRow
    source: str


@dataclass
class MergeResult:
    kept: list[KeptRow] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)  # per source: rows already seen


def _key(row: ParsedRow, account: str) -> tuple[str, ...]:
    return (
        account,
        row.posted_date.isoformat(),
        str(row.amount_cents),
        row.description,
        row.sub_description_norm,
        str(row.balance_cents),
    )


def fingerprint_export(rows: list[ParsedRow], account: str) -> list[str]:
    """One fingerprint per row, in the rows' order (the file's order: newest first)."""
    seen: Counter[tuple[str, ...]] = Counter()
    occurrence: dict[int, int] = {}
    for i in reversed(range(len(rows))):  # oldest first
        key = _key(rows[i], account)
        seen[key] += 1
        occurrence[i] = seen[key]
    return [
        hashlib.sha256("\x1f".join((*_key(r, account), str(occurrence[i]))).encode()).hexdigest()
        for i, r in enumerate(rows)
    ]


def merge_exports(
    exports: list[tuple[str, list[ParsedRow]]],
    account: str,
    seen: set[str] | None = None,
) -> MergeResult:
    """Keep each distinct row once across `exports` (name, rows), in the given order.

    `seen` holds fingerprints already imported earlier (e.g. from the database); rows
    matching them are skipped too.
    """
    known = set(seen or ())
    result = MergeResult()
    for source, rows in exports:
        result.skipped[source] = 0
        for fingerprint, row in zip(fingerprint_export(rows, account), rows, strict=True):
            if fingerprint in known:
                result.skipped[source] += 1
                continue
            known.add(fingerprint)
            result.kept.append(KeptRow(fingerprint, row, source))
    return result
