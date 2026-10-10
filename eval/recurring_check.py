"""Why recurring placement misses and adds payments: counts and shares only.

Method in DECISIONS.md (2026-10-12, "Why recurring placement misses and adds payments on my
account"). At each weekly origin of the real backtest the placed recurring payments of the next 30
days are matched with the real payments of those days, with the rule `eval.diagnose_real` uses
(one to one within a series, nearest day first, within 10 days), and the misses and extras are
broken down by what kind of series they come from. The database is opened read-only. Nothing is
written, and no name, date, amount or row is printed.

    python -m eval.recurring_check                 # your database (data/private)
    python -m eval.recurring_check --db some.db --limit 10     # a quick look
"""

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from eval.backtest import HORIZON, METHOD
from eval.backtest_real import real_origins
from eval.diagnose_real import MATCH_DAYS, day_index, match_recurring
from lookahead.db import connect_readonly
from lookahead.flows import Txn, flow_types
from lookahead.forecast.path import DEFAULT_OPTIONS, ForecastOptions, build_forecast
from lookahead.forecast.recurring_filter import RecurringFix
from lookahead.recurring_store import Stored, series_as_of
from lookahead.resolve import load_user_decisions
from lookahead.ui import private_db_path
from lookahead.views import accounts

PERIOD_DAYS = {"weekly": 7, "biweekly": 14, "semimonthly": 15, "monthly": 30, "yearly": 365}
_ETRANSFER_REASONS = {"outgoing e-Transfer", "incoming e-Transfer"}


def family_of(key: str) -> str:
    """The key without a trailing amount: a card cluster's key ends in the median amount of its
    purchases (`merchant|out|<name>|<amount>`), which moves as purchases arrive; its family does
    not. A key without an amount is its own family."""
    parts = key.split("|")
    base = 2 if parts[0] == "own transfer" else 3
    if len(parts) > base and parts[-1].lstrip("-").isdigit():
        return "|".join(parts[:-1])
    return key


def status_name(s: Stored | None) -> str:
    """detected / confirmed / added by me (a missing series: not in the list)."""
    if s is None:
        return "not in the list"
    return {"detected": "detected", "confirmed": "confirmed", "user-added": "added by me",
            "rejected": "rejected"}.get(s.status, s.status)  # fmt: skip


def day_bucket(days: int | None) -> str:
    if days is None:
        return "no payment found"
    return "under 45 days" if days < 45 else "45 to 90 days" if days <= 90 else "over 90 days"


def period_bucket(days: int | None, cadence: str) -> str:
    if days is None:
        return "no payment found"
    periods = days / PERIOD_DAYS.get(cadence, 30)
    return "up to 1.5 periods" if periods <= 1.5 else "1.5 to 3 periods" if periods <= 3 else (
        "more than 3 periods")  # fmt: skip


def placement_kind(note: str) -> str:
    if note.startswith("rent"):
        return "rent window, overdue" if "not seen yet" in note else "rent window"
    if "not seen yet" in note:
        return "due and not seen yet: expected on the first day"
    return "from its pattern"


def payment_kind(s: Stored, reasons: Sequence[str]) -> str:
    """What a series' payments are: the majority reason of its payments."""
    if s.kind == "e-transfer":
        return "e-Transfer"
    if not reasons:
        return "no payment found"
    top = Counter(reasons).most_common(1)[0][0]
    if top == "card purchase":
        return "card purchase"
    if top == "bill payment":
        return "bill payment"
    return "e-Transfer" if top in _ETRANSFER_REASONS else "other"


def attributes(
    s: Stored, today: Mapping[str, Stored], last_paid: date | None, origin: date,
    reasons: Sequence[str],
) -> dict[str, str]:  # fmt: skip
    """What a series is, as the breakdowns group it (nothing here is a name or a date)."""
    days = None if last_paid is None else (origin - last_paid).days
    now = today.get(s.key)
    return {
        "status at the origin": status_name(s),
        "status today": "not in today's list" if now is None else status_name(now),
        "active or ended today": "not in today's list" if now is None
        else "active" if now.active else "ended",  # fmt: skip
        "cadence": s.cadence,
        "amount type": s.amount_type,
        "direction": "money in" if s.direction == "in" else "money out",
        "rent rule": "rent rule" if s.allowed_cents else "no rent rule",
        "days since its last real payment": day_bucket(days),
        "the same in periods of its cadence": period_bucket(days, s.cadence),
        "what its payments are": payment_kind(s, reasons),
    }  # fmt: skip


def origin_rows(
    conn: Any, account: str, origin: date, today: Mapping[str, Stored],
    options: ForecastOptions = DEFAULT_OPTIONS,
) -> dict[str, Any]:  # fmt: skip
    """Placed and extra payments of one origin, each with its group attributes."""
    end = origin + timedelta(days=HORIZON)
    forecast = build_forecast(conn, account, METHOD, origin, None, HORIZON, 0, options=options,
                              keeps=None)  # fmt: skip
    if forecast is None:
        raise ValueError("no forecast for this origin")
    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ? AND posted_date <= ?", (account, end.isoformat()),
    ).fetchall()  # fmt: skip
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    day_of = {r[5]: date.fromisoformat(r[1]) for r in raw}
    row_of = {r[5]: r[0] for r in raw}
    flows = flow_types(txns, load_user_decisions(conn, origin))
    at_origin = {s.key: s for s in forecast.series if s.status != "rejected"}
    after, _ = series_as_of(conn, account, end, None)
    at_end = {s.key: s for s in after if s.status != "rejected"}
    key_of = {row_of[fp]: s.key for s in after if s.status != "rejected" for fp in s.evidence
              if fp in row_of}  # fmt: skip

    def last_paid(s: Stored) -> date | None:
        days = [day_of[fp] for fp in s.evidence if fp in day_of and day_of[fp] <= origin]
        return max(days) if days else None

    def reasons(s: Stored, until: date = origin) -> list[str]:
        return [flows[row_of[fp]].reason for fp in s.evidence
                if fp in row_of and day_of[fp] <= until]  # fmt: skip

    placed, notes = [], []
    for item in forecast.items:
        k = day_index(item.day, origin)
        if 1 <= k <= HORIZON and item.series_key in at_origin:
            placed.append((k, item.cents, item.series_key))
            notes.append(item.note)
    actual = [
        (day_index(t.posted_date, origin), t.amount_cents, key_of[t.row_id])
        for t in txns if t.posted_date > origin and t.row_id in key_of
    ]  # fmt: skip
    pairs, missed, extra = match_recurring(placed, actual)
    # which placed payment is which: `match_recurring` returns (day, cents) pairs, so the same
    # greedy one-to-one outcome is rebuilt over the full tuples
    outcome = _outcomes(placed, actual)
    # the same matching with a series' family instead of its key (the key of a card cluster
    # moves with the median amount of its purchases)
    outcome_f = _outcomes(
        [(k, c, family_of(key)) for k, c, key in placed],
        [(k, c, family_of(key)) for k, c, key in actual],
    )  # fmt: skip
    extra_f = set(outcome_f["extra"])
    families_at_end: dict[str, set[str]] = {}
    for key in at_end:
        families_at_end.setdefault(family_of(key), set()).add(key)
    changed = sum(
        1 for key in at_origin
        if key not in at_end and any(k != key for k in families_at_end.get(family_of(key), ()))
    )  # fmt: skip
    rows_placed = []
    for idx, (_, _, key) in enumerate(placed):
        s = at_origin[key]
        attrs = attributes(s, today, last_paid(s), origin, reasons(s))
        attrs["how it was placed"] = placement_kind(notes[idx])
        res = outcome["placed"][idx]
        rows_placed.append({
            "attrs": attrs, "result": res["result"], "elsewhere": res["elsewhere"],
            "result_family": outcome_f["placed"][idx]["result"],
        })  # fmt: skip
    rows_extra = []
    placed_keys = {key for _, _, key in placed}
    for idx in outcome["extra"]:
        k, cents, key = actual[idx]
        s_end = at_end[key]
        known = at_origin.get(key)
        # the series as it stands 30 days later (a label); how long since its last payment is
        # measured at the origin, and what its payments are over all of them
        attrs = attributes(s_end, today, last_paid(s_end), origin, reasons(s_end, end))
        before = sum(1 for fp in s_end.evidence if fp in day_of and day_of[fp] <= origin)
        attrs["payments of it before the origin"] = (
            "none" if before == 0 else "1" if before == 1 else "2" if before == 2 else "3 or more"
        )
        if known is None:
            where = "not detected at the origin"
        elif key in placed_keys:
            where = "detected and placed, but more than 10 days from the real payment"
        else:
            where = "detected, but not placed in the window"
        rows_extra.append({"attrs": attrs, "where": where, "extra_by_family": idx in extra_f})
    assert len(pairs) + len(missed) == len(placed) and len(extra) == len(rows_extra)
    return {
        "in_shortfall": forecast.start.balance_cents < forecast.buffer_cents,
        "placed": rows_placed, "extra": rows_extra,
        "series_known": len(at_origin), "keys_changed": changed,
    }  # fmt: skip


def _outcomes(placed: list[tuple[int, int, str]], actual: list[tuple[int, int, str]]
              ) -> dict[str, Any]:  # fmt: skip
    """The same greedy one-to-one matching as `match_recurring`, keeping which payment is which:
    for every placed payment 'matched' or 'missed' (and, if missed, whether its series had a real
    payment in the window left unmatched), and the indexes of the extra real payments."""
    candidates = sorted(
        (abs(p[0] - a[0]), i, j)
        for i, p in enumerate(placed)
        for j, a in enumerate(actual)
        if p[2] == a[2] and abs(p[0] - a[0]) <= MATCH_DAYS
    )  # fmt: skip
    used_p: set[int] = set()
    used_a: set[int] = set()
    for _, i, j in candidates:
        if i in used_p or j in used_a:
            continue
        used_p.add(i)
        used_a.add(j)
    left_keys = {actual[j][2] for j in range(len(actual)) if j not in used_a}
    out_placed = []
    for i, p in enumerate(placed):
        if i in used_p:
            out_placed.append({"result": "matched", "elsewhere": False})
        else:
            out_placed.append({"result": "missed", "elsewhere": p[2] in left_keys})
    return {"placed": out_placed, "extra": [j for j in range(len(actual)) if j not in used_a]}


# --- Summaries ---


def _pct(x: float) -> str:
    return f"{x:.0%}"


def summary_lines(rows: list[dict[str, Any]], title: str) -> list[str]:
    placed = [p for r in rows for p in r["placed"]]
    extra = [e for r in rows for e in r["extra"]]
    n_miss = sum(p["result"] == "missed" for p in placed)
    lines = [
        f"  {title}: {len(rows)} origins; {len(placed)} placed payments, "
        f"{len(placed) - n_miss} matched, {n_miss} missed; {len(extra)} extra real payments"
    ]
    if not placed:
        return lines
    elsewhere = sum(p["result"] == "missed" and p["elsewhere"] for p in placed)
    lines.append(f"    of the {n_miss} missed: the same series paid in the window more than "
                 f"{MATCH_DAYS} days off: {elsewhere}; the series did not pay in the window: "
                 f"{n_miss - elsewhere}")  # fmt: skip
    groups: dict[str, list[str]] = {}
    for p in placed:
        for name in p["attrs"]:
            groups.setdefault(name, [])
    for name in groups:
        lines.append(f"    placed payments by {name} (placed / matched / missed / share missed):")
        values = Counter(p["attrs"][name] for p in placed)
        for value in sorted(values):
            mine = [p for p in placed if p["attrs"][name] == value]
            miss = sum(p["result"] == "missed" for p in mine)
            lines.append(f"      {value}: {len(mine)} / {len(mine) - miss} / {miss} / "
                         f"{_pct(miss / len(mine))}")  # fmt: skip
    if extra:
        by_where = Counter(e["where"] for e in extra)
        lines.append("    extra real payments, by whether the series was known at the origin:")
        for value in sorted(by_where):
            lines.append(f"      {value}: {by_where[value]} ({_pct(by_where[value] / len(extra))})")
        for name in (
            "status at the origin",
            "cadence",
            "amount type",
            "what its payments are",
            "days since its last real payment",
            "the same in periods of its cadence",
            "payments of it before the origin",
        ):
            shown = {
                "status at the origin": "status 30 days after the origin",
                "days since its last real payment": "days from its last payment before the origin",
            }.get(name, name)
            lines.append(f"    extra real payments by {shown}:")
            values = Counter(e["attrs"][name] for e in extra)
            for value in sorted(values):
                lines.append(f"      {value}: {values[value]} ({_pct(values[value] / len(extra))})")
    return lines


def family_lines(rows: list[dict[str, Any]], title: str) -> list[str]:
    """The same placed and real payments matched by key and by family (DECISIONS.md, 2026-10-12)."""
    placed = [p for r in rows for p in r["placed"]]
    extra = [e for r in rows for e in r["extra"]]
    lines = [f"  {title}, matched by key and by family (the key without its trailing amount):"]
    groups = (
        ("all series", lambda p: True),
        ("weekly series", lambda p: p["attrs"]["cadence"] == "weekly"),
        ("weekly series of card purchases", lambda p: p["attrs"]["cadence"] == "weekly"
         and p["attrs"]["what its payments are"] == "card purchase"),
    )  # fmt: skip
    for name, keep in groups:
        mine = [p for p in placed if keep(p)]
        if not mine:
            lines.append(f"    {name}: no placed payments")
            continue
        by_key = sum(p["result"] == "matched" for p in mine)
        by_family = sum(p["result_family"] == "matched" for p in mine)
        lines.append(
            f"    {name}: {len(mine)} placed; matched by key {by_key} "
            f"({_pct(by_key / len(mine))}), by family {by_family} "
            f"({_pct(by_family / len(mine))})"
        )
    missed = [p for p in placed if p["result"] == "missed"]
    saved = sum(p["result_family"] == "matched" for p in missed)
    lines.append(f"    of the {len(missed)} payments missed by key, {saved} are matched by family "
                 f"(the same series under another key)")  # fmt: skip
    stay = sum(1 for e in extra if e["extra_by_family"])
    lines.append(
        f"    of the {len(extra)} extra real payments by key, {len(extra) - stay} are matched by "
        f"family (a placed payment of the same series under another key); {stay} stay extra"
    )
    weekly_card = [e for e in extra if e["attrs"]["cadence"] == "weekly"
                   and e["attrs"]["what its payments are"] == "card purchase"]  # fmt: skip
    if weekly_card:
        left = sum(1 for e in weekly_card if e["extra_by_family"])
        lines.append(f"    of the {len(weekly_card)} extra weekly card payments, {left} stay extra "
                     f"when matched by family")  # fmt: skip
    known = sum(r["series_known"] for r in rows)
    changed = sum(r["keys_changed"] for r in rows)
    lines.append(f"    series known at an origin: {known} (counted once per origin); of them "
                 f"{changed} have, 30 days later, the same family under another key")  # fmt: skip
    return lines


def collect(conn: Any, account: str, days: Sequence[date],
            progress: Callable[[int], None] | None = None,
            options: ForecastOptions = DEFAULT_OPTIONS) -> list[dict[str, Any]]:  # fmt: skip
    today_series, _ = series_as_of(conn, account, None, None)
    today = {s.key: s for s in today_series}
    rows = []
    for n, origin in enumerate(days, start=1):
        rows.append(origin_rows(conn, account, origin, today, options))
        if progress is not None:
            progress(n)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.recurring_check", description=__doc__)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None, help="only every n-th origin")
    parser.add_argument(
        "--by-family",
        action="store_true",
        help="also match by series family: the key without its trailing amount",
    )
    parser.add_argument("--recurring-fix", choices=("off", "b"), default="off",
                        help="the forecast with (b) possibly ended series not placed")  # fmt: skip
    args = parser.parse_args(argv)
    options = ForecastOptions(recurring_fix=RecurringFix() if args.recurring_fix == "b" else None)
    db_path = args.db or private_db_path()
    if not db_path.exists():
        print("No database found. Import your exports first.")
        return 1
    conn = connect_readonly(db_path)
    try:
        for number, account in enumerate(accounts(conn), start=1):
            span = conn.execute(
                "SELECT MIN(posted_date), MAX(posted_date) FROM transactions WHERE account = ?",
                (account,),
            ).fetchone()  # fmt: skip
            if span[0] is None:
                continue
            days = real_origins(date.fromisoformat(span[0]), date.fromisoformat(span[1]))
            if args.limit:
                days = days[:: args.limit]
            print(f"account-{number}: {len(days)} origins", flush=True)
            rows = collect(
                conn,
                account,
                days,
                lambda n: print(f"  ... {n}", flush=True) if n % 20 == 0 else None,
                options,
            )
            ordinary = [r for r in rows if not r["in_shortfall"]]
            if args.by_family:
                for line in family_lines(rows, "All origins"):
                    print(line)
                for line in family_lines(ordinary, "Origins not already below the buffer"):
                    print(line)
            for line in summary_lines(rows, "All origins"):
                print(line)
            for line in summary_lines(ordinary, "Origins not already below the buffer"):
                print(line)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
