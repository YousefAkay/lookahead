"""Parser for the Scotiabank chequing CSV export.

Format (see DECISIONS.md): UTF-8 with a BOM, CRLF line endings, no newline after
the last row, every field quoted, rows newest first, `Filter` text on the first data
row only, signed CAD amounts, a running `Balance` after each row. The export holds
posted transactions only, so nothing is filtered here.

Parsing never guesses: any malformed row raises ExportFormatError with its line number.
"""

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

EXPECTED_HEADER = [
    "Filter", "Date", "Description", "Sub-description", "Type of Transaction", "Amount", "Balance",
]  # fmt: skip

_MONEY = re.compile(r"(-?)(\d+)\.(\d{2})")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ExportFormatError(ValueError):
    """The file is not a Scotiabank chequing export in the expected format."""


@dataclass(frozen=True)
class ParsedRow:
    """One export row, parsed. Amounts are integer cents; text is kept as in the file."""

    posted_date: date
    amount_cents: int
    balance_cents: int  # running balance after this row
    description: str  # the bank's transaction type, e.g. "pos purchase"
    sub_description: str  # raw merchant text, trailing spaces kept
    position: int  # 1 = first (newest) data row
    line_no: int  # line in the file, for error messages (header = line 1)

    @property
    def sub_description_norm(self) -> str:
        """Merchant text for matching: runs of spaces collapsed, ends trimmed."""
        return " ".join(self.sub_description.split())


def parse_amount_cents(text: str) -> int:
    """Bank money text to integer cents: "-6.48" -> -648. Exactly two decimals, no floats."""
    match = _MONEY.fullmatch(text)
    if not match:
        raise ValueError(f"not an amount with two decimals: {text!r}")
    sign, whole, part = match.groups()
    cents = int(whole) * 100 + int(part)
    return -cents if sign else cents


def _parse_row(record: list[str], position: int, line_no: int) -> ParsedRow:
    def fail(reason: str) -> ExportFormatError:
        return ExportFormatError(f"line {line_no}: {reason}")

    if len(record) != len(EXPECTED_HEADER):
        raise fail(f"expected 7 fields, found {len(record)}")
    _filter, raw_date, description, sub_description, kind, raw_amount, raw_balance = record

    if not _ISO_DATE.fullmatch(raw_date):
        raise fail(f"date is not YYYY-MM-DD: {raw_date!r}")
    try:
        posted = date.fromisoformat(raw_date)  # dates after the export date are fine
    except ValueError as e:
        raise fail(f"invalid date {raw_date!r}") from e
    try:
        amount = parse_amount_cents(raw_amount)
    except ValueError as e:
        raise fail(f"bad amount {raw_amount!r}") from e
    try:
        balance = parse_amount_cents(raw_balance)
    except ValueError as e:
        raise fail(f"bad balance {raw_balance!r}") from e

    if kind not in ("Debit", "Credit"):
        raise fail(f"Type of Transaction must be Debit or Credit, found {kind!r}")
    if (amount > 0 and kind != "Credit") or (amount < 0 and kind != "Debit"):
        raise fail(f"{kind} does not match the sign of amount {raw_amount}")

    return ParsedRow(
        posted_date=posted,
        amount_cents=amount,
        balance_cents=balance,
        description=description.strip(),
        sub_description=sub_description,
        position=position,
        line_no=line_no,
    )


def parse_export(path: Path) -> list[ParsedRow]:
    """All rows of one export, in file order (newest first)."""
    try:
        # utf-8-sig strips the BOM (and works without one); newline="" lets csv
        # handle CRLF, LF, quoted fields and a missing final newline.
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is None:
                raise ExportFormatError(f"{path.name}: empty file")
            if header != EXPECTED_HEADER:
                raise ExportFormatError(f"line 1: unexpected header {header}")
            rows: list[ParsedRow] = []
            for record in reader:
                if not record:
                    continue  # tolerate a blank line
                rows.append(_parse_row(record, len(rows) + 1, reader.line_num))
    except UnicodeDecodeError as e:
        raise ExportFormatError(f"{path.name}: not UTF-8 text ({e.reason})") from e
    return rows


def balance_chain_breaks(rows: list[ParsedRow]) -> list[int]:
    """Positions where a row's balance != the next (older) row's balance + its amount.

    An empty list means the export's running balance is internally consistent.
    """
    return [
        newer.position
        for newer, older in zip(rows, rows[1:], strict=False)
        if newer.balance_cents != older.balance_cents + newer.amount_cents
    ]
