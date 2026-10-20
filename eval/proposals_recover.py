"""Would the proposers recover my existing hand entries? A counts-only veto (rule (f), DECISIONS.md,
2026-10-20). I run this myself on my real database; it prints yes/no counts and nothing else: no
amounts, dates, names or keys.

    python -m eval.proposals_recover --db data/private/db/lookahead.db

It opens the database read-only, works on an in-memory copy, removes my hand entries from the copy
(the rent rules, the "continues" links and the series I added by hand), runs the proposers as they
would run today, and counts for each kind whether a proposal would have given the same thing:
- rent: a proposal on that series with the same allowed amounts and a window that covers at least
  80 percent of the days of mine;
- link: a proposal that joins the same two series;
- hand-added: a "possible subscription" proposal with the same name, or a series that detection
  finds on its own from at least 80 percent of the same payments.
A "no" for a kind vetoes that kind. Nothing is written to the database or to disk.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from lookahead import recurring_store as rs
from lookahead.identity import _accounts, _in_memory_copy
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key
from lookahead.rent import RentRule
from lookahead.setting_proposals import SettingProposal, current_proposals

SHARE = 0.80


def _window_days(rule: RentRule) -> set[int]:
    first, last = rule.from_day, rule.to_day
    days = range(first, last + 1) if first <= last else [*range(first, 32), *range(1, last + 1)]
    return set(days)


def rent_recovered(mine: RentRule, proposal: SettingProposal) -> bool:
    theirs = proposal.rent
    if theirs is None or set(theirs.amounts_cents) != set(mine.amounts_cents):
        return False
    wanted = _window_days(mine)
    return len(wanted & _window_days(theirs)) >= SHARE * len(wanted)


def _latest_rent_rules(decisions: list[rs.Decision]) -> dict[str, RentRule]:
    """The rent rule I have now on each series (a later "no rule" takes it away)."""
    rules: dict[str, RentRule] = {}
    for d in decisions:
        if d.action != "rent":
            continue
        rule = rs._rule_from(d.details)
        if rule is None:
            rules.pop(d.key, None)
        else:
            rules[d.key] = rule
    return rules


def _links_recovered(links: list[tuple[str, str]], found: list[SettingProposal]) -> list[bool]:
    pairs = [{p.series_id, p.other_id} for p in found if p.kind == "link"]
    return [{new, old} in pairs for new, old in links]


def _added_recovered(
    added: list[rs.Stored], found: list[SettingProposal], after: list[rs.Stored]
) -> list[bool]:
    names = {(p.name or "").casefold() for p in found if p.kind == "possible"}
    detected = [set(o.evidence) for o in after if o.detected_now]
    out = []
    for s in added:
        mine = set(s.evidence)
        by_name = (s.name or "").casefold() in names
        alone = bool(mine) and any(len(mine & other) >= SHARE * len(mine) for other in detected)
        out.append(by_name or alone)
    return out


def recover(copy: sqlite3.Connection, key: bytes | None = None) -> dict[str, tuple[int, int]]:
    """{kind: (recovered, not recovered)} for the hand entries in `copy`, which is changed
    (the entries are deleted from it): hand it a throw-away copy."""
    results: dict[str, list[bool]] = {"rent": [], "link": [], "hand-added": []}
    for account in _accounts(copy):
        series, _ = rs.series_as_of(copy, account, None, key)
        decisions = rs.load_decisions(copy, account)
        rules = _latest_rent_rules(decisions)
        links = [
            (d.key, str(d.details.get("previous"))) for d in decisions if d.action == "continue"
        ]
        added = [s for s in series if s.status == "user-added"]
        with copy:
            copy.execute(
                "DELETE FROM recurring_decisions WHERE account = ?"
                " AND action IN ('rent', 'continue', 'add')", (account,),
            )  # fmt: skip
        found = current_proposals(copy, account, key)
        after, _ = rs.series_as_of(copy, account, None, key)
        results["rent"] += [
            any(p.kind == "rent" and p.series_id == sid and rent_recovered(rule, p) for p in found)
            for sid, rule in rules.items()
        ]
        results["link"] += _links_recovered(links, found)
        results["hand-added"] += _added_recovered(added, found, after)
    return {k: (sum(v), len(v) - sum(v)) for k, v in results.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.proposals_recover")
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 2
    try:
        key: bytes | None = pseudonym_key()
    except PseudonymKeyError:
        key = None
        print("note: no PSEUDONYM_KEY, e-Transfers that name a recipient are not detected")
    copy = _in_memory_copy(args.db)
    try:
        result = recover(copy, key)
    finally:
        copy.close()
    print("would the proposers recover my hand entries? (counts only)")
    for kind, (yes, no) in result.items():
        verdict = "nothing to check" if yes + no == 0 else ("VETO" if no else "ok")
        print(f"  {kind}: yes {yes}, no {no} -> {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
