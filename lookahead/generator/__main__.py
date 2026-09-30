"""Generate all v0 personas into data/synthetic/.

Run from the project root, with the virtual environment active:

    python -m lookahead.generator
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import export_rows, money, write_persona


def main(argv: list[str] | None = None) -> int:
    """Write one export and one answer key per persona; print a summary line for each."""
    parser = argparse.ArgumentParser(prog="python -m lookahead.generator", description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 6, 1))
    parser.add_argument("--export-date", type=date.fromisoformat, default=date(2026, 9, 30))
    parser.add_argument("--out", type=Path, default=Path("data/synthetic"))
    args = parser.parse_args(argv)

    for persona in PERSONAS.values():
        export, key = write_persona(persona, args.out, args.seed, args.start, args.export_date)
        rows = export_rows(persona, args.start, args.export_date, args.seed)
        closing = persona.opening_balance_cents + sum(t.amount_cents for t in rows)
        first, last = rows[0].posted_date, rows[-1].posted_date
        print(
            f"{export.name:16} {len(rows):4} rows  {first} to {last}  "
            f"opening {money(persona.opening_balance_cents):>9}  closing {money(closing):>9}  "
            f"(+ {key.name})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
