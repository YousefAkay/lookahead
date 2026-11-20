"""One line per persona of the generator mix: what its generated year looks like.

    python -m eval.persona_report                 # the 20 development personas
    python -m eval.persona_report --only v2-SAL-01
    python -m eval.persona_report --check --set long   # the 24-month set, development only

For each persona: archetype, development or held-out, coverage ratio (regular income over costs),
the lowest and closing balance of the generated year, and how many of the 40 weekly origins start
below the persona's buffer. These are properties of the generated histories, not of any forecast:
nothing here runs a forecast. Synthetic data only. Development personas only: a held-out
persona is refused (held-out data goes only through eval/held_out.py, protocol of 2026-11-19).
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import date

from eval.backtest import origins
from lookahead.generator.archetypes import (
    END,
    assert_development_only,
    base_id,
    build_persona,
    coverage,
    development_ids,
    is_long,
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
    rows = export_rows(p.persona, p.start, END, seed)
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
    assert_development_only([persona_id])  # before anything is generated
    p = build_persona(persona_id)
    by_day, opening, low = balances_by_day(persona_id, seed)
    days = sorted(by_day)
    below = 0
    days_origins = origins()
    for origin in days_origins:
        known = [d for d in days if d <= origin]
        start = by_day[known[-1]] if known else opening
        below += start < p.buffer_cents
    # The coverage ratio is defined on a regular term month of the base year; a 24-month persona
    # has the parameters of its 12-month namesake, so it is that persona's ratio.
    ratio = coverage(build_persona(base_id(persona_id)).persona).ratio
    return PersonaLine(
        persona_id, p.archetype, p.held_out, ratio, p.buffer_cents, low,
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
    codes: tuple[str, ...] = ()  # the archetypes the check is about; () means every persona


# SAL and SEM were left alone: their lowest and closing balance (cents) must be unchanged.
# Development personas only: v2-SAL-04 and v2-SEM-02 (held out) were removed on 2026-11-19.
UNCHANGED = {
    "v2-SAL-01": (295_000, 3_218_004), "v2-SAL-02": (300_000, 3_420_327),
    "v2-SAL-03": (301_056, 1_375_793), "v2-SEM-01": (79_664, 345_958),
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
        CheckResult("IFAM: at most 3 origins below the buffer", not ifam, ifam, ("IFAM",)),
        CheckResult("ITIGHT: dips near the buffer, no runaway", not itight, itight, ("ITIGHT",)),
        CheckResult(
            "DHOME, DRENT, TIGHT, FREE: at most 20 origins below the buffer",
            not others,
            others,
            ("DHOME", "DRENT", "TIGHT", "FREE"),
        ),  # fmt: skip
        CheckResult("SAL, SEM: unchanged", not same, same, ("SAL", "SEM")),
    ]


def missing_for(result: CheckResult, ids: list[str], every: list[str]) -> list[str]:
    """The personas a check is about that are not among `ids` (run with --only): a check that
    did not see all of them is skipped, not passed."""
    return [pid for pid in every if pid not in ids
            and (not result.codes or pid.split("-")[1] in result.codes)]  # fmt: skip


def verdict_line(result: CheckResult, ids: list[str], every: list[str]) -> str:
    """PASS, FAIL, or SKIPPED with the personas it would need."""
    missing = missing_for(result, ids, every)
    if missing:
        return f"SKIPPED  {result.name} (not run on all its personas; missing: " \
               f"{', '.join(missing)})"  # fmt: skip
    return f"{'PASS' if result.passed else 'FAIL'}  {result.name}"


LAST_ORIGIN = date(2026, 8, 31)
MIN_TUITION_PAYMENTS = 2
RUNAWAY_CLOSING_CENTS = -2_000_000  # -$20,000: the plan's proposal (DECISIONS.md, step A)


def tuition_payments(persona_id: str, by: date = LAST_ORIGIN, seed: int = SEED) -> int | None:
    """How many tuition payments to the one tuition payee were posted on or before `by`; None if
    the persona pays no tuition from chequing."""
    p = build_persona(persona_id)
    rows = export_rows(p.persona, p.start, END, seed)
    paid = [t for t in rows if (t.series_id or "").endswith("-tuition")]
    if not paid:
        return None
    assert len({t.merchant for t in paid}) == 1  # one payee
    return sum(t.posted_date <= by for t in paid)


def reconciles(persona_id: str, seed: int = SEED) -> bool:
    """The generated export imports and its balance chain reconciles (importer PASS)."""
    import tempfile
    from datetime import datetime
    from pathlib import Path

    from lookahead.db import connect
    from lookahead.generator.writer import write_persona
    from lookahead.ingest.reconcile import reconcile
    from lookahead.ingest.scotiabank import parse_export
    from lookahead.ingest.store import store_exports

    p = build_persona(persona_id)
    with tempfile.TemporaryDirectory() as folder:
        export, _ = write_persona(p.persona, Path(folder), seed, p.start, END)
        conn = connect(Path(folder) / "check.db")
        try:
            store_exports(conn, "acct", [(export.name, parse_export(export))],
                          datetime(2026, 10, 1))  # fmt: skip
            return reconcile(conn, "acct").ok
        finally:
            conn.close()


def long_set_gates(lines: list[PersonaLine]) -> list[CheckResult]:
    """The continue-only-if checks of the 24-month set (DECISIONS.md, 2026-10-11, step A):
    every development persona reconciles; at most half of those that pay tuition from chequing
    have fewer than two tuition payments by the last origin; no closing balance below -$20,000."""
    broken = [ln.persona_id for ln in lines if not reconciles(ln.persona_id)]
    counts = {ln.persona_id: tuition_payments(ln.persona_id) for ln in lines}
    payers = {pid: n for pid, n in counts.items() if n is not None}
    few = [f"{pid}: {n} tuition payment(s) by {LAST_ORIGIN}" for pid, n in payers.items()
           if n < MIN_TUITION_PAYMENTS]  # fmt: skip
    runaway = [f"{ln.persona_id}: closing {money(ln.closing_cents)}" for ln in lines
               if ln.closing_cents < RUNAWAY_CLOSING_CENTS]  # fmt: skip
    return [
        CheckResult("Every development persona imports and reconciles", not broken, broken),
        CheckResult(
            f"Tuition payers with 2+ payments by {LAST_ORIGIN}: "
            f"{len(payers) - len(few)} of {len(payers)} (stop if fewer than half)",
            len(few) * 2 <= len(payers),
            few,
        ),  # fmt: skip
        CheckResult("No closing balance below -$20,000", not runaway, runaway),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.persona_report", description=__doc__)
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--check", action="store_true", help="run the acceptance checks")
    parser.add_argument("--set", choices=("12", "long"), default="12",
                        help="the 12-month mix, or the 24-month set (dev only)")  # fmt: skip
    args = parser.parse_args(argv)
    long = args.set == "long"
    ids = args.only or development_ids(long=long)
    if long and [pid for pid in ids if not is_long(pid)]:
        raise ValueError("--set long takes v2L ids only")
    lines = [persona_line(pid) for pid in ids]
    for ln in lines:
        print(format_line(ln))
    if args.check:
        print()
        # Check 4 (SAL and SEM unchanged) is about the 12-month mix only.
        checks = acceptance_checks(lines)[:3] + long_set_gates(lines) if long else (
            acceptance_checks(lines))  # fmt: skip
        every = development_ids(long=long)
        for result in checks:
            line = verdict_line(result, ids, every)
            print(line)
            if not line.startswith("SKIPPED"):
                for detail in result.details:
                    print(f"        {detail}")
        if long:
            for ln in lines:
                n = tuition_payments(ln.persona_id)
                if n is not None:
                    print(f"  {ln.persona_id}: {n} tuition payments by {LAST_ORIGIN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
