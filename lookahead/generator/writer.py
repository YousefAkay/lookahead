"""Write a persona's transactions as a Scotiabank chequing export plus an answer key.

Export format (matches the real file byte for byte in structure, see DECISIONS.md):
UTF-8 with a BOM, then pure ASCII; CRLF line endings; no newline after the last row;
every field quoted; `Filter` text on the first data row only; rows newest first;
`Balance` = running balance after each row.

The answer key is ours, not the bank's: plain UTF-8, two "# key=value" lines, then a
CSV header and one row per export row (`row` = position in the export).
"""

import csv
import io
from datetime import date, timedelta
from pathlib import Path

from lookahead.generator.personas import Persona
from lookahead.generator.pipeline import generate_persona
from lookahead.generator.schedules import GeneratedTxn

HEADER = [
    "Filter",
    "Date",
    "Description",
    "Sub-description",
    "Type of Transaction",
    "Amount",
    "Balance",
]
FILTER_TEXT = "All available transactions (up to 2 years)"
KEY_COLUMNS = [
    "row", "date", "amount_cents", "txn_type", "merchant", "category", "flow_type",
    "series_id", "one_off", "link_id", "quirk", "online", "currency", "original_amount_cents",
]  # fmt: skip


def money(cents: int) -> str:
    """Integer cents as the bank writes them: -648 -> "-6.48". Exact, no floats."""
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}{whole}.{part:02d}"


def export_rows(persona: Persona, start: date, export_date: date, seed: int) -> list[GeneratedTxn]:
    """Rows up to the export date, plus exactly one dated the next day (oldest first).

    The real export contains one posting dated the day after the export date. We
    generate one day further and keep the first row of that day, if any.
    """
    rows = generate_persona(persona, start, export_date + timedelta(days=1), seed)
    kept = [t for t in rows if t.posted_date <= export_date]
    after = [t for t in rows if t.posted_date > export_date]
    return kept + after[:1]


def _csv_text(records: list[list[str]], quoting: int, line_end: str) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, quoting=quoting, lineterminator=line_end).writerows(records)
    return buffer.getvalue()


def write_export(rows: list[GeneratedTxn], opening_balance_cents: int, path: Path) -> None:
    """The bank file. `rows` oldest first; written newest first with running balances."""
    balances, balance = [], opening_balance_cents
    for t in rows:
        balance += t.amount_cents
        balances.append(balance)
    records = [HEADER]
    for i, (t, bal) in enumerate(zip(reversed(rows), reversed(balances), strict=True)):
        records.append(
            [
                FILTER_TEXT if i == 0 else "",
                t.posted_date.isoformat(),
                t.description,
                t.sub_description,
                "Credit" if t.amount_cents > 0 else "Debit",
                money(t.amount_cents),
                money(bal),
            ]
        )
    text = _csv_text(records, csv.QUOTE_ALL, "\r\n").removesuffix("\r\n")  # no final newline
    text.encode("ascii")  # the real file is pure ASCII after the BOM: fail loudly otherwise
    path.write_bytes(text.encode("utf-8-sig"))  # utf-8-sig writes the BOM


def write_answer_key(
    rows: list[GeneratedTxn], opening_balance_cents: int, export_date: date, path: Path
) -> None:
    """Ground truth, one row per export row, in the export's (newest-first) order."""
    records = [KEY_COLUMNS]
    for i, t in enumerate(reversed(rows), start=1):
        values = [
            i, t.posted_date.isoformat(), t.amount_cents, t.txn_type, t.merchant, t.category,
            t.flow_type, t.series_id, t.one_off, t.link_id, t.quirk, t.online, t.currency,
            t.original_amount_cents,
        ]  # fmt: skip
        records.append(["" if v is None else str(v) for v in values])
    meta = (
        f"# opening_balance_cents={opening_balance_cents}\n"
        f"# export_date={export_date.isoformat()}\n"
    )
    path.write_text(meta + _csv_text(records, csv.QUOTE_MINIMAL, "\n"), encoding="utf-8")


def write_persona(
    persona: Persona, out_dir: Path, seed: int, start: date, export_date: date
) -> tuple[Path, Path]:
    """Write persona_<id>.csv and persona_<id>_answer_key.csv; return both paths."""
    rows = export_rows(persona, start, export_date, seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    export = out_dir / f"persona_{persona.persona_id}.csv"
    key = out_dir / f"persona_{persona.persona_id}_answer_key.csv"
    write_export(rows, persona.opening_balance_cents, export)
    write_answer_key(rows, persona.opening_balance_cents, export_date, key)
    return export, key
