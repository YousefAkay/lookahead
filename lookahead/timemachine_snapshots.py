"""Time Machine snapshots for synthetic personas, and the pieces the Time Machine page shows.

The page steps through dates of a synthetic persona and shows, for each, the forecast band and the
alert state the app would have shown that day (rule zero: `lookahead/timemachine.py` builds each
step from what was available then). Building a forecast takes a moment, so the steps are
precomputed and saved; the page only reads them. A what-if scenario at the current date is the
one thing computed on the page, from the persona's own database.

    python -m lookahead.timemachine_snapshots                   # the default personas, every day
    python -m lookahead.timemachine_snapshots --only synthetic-C --every 7

Only synthetic personas: the three demo personas and a few development personas of the mix.
Held-out personas are refused. Files go to data/timemachine (not committed): for each persona its
database, the steps (one JSON line a date) and its daily balances.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from lookahead.db import connect, connect_readonly
from lookahead.forecast.path import build_forecast
from lookahead.forecast.scenario import ExtraItem, Scenario, ScenarioResult, run_scenario
from lookahead.forecast.simulate import N_FUTURES
from lookahead.forecast.start import HORIZON_DAYS
from lookahead.generator.archetypes import (
    ARCHETYPES,
    END,
    START,
    assert_development_only,
    build_persona,
)
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents
from lookahead.timemachine import METHOD, available_dates, precompute

DEMO_PERSONAS = ("synthetic-A", "synthetic-B", "synthetic-C")
MIX_PERSONAS = ("v2-TIGHT-03", "v2-ITIGHT-01", "v2-FREE-01", "v2-SAL-01")  # development only
HISTORY_SHOWN_DAYS = 60  # days of actual balance drawn before the date


def snapshot_dir() -> Path:
    return Path(os.environ.get("LOOKAHEAD_TIMEMACHINE_DIR", "data/timemachine"))


def persona_label(pid: str) -> str:
    """How a persona is named on the page."""
    if pid in DEMO_PERSONAS:
        return f"{pid} (demo persona)"
    code = pid.split("-")[1] if pid.count("-") == 2 else ""
    if code in ARCHETYPES:
        return f"{pid} ({ARCHETYPES[code].description.lower()})"
    return pid


# --- Building the files ---


def _import_persona(pid: str, db_path: Path, synthetic: Path, seed: int) -> None:
    """The persona's export imported into a fresh database at `db_path`, with its buffer."""
    if db_path.exists():
        db_path.unlink()
    conn = connect(db_path)
    try:
        if pid in DEMO_PERSONAS:
            export = synthetic / f"persona_{pid.removeprefix('synthetic-')}.csv"
            store_exports(conn, pid, [(export.name, parse_export(export))], datetime(2026, 10, 1))
        else:
            assert_development_only([pid])
            persona = build_persona(pid)
            work = db_path.parent / f"{pid}-export"
            try:
                export, _ = write_persona(persona.persona, work, seed, START, END)
                store_exports(conn, pid, [(export.name, parse_export(export))],
                              datetime(2026, 10, 1))  # fmt: skip
            finally:
                shutil.rmtree(work, ignore_errors=True)
            set_safety_buffer_cents(conn, persona.buffer_cents)
    finally:
        conn.close()


def closing_balances(conn: sqlite3.Connection, account: str) -> dict[date, int]:
    """The closing balance of every day from the first row to the last (a quiet day keeps the
    balance before it). The first row stored for a day is its last posting."""
    closing: dict[date, int] = {}
    for day, balance in conn.execute(
        "SELECT posted_date, balance_after_cents FROM transactions WHERE account = ?"
        " ORDER BY posted_date, id", (account,),
    ):  # fmt: skip
        closing.setdefault(date.fromisoformat(day), int(balance))
    if not closing:
        return {}
    days = sorted(closing)
    out, carried = {}, closing[days[0]]
    day = days[0]
    while day <= days[-1]:
        carried = closing.get(day, carried)
        out[day] = carried
        day += timedelta(days=1)
    return out


def build_persona_snapshots(
    pid: str, out_dir: Path, synthetic: Path, every: int = 1, futures: int = N_FUTURES,
    seed: int = 42,
) -> int:  # fmt: skip
    """From scratch: the persona's database, its steps for every `every`-th replayable day, and its
    daily balances. Returns the number of steps."""
    if pid not in DEMO_PERSONAS:
        assert_development_only([pid])
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / f"{pid}.db"
    steps_path = out_dir / f"{pid}.jsonl"
    for old in (steps_path, steps_path.with_suffix(".meta.json")):
        old.unlink(missing_ok=True)
    _import_persona(pid, db_path, synthetic, seed)
    conn = connect_readonly(db_path)
    try:
        dates = available_dates(conn, pid)[::every]
        n = precompute(conn, pid, dates, steps_path, futures)
        balances = closing_balances(conn, pid)
    finally:
        conn.close()
    (out_dir / f"{pid}.balances.json").write_text(
        json.dumps({d.isoformat(): c for d, c in balances.items()}), encoding="utf-8"
    )
    return n


# --- Reading them (the page) ---


def available_personas(directory: Path) -> list[str]:
    """Personas with saved steps, demo personas first."""
    found = {p.name.removesuffix(".jsonl") for p in directory.glob("*.jsonl")}
    order = [*DEMO_PERSONAS, *MIX_PERSONAS]
    return [p for p in order if p in found] + sorted(found - set(order))


def load_steps(directory: Path, pid: str) -> list[dict[str, Any]]:
    path = directory / f"{pid}.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def load_balances(directory: Path, pid: str) -> dict[date, int]:
    raw = json.loads((directory / f"{pid}.balances.json").read_text(encoding="utf-8"))
    return {date.fromisoformat(d): c for d, c in raw.items()}


def whatif_extra(dollars: float, day: date) -> tuple[ExtraItem, ...]:
    """The what-if's one added item. The app's sign rule: positive = money in (a deposit),
    negative = money out (a purchase). Zero adds nothing."""
    cents = round(dollars * 100)
    if cents == 0:
        return ()
    return (ExtraItem(day, cents, "a deposit" if cents > 0 else "a purchase"),)


def moved(dates: list[date], current: date, days: int) -> date:
    """The saved date `days` away from `current` (back when negative): the first saved date at or
    past the target in that direction, at least one step away, and never outside the saved
    dates. Saved dates may be every day or every few days."""
    target = current + timedelta(days=days)
    if days >= 0:
        later = [d for d in dates if d > current]
        return next((d for d in later if d >= target), later[-1] if later else current)
    earlier = [d for d in dates if d < current]
    return next((d for d in reversed(earlier) if d <= target), earlier[0] if earlier else current)


@dataclass(frozen=True)
class AlertView:
    kind: str  # "warning" or "ok", for the callout
    tag: str  # "Possible shortfall" or "On track"
    sentence: str  # plain text
    status: str  # what the alert engine did on this date, in words


def alert_view(step: dict[str, Any]) -> AlertView:
    """The alert state on the step's date, in the Forecast page's words."""
    alert = step["alert"]
    buffer = _dollars(step["buffer_cents"])
    if alert["condition"]:
        sentence = alert["message"] or "Possible shortfall."
        if not alert["notified"]:
            status = "The same alert as before: nothing material changed, so it stays quiet."
        elif alert["reason"]:
            status = f"Alert shown: {alert['reason']}."
        else:
            status = "Alert shown."
        return AlertView("warning", "Possible shortfall", sentence, status)
    chance = step["shortfall_probability"]
    if chance > 0:
        sentence = (f"Only {chance:.0%} of simulated futures dip below the {buffer} buffer: not "
                    "enough to alert.")  # fmt: skip
    else:
        sentence = f"No simulated future dips below the {buffer} buffer in the next 30 days."
    status = "No alert on this date." if not alert["active"] else "An earlier alert is still open."
    return AlertView("ok", "On track", sentence, status)


def _dollars(cents: int) -> str:
    return f"-${-cents / 100:,.2f}" if cents < 0 else f"${cents / 100:,.2f}"


def _day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def evidence_lines(step: dict[str, Any]) -> list[str]:
    """The evidence behind an alert, as plain lines (empty when there is no alert)."""
    ev = step["alert"]["evidence"]
    if not ev:
        return []
    lines = [
        f"Lowest balance in the middle future: {_dollars(ev['low_cents'])} on "
        f"{_day(ev['low_day'])}.",
        f"Chance below the {_dollars(ev['buffer_cents'])} buffer: {ev['probability']:.0%} "
        f"(without irregular deposits: {ev['probability_without_deposits']:.0%}); median lowest "
        f"balance across the {ev['futures']} futures: {_dollars(ev['median_lowest_cents'])}.",
    ]
    for p in ev["largest_payments"]:
        lines.append(f"Payment before the low: {p['name'] or 'Payment'}, {_day(p['day'])}, "
                     f"{_dollars(p['cents'])} ({p['basis']}).")  # fmt: skip
    counts = ev["assumptions"]
    wording = ", ".join(f"{counts[k]} {k}" for k in ("detected", "confirmed", "entered")
                        if k in counts)  # fmt: skip
    lines.append(f"Placed payments: {wording or 'none'}.")
    for big in ev["big_events"]:
        lines.append(f"Set aside, not forecast: {big['group'] or 'big event'}, {big['kind']}, "
                     f"{_day(big['day'])}, {_dollars(big['cents'])}.")  # fmt: skip
    lines.append(f"Everyday spending about {_dollars(ev['spending_per_day_cents'])} a day "
                 f"({ev['method']}). Rule: {ev['rule']}.")  # fmt: skip
    return lines


def history_frame(balances: dict[date, int], as_of: date,
                  days: int = HISTORY_SHOWN_DAYS) -> pd.DataFrame:  # fmt: skip
    """The actual balance over the `days` up to `as_of` (nothing after it)."""
    rows = [(d, c / 100) for d, c in sorted(balances.items()) if as_of - timedelta(days) < d
            <= as_of]  # fmt: skip
    return pd.DataFrame(rows, columns=["date", "balance ($)"])


def realized_frame(balances: dict[date, int], as_of: date,
                   days: int = HORIZON_DAYS) -> pd.DataFrame:  # fmt: skip
    """What really happened in the `days` after `as_of` (for the replay only: the forecast never
    saw it)."""
    rows = [(d, c / 100) for d, c in sorted(balances.items()) if as_of <= d
            <= as_of + timedelta(days)]  # fmt: skip
    return pd.DataFrame(rows, columns=["date", "balance ($)"])


def step_chart(step: dict[str, Any], history: pd.DataFrame,
               realized: pd.DataFrame | None = None) -> go.Figure:  # fmt: skip
    """The actual balance up to the date, the 10th-90th band and the middle future from the
    date's closing balance, the buffer, and (if given) what really happened next."""
    as_of = date.fromisoformat(step["data_date"])
    anchor = step["start_balance_cents"] / 100
    x = [as_of, *(date.fromisoformat(d) for d in step["days"])]

    def path(cents: list[int]) -> list[float]:
        return [anchor, *(c / 100 for c in cents)]

    figure = go.Figure()
    if not history.empty:
        figure.add_scatter(x=history["date"], y=history["balance ($)"], mode="lines",
                           line_shape="hv", name="Actual balance", meta="recent")  # fmt: skip
    figure.add_scatter(x=x, y=path(step["p90"]), mode="lines", line_shape="hv", showlegend=False,
                       hoverinfo="skip", meta="band_edge")  # fmt: skip
    figure.add_scatter(x=x, y=path(step["p10"]), mode="lines", line_shape="hv", fill="tonexty",
                       name=f"Range of {step['futures']} simulated futures (10th to 90th)",
                       meta="band")  # fmt: skip
    figure.add_scatter(x=x, y=path(step["p50"]), mode="lines", line_shape="hv",
                       name="Middle future", meta="median")  # fmt: skip
    if realized is not None and not realized.empty:
        figure.add_scatter(
            x=realized["date"],
            y=realized["balance ($)"],
            mode="lines",
            line_shape="hv",
            name="What really happened",
            meta="realized",
        )
    figure.add_hline(y=0, line_dash="dot", name="zero")
    figure.add_hline(y=step["buffer_cents"] / 100, line_dash="dash",
                     annotation_text="safety buffer", name="buffer")  # fmt: skip
    return figure


def step_low(step: dict[str, Any]) -> tuple[date, int]:
    """The middle future's lowest point (day, cents)."""
    k = min(range(len(step["p50"])), key=lambda i: (step["p50"][i], i))
    return date.fromisoformat(step["days"][k]), step["p50"][k]


def scenario_at(db_path: Path, account: str, as_of: date, scenario: Scenario,
                futures: int = N_FUTURES) -> ScenarioResult:  # fmt: skip
    """A what-if at a replayed date: the forecast as of that date (read-only, rule zero), and the
    same futures with the scenario's changes."""
    conn = connect_readonly(db_path)
    try:
        forecast = build_forecast(conn, account, METHOD, as_of, None, HORIZON_DAYS, futures)
    finally:
        conn.close()
    if forecast is None:
        raise ValueError(f"no forecast as of {as_of}")
    return run_scenario(forecast, scenario)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m lookahead.timemachine_snapshots",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--only", nargs="*", default=None, help="persona ids")
    parser.add_argument("--every", type=int, default=1, help="every n-th day (default: every day)")
    parser.add_argument("--futures", type=int, default=N_FUTURES)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--synthetic", type=Path, default=None, help="folder of persona_A.csv...")
    args = parser.parse_args(argv)
    from lookahead.ui import synthetic_dir

    out = args.out or snapshot_dir()
    synthetic = args.synthetic or synthetic_dir()
    ids = args.only or [*DEMO_PERSONAS, *MIX_PERSONAS]
    for n, pid in enumerate(ids, start=1):
        steps = build_persona_snapshots(pid, out, synthetic, args.every, args.futures)
        print(f"[{n}/{len(ids)}] {pid}: {steps} dates", flush=True)
    print(f"saved in {out} (synthetic only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
