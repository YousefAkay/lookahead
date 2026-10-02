"""Category decisions: fixed rules, merchant defaults and your preferences (DECISIONS.md).

Decides the clear cases so the categorizer (step 5) only sees the rest. In order:
0. a fully reversed pair (flows: netted) -> no category needed (it nets to zero);
1. transaction type (via the flow-typing reason): ABM -> Cash; bank fees -> Fees & interest;
   own-account transfers and e-Transfers out or returned -> Transfers; credit memos,
   deposits and incoming e-Transfers -> Income;
2. corrections and returns paired with a purchase -> that purchase's category;
3. your preference for the merchant group (stored, dated; the latest made by a date wins);
4. delivery platforms -> Food delivery (Instacart -> Groceries; their subscriptions ->
   Subscriptions);
5. merchant defaults (HANDOFF section 07): Couche-Tard and SAQ -> Groceries; Amazon ->
   Shopping, except Prime -> Subscriptions; Dollarama -> Shopping;
6. everything else -> the categorizer.

Counts-only check on a database:
    python -m lookahead.categories report --db data/private/db/lookahead.db
"""

import argparse
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from lookahead.flows import Txn, flow_types
from lookahead.merchants import PLATFORM_MERCHANTS, PLATFORM_SUBSCRIPTIONS, extract_merchant
from lookahead.resolve import UserDecisions, load_user_decisions, name_infos, name_key, resolve
from lookahead.taxonomy import CATEGORIES

# Flow-typing reason -> category
TYPE_RULES: dict[str, str] = {
    "cash withdrawal": "Cash",
    "bank fee": "Fees & interest",
    "fee reversal": "Fees & interest",
    "own-account transfer": "Transfers",
    "payment to my own account": "Transfers",  # a bill payment to my own card or credit line
    "outgoing e-Transfer": "Transfers",
    "withdrawal": "Transfers",
    "returned e-Transfer": "Transfers",
    "credit memo": "Income",
    "deposit": "Income",
    "incoming e-Transfer": "Income",
}
_INHERITS = {"pre-auth correction", "card return"}
_MONEY_BACK = {"accounts payable credit", "bill payment credit"}

# Defaults for ambiguous merchants (HANDOFF section 07): general knowledge, overridden by
# your preferences. Matched on whole words at the start of the name key; longest first.
MERCHANT_DEFAULTS: dict[str, str] = {
    "amazon prime": "Subscriptions",
    "prime video": "Subscriptions",
    "couche tard": "Groceries",
    "saq": "Groceries",
    "amazon": "Shopping",
    "dollarama": "Shopping",
}
PLATFORM_CATEGORIES = {"Instacart": "Groceries"}  # other platforms: Food delivery


@dataclass(frozen=True)
class Decision:
    category: str | None  # None = not decided by rules
    source: str
    inherit_from: int | None = None  # row_id of the paired purchase


def _default_for(keys: list[str]) -> str | None:
    for prefix in sorted(MERCHANT_DEFAULTS, key=len, reverse=True):
        if any(k == prefix or k.startswith(prefix + " ") for k in keys):
            return MERCHANT_DEFAULTS[prefix]
    return None


def conflicts(flow_type: str, category: str | None) -> bool:
    """True if a category contradicts the flow type (e.g. Income on spending)."""
    if category is None:
        return False
    if (category == "Income") != (flow_type == "income"):
        return True
    if category == "Transfers" and flow_type not in ("transfer", "refund"):
        return True
    # A transfer (e.g. an e-Transfer) may be confirmed as rent (Week 3) or a gift (your override).
    return flow_type == "transfer" and category not in (
        "Transfers",
        "Rent & housing",
        "Gifts & donations",
    )


def category_by_rules(
    txns: Sequence[Txn],
    user: UserDecisions | None = None,
    preferences: dict[str, str] | None = None,
) -> dict[int, Decision]:
    """Category decision for every row, keyed by row_id (None = left for the categorizer)."""
    mine = user or UserDecisions()
    prefs = preferences or {}
    flows = flow_types(txns, mine)
    resolution = resolve(name_infos(t.text for t in txns), mine)
    members = {g.name: {name_key(m) for m in g.members} for g in resolution.groups}
    for keys in members.values():  # your merges also cover names not in this data
        keys.update(*(k for k in mine.merges.values() if k & keys))

    decisions: dict[int, Decision] = {}
    for t in txns:
        flow = flows[t.row_id]
        extracted = extract_merchant(t.text).merchant
        if flow.netted:  # a fully reversed pair: nothing was spent
            decision = Decision(None, "netted pair, no category needed")
        elif flow.reason in TYPE_RULES:
            decision = Decision(TYPE_RULES[flow.reason], f"transaction type: {flow.reason}")
        elif flow.reason in _INHERITS and flow.link is not None:
            decision = Decision(None, "inherits", flow.link)  # filled in below
        elif extracted is None or flow.reason in _MONEY_BACK or flow.reason.startswith("no rule"):
            decision = Decision(None, "categorizer")  # owner's choice for unpaired money back
        else:  # purchases, bill payments, unpaired corrections and returns
            decision = _by_merchant(extracted, resolution.group_of(extracted), members, prefs)
        decisions[t.row_id] = decision

    for row_id, d in decisions.items():
        if d.source == "inherits" and d.inherit_from is not None:
            source = decisions[d.inherit_from]
            if source.category is not None:
                decisions[row_id] = Decision(
                    source.category, "inherited from the paired purchase", d.inherit_from
                )
            else:
                decisions[row_id] = Decision(
                    None, "inherits; waiting for the categorizer", d.inherit_from
                )
    return decisions


def _by_merchant(
    extracted: str, group: str, members: dict[str, set[str]], prefs: dict[str, str]
) -> Decision:
    keys = sorted(members.get(group, set()) | {name_key(extracted), name_key(group)})
    chosen = [prefs[k] for k in keys if k in prefs]
    if chosen:
        return Decision(chosen[0], "your preference")
    for name in (extracted, group):
        if name in PLATFORM_SUBSCRIPTIONS:
            return Decision("Subscriptions", "delivery platform subscription")
        if name in PLATFORM_MERCHANTS:
            return Decision(PLATFORM_CATEGORIES.get(name, "Food delivery"), "delivery platform")
    default = _default_for([name_key(extracted), name_key(group)])
    if default is not None:
        return Decision(default, "merchant default")
    return Decision(None, "categorizer")


# --- Your preferences, stored with a date ---


def set_category_preference(
    conn: sqlite3.Connection, name: str, category: str, decided_at: date | None = None
) -> None:
    """Remember that the merchant `name` (and its group) belongs in `category`."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO category_preferences (merchant_key, category, decided_at)"
            " VALUES (?, ?, ?)",
            (name_key(name), category, (decided_at or date.today()).isoformat()),
        )


def load_category_preferences(
    conn: sqlite3.Connection, as_of: date | None = None
) -> dict[str, str]:
    """merchant name key -> category: the latest preference made by `as_of` (all if None).
    An older database without the table has none."""
    exists = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'category_preferences'"
    ).fetchone()[0]
    if not exists:
        return {}
    sql = "SELECT merchant_key, category FROM category_preferences"
    params: tuple[str, ...] = ()
    if as_of is not None:
        sql += " WHERE decided_at <= ?"
        params = (as_of.isoformat(),)
    rows = conn.execute(sql + " ORDER BY decided_at", params)
    return dict(rows)  # later rows overwrite earlier ones


# --- Counts-only report ---


@dataclass
class CategoryCounts:
    rows: int
    by_source: Counter[str]
    by_type_rule: Counter[str]  # category -> rows decided by transaction type
    waiting: int  # inherited, but the purchase is left for the categorizer
    conflicts: int

    @property
    def ok(self) -> bool:
        return self.conflicts == 0


_SOURCE_GROUPS = {
    "transaction type": "decided by transaction type",
    "inherited from the paired purchase": "inherited from the paired purchase",
    "inherits; waiting for the categorizer": "inherited from the paired purchase",
    "your preference": "your preferences",
    "delivery platform": "delivery platforms",
    "delivery platform subscription": "delivery platforms",
    "merchant default": "merchant defaults",
    "categorizer": "left for the categorizer",
    "netted pair, no category needed": "netted pairs (no category needed)",
}


def count_categories(
    txns: Sequence[Txn], user: UserDecisions | None = None, prefs: dict[str, str] | None = None
) -> CategoryCounts:
    decisions = category_by_rules(txns, user, prefs)
    flows = flow_types(txns, user)
    c = CategoryCounts(len(txns), Counter(), Counter(), 0, 0)
    for t in txns:
        d = decisions[t.row_id]
        group = _SOURCE_GROUPS[d.source.split(":")[0]]
        c.by_source[group] += 1
        if group == "decided by transaction type" and d.category:
            c.by_type_rule[d.category] += 1
        c.waiting += d.source == "inherits; waiting for the categorizer"
        c.conflicts += conflicts(flows[t.row_id].flow_type, d.category)
    return c


def _report(db_path: Path, account: str) -> int:
    if not db_path.exists():
        print(f"database not found: {db_path}")
        return 2
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    txns = [
        Txn(row_id, date.fromisoformat(day), cents, kind, text)
        for row_id, day, cents, kind, text in conn.execute(
            "SELECT id, posted_date, amount_cents, description, sub_description"
            " FROM transactions WHERE account = ?",
            (account,),
        )
    ]
    user = load_user_decisions(conn)
    prefs = load_category_preferences(conn)
    conn.close()

    c = count_categories(txns, user, prefs)
    by_type = ", ".join(f"{k} {n}" for k, n in sorted(c.by_type_rule.items()))
    print(f"Category rules: {c.rows} rows, account {account}")
    print(f"decided by transaction type: {c.by_source['decided by transaction type']} ({by_type})")
    print(
        f"inherited from the paired purchase: {c.by_source['inherited from the paired purchase']}"
        f" (waiting for the categorizer: {c.waiting})"
    )
    for label in ("your preferences", "delivery platforms", "merchant defaults"):
        print(f"{label}: {c.by_source[label]}")
    print(f"netted pairs (no category needed): {c.by_source['netted pairs (no category needed)']}")
    print(f"left for the categorizer: {c.by_source['left for the categorizer']}")
    print(f"flow/category conflicts: {c.conflicts}")
    print("RESULT: PASS" if c.ok else "RESULT: CHECK")
    return 0 if c.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.categories")
    commands = parser.add_subparsers(dest="command", required=True)
    rep = commands.add_parser("report", help="counts-only check of the category rules")
    rep.add_argument("--db", type=Path, required=True)
    rep.add_argument("--account", default="scotia-chequing")
    args = parser.parse_args(argv)
    return _report(args.db, args.account)


if __name__ == "__main__":
    sys.exit(main())
