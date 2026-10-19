"""A stable identity for a recurring series (week 5, item 1; DECISIONS.md, 2026-10-12).

A detected series has a *key*, and a card-cluster key ends in the median amount of its
purchases, so it moves. The key stays as it is (the forecast draws its random numbers from it).
My decisions are stored against an *id* that does not move:

- the id is the family of the key (the key without its trailing amount) plus the fingerprint
  of the series' oldest payment. Payments are only ever added at the end, so the oldest one,
  and with it the id, stays the same when the median amount drifts or new payments arrive;
- after a price change a habit can show up as a new cluster. The new cluster takes over from an
  older one of the same family and cadence that stopped appearing: it starts after the older
  one's last payment, within two periods of it. It never takes over from a series I ended or
  rejected (a resubscription starts new).

This file only holds the rules, on plain values; `recurring_store` uses them.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date

PREFIX = "sid|"
PERIOD_DAYS = {"weekly": 7, "biweekly": 14, "semimonthly": 15, "monthly": 31, "yearly": 366}
MAX_GAP_PERIODS = 2


@dataclass(frozen=True)
class Habit:
    """What the identity rules need to know about one detected series."""

    key: str
    first_fingerprint: str
    first_day: date
    last_day: date
    cadence: str


def family_of(key: str) -> str:
    """The key without a trailing amount (`merchant|out|<name>|<amount>` -> the first three
    parts). A key without an amount is its own family."""
    parts = key.split("|")
    base = 2 if parts[0] == "own transfer" else 3
    if len(parts) > base and parts[-1].lstrip("-").isdigit():
        return "|".join(parts[:-1])
    return key


def is_id(ref: str) -> bool:
    return ref.startswith(PREFIX)


def make_id(family: str, first_fingerprint: str) -> str:
    return f"{PREFIX}{family}|@{first_fingerprint}"


def assign_ids(habits: Sequence[Habit]) -> dict[str, str]:
    """key -> id for every habit. Two habits that would get the same id (the same family and the
    same oldest payment) cannot both have it: the later key keeps its own key as its id."""
    out: dict[str, str] = {}
    taken: set[str] = set()
    for h in sorted(habits, key=lambda h: h.key):
        wanted = make_id(family_of(h.key), h.first_fingerprint)
        out[h.key] = h.key if wanted in taken else wanted
        taken.add(out[h.key])
    return out


def successors(habits: Sequence[Habit], blocked: Collection[str] = ()) -> dict[str, str]:
    """older key -> newer key, for habits that carry on one another. `blocked` holds the keys of
    series I ended or rejected: they are never carried on. One to one, nearest gap first."""
    by_family: dict[str, list[Habit]] = {}
    for h in habits:
        by_family.setdefault(family_of(h.key), []).append(h)
    out: dict[str, str] = {}
    for family in by_family.values():
        pairs = [
            ((new.first_day - old.last_day).days, old.key, new.key)
            for old in family
            if old.key not in blocked and old.cadence in PERIOD_DAYS
            for new in family
            if new.key != old.key and new.cadence == old.cadence
            and 0 < (new.first_day - old.last_day).days
            <= MAX_GAP_PERIODS * PERIOD_DAYS[old.cadence]
        ]  # fmt: skip
        taken: set[str] = set()
        for _, old_key, new_key in sorted(pairs):  # nearest pair first, then one to one
            if old_key not in out and new_key not in taken:
                out[old_key] = new_key
                taken.add(new_key)
    return out


def head_of(key: str, chain: dict[str, str]) -> str:
    """The newest habit of the chain that `key` belongs to (itself if nothing follows it)."""
    seen = {key}
    while key in chain and chain[key] not in seen:
        key = chain[key]
        seen.add(key)
    return key
