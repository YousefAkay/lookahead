"""Score merchant extraction against the generator's answer keys (synthetic data only).

    python -m eval.merchant_extraction            # data/synthetic/persona_{A,B,C}
    python -m eval.merchant_extraction --show-misses

The answer key holds the canonical merchant ("Uber Eats", "Café Velisaro"), which is the
target of resolution, not of extraction. A row counts as an exact match when the
extracted name equals the canonical name as the bank would print it, ignoring case:
accents become "?" on card rows ("Caf? Velisaro") and are dropped elsewhere
("Hydro-Quebec"). Rows whose key merchant is a placeholder for "no merchant in the text"
(ABM, bank fees, e-Transfers, own-account transfers) match when extraction is empty.
"""

import argparse
import csv
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lookahead.merchants import extract_merchant, has_leftovers

# Answer-key merchants that the bank text never names.
NO_MERCHANT = {"ABM", "Scotiabank", "Interac e-Transfer", "Own savings account"}


def _bank_forms(canonical: str) -> set[str]:
    """How the bank could print a name: accents as "?" (card) or dropped (payees)."""
    question = "".join(c if ord(c) < 128 else "?" for c in canonical)
    decomposed = unicodedata.normalize("NFKD", canonical)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return {question.casefold(), stripped.casefold()}


def is_match(extracted: str | None, canonical: str) -> bool:
    if canonical in NO_MERCHANT:
        return extracted is None
    return extracted is not None and extracted.casefold() in _bank_forms(canonical)


@dataclass
class Score:
    rows: int = 0
    matched: int = 0
    noisy: int = 0  # extracted names that still hold '#', '*', '+digits' or 3+ digits
    misses: Counter[tuple[str, str]] = field(default_factory=Counter)  # (canonical, extracted)

    @property
    def rate(self) -> float:
        return self.matched / self.rows if self.rows else 0.0


def score_persona(export: Path, answer_key: Path) -> Score:
    """Compare extraction on one export with its answer key (same rows, same order)."""
    with export.open(encoding="utf-8-sig", newline="") as f:
        texts = [r["Sub-description"] for r in csv.DictReader(f)]
    with answer_key.open(encoding="utf-8", newline="") as f:
        lines = [line for line in f if not line.startswith("#")]
    truth = [r["merchant"] for r in csv.DictReader(lines)]
    if len(texts) != len(truth):
        raise ValueError(f"{export.name}: {len(texts)} rows but {len(truth)} answer-key rows")

    result = Score()
    for text, canonical in zip(texts, truth, strict=True):
        got = extract_merchant(text).merchant
        result.rows += 1
        if got is not None and has_leftovers(got):
            result.noisy += 1
        if is_match(got, canonical):
            result.matched += 1
        else:
            result.misses[(canonical, got or "(empty)")] += 1
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.merchant_extraction")
    parser.add_argument("--dir", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--show-misses", action="store_true")
    args = parser.parse_args(argv)

    total = Score()
    print("Merchant extraction vs answer keys (synthetic)")
    for pid in "ABC":
        s = score_persona(
            args.dir / f"persona_{pid}.csv", args.dir / f"persona_{pid}_answer_key.csv"
        )
        print(f"  persona {pid}: {s.matched}/{s.rows} exact = {s.rate:.1%}, noisy names {s.noisy}")
        total.rows += s.rows
        total.matched += s.matched
        total.noisy += s.noisy
        total.misses.update(s.misses)
    print(
        f"  all: {total.matched}/{total.rows} exact = {total.rate:.1%}, noisy names {total.noisy}"
    )
    if args.show_misses:
        print("misses (canonical <- extracted, rows):")
        for (canonical, got), n in total.misses.most_common():
            print(f"  {canonical!r} <- {got!r}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
