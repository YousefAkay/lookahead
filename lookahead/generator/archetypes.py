"""Generator v1: 30 personas from 7 archetypes, 12 months each.

The mix, the parameter ranges, the held-out list and the buffers are fixed in DECISIONS.md
(2026-10-08, "Week 4 backtest design"), written before any backtest ran. Do not change them to
improve a result: a change needs a new DECISIONS.md entry and starts a new set of results.

Each persona's numbers are drawn once, from its own seeded stream (`PARAM_SEED` mixed with a
stable hash of its id), from the ranges below. The same id always gives the same persona.

Held-out personas (10 of 30) are never used to tune anything: tuning code calls
`assert_development_only` and gets an error for a held-out id.

    python -m lookahead.generator.archetypes --out data/synthetic_v1 [--only v1-SAL-01 ...]
"""

import argparse
import sys
import zlib
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from lookahead.generator.personas import (
    _SPENDING_A,
    _SPENDING_B,
    _SPENDING_C,
    Persona,
    Source,
    _big,
)
from lookahead.generator.schedules import (
    Amount,
    DatedItem,
    DatedSeries,
    IrregularSeries,
    RecurringSchedule,
    Shift,
)
from lookahead.generator.spending import SpendingProfile

START = date(2025, 10, 1)  # twelve months of history
END = date(2026, 9, 30)
PARAM_SEED = 2026  # mixed into every persona's parameter stream
OVERDRAFT = -50_000


@dataclass(frozen=True)
class Archetype:
    code: str
    n: int
    held_out: tuple[int, ...]  # the numbers (not ids) that are held out
    buffer_cents: int
    description: str


ARCHETYPES: dict[str, Archetype] = {
    a.code: a
    for a in (
        Archetype("SAL", 5, (4, 5), 30_000, "Salaried, biweekly pay, comfortable"),
        Archetype("SEM", 3, (3,), 30_000, "Salaried, paid the 15th and the last day"),
        Archetype("PTS", 4, (4,), 15_000, "Part-time student, small biweekly pay"),
        Archetype("TIGHT", 7, (6, 7), 10_000, "Tight budget, small buffer, near zero often"),
        Archetype("LUMP", 6, (5, 6), 50_000, "Lump-sum income: one deposit a term, no paycheque"),
        Archetype("FREE", 3, (3,), 20_000, "Freelancer, irregular deposits"),
        Archetype("AWAY", 2, (2,), 20_000, "Student away in summer and December"),
    )
}

PERSONA_IDS: tuple[str, ...] = tuple(
    f"v1-{a.code}-{i:02d}" for a in ARCHETYPES.values() for i in range(1, a.n + 1)
)
HELD_OUT: frozenset[str] = frozenset(
    f"v1-{a.code}-{i:02d}" for a in ARCHETYPES.values() for i in a.held_out
)
# `--quick` runs: three development personas of different kinds.
QUICK_IDS: tuple[str, ...] = ("v1-SAL-01", "v1-TIGHT-01", "v1-LUMP-01")


def development_ids() -> list[str]:
    """The personas that may be used for tuning and validation."""
    return [pid for pid in PERSONA_IDS if pid not in HELD_OUT]


def assert_development_only(ids: list[str] | tuple[str, ...]) -> None:
    """Tuning code calls this first: a held-out persona must never be used to choose anything."""
    bad = sorted(set(ids) & HELD_OUT)
    if bad:
        raise ValueError(f"held-out personas are never used for tuning: {', '.join(bad)}")


@dataclass(frozen=True)
class V1Persona:
    persona: Persona
    buffer_cents: int  # the persona's own safety buffer for the backtest
    archetype: str
    held_out: bool


# --- Drawing numbers ---


def _dollars(rng: np.random.Generator, low: int, high: int, step: int) -> int:
    """A whole-dollar amount in [low, high], a multiple of `step`, returned in cents."""
    return int(rng.integers(-(-low // step), high // step + 1)) * step * 100


def _spending(
    rng: np.random.Generator, base: SpendingProfile, low: float, high: float
) -> SpendingProfile:
    return replace(base, purchases_per_day=round(float(rng.uniform(low, high)), 2))


_EMPLOYERS = (
    "Boutique Mile End", "Groupe Conseil Plateau", "Clinique Verdun", "Studio Rosemont",
    "Librairie Atwater", "Atelier Jarry", "Resto Saint-Laurent", "Cafe Plateau Inc",
)  # fmt: skip
_FAMILY = ("Nadia Haddad", "Karim Haddad", "Layla Nasser", "Omar Khoury")  # made-up names
_BIG = (
    ("Concordia Bookstore", "Education", False),
    ("uBreakiFix", "Shopping", False),
    ("Ticketmaster", "Entertainment", True),
    ("Best Buy", "Shopping", False),
)


def _monthly(
    series_id: str, merchant: str, category: str, txn_type: str, day: int, cents: int,
    shift: Shift, spread: int = 0,
) -> RecurringSchedule:  # fmt: skip
    """A monthly outflow that already runs at the start of the window."""
    return RecurringSchedule(
        series_id=series_id, merchant=merchant, category=category,
        flow_type="transfer" if category == "Transfers" else "recurring_outflow",
        txn_type=txn_type, cadence="monthly", anchor=date(2025, 10, day),
        amount=Amount(-cents, spread), shift=shift,
        online=True if txn_type == "pos purchase" else None,
    )  # fmt: skip


def _rent(pid: str, rng: np.random.Generator, cents: int) -> RecurringSchedule:
    """Rent: an e-Transfer sent by hand, or a bill payment to a landlord."""
    day = int(rng.integers(1, 6))
    if rng.random() < 0.5:
        return _monthly(f"{pid}-rent", "Interac e-Transfer", "Rent & housing", "withdrawal", day,
                        cents, "card")  # fmt: skip
    return _monthly(f"{pid}-rent", "Gestion Immobilière", "Rent & housing", "bill payment", day,
                    cents, "later")  # fmt: skip


def _bills(pid: str, rng: np.random.Generator, savings: bool) -> list[Source]:
    """Phone always; internet, hydro, each subscription and (if asked) savings sometimes."""
    out: list[Source] = []
    day = lambda: int(rng.integers(5, 26))  # noqa: E731
    if rng.random() < 0.5:
        out.append(_monthly(f"{pid}-fizz", "Fizz", "Phone, internet & utilities", "pos purchase",
                            day(), _dollars(rng, 25, 55, 5), "card"))  # fmt: skip
    else:
        out.append(_monthly(f"{pid}-phone", "Vidéotron", "Phone, internet & utilities",
                            "bill payment", day(), _dollars(rng, 60, 110, 5), "later"))  # fmt: skip
    if rng.random() < 0.6:
        out.append(_monthly(f"{pid}-internet", "Bell Internet", "Phone, internet & utilities",
                            "bill payment", day(), _dollars(rng, 50, 80, 5), "later"))  # fmt: skip
    if rng.random() < 0.5:
        typical = _dollars(rng, 60, 140, 10)
        out.append(_monthly(f"{pid}-hydro", "Hydro-Québec", "Phone, internet & utilities",
                            "bill payment", day(), typical, "later", spread=3_000))  # fmt: skip
    for name, cents in (("Netflix", 1_899), ("Spotify", 1_299)):
        if rng.random() < 0.5:
            out.append(_monthly(f"{pid}-{name.lower()}", name, "Subscriptions", "pos purchase",
                                day(), cents, "card"))  # fmt: skip
    if savings and rng.random() < 0.6:
        amount = _dollars(rng, 100, 400, 50)
        out.append(_monthly(f"{pid}-savings", "Own savings account", "Transfers",
                            "customer transfer dr.", day(), amount, "later"))  # fmt: skip
    return out


def _one_offs(rng: np.random.Generator) -> list[Source]:
    """One to three larger purchases in the year."""
    out: list[Source] = []
    for _ in range(int(rng.integers(1, 4))):
        name, category, online = _BIG[int(rng.integers(len(_BIG)))]
        due = date(2025, 10, 15) + timedelta(days=int(rng.integers(0, 330)))
        out.append(_big(name, category, due, int(rng.integers(80, 401)), online))
    return out


def _pay(
    pid: str, rng: np.random.Generator, cadence: str, cents: int, spread: int = 0
) -> RecurringSchedule:  # fmt: skip
    employer = _EMPLOYERS[int(rng.integers(len(_EMPLOYERS)))]
    anchor = (
        date(2025, 10, 1)
        if cadence == "semimonthly"
        else date(2025, 10, 3) + timedelta(days=int(rng.integers(0, 5)))
    )
    return RecurringSchedule(
        series_id=f"{pid}-pay", merchant=employer, category="Income", flow_type="income",
        txn_type="deposit", cadence=cadence, anchor=anchor,  # type: ignore[arg-type]
        amount=Amount(cents, spread), shift="earlier",
    )  # fmt: skip


# --- The archetypes ---


def _salaried(pid: str, rng: np.random.Generator, cadence: str, pay: tuple[int, int],
              rent: tuple[int, int], opening: tuple[int, int]) -> Persona:  # fmt: skip
    pay_cents = _dollars(rng, *pay, 50)
    sources: list[Source] = [
        _pay(pid, rng, cadence, pay_cents),
        _rent(pid, rng, _dollars(rng, *rent, 25)),
    ]
    sources += _bills(pid, rng, savings=True) + _one_offs(rng)
    return Persona(
        persona_id=pid, description=f"{cadence} salaried", sources=tuple(sources),
        opening_balance_cents=_dollars(rng, *opening, 50),
        spending=_spending(rng, _SPENDING_B, 0.9, 1.3),
    )  # fmt: skip


def _part_time_student(pid: str, rng: np.random.Generator) -> Persona:
    pay_cents = _dollars(rng, 450, 900, 10)
    spread = int(rng.integers(6, 13)) * 1000  # $60-$120: hours vary
    sources: list[Source] = [_pay(pid, rng, "biweekly", pay_cents, spread),
                             _rent(pid, rng, _dollars(rng, 500, 800, 25))]  # fmt: skip
    sources += _bills(pid, rng, savings=False) + _one_offs(rng)
    return Persona(
        persona_id=pid, description="part-time student", sources=tuple(sources),
        opening_balance_cents=_dollars(rng, 300, 900, 10),
        spending=_spending(rng, _SPENDING_A, 1.0, 1.4),
    )  # fmt: skip


def _tight(pid: str, rng: np.random.Generator) -> Persona:
    pay_cents = _dollars(rng, 1100, 1700, 50)
    monthly_pay = pay_cents * 26 // 12
    share = float(rng.uniform(0.50, 0.62))
    rent_cents = round(monthly_pay * share / 500) * 500  # to $5
    sources: list[Source] = [_pay(pid, rng, "biweekly", pay_cents), _rent(pid, rng, rent_cents)]
    sources += _bills(pid, rng, savings=False) + _one_offs(rng)
    return Persona(
        persona_id=pid, description="tight budget", sources=tuple(sources),
        opening_balance_cents=_dollars(rng, 100, 500, 10),
        spending=_spending(rng, _SPENDING_B, 1.1, 1.5),
    )  # fmt: skip


def _lump(pid: str, rng: np.random.Generator) -> Persona:
    rent_cents = _dollars(rng, 600, 1100, 25)
    spending = _spending(rng, _SPENDING_C, 1.0, 1.6)
    monthly_spend = spending.purchases_per_day * 30.44 * 2000 + 6000  # about $20 a purchase
    tuition = _dollars(rng, 3000, 6000, 100)
    factor = float(rng.uniform(0.80, 1.15))
    deposit = round((tuition + 4 * (rent_cents + monthly_spend)) * factor / 10_000) * 10_000
    deposit = min(max(deposit, 600_000), 1_200_000)
    family = _FAMILY[int(rng.integers(len(_FAMILY)))]
    terms = (date(2026, 1, 5), date(2026, 5, 5), date(2026, 9, 3))
    sources: list[Source] = [
        DatedSeries(
            series_id=f"{pid}-family",
            merchant=family,
            category="Income",
            flow_type="income",
            txn_type="credit memo",
            shift="later",
            items=tuple(DatedItem(d, deposit) for d in terms),
        ),
        DatedSeries(
            series_id=f"{pid}-tuition",
            merchant="Concordia University",
            category="Education",
            flow_type="recurring_outflow",
            txn_type="bill payment",
            shift="later",
            items=tuple(DatedItem(d + timedelta(days=3), -tuition) for d in terms),
        ),
        _rent(pid, rng, rent_cents),
    ]
    sources += _bills(pid, rng, savings=False) + _one_offs(rng)
    return Persona(
        persona_id=pid, description="lump-sum income", sources=tuple(sources),
        opening_balance_cents=_dollars(rng, 3000, 8000, 50), spending=spending,
    )  # fmt: skip


def _freelancer(pid: str, rng: np.random.Generator) -> Persona:
    sources: list[Source] = [
        IrregularSeries(
            series_id=f"{pid}-gigs",
            merchant="Interac e-Transfer",
            category="Income",
            flow_type="income",
            txn_type="deposit",
            gap_days=(5, 20),
            amount_range_cents=(20_000, 150_000),
            round_to_cents=5_000,
        ),
        _rent(pid, rng, _dollars(rng, 700, 1200, 25)),
    ]
    sources += _bills(pid, rng, savings=False) + _one_offs(rng)
    return Persona(
        persona_id=pid, description="freelancer", sources=tuple(sources),
        opening_balance_cents=_dollars(rng, 500, 2000, 50),
        spending=_spending(rng, _SPENDING_B, 1.0, 1.4),
    )  # fmt: skip


def _away(pid: str, rng: np.random.Generator) -> Persona:
    bursary = RecurringSchedule(
        series_id=f"{pid}-bursary", merchant="Bourse Concordia", category="Income",
        flow_type="income", txn_type="deposit", cadence="monthly", anchor=date(2025, 10, 1),
        amount=Amount(_dollars(rng, 900, 1400, 50)), shift="earlier",
    )  # fmt: skip
    sources: list[Source] = [bursary, _rent(pid, rng, _dollars(rng, 500, 800, 25))]
    sources += _bills(pid, rng, savings=False) + _one_offs(rng)
    return Persona(
        persona_id=pid, description="away in summer and December", sources=tuple(sources),
        opening_balance_cents=_dollars(rng, 400, 1200, 50),
        spending=_spending(rng, _SPENDING_A, 1.0, 1.4),
        quiet_months=frozenset({7, 8, 12}), quiet_activity=0.15,
    )  # fmt: skip


def build_persona(persona_id: str) -> V1Persona:
    """The persona with this id. The same id always gives the same persona."""
    parts = persona_id.split("-")
    if len(parts) != 3 or parts[0] != "v1" or parts[1] not in ARCHETYPES or not parts[2].isdigit():
        raise ValueError(f"unknown persona id {persona_id!r}")
    archetype = ARCHETYPES[parts[1]]
    if not 1 <= int(parts[2]) <= archetype.n:
        raise ValueError(f"unknown persona id {persona_id!r}")
    rng = np.random.default_rng(
        np.random.SeedSequence([PARAM_SEED, zlib.crc32(persona_id.encode())])
    )
    code = archetype.code
    if code == "SAL":
        persona = _salaried(persona_id, rng, "biweekly", (1500, 2600), (900, 1500), (1500, 4000))
    elif code == "SEM":
        persona = _salaried(persona_id, rng, "semimonthly", (1300, 2200), (800, 1400), (1200, 3500))
    elif code == "PTS":
        persona = _part_time_student(persona_id, rng)
    elif code == "TIGHT":
        persona = _tight(persona_id, rng)
    elif code == "LUMP":
        persona = _lump(persona_id, rng)
    elif code == "FREE":
        persona = _freelancer(persona_id, rng)
    else:
        persona = _away(persona_id, rng)
    return V1Persona(persona, archetype.buffer_cents, code, persona_id in HELD_OUT)


def main(argv: list[str] | None = None) -> int:
    """Write the exports and answer keys of the chosen personas (default: all 30), one at a time."""
    from lookahead.generator.writer import export_rows, money, write_persona

    parser = argparse.ArgumentParser(prog="python -m lookahead.generator.archetypes",
                                     description=__doc__)  # fmt: skip
    parser.add_argument("--out", type=Path, default=Path("data/synthetic_v1"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args(argv)
    for pid in args.only or PERSONA_IDS:
        p = build_persona(pid)
        export, key = write_persona(p.persona, args.out, args.seed, START, END)
        rows = export_rows(p.persona, START, END, args.seed)
        balance, low = p.persona.opening_balance_cents, p.persona.opening_balance_cents
        for t in rows:
            balance += t.amount_cents
            low = min(low, balance)
        kind = "held-out" if p.held_out else "dev"
        print(f"{pid:12} {kind:8} {len(rows):4} rows  lowest {money(low):>9}  "
              f"closing {money(balance):>9}  ({export.name}, {key.name})")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
