"""Generator, mix v2: 30 personas from 8 archetypes, 12 months each.

The mix, the parameter ranges, the per-persona assignments, the held-out list and the buffers are
fixed in DECISIONS.md (2026-10-08, "Persona mix revised: four student archetypes"), written before
this code and before any forecast error, coverage or alert result was seen. Do not change them to
improve a result: a change needs a new DECISIONS.md entry and starts a new set of results.

Each persona's numbers are drawn once, from its own seeded stream (`PARAM_SEED` mixed with a
stable hash of its id), from the ranges below. The same id always gives the same persona.

Held-out personas (10 of 30) are never used to tune anything: tuning code calls
`assert_development_only` and gets an error for a held-out id.

Archetype codes: IFAM international, reliable family funding; ITIGHT international, stretched
family; DHOME domestic, living with parents; DRENT domestic, renting; SAL, SEM, TIGHT, FREE for
people who are not students.

    python -m lookahead.generator.archetypes --out data/synthetic_v2 [--only v2-SAL-01 ...]
"""

import argparse
import math
import sys
import zlib
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from lookahead.generator.personas import (
    _SPENDING_A,
    _SPENDING_B,
    Persona,
    Source,
    _big,
)
from lookahead.generator.schedules import (
    Amount,
    DatedItem,
    DatedSeries,
    GeneratedTxn,
    IrregularSeries,
    RecurringSchedule,
    Shift,
    _post,
)
from lookahead.generator.spending import CURRENCY_RATES, SpendingProfile

MIX = "v2"
START = date(2025, 10, 1)  # twelve months of history
END = date(2026, 9, 30)
PARAM_SEED = 2026  # mixed into every persona's parameter stream
NET_SHARE = 0.85  # the share of gross wages that reaches the account (a modeling assumption)
TERM_HOURS_CAP = 24  # international students: hours a week during term


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
        Archetype("IFAM", 4, (4,), 30_000, "International student, reliable family funding"),
        Archetype("ITIGHT", 5, (4, 5), 10_000, "International student, stretched family"),
        Archetype("DHOME", 4, (4,), 15_000, "Domestic student living with parents"),
        Archetype("DRENT", 5, (4, 5), 15_000, "Domestic student renting, working, some on aid"),
        Archetype("SAL", 4, (4,), 30_000, "Salaried, biweekly pay, comfortable"),
        Archetype("SEM", 2, (2,), 30_000, "Salaried, paid the 15th and the last day"),
        Archetype("TIGHT", 4, (4,), 10_000, "Worker on a tight budget, near zero often"),
        Archetype("FREE", 2, (2,), 20_000, "Freelancer, irregular deposits"),
    )
}

PERSONA_IDS: tuple[str, ...] = tuple(
    f"{MIX}-{a.code}-{i:02d}" for a in ARCHETYPES.values() for i in range(1, a.n + 1)
)
HELD_OUT: frozenset[str] = frozenset(
    f"{MIX}-{a.code}-{i:02d}" for a in ARCHETYPES.values() for i in a.held_out
)
# `--quick` runs: three development personas of different kinds.
QUICK_IDS: tuple[str, ...] = ("v2-SAL-01", "v2-ITIGHT-01", "v2-DRENT-01")

# The 24-month set (DECISIONS.md, 2026-10-11, step A): the same 30 specs, ids `v2L-...`, history
# from LONG_START to END. A separate set of results, never pooled with the 12-month set.
LONG_MIX = "v2L"
LONG_START = date(2024, 10, 1)
LONG_IDS: tuple[str, ...] = tuple(pid.replace(f"{MIX}-", f"{LONG_MIX}-", 1) for pid in PERSONA_IDS)
LONG_HELD_OUT: frozenset[str] = frozenset(
    pid.replace(f"{MIX}-", f"{LONG_MIX}-", 1) for pid in HELD_OUT
)
ALL_HELD_OUT: frozenset[str] = HELD_OUT | LONG_HELD_OUT


def is_long(persona_id: str) -> bool:
    """True for a persona of the 24-month set."""
    return persona_id.startswith(f"{LONG_MIX}-")


def base_id(persona_id: str) -> str:
    """The 12-month namesake of a 24-month persona (the id itself for a 12-month persona)."""
    return persona_id.replace(f"{LONG_MIX}-", f"{MIX}-", 1) if is_long(persona_id) else persona_id


def development_ids(long: bool = False) -> list[str]:
    """The personas that may be used for tuning and validation (of the 24-month set if `long`)."""
    return [pid for pid in (LONG_IDS if long else PERSONA_IDS) if pid not in ALL_HELD_OUT]


HARNESS = "eval/held_out.py"  # the only route to held-out data (protocol of 2026-11-19)


def called_by_harness() -> bool:
    """True when the held-out harness is on the call stack (in this process or a worker it
    started): only then may held-out data be read, forecast or scored."""
    harness = (Path(__file__).resolve().parents[2] / HARNESS).as_posix()  # this repository's own
    frame = sys._getframe(1)
    while frame is not None:
        if Path(frame.f_code.co_filename).resolve().as_posix() == harness:
            return True
        frame = frame.f_back
    return False


def assert_development_only(ids: list[str] | tuple[str, ...]) -> None:
    """Tuning code calls this first: a held-out persona (of either set) must never be used to
    choose anything. Only the held-out harness passes."""
    bad = sorted(set(ids) & ALL_HELD_OUT)
    if bad and not called_by_harness():
        raise ValueError(f"held-out personas are never used for tuning: {', '.join(bad)}")


@dataclass(frozen=True)
class MixPersona:
    persona: Persona
    buffer_cents: int  # the persona's own safety buffer for the backtest
    archetype: str
    held_out: bool
    start: date = START  # the first day of the history (LONG_START for the 24-month set)


@dataclass(frozen=True)
class StudentSpec:
    """The fixed, recorded assignments of one student persona (DECISIONS.md)."""

    tuition_dollars: tuple[int, int]  # per semester; (0, 0) is CEGEP
    from_chequing: bool  # False: the family pays the university; no tuition rows, no deposits
    installments: int = 1  # tuition paid in this many parts
    summer: bool = False  # a summer term as well as Fall and Winter
    away: bool = False  # away in July, August and December
    rent: tuple[int, int] | None = None  # None: no rent
    aid: str | None = None  # "loans" or "loans+bursary" (DRENT only)


PERSONA_SPECS: dict[str, StudentSpec] = {
    "v2-IFAM-01": StudentSpec((5_000, 6_500), True, rent=(700, 1400)),
    "v2-IFAM-02": StudentSpec((7_000, 12_000), True, installments=2, summer=True, rent=(700, 1400)),
    "v2-IFAM-03": StudentSpec((14_500, 21_500), False, rent=(700, 1400)),
    "v2-IFAM-04": StudentSpec((22_000, 30_000), True, installments=2, rent=(700, 1400)),
    "v2-ITIGHT-01": StudentSpec((15_000, 30_000), True, rent=(500, 800)),
    "v2-ITIGHT-02": StudentSpec((15_000, 30_000), False, rent=(500, 800)),
    "v2-ITIGHT-03": StudentSpec((15_000, 30_000), True, summer=True, rent=(500, 800)),
    "v2-ITIGHT-04": StudentSpec((15_000, 30_000), True, summer=True, rent=(500, 800)),
    "v2-ITIGHT-05": StudentSpec((15_000, 30_000), True, rent=(500, 800)),
    "v2-DHOME-01": StudentSpec((0, 0), False),
    "v2-DHOME-02": StudentSpec((1_500, 3_100), True, summer=True, rent=(150, 400)),
    "v2-DHOME-03": StudentSpec((1_500, 3_100), False, away=True),
    "v2-DHOME-04": StudentSpec((0, 0), False, rent=(150, 400)),
    "v2-DRENT-01": StudentSpec(
        (1_500, 2_500), True, summer=True, rent=(450, 900), aid="loans+bursary"
    ),
    "v2-DRENT-02": StudentSpec((1_500, 2_500), False, away=True, rent=(450, 900)),
    "v2-DRENT-03": StudentSpec((1_500, 2_500), True, rent=(450, 900), aid="loans"),
    "v2-DRENT-04": StudentSpec((4_800, 7_500), True, summer=True, rent=(450, 900)),
    "v2-DRENT-05": StudentSpec(
        (4_800, 7_500), False, away=True, rent=(450, 900), aid="loans+bursary"
    ),
}

# Per archetype: bills (recurring series, not rent, not subscriptions), subscriptions (count and
# total monthly cost, drawn separately), purchases a day, wage, hours a week, opening balance.
STUDENT_SHAPE = {
    "IFAM": dict(
        coverage=(1.05, 1.3),
        bills=(4, 6),
        subs=(2, 5),
        sub_cost=(20, 80),
        ppd=(0.9, 1.4),
        opening=(2000, 8000),
    ),
    "ITIGHT": dict(
        coverage=(0.9, 1.05),
        bills=(3, 5),
        subs=(1, 3),
        sub_cost=(10, 40),
        ppd=(0.8, 1.2),
        opening=(300, 2000),
    ),
    "DHOME": dict(
        coverage=(1.0, 1.3),
        bills=(2, 4),
        subs=(3, 7),
        sub_cost=(30, 110),
        ppd=(1.3, 1.9),
        opening=(300, 1500),
    ),
    "DRENT": dict(
        coverage=(0.95, 1.15),
        bills=(3, 5),
        subs=(2, 5),
        sub_cost=(20, 80),
        ppd=(1.0, 1.5),
        opening=(300, 2000),
    ),
}

# Calendar of the window (Oct 2025 to Sep 2026).
BREAKS = ((date(2025, 12, 20), date(2026, 1, 4)), (date(2026, 5, 1), date(2026, 8, 31)))
SUMMER_TERM_BREAKS = BREAKS[:1]  # a summer-term persona has no May to August break
EXAMS = ((date(2025, 12, 6), date(2025, 12, 20)), (date(2026, 4, 10), date(2026, 4, 30)))
AWAY = ((date(2025, 12, 20), date(2026, 1, 4)), (date(2026, 7, 1), date(2026, 8, 31)))
WINTER_START, SUMMER_START, FALL_START = date(2026, 1, 6), date(2026, 5, 5), date(2026, 9, 3)


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
_FAMILY = ("Nadia Haddad", "Karim Haddad", "Layla Nasser", "Omar Khoury", "Wei Zhang", "Ana Costa")
_BIG = (
    ("Concordia Bookstore", "Education", False),
    ("uBreakiFix", "Shopping", False),
    ("Ticketmaster", "Entertainment", True),
    ("Best Buy", "Shopping", False),
)
_SUBSCRIPTIONS = (
    "Netflix", "Spotify", "Disney Plus", "Apple iCloud", "YouTube Premium", "Crave",
    "Amazon Prime", "Notion",
)  # fmt: skip
LOAN_SENDER = "BMO Bank of Montreal"  # the bank that handles Quebec student-loan payments


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


def _student_bills(pid: str, rng: np.random.Generator, count: tuple[int, int],
                   archetype: str) -> list[Source]:  # fmt: skip
    """Recurring bill series: phone always, then others drawn from a pool. Not rent, and not
    subscriptions (those are drawn separately)."""
    pool = [
        ("internet", "Bell Internet", "Phone, internet & utilities", "bill payment", (45, 80), 0),
        ("transit", "STM", "Transport", "pos purchase", (55, 95), 0),
        ("healthins", "Sunlife Student Health", "Health & pharmacy", "bill payment", (60, 110), 0),
        ("tenantins", "Intact Insurance", "Rent & housing", "bill payment", (12, 30), 0),
        ("hydro", "Hydro-Québec", "Phone, internet & utilities", "bill payment", (50, 120), 3_000),
    ]
    if archetype == "DHOME":
        pool = pool[:3]  # living with parents: no tenant insurance, no hydro
    n = int(rng.integers(count[0], count[1] + 1))
    out: list[Source] = []
    day = lambda: int(rng.integers(5, 26))  # noqa: E731
    if rng.random() < 0.5:
        out.append(_monthly(f"{pid}-fizz", "Fizz", "Phone, internet & utilities", "pos purchase",
                            day(), _dollars(rng, 25, 55, 5), "card"))  # fmt: skip
    else:
        out.append(_monthly(f"{pid}-phone", "Vidéotron", "Phone, internet & utilities",
                            "bill payment", day(), _dollars(rng, 60, 110, 5), "later"))  # fmt: skip
    chosen = rng.permutation(len(pool))[: max(0, n - 1)]
    for k in sorted(int(c) for c in chosen):
        name, merchant, category, txn, (low, high), spread = pool[k]
        out.append(_monthly(f"{pid}-{name}", merchant, category, txn, day(),
                            _dollars(rng, low, high, 5),
                            "card" if txn == "pos purchase" else "later",
                            spread))  # fmt: skip
    return out


def _subscriptions(pid: str, rng: np.random.Generator, count: tuple[int, int],
                   total_dollars: tuple[int, int]) -> list[Source]:  # fmt: skip
    """Subscriptions, drawn apart from the bills: how many, and what they cost in all each month,
    are two separate draws; the total is then split across the chosen services."""
    n = int(rng.integers(count[0], count[1] + 1))
    total_cents = int(rng.integers(total_dollars[0], total_dollars[1] + 1)) * 100
    names = [_SUBSCRIPTIONS[int(k)] for k in rng.permutation(len(_SUBSCRIPTIONS))[:n]]
    weights = rng.uniform(0.5, 1.5, size=n) if n else np.array([])
    shares = weights / weights.sum() if n else weights
    out: list[Source] = []
    for name, share in zip(names, shares, strict=True):
        cents = max(299, round(total_cents * float(share)))
        out.append(_monthly(f"{pid}-sub-{name.lower().replace(' ', '')}", name, "Subscriptions",
                            "pos purchase", int(rng.integers(3, 28)), cents, "card"))  # fmt: skip
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


@dataclass(frozen=True)
class HourlyPay:
    """Biweekly wages from hours: `term_hours` a week, `break_hours` inside the break windows,
    nothing inside the `off` windows, scaled by `dip_factor` inside the `dips` (exam periods).
    Each pay varies by up to +-`spread` (hours vary)."""

    series_id: str
    merchant: str
    anchor: date
    net_cents_per_hour: int
    term_hours: float
    break_hours: float
    breaks: tuple[tuple[date, date], ...] = ()
    off: tuple[tuple[date, date], ...] = ()
    dips: tuple[tuple[date, date], ...] = ()
    dip_factor: float = 1.0
    spread: float = 0.2

    @staticmethod
    def _inside(day: date, windows: tuple[tuple[date, date], ...]) -> bool:
        return any(a <= day <= b for a, b in windows)

    def hours_on(self, due: date) -> float:
        """The hours a week behind the pay due on `due` (0: not paid)."""
        if self._inside(due, self.off):
            return 0.0
        hours = self.break_hours if self._inside(due, self.breaks) else self.term_hours
        return hours * (self.dip_factor if self._inside(due, self.dips) else 1.0)

    def generate(self, start: date, end: date, rng: np.random.Generator) -> list[GeneratedTxn]:
        out = []
        due = self.anchor
        while due <= end + timedelta(days=7):
            hours = self.hours_on(due)
            posted = _post(due, "earlier")
            if hours > 0 and start <= posted <= end:
                cents = round(self.net_cents_per_hour * hours * 2
                              * float(rng.uniform(1 - self.spread, 1 + self.spread)))  # fmt: skip
                out.append(GeneratedTxn(
                    posted_date=posted, amount_cents=cents, txn_type="deposit",
                    merchant=self.merchant, category="Income", flow_type="income",
                    series_id=self.series_id,
                ))  # fmt: skip
            due += timedelta(days=14)
        return out


DAYS_PER_MONTH = 30.44
_PER_MONTH = {"weekly": 52 / 12, "biweekly": 26 / 12, "semimonthly": 2.0, "monthly": 1.0}


def expected_monthly_spending(profile: SpendingProfile) -> int:
    """What the profile spends in an average month, in cents, before any cutting back: purchases a
    day (times the average weekday factor), the average purchase (log-normal, so a long tail of
    larger ones raises the mean above the median), and the cash withdrawals."""
    weights = [m.weight for m in profile.merchants]

    def cad(m) -> float:  # type: ignore[no-untyped-def]
        rate = 1.0 if m.currency == "CAD" else sum(CURRENCY_RATES[m.currency]) / 2
        return m.median_cents * math.exp(profile.amount_sigma**2 / 2) * rate

    mean_purchase = sum(w * cad(m) for w, m in zip(weights, profile.merchants, strict=True)) / sum(
        weights
    )
    per_day = profile.purchases_per_day * sum(profile.weekday_factors) / 7
    cash = (
        profile.cash_per_month * sum(profile.cash_amounts_cents) / len(profile.cash_amounts_cents)
    )
    return round(per_day * DAYS_PER_MONTH * mean_purchase + cash)


@dataclass(frozen=True)
class Coverage:
    """Regular monthly income against monthly costs, in cents, in a regular term month."""

    income_cents: int
    costs_cents: int

    @property
    def ratio(self) -> float:
        return self.income_cents / self.costs_cents


def _monthly_costs(sources: list[Source], spending: SpendingProfile | None) -> float:
    """Rent, bills and subscriptions (recurring outflows that are not transfers to oneself) plus
    expected everyday spending. One-off purchases and tuition are not in the costs."""
    costs = 0.0
    for s in sources:
        if (
            isinstance(s, RecurringSchedule)
            and s.amount.typical_cents < 0
            and s.category != "Transfers"
        ):
            costs += -s.amount.typical_cents * _PER_MONTH[s.cadence]
    return costs + (expected_monthly_spending(spending) if spending is not None else 0)


def _aid_regular_monthly(sources: list[Source]) -> float:
    """Aid that counts as regular income: all of it, except the part that equals the tuition it
    funds (tuition and its funding stay outside the coverage ratio)."""
    tuition = aid = 0
    for s in sources:
        if isinstance(s, DatedSeries):
            if s.series_id.endswith("-tuition"):
                tuition += -sum(i.amount_cents for i in s.items)
            elif s.series_id.endswith("-aid"):
                aid += sum(i.amount_cents for i in s.items)
    return max(0, aid - tuition) / 12


def coverage(persona: Persona) -> Coverage:
    """The persona's coverage ratio, recomputed from its sources: regular income (allowance, family
    transfers, wages, pay, the aid that is not tuition funding) over costs (rent, bills,
    subscriptions, expected everyday spending). Tuition and the deposits that fund it are
    outside."""
    income = 0.0
    for s in persona.sources:
        if isinstance(s, RecurringSchedule) and s.amount.typical_cents > 0:
            income += s.amount.typical_cents * _PER_MONTH[s.cadence]
        elif isinstance(s, HourlyPay):
            income += s.net_cents_per_hour * s.term_hours * 2 * 26 / 12
        elif isinstance(s, IrregularSeries) and s.category == "Income":
            low, high = s.amount_range_cents
            income += (low + high) / 2 * DAYS_PER_MONTH / ((s.gap_days[0] + s.gap_days[1]) / 2)
        elif (
            isinstance(s, DatedSeries)
            and s.category == "Income"
            and not s.series_id.endswith(("-aid", "-tuition-funding", "-tuition-topup"))
        ):
            income += sum(i.amount_cents for i in s.items) / 12
    income += _aid_regular_monthly(list(persona.sources))
    return Coverage(round(income), round(_monthly_costs(list(persona.sources), persona.spending)))


def split_income(target: float, wage: float, family: float, wage_cap: float) -> tuple[float, float]:
    """Split a target monthly income between wages and family transfers in proportion to the drawn
    wage and family amounts, but wages never above `wage_cap` (the 24 hours a week cap): what the
    wages cannot cover is made up by the family."""
    wages = min(target * wage / (wage + family), wage_cap)
    return wages, target - wages


def _net_per_hour(rng: np.random.Generator, wage: tuple[int, int]) -> int:
    return round(float(rng.uniform(wage[0], wage[1])) * 100 * NET_SHARE)


def _monthly_per_hour(net_per_hour: int) -> float:
    """What one weekly hour is worth in a month: two weeks per pay, 26 pays a year."""
    return net_per_hour * 2 * 26 / 12


def _hourly(
    pid: str, rng: np.random.Generator, net_per_hour: int, term_hours: float,
    break_hours: tuple[int, int] | None, spec: StudentSpec, dips: bool,
) -> HourlyPay:  # fmt: skip
    breaks = (SUMMER_TERM_BREAKS if spec.summer else BREAKS) if break_hours else ()
    return HourlyPay(
        series_id=f"{pid}-pay",
        merchant=_EMPLOYERS[int(rng.integers(len(_EMPLOYERS)))],
        anchor=date(2025, 10, 3) + timedelta(days=int(rng.integers(0, 5))),
        net_cents_per_hour=net_per_hour,
        term_hours=round(term_hours, 2),
        break_hours=(
            int(rng.integers(break_hours[0], break_hours[1] + 1)) if break_hours else term_hours
        ),
        breaks=breaks,
        off=AWAY if spec.away else (),
        dips=EXAMS if dips else (),
        dip_factor=round(float(rng.uniform(0.5, 0.7)), 2) if dips else 1.0,
    )  # fmt: skip


# --- Tuition, family money and aid ---


def _tuition_dates(rng: np.random.Generator, summer: bool) -> list[date]:
    """When tuition falls due: Winter (Jan), Summer (May, summer persona only), Fall (Sep)."""
    dates = [date(2026, 1, 15) + timedelta(days=int(rng.integers(0, 11)))]
    if summer:
        dates.append(date(2026, 5, 15) + timedelta(days=int(rng.integers(0, 11))))
    dates.append(date(2026, 9, 7) + timedelta(days=int(rng.integers(0, 11))))
    return dates


def _tuition_sources(
    pid: str, rng: np.random.Generator, spec: StudentSpec, archetype: str
) -> list[Source]:
    """Tuition payments from chequing and the family money that funds them. Nothing at all when
    the tuition is not paid from chequing (CEGEP, or the family pays the university)."""
    low, high = spec.tuition_dollars
    if not spec.from_chequing or high == 0:
        return []
    tuition = _dollars(rng, low, high, 50)
    dates = _tuition_dates(rng, spec.summer)
    payments: list[DatedItem] = []
    deposits: list[DatedItem] = []
    topups: list[DatedItem] = []
    funded = spec.aid is None  # aid-funded personas are funded by their aid
    for n, due in enumerate(dates):
        link = f"{pid}-tuition-{n}"
        if spec.installments == 2:
            first = tuition // 2
            payments += [DatedItem(due, -first, link),
                         DatedItem(due + timedelta(days=int(rng.integers(30, 46))),
                                   -(tuition - first), link)]  # fmt: skip
        else:
            payments.append(DatedItem(due, -tuition, link))
        if funded:
            share = {
                "IFAM": float(rng.uniform(1.00, 1.40)),  # 100-140 percent of the tuition
                "ITIGHT": float(rng.uniform(0.60, 1.05)),  # often partial
            }.get(archetype, float(rng.uniform(0.80, 1.10)))  # a family contribution
            arrives = due - timedelta(days=int(rng.integers(1, 31)))  # 1-30 days before
            amount = round(tuition * share / 5_000) * 5_000
            deposits.append(DatedItem(arrives, amount, link))
            if (
                archetype == "ITIGHT"
            ):  # about 70 percent of terms: a second transfer, part of the gap
                comes, later, part = (
                    rng.random() < 0.7,
                    int(rng.integers(15, 46)),
                    rng.uniform(0.5, 1.0),
                )
                gap = tuition - amount
                if comes and gap >= 5_000:
                    cents = min(gap, max(5_000, int(gap * part / 5_000) * 5_000))
                    topups.append(DatedItem(due + timedelta(days=later), cents, link))
    family = _FAMILY[int(rng.integers(len(_FAMILY)))]
    sources: list[Source] = [
        DatedSeries(
            series_id=f"{pid}-tuition",
            merchant="Concordia University",
            category="Education",
            flow_type="recurring_outflow",
            txn_type="bill payment",
            shift="later",
            items=tuple(payments),
        )
    ]
    if deposits:
        sources.append(DatedSeries(
            series_id=f"{pid}-tuition-funding", merchant=family, category="Income",
            flow_type="income", txn_type="credit memo", shift="later", items=tuple(deposits),
        ))  # fmt: skip
    if topups:
        sources.append(DatedSeries(
            series_id=f"{pid}-tuition-topup", merchant=family, category="Income",
            flow_type="income", txn_type="credit memo", shift="later", items=tuple(topups),
        ))  # fmt: skip
    return sources


def _aid_sources(pid: str, rng: np.random.Generator, spec: StudentSpec) -> list[Source]:
    """Quebec loans and bursaries, paid on set dates. Loans only: one payment at the start of each
    semester. Loans plus bursaries: a larger payment at the start of the semester, then smaller
    monthly payments for the rest of the term (the monthly part is likely, not fully confirmed)."""
    if spec.aid is None:
        return []
    starts = [WINTER_START] + ([SUMMER_START] if spec.summer else []) + [FALL_START]
    items: list[DatedItem] = []
    if spec.aid == "loans":
        items = [DatedItem(d, _dollars(rng, 2500, 4500, 50)) for d in starts]
    else:
        items = [DatedItem(d, _dollars(rng, 3500, 6000, 50)) for d in starts]
        monthly = [  # the rest of each term, on the 5th
            date(2025, 10, 5),
            date(2025, 11, 5),
            date(2025, 12, 5),  # Fall 2025
            date(2026, 2, 5),
            date(2026, 3, 5),
            date(2026, 4, 5),  # Winter 2026
        ] + ([date(2026, 6, 5), date(2026, 7, 5), date(2026, 8, 5)] if spec.summer else [])
        items += [DatedItem(d, _dollars(rng, 300, 900, 50)) for d in monthly]
    return [DatedSeries(
        series_id=f"{pid}-aid", merchant=LOAN_SENDER, category="Income", flow_type="income",
        txn_type="deposit", shift="later", items=tuple(sorted(items, key=lambda i: i.due)),
    )]  # fmt: skip


def _student(pid: str, rng: np.random.Generator, archetype: str) -> Persona:
    """Costs first, then regular income scaled to the coverage ratio drawn for the persona."""
    spec = PERSONA_SPECS[pid]
    shape = STUDENT_SHAPE[archetype]
    spending = _spending(rng, _SPENDING_A, *shape["ppd"])
    fixed: list[Source] = []
    if spec.rent is not None:
        fixed.append(_rent(pid, rng, _dollars(rng, *spec.rent, 25)))
    fixed += _student_bills(pid, rng, shape["bills"], archetype)
    fixed += _subscriptions(pid, rng, shape["subs"], shape["sub_cost"])
    extras = (
        _one_offs(rng) + _tuition_sources(pid, rng, spec, archetype) + _aid_sources(pid, rng, spec)
    )
    costs = _monthly_costs(fixed, spending)
    target = float(rng.uniform(*shape["coverage"])) * costs  # the regular income to reach
    aid = _aid_regular_monthly(extras)
    income: list[Source] = []
    if archetype == "IFAM":
        family = _FAMILY[int(rng.integers(len(_FAMILY)))]
        income.append(RecurringSchedule(  # the steady monthly allowance
            series_id=f"{pid}-allowance", merchant=family, category="Income", flow_type="income",
            txn_type="credit memo", cadence="monthly",
            anchor=date(2025, 10, int(rng.integers(1, 6))),
            amount=Amount(round(target / 1000) * 1000), shift="later",
        ))  # fmt: skip
    elif archetype == "ITIGHT":
        net = _net_per_hour(rng, (16, 17))
        per_hour = _monthly_per_hour(net)
        drawn = int(rng.integers(10, TERM_HOURS_CAP + 1))  # hours a week the persona would work
        wages, family = split_income(target, drawn * per_hour, 115_000, TERM_HOURS_CAP * per_hour)
        income.append(_hourly(pid, rng, net, wages / per_hour, (25, 40), spec, False))
        mean = family * 30 / DAYS_PER_MONTH  # a transfer every 15-45 days, 30 on average
        low = max(5_000, round(0.4 * mean / 5_000) * 5_000)
        high = max(low + 5_000, round(1.6 * mean / 5_000) * 5_000)
        income.append(IrregularSeries(  # irregular family transfers
            series_id=f"{pid}-family", merchant=_FAMILY[int(rng.integers(len(_FAMILY)))],
            category="Income", flow_type="income", txn_type="credit memo", gap_days=(15, 45),
            amount_range_cents=(low, high), round_to_cents=5_000,
        ))  # fmt: skip
    elif archetype == "DHOME":
        net = _net_per_hour(rng, (16, 20))
        income.append(_hourly(pid, rng, net, target / _monthly_per_hour(net), None, spec, False))
    else:  # DRENT: wages make up what the aid does not
        net = _net_per_hour(rng, (16, 21))
        wages = max(target - aid, 0.25 * target)
        income.append(_hourly(pid, rng, net, wages / _monthly_per_hour(net), None, spec, True))
    return Persona(
        persona_id=pid, description=ARCHETYPES[archetype].description,
        sources=tuple(income + fixed + extras),
        opening_balance_cents=_dollars(rng, *shape["opening"], 50), spending=spending,
        quiet_months=frozenset({7, 8, 12}) if spec.away else frozenset(),
        quiet_activity=0.15 if spec.away else 1.0,
    )  # fmt: skip


# --- Non-student archetypes (the ranges recorded in the superseded entry) ---


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


FREE_COVERAGE = (0.95, 1.15)


def _freelancer(pid: str, rng: np.random.Generator) -> Persona:
    """Costs first, then the deposits scaled so the coverage ratio is in FREE_COVERAGE. The shape
    of the old range is kept: the lowest deposit about a quarter of the average, the highest about
    1.75 times it."""
    spending = _spending(rng, _SPENDING_B, 1.0, 1.4)
    fixed: list[Source] = [_rent(pid, rng, _dollars(rng, 700, 1200, 25))]
    fixed += _bills(pid, rng, savings=False)
    extras = _one_offs(rng)
    target = float(rng.uniform(*FREE_COVERAGE)) * _monthly_costs(fixed, spending)
    mean = target * 12.5 / DAYS_PER_MONTH  # a deposit every 5-20 days, 12.5 on average
    low = max(5_000, round(0.235 * mean / 5_000) * 5_000)
    high = max(low + 5_000, round((2 * mean - low) / 5_000) * 5_000)
    gigs = IrregularSeries(
        series_id=f"{pid}-gigs", merchant="Interac e-Transfer", category="Income",
        flow_type="income", txn_type="deposit", gap_days=(5, 20),
        amount_range_cents=(low, high), round_to_cents=5_000,
    )  # fmt: skip
    return Persona(
        persona_id=pid, description="freelancer", sources=tuple([gigs] + fixed + extras),
        opening_balance_cents=_dollars(rng, 500, 2000, 50), spending=spending,
    )  # fmt: skip


def build_persona(persona_id: str) -> MixPersona:
    """The persona with this id. The same id always gives the same persona. A `v2L-` id is the
    24-month persona: the parameters of its `v2-` namesake, lengthened by one year (`_lengthen`)."""
    parts = persona_id.split("-")
    if persona_id not in PERSONA_IDS and persona_id not in LONG_IDS:
        # "v2-SAL-1" or "v2-SAL-001" would seed a different, unrecorded persona
        raise ValueError(f"unknown persona id {persona_id!r}")
    if (
        len(parts) != 3
        or parts[0] not in (MIX, LONG_MIX)
        or parts[1] not in ARCHETYPES
        or not parts[2].isdigit()
    ):
        raise ValueError(f"unknown persona id {persona_id!r}")
    if parts[0] == LONG_MIX:
        base_id = f"{MIX}-{parts[1]}-{parts[2]}"
        base = build_persona(base_id)
        earlier = np.random.default_rng(
            np.random.SeedSequence([PARAM_SEED, zlib.crc32(persona_id.encode())])
        )
        persona = _lengthen(base.persona, base_id, persona_id, earlier)
        return MixPersona(persona, base.buffer_cents, base.archetype, persona_id in ALL_HELD_OUT,
                          LONG_START)  # fmt: skip
    archetype = ARCHETYPES[parts[1]]
    if not 1 <= int(parts[2]) <= archetype.n:
        raise ValueError(f"unknown persona id {persona_id!r}")
    rng = np.random.default_rng(
        np.random.SeedSequence([PARAM_SEED, zlib.crc32(persona_id.encode())])
    )
    code = archetype.code
    if code in STUDENT_SHAPE:
        persona = _student(persona_id, rng, code)
    elif code == "SAL":
        persona = _salaried(persona_id, rng, "biweekly", (1500, 2600), (900, 1500), (1500, 4000))
    elif code == "SEM":
        persona = _salaried(persona_id, rng, "semimonthly", (1300, 2200), (800, 1400), (1200, 3500))
    elif code == "TIGHT":
        persona = _tight(persona_id, rng)
    else:
        persona = _freelancer(persona_id, rng)
    return MixPersona(persona, archetype.buffer_cents, code, persona_id in HELD_OUT)


# --- The 24-month set: the same rules one year earlier (DECISIONS.md, 2026-10-11, step A) ---

YEAR_WEEKS = timedelta(weeks=52)  # weekly and biweekly schedules keep their weekday and phase


def _year_back(day: date) -> date:
    """The same calendar day one year earlier (no recorded date is a February 29)."""
    return day.replace(year=day.year - 1)


def _windows_back(windows: tuple[tuple[date, date], ...]) -> tuple[tuple[date, date], ...]:
    """The windows repeated one year earlier, followed by the windows themselves."""
    return tuple((_year_back(a), _year_back(b)) for a, b in windows) + windows


def _rename(text: str | None, base_id: str, long_id: str) -> str | None:
    return None if text is None else text.replace(base_id, long_id, 1)


def _earlier_tuition(
    sources: list[Source], base_id: str, rng: np.random.Generator, spec: StudentSpec,
    archetype: str,
) -> dict[str, list[DatedItem]]:  # fmt: skip
    """The tuition, family deposits and second transfers of the year before (Winter 2025,
    Summer 2025 for a summer persona, Fall 2025), by the same rules as `_tuition_sources`: the
    same tuition amount (no yearly rise), dates drawn the same way one year earlier, the same
    funding rules. Keyed by the suffix of the series they belong to."""
    by_id = {s.series_id: s for s in sources if isinstance(s, DatedSeries)}
    tuition_series = by_id.get(f"{base_id}-tuition")
    if tuition_series is None:
        return {}
    link0 = f"{base_id}-tuition-0"
    tuition = -sum(i.amount_cents for i in tuition_series.items if i.link_id == link0)
    dates = [_year_back(d) for d in _tuition_dates(rng, spec.summer)]
    out: dict[str, list[DatedItem]] = {"tuition": [], "funding": [], "topup": []}
    funded = spec.aid is None
    for n, due in enumerate(dates):
        link = f"{base_id}-tuition-p{n}"  # "p": a term of the previous year
        if spec.installments == 2:
            first = tuition // 2
            out["tuition"] += [DatedItem(due, -first, link),
                               DatedItem(due + timedelta(days=int(rng.integers(30, 46))),
                                         -(tuition - first), link)]  # fmt: skip
        else:
            out["tuition"].append(DatedItem(due, -tuition, link))
        if funded:
            share = {
                "IFAM": float(rng.uniform(1.00, 1.40)),
                "ITIGHT": float(rng.uniform(0.60, 1.05)),
            }.get(archetype, float(rng.uniform(0.80, 1.10)))
            arrives = due - timedelta(days=int(rng.integers(1, 31)))
            amount = round(tuition * share / 5_000) * 5_000
            out["funding"].append(DatedItem(arrives, amount, link))
            if archetype == "ITIGHT":
                comes, later, part = (
                    rng.random() < 0.7,
                    int(rng.integers(15, 46)),
                    rng.uniform(0.5, 1.0),
                )
                gap = tuition - amount
                if comes and gap >= 5_000:
                    cents = min(gap, max(5_000, int(gap * part / 5_000) * 5_000))
                    out["topup"].append(DatedItem(due + timedelta(days=later), cents, link))
    return out


def _earlier_aid(rng: np.random.Generator, spec: StudentSpec) -> list[DatedItem]:
    """The aid of the year before, by the same rules as `_aid_sources`, one year earlier."""
    if spec.aid is None:
        return []
    starts = [WINTER_START] + ([SUMMER_START] if spec.summer else []) + [FALL_START]
    starts = [_year_back(d) for d in starts]
    if spec.aid == "loans":
        return [DatedItem(d, _dollars(rng, 2500, 4500, 50)) for d in starts]
    items = [DatedItem(d, _dollars(rng, 3500, 6000, 50)) for d in starts]
    monthly = [date(2025, 10, 5), date(2025, 11, 5), date(2025, 12, 5), date(2026, 2, 5),
               date(2026, 3, 5), date(2026, 4, 5)]  # fmt: skip
    if spec.summer:
        monthly += [date(2026, 6, 5), date(2026, 7, 5), date(2026, 8, 5)]
    items += [DatedItem(_year_back(d), _dollars(rng, 300, 900, 50)) for d in monthly]
    return items


def _lengthen(
    base: Persona, base_id: str, long_id: str, rng: np.random.Generator
) -> Persona:  # fmt: skip
    """The 24-month persona: the base persona's sources with the same rules applied to the year
    before. Recurring schedules start 12 months earlier (weekly and biweekly ones 52 weeks
    earlier); wage breaks, exam dips and months away repeat one year earlier; the earlier year's
    tuition, family money and aid are added (`rng` only: the base draws are untouched); 1 to 3
    more larger one-off purchases fall in the earlier year. Series ids carry the long id."""
    spec = PERSONA_SPECS.get(base_id)
    archetype = base_id.split("-")[1]
    earlier = _earlier_tuition(list(base.sources), base_id, rng, spec, archetype) if spec else {}
    aid = _earlier_aid(rng, spec) if spec else []
    sources: list[Source] = []
    for s in base.sources:
        if isinstance(s, RecurringSchedule):
            back = (
                s.anchor - YEAR_WEEKS
                if s.cadence in ("weekly", "biweekly")
                else _year_back(s.anchor)
            )
            s = replace(s, anchor=back)
        elif isinstance(s, HourlyPay):
            s = replace(s, anchor=s.anchor - YEAR_WEEKS, breaks=_windows_back(s.breaks),
                        off=_windows_back(s.off), dips=_windows_back(s.dips))  # fmt: skip
        elif isinstance(s, DatedSeries):
            suffix = s.series_id.removeprefix(base_id + "-")
            extra = {
                "tuition": earlier.get("tuition", []), "aid": aid,
                "tuition-funding": earlier.get("funding", []),
                "tuition-topup": earlier.get("topup", []),
            }.get(suffix, [])  # fmt: skip
            s = replace(s, items=tuple(extra) + s.items)
        if hasattr(s, "series_id"):
            s = replace(s, series_id=_rename(s.series_id, base_id, long_id))  # type: ignore[type-var]
        if isinstance(s, DatedSeries):
            s = replace(s, items=tuple(replace(i, link_id=_rename(i.link_id, base_id, long_id))
                                       for i in s.items))  # fmt: skip
        sources.append(s)
    present = {s.series_id for s in sources if isinstance(s, DatedSeries)}
    topup_id = f"{long_id}-tuition-topup"
    if earlier.get("topup") and topup_id not in present:  # the base year had no second transfer
        funding = next(s for s in sources if isinstance(s, DatedSeries)
                       and s.series_id == f"{long_id}-tuition-funding")  # fmt: skip
        sources.append(DatedSeries(
            series_id=topup_id, merchant=funding.merchant, category="Income", flow_type="income",
            txn_type="credit memo", shift="later",
            items=tuple(replace(i, link_id=_rename(i.link_id, base_id, long_id))
                        for i in earlier["topup"]),
        ))  # fmt: skip
    earlier_one_offs = [replace(o, due=_year_back(o.due)) for o in _one_offs(rng)]
    return replace(base, persona_id=long_id, sources=tuple(sources + earlier_one_offs))


def main(argv: list[str] | None = None) -> int:
    """Write the exports and answer keys of the chosen personas (default: the 20 development
    personas), one at a time. Held-out personas are refused: only eval/held_out.py uses them."""
    from lookahead.generator.writer import export_rows, money, write_persona

    parser = argparse.ArgumentParser(prog="python -m lookahead.generator.archetypes",
                                     description=__doc__)  # fmt: skip
    parser.add_argument("--out", type=Path, default=Path("data/synthetic_v2"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args(argv)
    ids = args.only or development_ids()
    assert_development_only(ids)  # before anything is generated
    for pid in ids:
        p = build_persona(pid)
        export, key = write_persona(p.persona, args.out, args.seed, START, END)
        rows = export_rows(p.persona, START, END, args.seed)
        balance, low = p.persona.opening_balance_cents, p.persona.opening_balance_cents
        for t in rows:
            balance += t.amount_cents
            low = min(low, balance)
        kind = "held-out" if p.held_out else "dev"
        print(f"{pid:14} {kind:8} {len(rows):4} rows  lowest {money(low):>9}  "
              f"closing {money(balance):>9}  ({export.name}, {key.name})")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
