"""Flow typing: how each transaction affects the forecast (HANDOFF 05, DECISIONS.md).

Per-row rules from the bank's transaction type, the direction (money in or out) and, for
e-Transfers, the text. Then pairing, which only ever looks back in time (rule zero):
Same merchant = the same resolved merchant group from step 2 (rules + your merges/splits).
- pre-authorization correction (credit) -> the hold it reverses: same merchant, same amount,
  the hold posted on the same day or up to 7 days before;
- return -> its purchase: same merchant, purchase at least the refund, up to 90 days before;
- returned e-Transfer -> the e-Transfer it returns: same amount, up to 30 days before.
Each earlier row is used by at most one pair; the nearest one wins. A pair whose credit
equals the whole purchase is "netted" (both rows: no spending, no category).

Not decided here (Week 3, recurring rules): card subscriptions and e-Transfer rent start as
everyday / transfer and are upgraded to recurring_outflow by the recurring detector, which
should also downgrade one-off bill payments.

Counts-only check on a database:
    python -m lookahead.flows report --db data/private/db/lookahead.db
"""

import argparse
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from lookahead.merchants import extract_merchant
from lookahead.resolve import UserDecisions, load_user_decisions, name_infos, resolve

PREAUTH_DAYS = 7
RETURN_DAYS = 90
ETRANSFER_RETURN_DAYS = 30

_FEES = {"service charge", "overdraft interest", "overdrawn handling chg."}

# (transaction type, direction) -> (flow type, reason)
_RULES: dict[tuple[str, str], tuple[str, str]] = {
    ("credit memo", "in"): ("income", "credit memo"),
    ("deposit", "in"): ("income", "deposit"),
    ("withdrawal", "out"): ("transfer", "withdrawal"),
    ("customer transfer cr.", "in"): ("transfer", "own-account transfer"),
    ("customer transfer dr.", "out"): ("transfer", "own-account transfer"),
    ("bill payment", "out"): ("recurring_outflow", "bill payment"),
    ("bill payment", "in"): ("refund", "bill payment credit"),  # money back from a payee
    ("pos purchase", "out"): ("everyday", "card purchase"),
    ("abm withdrawal", "out"): ("everyday", "cash withdrawal"),
    ("pos return", "in"): ("refund", "card return"),
    ("correction", "in"): ("refund", "pre-auth correction"),
    ("accounts payable", "in"): ("refund", "accounts payable credit"),
    **{(fee, "out"): ("everyday", "bank fee") for fee in _FEES},
    **{(fee, "in"): ("refund", "fee reversal") for fee in _FEES},
}


@dataclass(frozen=True)
class Txn:
    row_id: int
    posted_date: date
    amount_cents: int
    txn_type: str  # the export's Description column
    text: str  # the export's Sub-description column


@dataclass(frozen=True)
class Flow:
    flow_type: str
    reason: str
    link: int | None = None  # row_id of the paired row
    link_kind: str | None = None  # "preauth", "return" or "etransfer_return"
    # A fully reversed pair (the credit equals the whole purchase): both rows net to zero,
    # so they are left out of spending totals, the forecaster's spending history and
    # categorization. Known only once the credit posts (rule zero).
    netted: bool = False


def _type(t: Txn) -> str:
    return " ".join(t.txn_type.split()).casefold()


def _is_etransfer(t: Txn) -> bool:
    return "e-transfer" in t.text.casefold()


def _is_returned_etransfer(t: Txn) -> bool:
    folded = " ".join(t.text.casefold().split())
    return "e-transfer" in folded and "rtn" in folded.split()


def _direction(t: Txn) -> str:
    return "in" if t.amount_cents > 0 else "out"  # a $0.00 row counts as out


def _base_flow(t: Txn) -> Flow:
    """The flow type from this row alone."""
    kind, direction = _type(t), _direction(t)
    if kind == "deposit" and direction == "in" and _is_returned_etransfer(t):
        return Flow("refund", "returned e-Transfer")
    if kind == "deposit" and direction == "in" and _is_etransfer(t):
        return Flow("income", "incoming e-Transfer")
    if kind == "withdrawal" and direction == "out" and _is_etransfer(t):
        return Flow("transfer", "outgoing e-Transfer")
    rule = _RULES.get((kind, direction))
    if rule is not None:
        return Flow(*rule)
    # Unknown: never guess income (it would be placed on the income schedule).
    return Flow("refund" if direction == "in" else "everyday", "no rule (fallback)")


def _days(earlier: Txn, later: Txn) -> int:
    return (later.posted_date - earlier.posted_date).days


def merchant_groups(
    txns: Sequence[Txn], user: UserDecisions | None = None
) -> dict[int, str | None]:
    """row_id -> resolved merchant group (step 2: rules plus your stored merges and splits).

    Built only from the rows given, so on data cut at a date no later name is used (rule
    zero). Rows without a merchant (blank, e-Transfers) map to None.
    """
    texts = [t.text for t in txns]
    result = resolve(name_infos(texts), user)
    groups: dict[int, str | None] = {}
    for t in txns:
        name = extract_merchant(t.text).merchant
        groups[t.row_id] = None if name is None else result.group_of(name)
    return groups


def flow_types(txns: Sequence[Txn], user: UserDecisions | None = None) -> dict[int, Flow]:
    """Flow type (and pairing link, if any) for every row, keyed by row_id.

    Corrections and returns pair within one resolved merchant group (`merchant_groups`).
    """
    ordered = sorted(txns, key=lambda t: (t.posted_date, t.row_id))
    flows = {t.row_id: _base_flow(t) for t in ordered}
    used: set[int] = set()

    def pair(refund: Txn, kind: str, candidates: list[Txn]) -> None:
        free = [c for c in candidates if c.row_id not in used]
        if not free:
            return
        # Exact amount first, then the nearest in time, then the latest row of that day.
        best = max(
            free,
            key=lambda c: (-c.amount_cents == refund.amount_cents, c.posted_date, c.row_id),
        )
        used.update((best.row_id, refund.row_id))
        r, b = flows[refund.row_id], flows[best.row_id]
        netted = -best.amount_cents == refund.amount_cents
        flows[refund.row_id] = Flow(r.flow_type, r.reason, best.row_id, kind, netted)
        flows[best.row_id] = Flow(b.flow_type, b.reason, refund.row_id, kind, netted)

    names = merchant_groups(ordered, user)  # same group = same merchant
    for t in ordered:
        reason = flows[t.row_id].reason
        if reason not in ("pre-auth correction", "card return", "returned e-Transfer"):
            continue
        # Same day or before, whatever the position within the day (no time of day).
        earlier = [c for c in ordered if c.posted_date <= t.posted_date and c.amount_cents < 0]
        if reason == "pre-auth correction":
            pair(t, "preauth", [
                c for c in earlier
                if _type(c) == "pos purchase" and -c.amount_cents == t.amount_cents
                and _days(c, t) <= PREAUTH_DAYS and names[c.row_id] == names[t.row_id]
            ])  # fmt: skip
        elif reason == "card return":
            pair(t, "return", [
                c for c in earlier
                if _type(c) == "pos purchase" and -c.amount_cents >= t.amount_cents
                and _days(c, t) <= RETURN_DAYS and names[c.row_id] == names[t.row_id]
            ])  # fmt: skip
        else:
            pair(t, "etransfer_return", [
                c for c in earlier
                if _type(c) == "withdrawal" and _is_etransfer(c)
                and -c.amount_cents == t.amount_cents and _days(c, t) <= ETRANSFER_RETURN_DAYS
            ])  # fmt: skip
    return flows


# --- Counts-only report ---


def _gap_bucket(days: int) -> str:
    if days <= 1:
        return str(days)
    return "2-3" if days <= 3 else "4-7"


_SMALL_DIFFERENCE = 5  # percent: "nearly the same amount" for a returned e-Transfer


def _why_return_unpaired(t: Txn, txns: Sequence[Txn], names: dict[int, str | None]) -> str:
    earlier = [
        c for c in txns
        if _type(c) == "pos purchase" and c.amount_cents < 0 and c.posted_date <= t.posted_date
    ]  # fmt: skip
    same = [c for c in earlier if names[c.row_id] == names[t.row_id]]
    if not same:
        if any(-c.amount_cents == t.amount_cents and _days(c, t) <= RETURN_DAYS for c in earlier):
            return "same amount, different merchant group"
        return "no earlier purchase from that merchant"
    recent = [c for c in same if _days(c, t) <= RETURN_DAYS]
    if not recent:
        return "nearest purchase over 90 days earlier"
    if not any(-c.amount_cents >= t.amount_cents for c in recent):
        return "purchases too small"
    return "matching purchases already paired"


def _why_etransfer_unpaired(t: Txn, txns: Sequence[Txn]) -> str:
    debits = [c for c in txns if c.amount_cents < 0 and c.posted_date <= t.posted_date]
    sent = [c for c in debits if _type(c) == "withdrawal" and _is_etransfer(c)]
    within = [c for c in sent if _days(c, t) <= ETRANSFER_RETURN_DAYS]
    if any(-c.amount_cents == t.amount_cents for c in within):
        return "matching e-Transfers already paired"
    if not within:
        if any(-c.amount_cents == t.amount_cents for c in sent):
            return "same amount, over 30 days earlier"
        if any(
            -c.amount_cents == t.amount_cents and _days(c, t) <= ETRANSFER_RETURN_DAYS
            for c in debits
        ):
            return "same amount within 30 days, not an e-Transfer withdrawal"
        return "no outgoing e-Transfer within 30 days"
    gap = min(abs(-c.amount_cents - t.amount_cents) for c in within)
    if gap * 100 <= _SMALL_DIFFERENCE * t.amount_cents:
        return "nearest amount within 30 days differs by 5% or less"
    return "nearest amount within 30 days differs by more than 5%"


@dataclass
class FlowCounts:
    rows: int
    by_flow: Counter[str]
    # (transaction type, direction) -> rows; types are the bank's fixed vocabulary
    no_rule: Counter[tuple[str, str]]
    fees: int
    bill_credits: int
    pairs: Counter[str]  # reason -> pairs found
    refunds: Counter[str]  # reason -> rows that look for a partner
    preauth_gaps: Counter[str]  # "0", "1", "2-3", "4-7" -> pairs
    unpaired: Counter[tuple[str, str]]  # (reason, why it found no partner) -> rows
    netted: int = 0  # rows in fully reversed pairs (both rows of each pair)

    @property
    def ok(self) -> bool:
        return not self.no_rule


def count_flows(txns: Sequence[Txn], user: UserDecisions | None = None) -> FlowCounts:
    flows = flow_types(txns, user)
    by_id = {t.row_id: t for t in txns}
    names = merchant_groups(txns, user)
    c = FlowCounts(
        len(txns), Counter(), Counter(), 0, 0, Counter(), Counter(), Counter(), Counter()
    )
    for t in txns:
        f = flows[t.row_id]
        c.by_flow[f.flow_type] += 1
        c.netted += f.netted
        if f.reason.startswith("no rule"):
            c.no_rule[(_type(t), _direction(t))] += 1
        c.fees += f.reason == "bank fee"
        c.bill_credits += f.reason == "bill payment credit"
        if f.reason not in ("pre-auth correction", "card return", "returned e-Transfer"):
            continue
        c.refunds[f.reason] += 1
        if f.link is not None:
            c.pairs[f.reason] += 1
            if f.link_kind == "preauth":
                gap = (t.posted_date - by_id[f.link].posted_date).days
                c.preauth_gaps[_gap_bucket(gap)] += 1
        elif f.reason == "card return":
            c.unpaired[(f.reason, _why_return_unpaired(t, txns, names))] += 1
        elif f.reason == "returned e-Transfer":
            c.unpaired[(f.reason, _why_etransfer_unpaired(t, txns))] += 1
    return c


_PAIRABLE = ("pre-auth correction", "card return", "returned e-Transfer")


def _why_correction_unpaired(t: Txn, txns: Sequence[Txn], names: dict[int, str | None]) -> str:
    same = [
        c for c in txns
        if _type(c) == "pos purchase" and c.amount_cents < 0 and c.posted_date <= t.posted_date
        and names[c.row_id] == names[t.row_id]
    ]  # fmt: skip
    if not same:
        return "no earlier purchase from that merchant"
    exact = [c for c in same if -c.amount_cents == t.amount_cents]
    if not exact:
        return "no purchase of the same amount"
    if all(_days(c, t) > PREAUTH_DAYS for c in exact):
        return "purchase over 7 days earlier"
    return "matching purchases already paired"


def _gap_bucket_wide(days: int) -> str:
    for upper, label in ((0, "0"), (1, "1"), (7, "2-7"), (30, "8-30")):
        if days <= upper:
            return label
    return "31-90"


@dataclass
class GroupExplanation:
    rows: int = 0
    debits: int = 0
    credits: int = 0
    credit_types: Counter[str] = field(default_factory=Counter)  # bank types (fixed vocabulary)
    paired: Counter[str] = field(default_factory=Counter)  # link kind -> credits paired
    fully_reversed: int = 0  # credit = the full amount of its purchase: the pair nets to zero
    partly_reversed: int = 0
    gap_days: Counter[str] = field(default_factory=Counter)  # purchase -> credit
    unpaired: Counter[tuple[str, str]] = field(default_factory=Counter)  # (reason, why)
    in_categorizer_queue: int = 0  # rows whose category is left to you


def explain_group(
    txns: Sequence[Txn], group: str, user: UserDecisions | None = None
) -> GroupExplanation:
    """Counts showing how one merchant group's rows were typed and paired."""
    from lookahead.categories import category_by_rules  # here: categories imports this module

    flows = flow_types(txns, user)
    names = merchant_groups(txns, user)
    by_id = {t.row_id: t for t in txns}
    decisions = category_by_rules(txns, user)
    e = GroupExplanation()
    for t in txns:
        if names[t.row_id] != group:
            continue
        f = flows[t.row_id]
        e.rows += 1
        e.in_categorizer_queue += decisions[t.row_id].source == "categorizer"
        if t.amount_cents <= 0:
            e.debits += 1
            continue
        e.credits += 1
        e.credit_types[_type(t)] += 1
        if f.reason not in _PAIRABLE:
            e.unpaired[(f.reason, "this credit type is never paired")] += 1
        elif f.link is None:
            if f.reason == "pre-auth correction":
                why = _why_correction_unpaired(t, txns, names)
            elif f.reason == "card return":
                why = _why_return_unpaired(t, txns, names)
            else:
                why = _why_etransfer_unpaired(t, txns)
            e.unpaired[(f.reason, why)] += 1
        else:
            partner = by_id[f.link]
            e.paired[f.link_kind or ""] += 1
            if -partner.amount_cents == t.amount_cents:
                e.fully_reversed += 1
            else:
                e.partly_reversed += 1
            e.gap_days[_gap_bucket_wide(_days(partner, t))] += 1
    return e


def _load(db_path: Path, account: str) -> tuple[list[Txn], UserDecisions]:
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    txns = [
        Txn(row_id, date.fromisoformat(day), cents, kind, text)
        for row_id, day, cents, kind, text in conn.execute(
            "SELECT id, posted_date, amount_cents, description, sub_description"
            " FROM transactions WHERE account = ?",
            (account,),
        )
    ]
    user = load_user_decisions(conn)  # your merges and splits from step 2
    conn.close()
    return txns, user


def _explain(db_path: Path, account: str, group: str) -> int:
    """Counts only; the group name is not echoed."""
    if not db_path.exists():
        print(f"database not found: {db_path}")
        return 2
    txns, user = _load(db_path, account)
    e = explain_group(txns, group, user)
    print(f"rows in this merchant group: {e.rows} (debits {e.debits}, credits {e.credits})")
    if not e.rows:
        print("  no rows: copy the merchant's name exactly as the Label review page shows it")
        return 0
    types = ", ".join(f"{k} {n}" for k, n in sorted(e.credit_types.items())) or "none"
    print(f"credits by bank type: {types}")
    paired = ", ".join(f"{k} {n}" for k, n in sorted(e.paired.items())) or "none"
    print(f"credits paired with an earlier purchase: {sum(e.paired.values())} ({paired})")
    print(f"  fully reversed (nets to zero): {e.fully_reversed}, partly: {e.partly_reversed}")
    gaps = ", ".join(f"{b} days {e.gap_days[b]}" for b in ("0", "1", "2-7", "8-30", "31-90"))
    print(f"  purchase to credit: {gaps}")
    for (reason, why), n in sorted(e.unpaired.items()):
        print(f"credits not paired: {reason}: {why} {n}")
    print(f"rows of this group in the categorizer queue: {e.in_categorizer_queue}")
    return 0


def _report(db_path: Path, account: str) -> int:
    if not db_path.exists():
        print(f"database not found: {db_path}")
        return 2
    txns, user = _load(db_path, account)
    c = count_flows(txns, user)
    flows = ("income", "recurring_outflow", "everyday", "transfer", "refund")
    print(f"Flow typing: {c.rows} rows, account {account}")
    print("rows per flow type: " + ", ".join(f"{f} {c.by_flow[f]}" for f in flows))
    print(f"bank fees (typed everyday for now): {c.fees} rows")
    print(f"netted rows (fully reversed pairs: no spending, no category): {c.netted}")
    print(f"bill payment credits (money in, typed refund): {c.bill_credits} rows")
    for reason, label in (
        ("pre-auth correction", "pre-auth corrections paired with their hold"),
        ("card return", "card returns paired with their purchase"),
        ("returned e-Transfer", "returned e-Transfers paired with the e-Transfer"),
    ):
        print(f"{label}: {c.pairs[reason]} of {c.refunds[reason]}")
        why = [(w, n) for (r, w), n in sorted(c.unpaired.items()) if r == reason]
        if why:
            print("  not paired: " + "; ".join(f"{w} {n}" for w, n in why))
    gaps = ", ".join(f"{b} days {c.preauth_gaps[b]}" for b in ("0", "1", "2-3", "4-7"))
    print(f"  hold to correction: {gaps}")
    no_rule = ", ".join(
        f"{kind}, money {direction} {n}" for (kind, direction), n in sorted(c.no_rule.items())
    )
    print(f"rows without a rule: {sum(c.no_rule.values())}" + (f" ({no_rule})" if no_rule else ""))
    print("RESULT: PASS" if c.ok else "RESULT: CHECK")
    return 0 if c.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.flows")
    commands = parser.add_subparsers(dest="command", required=True)
    rep = commands.add_parser("report", help="counts-only check of flow typing")
    exp = commands.add_parser("explain", help="counts: how one merchant group paired")
    exp.add_argument("--group", required=True, help="the merchant's name as on the page")
    for sub in (rep, exp):
        sub.add_argument("--db", type=Path, required=True)
        sub.add_argument("--account", default="scotia-chequing")
    args = parser.parse_args(argv)
    if args.command == "explain":
        return _explain(args.db, args.account, args.group)
    return _report(args.db, args.account)


if __name__ == "__main__":
    sys.exit(main())
