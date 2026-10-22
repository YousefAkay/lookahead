"""Would the proposers recover my existing hand entries? A counts-only veto (rule (f), DECISIONS.md,
2026-10-20). I run this myself on my real database; it prints yes/no counts and nothing else: no
amounts, dates, names or keys.

    python -m eval.proposals_recover --db data/private/db/lookahead.db
    python -m eval.proposals_recover --db data/private/db/lookahead.db --explain

It opens the database read-only, works on an in-memory copy, removes my hand entries from the copy
(the rent rules, the "continues" links and the series I added by hand), runs the proposers as they
run today (rows to the data's last day, every other decision of mine still in force), and counts for
each kind whether a proposal would have given the same thing. An entry is found by the payments it
rests on, not by its id (a migrated link can name one id on both sides):
- rent (DECISIONS.md, 2026-10-22): a rent proposal on the same payments whose larger allowed
  amount is the one my rule uses, and whose window covers every day of my past rent payments
  that falls inside my rule's window; missing or extra older amounts and a wider or narrower
  window are only reported;
- link: a link proposal that rests on at least 80 percent of the same payments;
- hand-added: a "possible subscription" proposal with the same name, or a series that detection
  finds on its own from at least 80 percent of the same payments.
A "no" for a kind vetoes that kind. With --explain, each "no" also prints only a reason code and its
plain meaning (a count per code): never a name, amount, date, merchant or key. Nothing is written
to the database or to disk.
"""

import argparse
import sqlite3
import sys
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from eval.rent_rule_origins import detection_only
from lookahead import recurring_store as rs
from lookahead.identity import _accounts, _in_memory_copy
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key
from lookahead.rent import RentRule
from lookahead.setting_proposals import (
    REASONS,
    SettingProposal,
    current_proposals,
    link_reason,
    possible_reason,
    proposal_inputs,
    rent_profile,
    rent_reason,
)

SHARE = 0.80
KINDS = ("rent", "link", "hand-added")
NOT_INDEPENDENT_NOTE = (
    "note: not an independent check after the changes of 2026-10-21 (rent cluster, 5 percent "
    "tolerance, planter version 3): I chose them because of what this account showed."
)


def _window_days(rule: RentRule) -> set[int]:
    first, last = rule.from_day, rule.to_day
    days = range(first, last + 1) if first <= last else [*range(first, 32), *range(1, last + 1)]
    return set(days)


@dataclass(frozen=True)
class RentComparison:
    """How a rent proposal compares with my rent rule (counts and yes/no only)."""

    forward_same: bool  # the larger allowed amount is the same
    missing: int  # allowed amounts of mine the proposal does not have
    extra: int  # allowed amounts the proposal has and mine does not
    covered: int  # days of my past payments inside my window that its window covers
    inside: int  # days of my past payments inside my window

    @property
    def recovered(self) -> bool:
        """DECISIONS.md, 2026-10-22: (a) the same forward amount, (b) its window covers every day
        of my past rent payments that falls inside my window. Missing or extra older amounts and
        a wider or narrower window are reported but do not veto."""
        return self.forward_same and self.covered == self.inside

    @property
    def reason(self) -> str | None:
        if self.recovered:
            return None
        return "forward_amount_differs" if not self.forward_same else "window_misses_payment_days"

    def line(self) -> str:
        return (
            f"rent: forward amount same {'yes' if self.forward_same else 'no'}; allowed amounts "
            f"missing {self.missing}, extra {self.extra}; payment days covered {self.covered} "
            f"of {self.inside}"
        )


def compare_rent(mine: RentRule, proposal: SettingProposal, my_days: list[int]) -> RentComparison:
    """Compare a rent proposal with my rule; `my_days` are the days of the month of my past rent
    payments."""
    theirs = proposal.rent
    if theirs is None:
        return RentComparison(False, len(set(mine.amounts_cents)), 0, 0, 0)
    inside = [d for d in my_days if d in _window_days(mine)]
    return RentComparison(
        forward_same=max(theirs.amounts_cents) == max(mine.amounts_cents),
        missing=len(set(mine.amounts_cents) - set(theirs.amounts_cents)),
        extra=len(set(theirs.amounts_cents) - set(mine.amounts_cents)),
        covered=sum(d in _window_days(theirs) for d in inside),
        inside=len(inside),
    )


def rent_recovered(mine: RentRule, proposal: SettingProposal, my_days: list[int]) -> bool:
    return compare_rent(mine, proposal, my_days).recovered


EXTRA_REASONS = {
    "forward_amount_differs": "a proposal exists but its larger amount is not the one my rule uses",
    "window_misses_payment_days": "a proposal exists but its window misses days of my payments",
}


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


def _overlap(a: Collection[str], b: Collection[str], share_of: str = "smaller") -> bool:
    """The two sets of payments are mostly the same ones."""
    a, b = set(a), set(b)
    if not a or not b:
        return False
    base = min(len(a), len(b)) if share_of == "smaller" else len(b)
    return len(a & b) >= SHARE * base


def _find(series: list[rs.Stored], ref: str) -> rs.Stored | None:
    return next((s for s in series if ref in (s.id, s.key) or ref in s.continues), None)


def _undo_link_merges(
    copy: sqlite3.Connection, account: str, entries: list[rs.Stored], key: bytes | None
) -> list[str]:
    """Hiding a link must also undo the merge the migration left behind. The migration remembers
    the payments of both names under one id (the members table), which joins the two series again
    even without the link decision. For each linked series, detection alone says which payments are
    the newer name's; those are forgotten in the copy, so the two names are two series, as they
    were before I linked them. Returns one counts-only line per entry."""
    lines = []
    inputs = proposal_inputs(copy, account, key)
    for both in entries:
        if inputs is None:
            lines.append("link entry: nothing posted")
            continue
        raw = detection_only(copy, account, inputs.day, key)
        parts = [r for r in raw if _overlap(r.evidence, both.evidence, "smaller")]
        covered = len(set().union(*(set(r.evidence) for r in parts)) & set(both.evidence))
        lines.append(
            f"link entry rests on {len(both.evidence)} payments; detection alone finds "
            f"{len(parts)} series covering {covered} of them"
        )
        if len(parts) >= 2:
            newer = max(parts, key=lambda r: (r.last_paid or date.min, r.key))
            with copy:
                copy.executemany(
                    "DELETE FROM series_members WHERE account = ? AND fingerprint = ?",
                    [(account, fp) for fp in newer.evidence],
                )
    return lines


def recover_detailed(
    copy: sqlite3.Connection, key: bytes | None = None
) -> tuple[dict[str, tuple[int, int]], dict[str, Counter[str]], dict[str, list[str]]]:
    """({kind: (recovered, not recovered)}, {kind: count of each reason code for a "no"}, {kind:
    counts-only detail lines}) for the hand entries in `copy`, which is changed (the entries are
    deleted from it): hand it a throw-away copy."""
    results: dict[str, list[bool]] = {k: [] for k in KINDS}
    reasons: dict[str, Counter[str]] = {k: Counter() for k in KINDS}
    details: dict[str, list[str]] = {k: [] for k in KINDS}

    def note(kind: str, reason: str | None) -> None:
        results[kind].append(reason is None)
        if reason is not None:
            reasons[kind][reason] += 1

    for account in _accounts(copy):
        before, _ = rs.series_as_of(copy, account, None, key)
        decisions = rs.load_decisions(copy, account)
        rules = _latest_rent_rules(decisions)
        links = [
            (d.key, str(d.details.get("previous"))) for d in decisions if d.action == "continue"
        ]
        added = [s for s in before if s.status == "user-added"]
        refs = {n for n, _ in links} | {o for _, o in links}
        linked = {t.key: t for ref in refs if (t := _find(before, ref)) is not None}
        details["link"] += _undo_link_merges(copy, account, list(linked.values()), key)
        with copy:  # hide my hand entries from the copy before asking the proposers
            copy.execute(
                "DELETE FROM recurring_decisions WHERE account = ?"
                " AND action IN ('rent', 'continue', 'add')", (account,),
            )  # fmt: skip
        found = current_proposals(copy, account, key)
        inputs = proposal_inputs(copy, account, key)
        for ref, rule in rules.items():
            mine = _find(before, ref)
            if mine is None or inputs is None:
                note("rent", "entry_not_found")
                continue
            near = [p for p in found if p.kind == "rent" and _overlap(p.evidence, mine.evidence)]
            when = {inputs.fingerprint[t.row_id]: t.posted_date for t in inputs.txns}
            my_days = [when[fp].day for fp in mine.evidence if fp in when]
            compared = [compare_rent(rule, p, my_days) for p in near]
            if compared:  # every comparison is reported; the best one decides
                best = max(compared, key=lambda c: (c.recovered, c.forward_same, c.covered))
                details["rent"].append(best.line())
            if any(c.recovered for c in compared):
                note("rent", None)
            elif compared:
                note("rent", best.reason)
            else:
                note("rent", rent_reason(
                    inputs.series, inputs.decisions, inputs.txns, inputs.fingerprint,
                    inputs.user, key, inputs.day, mine.evidence,
                ))  # fmt: skip
                profile = rent_profile(
                    inputs.series,
                    inputs.txns,
                    inputs.fingerprint,
                    inputs.user,
                    key,
                    inputs.day,
                    mine.evidence,
                )
                if profile is not None:
                    details["rent"].append(
                        f"rent recipient has {profile[0]} payments, {profile[1]} of them at or "
                        f"above the $100 floor, in {profile[2]} different months"
                    )
        for new_ref, old_ref in links:
            both = _find(before, new_ref) or _find(before, old_ref)
            if both is None or inputs is None:
                note("link", "entry_not_found")
                continue
            hit = any(
                p.kind == "link" and _overlap(p.evidence, both.evidence, "mine") for p in found
            )
            note("link", None if hit else link_reason(
                inputs.series, inputs.decisions, inputs.txns, inputs.fingerprint, inputs.day,
                both.evidence,
            ))  # fmt: skip
        after, _ = rs.series_as_of(copy, account, None, key)
        detected = [set(o.evidence) for o in after if o.detected_now]
        names = {(p.name or "").casefold() for p in found if p.kind == "possible"}
        for s in added:
            mine_ev = set(s.evidence)
            alone = bool(mine_ev) and any(
                len(mine_ev & other) >= SHARE * len(mine_ev) for other in detected
            )
            if (s.name or "").casefold() in names or alone:
                note("hand-added", None)
            elif inputs is None:
                note("hand-added", "entry_not_found")
            else:
                note("hand-added", possible_reason(
                    inputs.series, inputs.decisions, inputs.txns, inputs.fingerprint,
                    inputs.user, key, inputs.day, s.name or "",
                ))  # fmt: skip
    counts = {k: (sum(v), len(v) - sum(v)) for k, v in results.items()}
    return counts, reasons, details


def recover(copy: sqlite3.Connection, key: bytes | None = None) -> dict[str, tuple[int, int]]:
    """{kind: (recovered, not recovered)}; see `recover_detailed`."""
    return recover_detailed(copy, key)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.proposals_recover")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--explain", action="store_true", help="a reason code for each no")
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
        result, reasons, details = recover_detailed(copy, key)
    finally:
        copy.close()
    print("would the proposers recover my hand entries? (counts only)")
    print(f"  {NOT_INDEPENDENT_NOTE}")
    for kind, (yes, no) in result.items():
        verdict = "nothing to check" if yes + no == 0 else ("VETO" if no else "ok")
        print(f"  {kind}: yes {yes}, no {no} -> {verdict}")
        if args.explain:
            for code, n in sorted(reasons[kind].items()):
                print(
                    f"    why not ({n}): {code} - {({**REASONS, **EXTRA_REASONS}).get(code, code)}"
                )
            for line in details[kind]:
                print(f"    detail: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
