"""Parser for the Scotiabank chequing CSV export.

Format (see DECISIONS.md): UTF-8 with a BOM (a file that is not valid UTF-8 is read as
Windows-1252, the other encoding French text comes in), CRLF line endings, no newline after
the last row, every field quoted, rows newest first, `Filter` text on the first data
row only, signed CAD amounts, a running `Balance` after each row. The export holds
posted transactions only, so nothing is filtered here.

Parsing never guesses: any malformed row raises ExportFormatError with its line number.
"""

import csv
import io
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
    """The file is not a Scotiabank chequing export in the expected format.

    The message may quote the offending text (useful on synthetic files). `kind` and
    `line` carry the same information without any data, for counts-only reports.
    """

    def __init__(self, message: str, kind: str, line: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind  # fixed label, e.g. "amount", "header", "encoding"
        self.line = line


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
    def fail(kind: str, reason: str) -> ExportFormatError:
        return ExportFormatError(f"line {line_no}: {reason}", kind, line_no)

    if len(record) != len(EXPECTED_HEADER):
        raise fail("field count", f"expected 7 fields, found {len(record)}")
    _filter, raw_date, description, sub_description, kind, raw_amount, raw_balance = record

    if not _ISO_DATE.fullmatch(raw_date):
        raise fail("date", f"date is not YYYY-MM-DD: {raw_date!r}")
    try:
        posted = date.fromisoformat(raw_date)  # dates after the export date are fine
    except ValueError as e:
        raise fail("date", f"invalid date {raw_date!r}") from e
    try:
        amount = parse_amount_cents(raw_amount)
    except ValueError as e:
        raise fail("amount", f"bad amount {raw_amount!r}") from e
    try:
        balance = parse_amount_cents(raw_balance)
    except ValueError as e:
        raise fail("balance", f"bad balance {raw_balance!r}") from e

    if kind not in ("Debit", "Credit"):
        raise fail("type", f"Type of Transaction must be Debit or Credit, found {kind!r}")
    if (amount > 0 and kind != "Credit") or (amount < 0 and kind != "Debit"):
        raise fail("sign", f"{kind} does not match the sign of amount {raw_amount}")

    return ParsedRow(
        posted_date=posted,
        amount_cents=amount,
        balance_cents=balance,
        description=description.strip(),
        sub_description=sub_description,
        position=position,
        line_no=line_no,
    )


# Tried in this order; the first that decodes the whole file wins. Both are strict: a byte that
# does not belong to the encoding fails it, so nothing is replaced by "?" or dropped.
# utf-8-sig strips the BOM (and works without one).
ENCODINGS = (("utf-8-sig", "UTF-8"), ("cp1252", "Windows-1252"))


def read_export_text(path: Path) -> tuple[str, str]:
    """The file's text and the name of the encoding it was read in."""
    data = path.read_bytes()
    for codec, name in ENCODINGS:
        try:
            return data.decode(codec), name
        except UnicodeDecodeError:
            continue
    raise ExportFormatError(f"{path.name}: neither UTF-8 nor Windows-1252 text", "encoding")


def export_encoding(path: Path) -> str:
    """The encoding an export is read in: "UTF-8" or "Windows-1252"."""
    return read_export_text(path)[1]


def parse_export(path: Path) -> list[ParsedRow]:
    """All rows of one export, in file order (newest first)."""
    text, _ = read_export_text(path)
    # newline="" lets csv handle CRLF, LF, quoted fields and a missing final newline.
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None:
        raise ExportFormatError(f"{path.name}: empty file", "empty file")
    if header != EXPECTED_HEADER:
        raise ExportFormatError(f"line 1: unexpected header {header}", "header", 1)
    rows: list[ParsedRow] = []
    for record in reader:
        if not record:
            continue  # tolerate a blank line
        rows.append(_parse_row(record, len(rows) + 1, reader.line_num))
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
