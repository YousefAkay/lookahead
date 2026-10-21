"""Scoring the "Looks right" proposals on the planted development personas (week 5, item 2).

The method and the adopt rule are DECISIONS.md, 2026-10-20 (written before any proposer or
planter code); this file only carries them out. **Development personas only: the planted copies
of the 12-month and 24-month development sets. Held-out personas are refused.** Real and
synthetic results are never in one column (the real account only vetoes, `eval.proposals_recover`).

    python -m eval.proposals_v2 quick                       # a few origins, to time it
    python -m eval.proposals_v2 score --label "spec as written" [--set both] [--arms]

(b) per kind: precision >= 80 percent and recall >= 60 percent, with at least 20 planted events;
(c) "one click" is not worse than zero confirmations by more than 1 percent on the 30-day error
    for any archetype (--arms: heavy, run on the bigger machine);
(d) zero-confirmation forecasts are unchanged: nothing in the forecast reads the proposers
    (a test checks the imports; the saved default still equals the experiment 3 records);
(e) precision above 95 percent raises a leakage flag before anything is claimed;
(g) every run is a line in the variant ledger and the report prints how many there are.
"""

import argparse
import gc
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from eval.anchor import rel, stats
from eval.backtest import origins as backtest_origins
from eval.backtest import run_origin_full
from lookahead import recurring_store as rs
from lookahead.db import connect
from lookahead.generator.archetypes import (
    END,
    MixPersona,
    assert_development_only,
    build_persona,
    development_ids,
)
from lookahead.generator.plant import (
    PLANTER_VERSION,
    SEED,
    PlantedEvent,
    month_index,
    plant,
    read_answer_key,
)
from lookahead.generator.schedules import GeneratedTxn
from lookahead.generator.writer import export_rows
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.rent import RentRule
from lookahead.setting_proposals import SettingProposal, accept, current_proposals
from lookahead.settings import set_safety_buffer_cents

PLANTED = Path("data/backtest/planted")
KINDS = ("rent", "link", "possible")
MIN_EVENTS, MIN_PRECISION, MIN_RECALL, LEAK_PRECISION = 20, 0.80, 0.60, 0.95
SHARE = 0.80  # a proposal is about a planted event if this share of its payments are the event's
RECALL_DAYS = 60  # a proposal must be shown within 60 days of the event becoming observable
HARM = 0.01  # (c): one click may not be worse than zero confirmations by more than 1 percent


# --- The planted persona as a database ---


@dataclass
class PlantedDb:
    conn: sqlite3.Connection
    persona: MixPersona
    rows: list[GeneratedTxn]  # the planted rows (the generator's ground truth, series ids kept)
    events: list[PlantedEvent]


@contextmanager
def planted_db(
    persona_id: str, folder: Path, work_dir: Path, planted_root: Path = PLANTED
) -> Iterator[PlantedDb]:
    """The planted copy of a development persona in a throw-away database. Refuses held-out."""
    assert_development_only([persona_id])
    persona = build_persona(persona_id)
    events = read_answer_key(planted_root / folder, persona_id)
    rows, again = plant(persona_id, export_rows(persona.persona, persona.start, END, 42), SEED)
    if again != events:
        raise ValueError(f"{persona_id}: the answer key on disk is not the planter's output")
    work = work_dir / persona_id
    work.mkdir(parents=True, exist_ok=True)
    conn = None
    try:
        export = planted_root / folder / persona_id / "export.csv"
        conn = connect(work / "run.db")
        store_exports(
            conn, persona_id, [(export.name, parse_export(export))], datetime(2026, 10, 1)
        )
        set_safety_buffer_cents(conn, persona.buffer_cents)
        yield PlantedDb(conn, persona, rows, events)
    finally:
        if conn is not None:
            conn.close()
        gc.collect()
        shutil.rmtree(work, ignore_errors=True)


# --- Matching a proposal to a planted event (the spec's "what counts as a match") ---


def evidence_pairs(
    conn: sqlite3.Connection, account: str, prints: Sequence[str]
) -> set[tuple[date, int]]:
    """(posting date, amount) of the stored payments a proposal rests on."""
    out: set[tuple[date, int]] = set()
    for fp in prints:
        row = conn.execute(
            "SELECT posted_date, amount_cents FROM transactions"
            " WHERE account = ? AND fingerprint = ?",
            (account, fp),
        ).fetchone()
        if row is not None:
            out.add((date.fromisoformat(row[0]), int(row[1])))
    return out


def share_in(pairs: set[tuple[date, int]], truth: set[tuple[date, int]]) -> float:
    return len(pairs & truth) / len(pairs) if pairs else 0.0


def in_window(day: int, rule: RentRule) -> bool:
    first, last = rule.from_day, rule.to_day
    return first <= day <= last if first <= last else day >= first or day <= last


def _series_pairs(rows: Sequence[GeneratedTxn], select: Any) -> set[tuple[date, int]]:
    return {(t.posted_date, t.amount_cents) for t in rows if select(t)}


def matches(
    proposal: SettingProposal, event: PlantedEvent, rows: Sequence[GeneratedTxn],
    pairs: set[tuple[date, int]],
) -> bool:  # fmt: skip
    """Right series and right content, as DECISIONS.md (2026-10-20) says."""
    if proposal.kind != event.kind:
        return False
    d = event.details
    if event.kind == "rent" and proposal.rent is not None:
        rent = _series_pairs(rows, lambda t: bool(t.series_id and t.series_id.endswith("-rent")))
        if share_in(pairs, rent) < SHARE:
            return False
        if event.what == "increase":
            sizes = set(d["sizes_cents"])
            days = [x.day for x, _ in rent if x >= date.fromisoformat(d["first_new_payment"])]
        else:
            sizes = {abs(c) for x, c in rent if month_index(x) >= d["from_month"]}
            days = list(d["send_days"])
        covered = sum(in_window(x, proposal.rent) for x in days) / len(days) if days else 0.0
        return sizes <= set(proposal.rent.amounts_cents) and covered >= SHARE
    if event.kind == "link":
        series = _series_pairs(rows, lambda t: t.series_id == d["series_id"])
        cut = date.fromisoformat(d["first_new_payment"])
        mine = pairs & series
        old_side, new_side = [p for p in mine if p[0] < cut], [p for p in mine if p[0] >= cut]
        return share_in(pairs, series) >= SHARE and bool(old_side) and bool(new_side)
    if event.kind == "possible":
        planted = _series_pairs(rows, lambda t: bool(t.series_id and "-sub-planted" in t.series_id))
        return share_in(pairs, planted) >= SHARE
    return False


# --- (b) proposal quality ---


@dataclass
class KindScore:
    shown: int = 0  # distinct proposals shown at any origin
    matched: int = 0  # of those, matching a planted event
    events: int = 0  # planted events (n)
    observable: int = 0  # of those, complete by the last origin: the recall denominator
    recovered: int = 0  # of those, matched by a proposal shown in time


@dataclass
class Scores:
    kinds: dict[str, KindScore] = field(default_factory=lambda: {k: KindScore() for k in KINDS})
    personas: int = 0

    def add(self, other: "Scores") -> None:
        for kind in KINDS:
            mine, theirs = self.kinds[kind], other.kinds[kind]
            for name in ("shown", "matched", "events", "observable", "recovered"):
                setattr(mine, name, getattr(mine, name) + getattr(theirs, name))
        self.personas += other.personas


def score_planted(db: PlantedDb, origin_days: Sequence[date]) -> Scores:
    """Run the proposers at each origin (rows and decisions as of that day) and score them."""
    persona_id = db.persona.persona.persona_id
    seen: dict[str, tuple[SettingProposal, list[date]]] = {}
    for day in origin_days:
        for p in current_proposals(db.conn, persona_id, as_of=day):
            seen.setdefault(p.key, (p, []))[1].append(day)
    pairs = {key: evidence_pairs(db.conn, persona_id, p.evidence) for key, (p, _) in seen.items()}
    scores = Scores(personas=1)
    last = max(origin_days) if origin_days else date.min
    for kind in KINDS:
        ks = scores.kinds[kind]
        mine = [(k, p, days) for k, (p, days) in seen.items() if p.kind == kind]
        events = [e for e in db.events if e.kind == kind]
        ks.shown, ks.events = len(mine), len(events)
        ks.matched = sum(any(matches(p, e, db.rows, pairs[k]) for e in events) for k, p, _ in mine)
        # Only events whose evidence is complete by the last origin, as the answer key defines it
        # (never by what a proposer showed), can be recovered or missed; the rest are excluded.
        counted = [e for e in events if e.observable_from <= last]
        ks.observable = len(counted)
        ks.recovered = sum(
            any(
                matches(p, e, db.rows, pairs[k])
                and any(d <= e.observable_from + timedelta(days=RECALL_DAYS) for d in days)
                for k, p, days in mine
            )
            for e in counted
        )
    return scores


def kind_status(ks: KindScore) -> tuple[bool, list[str]]:
    """The adopt rule (b) and the leakage flag (e) for one kind. Recall is over the planted events
    that are complete by the last origin; the rest are reported as excluded."""
    precision = ks.matched / ks.shown if ks.shown else 0.0
    recall = ks.recovered / ks.observable if ks.observable else 0.0
    reasons = []
    if ks.events < MIN_EVENTS:
        reasons.append(f"only {ks.events} planted events (need {MIN_EVENTS}): cannot pass")
    if precision < MIN_PRECISION:
        reasons.append(f"precision {precision:.0%} is under {MIN_PRECISION:.0%}")
    if recall < MIN_RECALL:
        reasons.append(f"recall {recall:.0%} is under {MIN_RECALL:.0%}")
    if precision > LEAK_PRECISION:
        reasons.append(
            f"precision {precision:.0%} is above {LEAK_PRECISION:.0%}: LEAKAGE CHECK FIRST"
        )
    return not reasons, reasons


# --- (c) one click against zero confirmations ---


def accept_all_valid(
    conn: sqlite3.Connection, persona_id: str, origin_days: Sequence[date]
) -> dict[str, int]:
    """ "One click": at each origin in order, accept every proposal valid on that day (using only
    data up to it), dated that day, so it counts from then on. Returns counts by kind."""
    accepted: set[str] = set()
    done = {k: 0 for k in KINDS}
    for day in origin_days:
        series, _ = rs.series_as_of(conn, persona_id, day)
        for p in current_proposals(conn, persona_id, as_of=day):
            if p.key in accepted:
                continue
            accept(conn, persona_id, p, series, day)
            accepted.add(p.key)
            done[p.kind] += 1
    return done


def compare_arms(
    zero: list[dict[str, Any]], one: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Per archetype: the 30-day error with zero confirmations and with one click, and whether one
    click is within 1 percent (rule (c)). Same measure as the earlier experiments."""
    out: dict[str, dict[str, Any]] = {}
    names = sorted({r["archetype"] for r in zero})
    for name in names:
        z = stats([r for r in zero if r["archetype"] == name])
        o = stats([r for r in one if r["archetype"] == name])
        if z is None or o is None:
            continue
        change = rel(o["err30"], z["err30"])
        out[name] = {"n": z["n"], "zero": z["err30"], "one_click": o["err30"], "change": change,
                     "ok": change <= HARM}  # fmt: skip
    return out


def forecast_arms(
    db: PlantedDb, origin_days: Sequence[date], futures: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """The zero arm, then the one-click arm, on one planted persona."""
    persona_id, archetype = db.persona.persona.persona_id, db.persona.archetype

    def run() -> list[dict[str, Any]]:
        return [
            run_origin_full(db.conn, persona_id, "synthetic", day, futures, archetype, False,
                            keep_paths=False)[0]
            for day in origin_days
        ]  # fmt: skip

    zero = run()
    done = accept_all_valid(db.conn, persona_id, origin_days)
    return zero, run(), done


# --- (g) the variant ledger ---


def code_version() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def record_variant(
    ledger: Path, label: str, sets: Sequence[str], arms: bool, kind: str = "scored run"
) -> int:
    """Append this run to the ledger; returns how many variants are on it now (this one
    included)."""
    entries = json.loads(ledger.read_text("utf-8")) if ledger.exists() else []
    entries.append({
        "label": label, "kind": kind, "when": datetime.now().isoformat(timespec="seconds"),
        "code": code_version(), "planter_version": PLANTER_VERSION, "seed": SEED,
        "sets": list(sets), "arms": arms,
    })  # fmt: skip
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    return len(entries)


# --- The report ---


NOT_INDEPENDENT = (
    "Not an independent check: the rent cluster rule, the 5 percent tolerance of the possible-"
    "subscription proposer and the planter additions of version 3 (noise and currency pairs) were "
    "chosen on 2026-10-21 because of what my real-account veto run showed, so a veto rerun after "
    "them is expected to pass and shows that the changes work on my case, not that they would "
    "have worked blind."
)
HEADER = (
    "| kind | planted events (n) | counted for recall | excluded (not yet observable) "
    "| proposals shown | precision | recall | adopt rule (b), (e) |"
)
CARD_NOTE = (
    "Caution on the possible-subscription precision: the card-pair rule of the proposer (exactly "
    "equal amounts a calendar month apart; commit 308757c) was made after the planter was written "
    "and before any scoring, and it matches exactly what the planter plants, so precision on "
    "planted card pairs is likely flattering."
)


def report_text(
    scores: Scores, variants: int, arms: dict[str, dict[str, Any]] | None = None
) -> str:
    lines = [
        "# Proposals for recurring settings: planted development personas",
        f"Personas scored: {scores.personas}. Variants tried so far: {variants}. "
        "Synthetic, development only; the real account only vetoes (a separate script).",
        "",
        CARD_NOTE,
        "",
        NOT_INDEPENDENT,
        "",
        HEADER,
        "|---|---|---|---|---|---|---|---|",
    ]
    for kind in KINDS:
        ks = scores.kinds[kind]
        ok, reasons = kind_status(ks)
        shown = f"{ks.matched / ks.shown:.0%} ({ks.matched}/{ks.shown})" if ks.shown else "- (0/0)"
        found = (
            f"{ks.recovered / ks.observable:.0%} ({ks.recovered}/{ks.observable})"
            if ks.observable
            else "- (0/0)"
        )
        verdict = "passes" if ok else "; ".join(reasons)
        cells = [kind, ks.events, ks.observable, ks.events - ks.observable, ks.shown, shown, found]
        lines.append("| " + " | ".join(str(c) for c in cells) + f" | {verdict} |")
    if arms is not None:
        lines += [
            "",
            "## One click against zero confirmations (30-day error, ordinary origins)",
            "| archetype | n | zero | one click | change | within 1% |",
            "|---|---|---|---|---|---|",
        ]
        for name, a in arms.items():
            lines.append(
                f"| {name} | {a['n']} | ${a['zero']:,.0f} | ${a['one_click']:,.0f} "
                f"| {a['change']:+.1%} | {'yes' if a['ok'] else 'NO'} |"
            )
        worse = sum(not a["ok"] for a in arms.values())
        lines.append(
            f"{chr(10)}Rule (c): {'holds' if not worse else 'FAILS'} "
            f"({worse} of {len(arms)} archetypes worse by more than 1%)."
        )
    return chr(10).join(lines) + chr(10)


def run_score(
    sets: Sequence[str], label: str, work_dir: Path, planted_root: Path = PLANTED,
    origin_days: Sequence[date] | None = None, personas: int | None = None,
    arms: bool = False, futures: int = 500, ledger: Path | None = None,
) -> str:  # fmt: skip
    """Score the proposers on the planted copies; with `arms`, also the forecast arms (heavy)."""
    days = list(origin_days or backtest_origins())
    scores, zero, one = Scores(), [], []
    for name in sets:
        for pid in development_ids(long=name == "long")[:personas]:
            with planted_db(pid, Path(name), work_dir, planted_root) as db:
                scores.add(score_planted(db, days))
                if arms:
                    z, o, _ = forecast_arms(db, days, futures)
                    zero, one = zero + z, one + o
    variants = record_variant(ledger or planted_root / "variants.json", label, sets, arms)
    return report_text(scores, variants, compare_arms(zero, one) if arms else None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.proposals_v2")
    parser.add_argument("command", choices=["quick", "score", "variant"])
    parser.add_argument("--label", default="spec as written")
    parser.add_argument("--set", choices=["12", "long", "both"], default="both")
    parser.add_argument("--arms", action="store_true", help="also the forecast arms (heavy)")
    parser.add_argument("--work", type=Path, default=Path("data/backtest/planted-work"))
    args = parser.parse_args(argv)
    sets = {"12": ["12"], "long": ["long"], "both": ["12", "long"]}[args.set]
    start = time.time()
    if args.command == "variant":  # a change of a proposer or of the planter: counted, not scored
        ledger = PLANTED / "variants.json"
        print(f"variants on the ledger: {record_variant(ledger, args.label, [], False, 'change')}")
        return 0
    if args.command == "quick":
        text = run_score(
            sets[:1],
            f"quick: {args.label}",
            args.work,
            origin_days=backtest_origins()[::10],
            personas=2,
            arms=args.arms,
            futures=50,
            ledger=args.work / "quick-runs.json",
        )  # a sanity run: it is not a scored variant, so it has its own ledger
    else:
        text = run_score(sets, args.label, args.work, arms=args.arms)
    print(text)
    print(f"({time.time() - start:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
