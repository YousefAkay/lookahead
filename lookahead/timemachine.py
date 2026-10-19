"""The Time Machine back end: the forecast and its alert state as of any past date.

For a date D it builds the forecast the app would have shown on D, with only what was available
then (rule zero): rows posted on or before D, recurring series re-detected from those rows,
decisions and planned items made by D, and an alert record made only from the looks before it.
Replaying many dates in order shows how alerts merge into episodes and stay quiet between.

What it deliberately does not do:
- It never writes to the database it reads (a read-only connection works), and it keeps its own
  alert record in memory, so replaying never touches the live alerts, dismissals or episodes.
- It does not replay dismissals: the replay shows the alert as if nothing had been dismissed.
- The app stores one safety buffer, not a dated history, so every date uses the buffer as stored.
- The forecast does not use the categorizer, so there is nothing to retrain along the way; a
  model that does would have to be retrained inside the replay on rows up to each date.

Dates must be given in increasing order (the alert record depends on the order).

A saved step holds counts, cents and dates only: no account name, no item or series names, no
series keys. Steps made from a database in data/private are saved in data/private only, and a
saved file carries the format and logic version it was made with (a resume across versions is
refused).
"""

import json
import re
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from lookahead.alerts import (
    Episode,
    RunResult,
    Snapshot,
    active_alert,
    alert_message,
    build_evidence,
    record_snapshot,
    snapshot_of,
)
from lookahead.db import SCHEMA
from lookahead.forecast.path import (
    Forecast,
    build_forecast,
    forecaster_settings,
    settings_differences,
)
from lookahead.forecast.simulate import N_FUTURES
from lookahead.forecast.start import HORIZON_DAYS
from lookahead.merchants import is_private_path
from lookahead.recurring_store import REPLAY_VERSION

MIN_HISTORY = timedelta(weeks=8)  # the weekday baseline needs eight weeks behind the date
METHOD = "weekday"
STEP_FORMAT = 2  # 2: the saved evidence keeps no account name, item names or series keys


@dataclass(frozen=True)
class Step:
    """One replayed date."""

    as_of: date
    forecast: Forecast
    snapshot: Snapshot | None  # what the alert rule said (None: it did not alert)
    alert: RunResult  # whether it was worth telling me about, and why
    episode: Episode | None  # the open, undismissed episode on that date, with its evidence
    history_days: int  # rows before the date span this many days
    series_count: int  # recurring series found from those rows


def _bounds(conn: sqlite3.Connection, account: str) -> tuple[date, date] | None:
    first, last = conn.execute(
        "SELECT MIN(posted_date), MAX(posted_date) FROM transactions WHERE account = ?",
        (account,),
    ).fetchone()
    return None if first is None else (date.fromisoformat(first), date.fromisoformat(last))


def available_dates(conn: sqlite3.Connection, account: str) -> list[date]:
    """Every day that can be replayed: from eight weeks after the first row to the last row."""
    bounds = _bounds(conn, account)
    if bounds is None or bounds[0] + MIN_HISTORY > bounds[1]:
        return []
    day, last = bounds[0] + MIN_HISTORY, bounds[1]
    return [day + timedelta(days=k) for k in range((last - day).days + 1)]


def _scratch() -> sqlite3.Connection:
    """An in-memory alert record: the replay's own, never the live one."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(SCHEMA)
    return conn


def _check_dates(conn: sqlite3.Connection, account: str, dates: Sequence[date]) -> None:
    bounds = _bounds(conn, account)
    if bounds is None:
        raise ValueError("no rows for this account")
    for earlier, later in zip(dates, dates[1:], strict=False):
        if later <= earlier:
            raise ValueError("dates must be in increasing order")
    for day in dates:
        if day < bounds[0] + MIN_HISTORY:
            raise ValueError(f"{day} has less than eight weeks of history behind it")
        if day > bounds[1]:
            raise ValueError(f"{day} is after the last row")


def replay(
    conn: sqlite3.Connection, account: str, dates: Sequence[date], futures: int = N_FUTURES,
    method: str = METHOD, key: bytes | None = None, alert_conn: sqlite3.Connection | None = None,
) -> Iterator[Step]:  # fmt: skip
    """The steps for `dates`, in order, one at a time (a step holds a whole forecast, so they are
    handed out and dropped rather than kept). `alert_conn` carries an alert record to continue
    from (`precompute` uses it to resume); by default a new empty one."""
    _check_dates(conn, account, dates)
    return _steps(conn, account, list(dates), futures, method, key, alert_conn or _scratch())


def _steps(
    conn: sqlite3.Connection, account: str, dates: list[date], futures: int, method: str,
    key: bytes | None, alerts: sqlite3.Connection,
) -> Iterator[Step]:  # fmt: skip
    first = _bounds(conn, account)
    assert first is not None
    for day in dates:
        forecast = build_forecast(conn, account, method, day, key, HORIZON_DAYS, futures)
        if forecast is None or forecast.simulation is None:
            raise ValueError(f"no forecast as of {day}")
        snapshot = snapshot_of(forecast)
        result = record_snapshot(
            alerts, account, snapshot, day, build_evidence(forecast) if snapshot else None
        )
        yield Step(
            as_of=day, forecast=forecast, snapshot=snapshot, alert=result,
            episode=active_alert(alerts, account),
            history_days=(day - first[0]).days + 1,
            series_count=sum(1 for s in forecast.series if s.active and s.status != "rejected"),
        )  # fmt: skip


def _saved_item(item: dict[str, Any]) -> dict[str, Any]:
    return {"day": item["day"], "cents": item["cents"], "basis": item["basis"]}


def saved_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """The alert evidence as it may be saved: counts, cents and dates only. The account name, the
    names and series keys of placed payments and the group names of big events are left out."""
    out = {k: v for k, v in evidence.items() if k != "account"}
    out["largest_payments"] = [_saved_item(i) for i in evidence["largest_payments"]]
    out["assumption_items"] = [_saved_item(i) for i in evidence["assumption_items"]]
    out["big_events"] = [{k: v for k, v in e.items() if k != "group"}
                         for e in evidence["big_events"]]  # fmt: skip
    return out


def step_to_dict(step: Step) -> dict[str, Any]:
    """A step as plain data, to save for a demo. It says which date the data runs to, and holds no
    text from the data (no account name, no merchant or series names, no series keys)."""
    forecast, sim = step.forecast, step.forecast.simulation
    assert sim is not None
    snap = step.snapshot
    return {
        "as_of": step.as_of.isoformat(),
        "data_date": forecast.start.data_last_updated.isoformat(),
        "futures": sim.futures,
        "buffer_cents": forecast.buffer_cents,
        "start_balance_cents": forecast.start.balance_cents,
        "days": [d.isoformat() for d in sim.days],
        "p10": list(sim.p10), "p50": list(sim.p50), "p90": list(sim.p90),
        "point": [d.balance_cents for d in forecast.days],
        "shortfall_probability": sim.shortfall_probability,
        "median_lowest_cents": sim.median_lowest_cents,
        "n_items": len(forecast.items),
        "has_income": forecast.has_income,
        "history_days": step.history_days,
        "n_series": step.series_count,
        "alert": {
            "condition": snap is not None,
            "notified": step.alert.notified,
            "reason": step.alert.reason,
            "episode_id": step.alert.episode_id,
            "active": step.episode is not None,
            "message": alert_message(snap) if snap is not None else None,
            "snapshot": None if snap is None else {
                "data_date": snap.data_date.isoformat(), "low_day": snap.low_day.isoformat(),
                "low_cents": snap.low_cents, "probability": snap.probability,
                "buffer_cents": snap.buffer_cents,
                "median_lowest_cents": snap.median_lowest_cents,
            },
            "evidence": None if snap is None else saved_evidence(build_evidence(forecast)),
        },
    }  # fmt: skip


def _restore_alerts(account: str, saved: list[dict[str, Any]]) -> sqlite3.Connection:
    """The alert record as it stood after the saved steps, rebuilt from them (no forecasts)."""
    alerts = _scratch()
    for record in saved:
        a = record["alert"]
        raw = a["snapshot"]
        snap = None if raw is None else Snapshot(
            date.fromisoformat(raw["data_date"]), date.fromisoformat(raw["low_day"]),
            raw["low_cents"], raw["probability"], raw["buffer_cents"], raw["median_lowest_cents"],
        )  # fmt: skip
        record_snapshot(alerts, account, snap, date.fromisoformat(record["as_of"]), a["evidence"])
    return alerts


SYNTHETIC_ACCOUNT = re.compile(r"synthetic-[A-Z]|v2L?-[A-Z]+-\d{2}")  # the generator's own names


def _is_private_db(conn: sqlite3.Connection) -> bool:
    """True if this database may hold real data: its file is inside data/private, or any of its
    accounts is not one of the generator's own (so a copy of a private database outside
    data/private is still treated as private)."""
    files = [row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"]
    if files and files[0] and is_private_path(Path(files[0])):
        return True
    accounts = [row[0] for row in conn.execute("SELECT DISTINCT account FROM transactions")]
    return any(not SYNTHETIC_ACCOUNT.fullmatch(a) for a in accounts)


def precompute(
    conn: sqlite3.Connection, account: str, dates: Sequence[date], out_path: Path,
    futures: int = N_FUTURES, method: str = METHOD, key: bytes | None = None,
) -> int:  # fmt: skip
    """Save a step for each date as one JSON line, resuming where an earlier run stopped (dates
    already saved are skipped, and the alert record is rebuilt from them). Returns how many were
    newly made. New dates must come after the saved ones.

    Steps of a database in data/private are saved in data/private only. The meta file records
    the settings and the format and logic versions; saved steps from another version are never
    continued."""
    if _is_private_db(conn) and not is_private_path(out_path):
        raise ValueError("steps of a private database are saved in data/private/ only")
    settings = {"futures": futures, "method": method, "format": STEP_FORMAT,
                "logic": REPLAY_VERSION, "forecaster": forecaster_settings()}  # fmt: skip
    meta_path = out_path.with_suffix(".meta.json")
    if meta_path.exists():
        saved = json.loads(meta_path.read_text(encoding="utf-8"))
        if (saved.get("format"), saved.get("logic")) != (STEP_FORMAT, REPLAY_VERSION):
            raise ValueError("these snapshots were made with an older version: make them again")
        if saved != settings:
            raise ValueError(
                "these snapshots were made with different settings ("
                + "; ".join(settings_differences(saved, settings))
                + "): they are never continued with other settings; make them again in a new file"
            )
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(settings), encoding="utf-8")
    saved = []
    if out_path.exists():
        saved = [json.loads(x) for x in out_path.read_text(encoding="utf-8").splitlines() if x]
    done = {r["as_of"] for r in saved}
    todo = [d for d in dates if d.isoformat() not in done]
    if saved and todo and todo[0].isoformat() < saved[-1]["as_of"]:
        raise ValueError("new dates must come after the saved ones")
    if not todo:
        return 0
    _check_dates(conn, account, todo)
    alerts = _restore_alerts(account, saved)
    with out_path.open("a", encoding="utf-8") as out:
        for step in replay(conn, account, todo, futures, method, key, alerts):
            out.write(json.dumps(step_to_dict(step)) + "\n")
            out.flush()
    return len(todo)
