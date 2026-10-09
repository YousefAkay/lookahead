"""One line per persona of the generator mix: what its generated year looks like.

    python -m eval.persona_report                 # all 30
    python -m eval.persona_report --only v2-SAL-01

For each persona: archetype, development or held-out, coverage ratio (regular income over costs),
the lowest and closing balance of the generated year, and how many of the 40 weekly origins start
below the persona's buffer. These are properties of the generated histories, not of any forecast:
nothing here runs a forecast. Synthetic data only.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import date

from eval.backtest import origins
from lookahead.generator.archetypes import (
    END,
    PERSONA_IDS,
    START,
    build_persona,
    coverage,
)
from lookahead.generator.writer import export_rows, money

SEED = 42  # the seed the backtest generates the histories with


@dataclass(frozen=True)
class PersonaLine:
    persona_id: str
    archetype: str
    held_out: bool
    coverage_ratio: float
    buffer_cents: int
    lowest_cents: int
    closing_cents: int
    origins_below_buffer: int
    origins: int


def balances_by_day(persona_id: str, seed: int = SEED) -> tuple[dict[date, int], int, int]:
    """(closing balance of each day with a posting, opening balance, lowest balance)."""
    p = build_persona(persona_id)
    rows = export_rows(p.persona, START, END, seed)
    balance = low = p.persona.opening_balance_cents
    by_day: dict[date, int] = {}
    for t in rows:
        if t.posted_date > END:
            continue  # the one posting dated after the export is not part of the year
        balance += t.amount_cents
        low = min(low, balance)
        by_day[t.posted_date] = balance  # rows are oldest first, so the last of a day wins
    return by_day, p.persona.opening_balance_cents, low


def persona_line(persona_id: str, seed: int = SEED) -> PersonaLine:
    p = build_persona(persona_id)
    by_day, opening, low = balances_by_day(persona_id, seed)
    days = sorted(by_day)
    below = 0
    days_origins = origins()
    for origin in days_origins:
        known = [d for d in days if d <= origin]
        start = by_day[known[-1]] if known else opening
        below += start < p.buffer_cents
    return PersonaLine(
        persona_id, p.archetype, p.held_out, coverage(p.persona).ratio, p.buffer_cents, low,
        by_day[days[-1]], below, len(days_origins),
    )  # fmt: skip


def format_line(line: PersonaLine) -> str:
    kind = "held-out" if line.held_out else "dev"
    return (
        f"{line.persona_id:14} {kind:8} coverage {line.coverage_ratio:4.2f}  "
        f"buffer {money(line.buffer_cents):>7}  lowest {money(line.lowest_cents):>10}  "
        f"closing {money(line.closing_cents):>10}  "
        f"{line.origins_below_buffer:2d} of {line.origins} origins start below the buffer"
    )


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    details: list[str]  # the personas that failed, and why


# SAL and SEM were left alone: their lowest and closing balance (cents) must be unchanged.
UNCHANGED = {
    "v2-SAL-01": (295_000, 3_218_004), "v2-SAL-02": (300_000, 3_420_327),
    "v2-SAL-03": (301_056, 1_375_793), "v2-SAL-04": (213_717, 3_684_799),
    "v2-SEM-01": (79_664, 345_958), "v2-SEM-02": (59_681, 1_603_626),
}  # fmt: skip


def _code(line: PersonaLine) -> str:
    return line.persona_id.split("-")[1]


def acceptance_checks(lines: list[PersonaLine]) -> list[CheckResult]:
    """The four checks fixed in DECISIONS.md (2026-10-09) before the last regeneration, from
    generated balances only:
    1. IFAM below the buffer at no more than 3 of the 40 origins;
    2. ITIGHT: lowest balance at most the buffer plus $300 (a dip near or below it) and at least
       -$3,000 (no runaway);
    3. DHOME, DRENT, TIGHT, FREE below the buffer at no more than 20 of the 40 origins;
    4. SAL and SEM exactly as before."""
    ifam, itight, others, same = [], [], [], []
    for ln in lines:
        code = _code(ln)
        if code == "IFAM" and ln.origins_below_buffer > 3:
            ifam.append(f"{ln.persona_id}: below the buffer at {ln.origins_below_buffer} origins")
        if code == "ITIGHT":
            if ln.lowest_cents > ln.buffer_cents + 30_000:
                itight.append(f"{ln.persona_id}: never dips near its buffer "
                              f"(lowest {money(ln.lowest_cents)})")  # fmt: skip
            if ln.lowest_cents < -300_000:
                itight.append(f"{ln.persona_id}: runs away (lowest {money(ln.lowest_cents)})")
        if code in ("DHOME", "DRENT", "TIGHT", "FREE") and ln.origins_below_buffer > 20:
            others.append(f"{ln.persona_id}: below the buffer at {ln.origins_below_buffer} origins")
        if (
            ln.persona_id in UNCHANGED
            and (ln.lowest_cents, ln.closing_cents) != UNCHANGED[ln.persona_id]
        ):
            same.append(f"{ln.persona_id}: lowest or closing balance changed")
    return [
        CheckResult("IFAM: at most 3 origins below the buffer", not ifam, ifam),
        CheckResult("ITIGHT: dips near the buffer, no runaway", not itight, itight),
        CheckResult(
            "DHOME, DRENT, TIGHT, FREE: at most 20 origins below the buffer", not others, others
        ),  # fmt: skip
        CheckResult("SAL, SEM: unchanged", not same, same),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.persona_report", description=__doc__)
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--check", action="store_true", help="run the acceptance checks")
    args = parser.parse_args(argv)
    lines = [persona_line(pid) for pid in args.only or PERSONA_IDS]
    for ln in lines:
        print(format_line(ln))
    if args.check:
        print()
        for result in acceptance_checks(lines):
            print(f"{'PASS' if result.passed else 'FAIL'}  {result.name}")
            for detail in result.details:
                print(f"        {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
