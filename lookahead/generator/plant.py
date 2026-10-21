"""The planter: copies of the development personas with planted recurring-setting events.

The spec is DECISIONS.md, 2026-10-20 ("Looks right" proposals, written before this code). Any
change to an event, a rate, a range or the seed is a variant and is counted in the report.

    python -m lookahead.generator.plant --set both --out data/backtest/planted

The existing persona sets are not touched: this reads the generator's rows and writes *copies*
(a bank export and an answer key per persona) into its own folder. Held-out personas are refused.

Events, each decided independently per persona (one random stream per (seed, persona id, event)).
Rates are those of the amendment of 2026-10-20 (planter version 2; the first spec had 0.6, 0.5, 0.5,
0.5 and P1 in month 10 or 11):
- R1 late rent (rent paid by e-Transfer): with probability 0.9, from month 4 on, each rent payment
  is sent late with probability 0.4, by 2 to 8 days, with the usual business-day shift;
- R2 rent increase (the same personas): with probability 0.8, in a month drawn from 5 to 9, rent
  rises by 5 to 12 percent, rounded to $5, for all later payments;
- L1 renamed subscription (personas with subscriptions): with probability 1.0 one is picked; in a
  month drawn from 4 to 9 its text changes to another made-up name; amount and cadence unchanged.
  (One rename per persona, and only 24 of the 40 personas have a subscription that qualifies, so
  24 is the most this kind can have; 1.0 is the rate that reaches it);
- P1 new subscription: with probability 0.8, a made-up name, whole dollars from $5 to $30, monthly,
  first charge in month 8 (so it has exactly two charges at origins in June and July).
Added in planter version 3 (2026-10-21), prompted by my real-account veto run:
- N1 small e-Transfer noise (personas that pay rent by e-Transfer): with probability 0.8, from the
  month of the first rent payment on, 2 to 5 small unnamed e-Transfers a month ($5 to $60, whole
  dollars, any day 1 to 28), so the rent is one cluster among many small payments to "no one";
- P2 new subscription in a foreign currency: with probability 0.5, a made-up name, a USD price of
  whole dollars from $5 to $20, monthly, first charge in month 8; the CAD amount follows the rate
  (1.30 to 1.45), and the second charge differs from the first by 1 to 4 percent, as a currency
  conversion does; later charges stay within 4 percent of the first.
Noise: none. **Months are counted from 1 October 2025 in both sets** (the 24-month personas have a
year of history before that: "the same rules one year earlier"), so month 1 is October 2025 and
month 12 is September 2026 whichever set a persona belongs to.
"""

import argparse
import json
import sys
import time
import zlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from lookahead.calendar_rules import shift_to_business_day
from lookahead.generator.archetypes import (
    END,
    assert_development_only,
    build_persona,
    development_ids,
)
from lookahead.generator.schedules import GeneratedTxn
from lookahead.generator.writer import export_rows, write_export

SEED = 2026
PLANTER_VERSION = 3  # 2: the amendment of 2026-10-20; 3: noise and currency pairs (2026-10-21)
FIRST_MONTH = date(2025, 10, 1)  # month 1, in both sets

R1_PERSONA, R1_PAYMENT = 0.9, 0.4
R1_FROM_MONTH, R1_DAYS = 4, (2, 8)
R2_PERSONA, R2_MONTHS, R2_PERCENT, R2_ROUND_CENTS = 0.8, (5, 9), (5, 12), 500
L1_PERSONA, L1_MONTHS = 1.0, (4, 9)
P1_PERSONA, P1_MONTHS, P1_DOLLARS = 0.8, (8, 8), (5, 30)
# Added on 2026-10-21 (planter version 3), prompted by my real-account veto run:
N1_PERSONA, N1_PER_MONTH, N1_DOLLARS = 0.8, (2, 6), (5, 60)  # small unnamed e-Transfers
P2_PERSONA, P2_USD, P2_RATE, P2_SWING = 0.5, (5, 20), (1.30, 1.45), (0.01, 0.04)
NAMES = (
    "Zenstudio", "Quillbox", "Vantaplay", "Mirabeam", "Orbitly", "Pixelnest", "Lumora",
    "Tidewave", "Brightloom", "Kestrelhub",
)  # fmt: skip


def month_index(day: date) -> int:
    """1 for October 2025 ... 12 for September 2026 (and below 1 before that)."""
    return (day.year - FIRST_MONTH.year) * 12 + day.month - FIRST_MONTH.month + 1


@dataclass(frozen=True)
class PlantedEvent:
    """One planted event: the answer key. `observable_from` is the date from which a proposer can
    be expected to see it (rent: the third payment at the new setting; link: the second payment
    under the new name; possible subscription: its second charge)."""

    event_id: str
    persona_id: str
    kind: str  # rent / link / possible
    what: str  # late / increase / rename / new
    observable_from: date
    details: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["observable_from"] = self.observable_from.isoformat()
        return out


def _streams(persona_id: str, seed: int) -> dict[str, np.random.Generator]:
    # Streams 1 to 5 are those of version 2: spawning more children does not change them.
    children = np.random.SeedSequence([seed, zlib.crc32(persona_id.encode())]).spawn(7)
    names = ("r1", "r2", "l1", "p1", "names", "n1", "p2")
    return {n: np.random.default_rng(c) for n, c in zip(names, children, strict=True)}


def _rent_rows(rows: Sequence[GeneratedTxn]) -> list[int]:
    return [
        i for i, t in enumerate(rows)
        if t.series_id and t.series_id.endswith("-rent") and t.txn_type == "withdrawal"
    ]  # fmt: skip


def _third(dates: Sequence[date], start: date) -> date:
    after = sorted(d for d in dates if d >= start)
    return after[min(2, len(after) - 1)]


def _plant_rent_increase(
    rows: list[GeneratedTxn], idx: list[int], persona_id: str, rng: np.random.Generator
) -> list[PlantedEvent]:
    chosen = rng.random() < R2_PERSONA
    month = int(rng.integers(R2_MONTHS[0], R2_MONTHS[1] + 1))
    percent = int(rng.integers(R2_PERCENT[0], R2_PERCENT[1] + 1))
    if not chosen or not idx:
        return []
    old = abs(rows[idx[0]].amount_cents)
    new = int(round(old * (1 + percent / 100) / R2_ROUND_CENTS)) * R2_ROUND_CENTS
    new = max(new, old + R2_ROUND_CENTS)
    later = [i for i in idx if month_index(rows[i].posted_date) >= month]
    if not later:
        return []
    for i in later:
        rows[i] = replace(rows[i], amount_cents=-new)
    paid = [rows[i].posted_date for i in later]
    return [PlantedEvent(
        f"{persona_id}:rent-increase", persona_id, "rent", "increase", _third(paid, paid[0]),
        {"old_cents": old, "new_cents": new, "percent": percent, "month": month,
         "first_new_payment": paid[0].isoformat(), "sizes_cents": [old, new]},
    )]  # fmt: skip


def _plant_late_rent(
    rows: list[GeneratedTxn], idx: list[int], persona_id: str, rng: np.random.Generator
) -> list[PlantedEvent]:
    chosen = rng.random() < R1_PERSONA
    draws = []
    for _ in idx:  # both numbers are drawn for every rent payment, so the stream never shifts
        late = bool(rng.random() < R1_PAYMENT)
        draws.append((late, int(rng.integers(R1_DAYS[0], R1_DAYS[1] + 1))))
    if not chosen:
        return []
    late_dates: list[date] = []
    for i, (late, delay) in zip(idx, draws, strict=True):
        t = rows[i]
        if month_index(t.posted_date) < R1_FROM_MONTH or not late:
            continue
        moved = shift_to_business_day(t.posted_date + timedelta(days=delay), "later")
        if moved > END:
            continue
        rows[i] = replace(t, posted_date=moved)
        late_dates.append(moved)
    if not late_dates:
        return []
    sent = [rows[i].posted_date for i in idx]
    from_month = [d for d in sent if month_index(d) >= R1_FROM_MONTH]
    first_late = min(late_dates)
    return [PlantedEvent(
        f"{persona_id}:rent-late", persona_id, "rent", "late", _third(from_month, first_late),
        {"late_payments": len(late_dates), "first_late": first_late.isoformat(),
         "send_days": sorted(d.day for d in from_month), "from_month": R1_FROM_MONTH},
    )]  # fmt: skip


def _subscription_series(rows: Sequence[GeneratedTxn]) -> list[str]:
    counts = Counter(
        t.series_id for t in rows
        if t.series_id and "-sub-" in t.series_id and t.category == "Subscriptions"
        and month_index(t.posted_date) >= L1_MONTHS[0]
    )  # fmt: skip
    return sorted(s for s, n in counts.items() if n >= 6)


def _plant_rename(
    rows: list[GeneratedTxn], persona_id: str, rng: np.random.Generator, names: list[str]
) -> list[PlantedEvent]:
    chosen = rng.random() < L1_PERSONA
    candidates = _subscription_series(rows)
    pick = int(rng.integers(0, max(len(candidates), 1)))
    month = int(rng.integers(L1_MONTHS[0], L1_MONTHS[1] + 1))
    if not chosen or not candidates:
        return []
    series_id = candidates[pick]
    new_name = names.pop(0)
    mine = [i for i, t in enumerate(rows) if t.series_id == series_id]
    old_name = rows[mine[0]].merchant
    renamed = [i for i in mine if month_index(rows[i].posted_date) >= month]
    if len(renamed) < 2:
        return []
    for i in renamed:
        city = rows[i].sub_description.rstrip().split(" ")[-1]
        rows[i] = replace(rows[i], merchant=new_name, sub_description=f"Opos {new_name:<22}{city} ")
    paid = [rows[i].posted_date for i in renamed]
    last_old = max(rows[i].posted_date for i in mine if i not in renamed)
    return [PlantedEvent(
        f"{persona_id}:subscription-rename", persona_id, "link", "rename", sorted(paid)[1],
        {"series_id": series_id, "old_name": old_name, "new_name": new_name, "month": month,
         "last_old_payment": last_old.isoformat(), "first_new_payment": min(paid).isoformat()},
    )]  # fmt: skip


def _plant_new_subscription(
    rows: list[GeneratedTxn], persona_id: str, rng: np.random.Generator, names: list[str]
) -> list[PlantedEvent]:
    chosen = rng.random() < P1_PERSONA
    first = int(rng.integers(P1_MONTHS[0], P1_MONTHS[1] + 1))
    dollars = int(rng.integers(P1_DOLLARS[0], P1_DOLLARS[1] + 1))
    day = int(rng.integers(1, 29))
    if not chosen:
        return []
    name = names.pop(0)
    dates = []
    for m in range(first, 13):
        year, month = (FIRST_MONTH.year + (FIRST_MONTH.month - 1 + m - 1) // 12,
                       (FIRST_MONTH.month - 1 + m - 1) % 12 + 1)  # fmt: skip
        posted = shift_to_business_day(date(year, month, day), "later")
        if posted <= END:
            dates.append(posted)
    if len(dates) < 2:
        return []
    for posted in dates:
        rows.append(GeneratedTxn(
            posted_date=posted, amount_cents=-100 * dollars, txn_type="pos purchase",
            merchant=name, category="Subscriptions", flow_type="recurring_outflow",
            series_id=f"{persona_id}-sub-planted", online=True, description="pos purchase",
            sub_description=f"Opos {name:<22}Montr ",
        ))  # fmt: skip
    return [PlantedEvent(
        f"{persona_id}:subscription-new", persona_id, "possible", "new", dates[1],
        {"name": name, "amount_cents": 100 * dollars, "first_month": first,
         "charge_dates": [d.isoformat() for d in dates]},
    )]  # fmt: skip


def _plant_small_etransfers(
    rows: list[GeneratedTxn], idx: list[int], persona_id: str, rng: np.random.Generator
) -> list[PlantedEvent]:
    """N1: small unnamed e-Transfers among which the rent sits. Noise, not a scored event."""
    chosen = rng.random() < N1_PERSONA
    if not chosen or not idx:
        return []
    first = min(rows[i].posted_date for i in idx)
    count, month = 0, date(first.year, first.month, 1)
    while month <= END:
        for _ in range(int(rng.integers(N1_PER_MONTH[0], N1_PER_MONTH[1]))):
            dollars = int(rng.integers(N1_DOLLARS[0], N1_DOLLARS[1] + 1))
            day = int(rng.integers(1, 29))
            posted = shift_to_business_day(date(month.year, month.month, day), "later")
            if posted > END or posted < first:
                continue
            rows.append(GeneratedTxn(
                posted_date=posted, amount_cents=-100 * dollars, txn_type="withdrawal",
                merchant="Interac e-Transfer", category="Transfers", flow_type="transfer",
                series_id=f"{persona_id}-noise-etransfer", description="withdrawal",
                sub_description="Free Interac E-Transfer ",
            ))  # fmt: skip
            count += 1
        month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    if not count:
        return []
    return [PlantedEvent(f"{persona_id}:etransfer-noise", persona_id, "noise", "small-etransfers",
                         first, {"count": count})]  # fmt: skip


def _plant_new_fx_subscription(
    rows: list[GeneratedTxn], persona_id: str, rng: np.random.Generator, names: list[str]
) -> list[PlantedEvent]:
    """P2: a subscription billed in USD; the CAD amount moves 1 to 4 percent with the rate."""
    chosen = rng.random() < P2_PERSONA
    usd = 100 * int(rng.integers(P2_USD[0], P2_USD[1] + 1))
    base = float(rng.uniform(P2_RATE[0], P2_RATE[1]))
    swings = [float(rng.uniform(P2_SWING[0], P2_SWING[1])) * (1 if rng.random() < 0.5 else -1)
              for _ in range(6)]  # fmt: skip
    day = int(rng.integers(1, 29))
    if not chosen or not names:
        return []
    name = names.pop(0)
    dates, amounts = [], []
    for k, m in enumerate(range(P1_MONTHS[0], 13)):
        year, month = (FIRST_MONTH.year + (FIRST_MONTH.month - 1 + m - 1) // 12,
                       (FIRST_MONTH.month - 1 + m - 1) % 12 + 1)  # fmt: skip
        posted = shift_to_business_day(date(year, month, day), "later")
        if posted <= END:
            dates.append(posted)
            amounts.append(round(usd * base * (1 + (swings[k] if k else 0.0))))
    if len(dates) < 2:
        return []
    for posted, cents in zip(dates, amounts, strict=True):
        rows.append(GeneratedTxn(
            posted_date=posted, amount_cents=-cents, txn_type="pos purchase", merchant=name,
            category="Subscriptions", flow_type="recurring_outflow",
            series_id=f"{persona_id}-sub-planted-fx", online=True, currency="USD",
            original_amount_cents=-usd, description="pos purchase",
            sub_description=f"Opos {name:<22}Montr ",
        ))  # fmt: skip
    a, b = amounts[0], amounts[1]
    return [PlantedEvent(
        f"{persona_id}:subscription-new-fx", persona_id, "possible", "new-fx", dates[1],
        {"name": name, "usd_cents": usd, "charge_dates": [d.isoformat() for d in dates],
         "amounts_cents": amounts, "first_pair_difference": round(abs(a - b) / max(a, b), 4)},
    )]  # fmt: skip


def plant(
    persona_id: str, rows: Sequence[GeneratedTxn], seed: int = SEED
) -> tuple[list[GeneratedTxn], list[PlantedEvent]]:
    """A planted copy of a development persona's rows (oldest first), and its answer key. The
    input rows are not changed. Held-out personas are refused."""
    assert_development_only([persona_id])
    streams = _streams(persona_id, seed)
    out = list(rows)
    idx = _rent_rows(out)
    events = _plant_rent_increase(out, idx, persona_id, streams["r2"])
    events += _plant_late_rent(out, idx, persona_id, streams["r1"])
    taken = {t.merchant.casefold() for t in out}
    shuffled = streams["names"].permutation(NAMES)
    names = [str(n) for n in shuffled if str(n).casefold() not in taken]
    events += _plant_rename(out, persona_id, streams["l1"], names)
    events += _plant_new_subscription(out, persona_id, streams["p1"], names)
    events += _plant_new_fx_subscription(out, persona_id, streams["p2"], names)
    events += _plant_small_etransfers(out, idx, persona_id, streams["n1"])
    out.sort(key=lambda t: t.posted_date)  # stable: rows of one day keep their order
    return out, sorted(events, key=lambda e: e.event_id)


def write_planted(
    persona_id: str, folder: Path, seed: int = SEED, export_seed: int = 42
) -> list[PlantedEvent]:
    """Write `folder/<persona_id>/export.csv` (the bank file, as the backtest builds it) and
    `answer_key.json`. Returns the events."""
    assert_development_only([persona_id])
    persona = build_persona(persona_id)
    rows = export_rows(persona.persona, persona.start, END, export_seed)
    planted, events = plant(persona_id, rows, seed)
    target = folder / persona_id
    target.mkdir(parents=True, exist_ok=True)
    write_export(planted, persona.persona.opening_balance_cents, target / "export.csv")
    key = {
        "persona_id": persona_id, "seed": seed, "export_seed": export_seed,
        "planter_version": PLANTER_VERSION, "events": [e.to_json() for e in events],
    }  # fmt: skip
    (target / "answer_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    return events


def read_answer_key(folder: Path, persona_id: str) -> list[PlantedEvent]:
    assert_development_only([persona_id])
    data = json.loads((folder / persona_id / "answer_key.json").read_text("utf-8"))
    return [
        PlantedEvent(e["event_id"], e["persona_id"], e["kind"], e["what"],
                     date.fromisoformat(e["observable_from"]), e["details"])
        for e in data["events"]
    ]  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.generator.plant")
    parser.add_argument("--set", choices=["12", "long", "both"], default="both")
    parser.add_argument("--out", type=Path, default=Path("data/backtest/planted"))
    args = parser.parse_args(argv)
    sets = {"12": [False], "long": [True], "both": [False, True]}[args.set]
    start = time.time()
    counts: dict[str, Counter[str]] = {}
    for long in sets:
        name = "long" if long else "12"
        counts[name] = Counter()
        for pid in development_ids(long=long):
            for event in write_planted(pid, args.out / name):
                counts[name][event.kind] += 1
    for name, c in counts.items():
        shown = ", ".join(f"{k} {c[k]}" for k in ("rent", "link", "possible"))
        print(f"{name}-month set: planted events (n) {shown}")
    total = sum(counts.values(), Counter())
    print("both sets: " + ", ".join(f"{k} {total[k]}" for k in ("rent", "link", "possible")))
    print(f"done in {time.time() - start:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
