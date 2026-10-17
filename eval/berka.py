"""Berka (PKDD'99) as an account the forecaster can read: the adapter and the sanity check.

Method in DECISIONS.md (2026-10-17, experiment 2, "Berka: the choices I made from the gate's
counts"). This is "does it run and look sane", with counts only and no scores: no forecast is
compared with what happened.

    python -m eval.berka sanity --group primary           # the 200 primary development accounts
    python -m eval.berka sanity --group sensitivity       # the 200 sensitivity development accounts
    python -m eval.berka sanity --group primary --limit 5 # a quick look
    python -m eval.berka leakage                          # full database against one cut off

Only development accounts are ever read into a run (`assert_development_only`). The adapter turns
each account's rows into a database the forecaster reads like any other: koruna become cents at
5 cents per koruna (a scaling choice, not an exchange rate: it puts the amounts in the range the
forecaster's absolute amounts, such as big money from $500, were written for), descriptions come
from the bank's codes, the balance after each row is the running total, and the dip line "fell
below one month's outflow" is fixed once per account from the year before its first origin.
"""

import argparse
import json
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from eval.berka_gate import closing_balances, load
from eval.berka_split import assert_development_only, development_accounts
from lookahead.db import connect
from lookahead.forecast.path import DEFAULT_OPTIONS, build_forecast
from lookahead.forecast.spending import WEEKDAY_WEEKS
from lookahead.ingest.scotiabank import ParsedRow
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents

DATA = Path("data/berka")
REPORT = Path("docs/results/berka_sanity_2026-10-17.md")
CENTS_PER_KORUNA = 5  # a scaling choice so amounts match the forecaster's, not an exchange rate
N_ORIGINS = 40
STEP_DAYS = 7
HORIZON = 30
FUTURES = 100  # a sanity check, not a score
DIP_FRACTION = 1.0  # "fell below one month's outflow"; 0.5 is the sensitivity
DIP_YEAR = 365
FEE_CODES = ("SLUZBY", "SANKC. UROK")
EVERYDAY_OPERATIONS = ("VYBER", "VYBER KARTOU")
QUANTILES = (0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0)
SMALL_SHARE = 0.05  # a zero everyday estimate at more than this share of origins is flagged
SET_ASIDE = ("Large payment", "Pass-through")


# --- The adapter ---


def describe(row: pd.Series) -> tuple[str, str]:
    """(the export's Description, Sub-description) for one Berka row, in our vocabulary."""
    code = row["k_symbol"] if isinstance(row["k_symbol"], str) else ""
    operation = row["operation"] if isinstance(row["operation"], str) else ""
    bank = row["bank"] if isinstance(row["bank"], str) else ""
    partner = f"{bank} {int(row['account'])}".strip() if pd.notna(row["account"]) else bank
    if row["signed"] > 0:
        if operation == "VKLAD":
            return "deposit", "BERKA CASH DEPOSIT"
        if operation == "PREVOD Z UCTU":
            return "credit memo", f"BERKA IN {code or 'TRANSFER'} {partner}".strip()
        return "credit memo", "BERKA INTEREST"
    if code in FEE_CODES:
        return "service charge", "BERKA FEE"
    if operation in EVERYDAY_OPERATIONS or row["type"] == "VYBER":
        return "abm withdrawal", "BERKA CARD" if operation == "VYBER KARTOU" else "BERKA CASH"
    return "bill payment", f"BERKA OUT {code or 'TRANSFER'} {partner}".strip()


def to_cents(koruna: float) -> int:
    """Koruna to cents; a row that is not zero stays at least one cent."""
    cents = int(round(abs(koruna) * CENTS_PER_KORUNA))
    return max(cents, 1) if koruna else 0


def account_rows(frame: pd.DataFrame) -> list[ParsedRow]:
    """One account's rows as parsed export rows, newest first. The frame is that account's
    part of `eval.berka_gate.load` (sorted by day and transaction id)."""
    ordered = frame.sort_values(["day", "signed"], ascending=[True, False], kind="stable")
    first = ordered.iloc[0]
    balance = to_cents(float(first["balance"] - first["signed"])) * (
        1 if first["balance"] - first["signed"] >= 0 else -1
    )  # the opening balance (zero for nearly every account)
    built: list[tuple[date, int, int, str, str]] = []
    for _, row in ordered.iterrows():
        cents = to_cents(float(row["amount"])) * (1 if row["signed"] > 0 else -1)
        balance += cents
        description, sub = describe(row)
        built.append((row["day"].date(), cents, balance, description, sub))
    out = []
    for position, (day, cents, after, description, sub) in enumerate(reversed(built), start=1):
        out.append(ParsedRow(day, cents, after, description, sub, position, position + 1))
    return out


def origin_dates(frame: pd.DataFrame) -> list[date]:
    """40 weekly origins, the last 30 days before the account's last posting (so every origin has
    30 observed days after it), oldest first."""
    last = frame["day"].max().date() - timedelta(days=HORIZON)
    return [last - timedelta(days=STEP_DAYS * k) for k in range(N_ORIGINS - 1, -1, -1)]


def dip_line_cents(frame: pd.DataFrame, first_origin: date, fraction: float = DIP_FRACTION) -> int:
    """ "Fell below one month's outflow": `fraction` times the median 30-day outflow (the 30 days
    ending on each day) over the 365 days up to the first origin. Rows after the first origin are
    never read. 0 if there was no outflow."""
    out = frame[(frame["signed"] < 0) & (frame["day"] <= pd.Timestamp(first_origin))]
    if out.empty:
        return 0
    days = pd.date_range(frame["day"].min(), pd.Timestamp(first_origin), freq="D")
    daily = out.groupby("day")["amount"].sum().reindex(days).fillna(0.0)
    month = daily.rolling(30, min_periods=30).sum().dropna()
    window = month[month.index > pd.Timestamp(first_origin) - pd.Timedelta(days=DIP_YEAR)]
    window = window[window > 0]
    if window.empty:
        return 0
    return int(round(float(np.median(window)) * CENTS_PER_KORUNA * fraction))


def build_db(frame: pd.DataFrame, account: int, path: Path, line_cents: int) -> sqlite3.Connection:
    """The account in a database of its own at `path`, with the dip line as its safety buffer."""
    if path.exists():
        path.unlink()
    conn = connect(path)
    store_exports(conn, str(account), [(f"berka-{account}", account_rows(frame))],
                  datetime(2026, 10, 17))  # fmt: skip
    set_safety_buffer_cents(conn, line_cents)
    return conn


# --- The sanity check ---


def origin_counts(conn: sqlite3.Connection, account: int, origin: date) -> dict[str, Any]:
    """One origin: did the forecast run, and what is it made of (cents, counts only)."""
    try:
        f = build_forecast(conn, str(account), "weekday", origin, None, HORIZON, FUTURES,
                           options=DEFAULT_OPTIONS)  # fmt: skip
    except Exception as error:  # noqa: BLE001 - a crash is a count, not a stop
        return {"ok": False, "error": type(error).__name__}
    if f is None:
        return {"ok": False, "error": "no forecast"}
    scheduled = [d.scheduled_cents for d in f.days]
    return {
        "ok": True,
        "recurring_out": -sum(min(s, 0) for s in scheduled),
        "recurring_in": sum(max(s, 0) for s in scheduled),
        "everyday": sum(d.spending_cents for d in f.days),
        "fees": sum(d.fees_cents for d in f.days),
        "items": len(f.items),
        "series": sum(1 for s in f.series if s.active and s.status != "rejected"),
        "has_income": f.has_income,
        "arrivals_in": len(f.arrivals.in_sizes), "arrivals_out": len(f.arrivals.out_sizes),
        "large_deposits": len(f.large_deposits), "big_events": len(f.big_events),
        "start_below_line": f.start.balance_cents < f.buffer_cents,
    }  # fmt: skip


def run_account(args: tuple[int, pd.DataFrame, Path]) -> dict[str, Any]:
    """All 40 origins of one development account. Saved as it finishes (resumable)."""
    account, frame, out_dir = args
    assert_development_only([account])
    path = out_dir / f"{account}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    origins = origin_dates(frame)
    line = dip_line_cents(frame, origins[0])
    db = out_dir / "work" / f"{account}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = build_db(frame, account, db, line)
    try:
        rows = [{"origin": o.isoformat(), **origin_counts(conn, account, o)} for o in origins]
    finally:
        conn.close()
        db.unlink(missing_ok=True)
    result = {"account": account, "line_cents": line, "origins": rows}
    path.write_text(json.dumps(result), encoding="utf-8")
    return result


def zero_everyday_reason(conn: sqlite3.Connection, account: int, origin: date) -> dict[str, Any]:
    """Why the everyday spending estimate at `origin` is zero: what the money-out rows of the last
    8 weeks (the window of the weekday baseline) turned into. Counts only."""
    f = build_forecast(conn, str(account), "weekday", origin, None, HORIZON, FUTURES,
                       options=DEFAULT_OPTIONS)  # fmt: skip
    if f is None:
        return {"reason": "no forecast"}
    first = origin - timedelta(days=WEEKDAY_WEEKS * 7 - 1)
    rows = conn.execute(
        "SELECT posted_date, amount_cents, description, fingerprint FROM transactions"
        " WHERE account = ? AND posted_date BETWEEN ? AND ? AND amount_cents < 0",
        (str(account), first.isoformat(), origin.isoformat()),
    ).fetchall()  # fmt: skip
    in_series = {fp for s in f.series if s.status != "rejected" for fp in s.evidence}
    aside = Counter((e.day, -e.cents) for e in f.big_events if e.group in SET_ASIDE and e.cents < 0)
    return classify_rows(rows, in_series, aside)


def classify_rows(
    rows: list[tuple[str, int, str, str]], in_series: set[str], aside: Counter[tuple[date, int]]
) -> dict[str, Any]:
    """Sort the money-out rows of the window (day, cents, description, fingerprint) into bank
    fees, rows in a recurring series, rows set aside as big one-time money or a pass-through, and
    the rest (counted as everyday spending), and give the reason an estimate of zero follows."""
    aside = Counter(aside)
    n: Counter[str] = Counter()
    for day, cents, description, fingerprint in rows:
        key = (date.fromisoformat(day), -cents)
        if description == "service charge":
            n["fee"] += 1
        elif fingerprint in in_series:
            n["series"] += 1
        elif aside[key] > 0:
            aside[key] -= 1
            n["set_aside"] += 1
        else:
            n["counted"] += 1
    if not rows:
        reason = "no money-out rows in the last 8 weeks"
    elif n["counted"]:
        reason = "rows were counted but net to zero or less (returns)"
    elif n["set_aside"]:
        reason = "every money-out row was set aside as big one-time money or a pass-through"
    elif n["series"]:
        reason = "every money-out row belongs to a recurring series"
    else:
        reason = "only bank fees"
    return {
        "reason": reason, "rows_8w": len(rows),
        **{k: n[k] for k in ("fee", "series", "set_aside", "counted")},
    }  # fmt: skip


def diagnose_zero(args: tuple[int, pd.DataFrame, Path]) -> dict[str, Any]:
    """Add the reasons for the zero everyday estimates of one account to its saved result (once)."""
    account, frame, out_dir = args
    assert_development_only([account])
    path = out_dir / f"{account}.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    zeros = [o["origin"] for o in result["origins"] if o["ok"] and o["everyday"] == 0]
    if not zeros or "zero_reasons" in result:
        return result
    db = out_dir / "work" / f"{account}-zero.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = build_db(frame, account, db, result["line_cents"])
    try:
        result["zero_reasons"] = {
            o: zero_everyday_reason(conn, account, date.fromisoformat(o)) for o in zeros
        }
    finally:
        conn.close()
        db.unlink(missing_ok=True)
    path.write_text(json.dumps(result), encoding="utf-8")
    return result


def drift_koruna(frame: pd.DataFrame, closes: pd.DataFrame) -> float:
    """The largest distance, over the account's days, between the adapter's closing balance (the
    running total) and the file's own closing balance, in koruna."""
    ordered = frame.sort_values(["day", "signed"], ascending=[True, False], kind="stable")
    first = ordered.iloc[0]
    running = (ordered["signed"].cumsum() + (first["balance"] - first["signed"])).to_numpy()
    ours = pd.Series(running, index=ordered["day"].to_numpy()).groupby(level=0).last()
    theirs = closes.set_index("day")["close"]
    return float((ours - theirs.reindex(ours.index)).abs().max())


def quantile_line(values: list[float], fmt: str = "{:.1f}") -> str:
    q = np.quantile(values, QUANTILES) if values else []
    return " / ".join(fmt.format(x) for x in q) if values else "-"


def zero_section(results: list[dict[str, Any]], ok: list[dict[str, Any]]) -> list[str]:
    """The origins whose everyday spending estimate is zero, and why (counts only)."""
    zero = [o for o in ok if o["everyday"] == 0]
    share = len(zero) / len(ok) if ok else 0.0
    with_zero = [r for r in results if any(o["ok"] and o["everyday"] == 0 for o in r["origins"])]
    reasons: Counter[str] = Counter()
    for r in with_zero:
        for info in r.get("zero_reasons", {}).values():
            reasons[info["reason"]] += 1
    explained = sum(reasons.values())
    verdict = "FLAGGED" if share > SMALL_SHARE else "small"
    mostly_zero = sum(
        sum(o["ok"] and o["everyday"] == 0 for o in r["origins"]) > N_ORIGINS / 2 for r in results
    )
    lines = [
        "### Origins whose everyday spending estimate is zero", "",
        f"- {len(zero):,} of {len(ok):,} origins ({share:.1%}), in {len(with_zero)} of "
        f"{len(results)} accounts. A share above {SMALL_SHARE:.0%} is flagged; this one is "
        f"**{verdict}**.",
        f"- Accounts where more than half of the origins are zero: {mostly_zero}.",
    ]  # fmt: skip
    if explained < len(zero):
        lines.append(f"- Reasons not worked out yet for {len(zero) - explained} of them.")
    lines += ["", "| why the estimate is zero | origins |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in reasons.most_common()]
    rows = [i["rows_8w"] for r in with_zero for i in r.get("zero_reasons", {}).values()]
    spread = quantile_line([float(x) for x in rows], "{:.0f}")
    lines += [
        "",
        "Money-out rows in the 8 weeks before those origins (min / 10% / 25% / median / 75% / "
        f"90% / max): {spread}.",
        "",
    ]
    return lines


def summarize(results: list[dict[str, Any]], drift: list[float], group: str) -> list[str]:
    origins = [o for r in results for o in r["origins"]]
    ok = [o for o in origins if o["ok"]]
    full = [r for r in results if all(o["ok"] for o in r["origins"])]
    errors: dict[str, int] = {}
    for o in origins:
        if not o["ok"]:
            errors[o["error"]] = errors.get(o["error"], 0) + 1
    lines = [
        f"## {group} development accounts",
        "",
        f"- Accounts: {len(results)}. Accounts where all {N_ORIGINS} origins ran: {len(full)} "
        f"({len(full) / len(results):.1%}); the gate needs 90% or more.",
        f"- Origins: {len(origins):,}; a forecast came back at {len(ok):,} "
        f"({len(ok) / len(origins):.1%}). Crashes or no forecast: "
        + (", ".join(f"{k} {v}" for k, v in sorted(errors.items())) if errors else "none")
        + ".",
        f"- Origins that started below the dip line: "
        f"{sum(o['start_below_line'] for o in ok):,} of {len(ok):,}.",
        "",
        "How a forecast is made up over its 30 days (origins where a forecast ran; quantiles are "
        "min / 10% / 25% / median / 75% / 90% / max):",
        "",
        "| | value |",
        "|---|---|",
    ]
    totals = [(o["recurring_out"], o["everyday"] + o["fees"]) for o in ok]
    shares = [a / (a + b) for a, b in totals if a + b > 0]
    lines += [
        "| recurring placements' share of the money going out | "
        f"{quantile_line(shares, '{:.2f}')} |",
        f"| origins with no recurring outflow placed | {sum(a == 0 for a, _ in totals):,} of "
        f"{len(ok):,} |",
        f"| origins with no recurring income placed | "
        f"{sum(o['recurring_in'] == 0 for o in ok):,} of {len(ok):,} |",
        f"| origins whose forecast has an income series | {sum(o['has_income'] for o in ok):,} "
        f"of {len(ok):,} |",
        f"| recurring series detected per origin | "
        f"{quantile_line([float(o['series']) for o in ok], '{:.0f}')} |",
        "| scheduled items per origin | "
        f"{quantile_line([float(o['items']) for o in ok], '{:.0f}')} |",
        f"| origins with deposit arrivals drawn (keeps coming) | "
        f"{sum(o['arrivals_in'] > 0 for o in ok):,} of {len(ok):,} |",
        f"| origins with payment arrivals drawn | {sum(o['arrivals_out'] > 0 for o in ok):,} of "
        f"{len(ok):,} |",
        f"| origins with a big deposit or payment set aside | "
        f"{sum(o['big_events'] > 0 for o in ok):,} of {len(ok):,} |",
        "",
        *zero_section(results, ok),
        "The adapter's closing balance (the running total) against the file's own closing balance, "
        "the largest distance over each account's days, in koruna (min / 10% / 25% / median / "
        f"75% / 90% / max over accounts): {quantile_line(drift)}.",
        "",
    ]
    return lines


def sanity(group: str, limit: int | None, jobs: int, directory: Path) -> int:
    ids = development_accounts(group)[: limit or None]
    assert_development_only(ids)
    t = load(directory)
    t = t[t["account_id"].isin(ids)].reset_index(drop=True)  # only development rows are kept
    closes = closing_balances(t)
    frames = {a: g.reset_index(drop=True) for a, g in t.groupby("account_id")}
    drift = [drift_koruna(frames[a], closes[closes["account_id"] == a]) for a in ids]
    out_dir = DATA / "runs" / f"sanity-{group}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(run_account, [(a, frames[a], out_dir) for a in ids]))
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(diagnose_zero, [(a, frames[a], out_dir) for a in ids]))
    lines = summarize(results, drift, group)
    print("\n".join(lines))
    if limit is None:
        existing = REPORT.read_text(encoding="utf-8") if REPORT.exists() else ""
        head = (
            "# Berka: does it run and look sane (development accounts only, nothing scored)\n\n"
            "Made with `python -m eval.berka sanity`. Method in DECISIONS.md (2026-10-17). Counts "
            'only: no forecast is compared with what happened. The dip line is called "fell '
            "below one month's outflow\" and was chosen from dip counts only, not from any "
            "forecast result. Held-out accounts are not read.\n\n"
        )
        sections = [s for s in existing.split("\n## ")[1:] if not s.startswith(f"{group} ")]
        body = "\n## ".join([*sections, "\n".join(lines)])
        REPORT.write_text(head + ("## " + body if sections else "\n".join(lines)) + "\n",
                          encoding="utf-8")  # fmt: skip
        print(f"written: {REPORT}")
    return 0


# --- The truncated-future (leakage) test ---


def forecast_fingerprint(conn: sqlite3.Connection, account: int, origin: date) -> Any:
    f = build_forecast(conn, str(account), "weekday", origin, None, HORIZON, FUTURES,
                       keep_paths=True, options=DEFAULT_OPTIONS)  # fmt: skip
    if f is None or f.simulation is None:
        return None
    s = f.simulation
    return (s.p10, s.p50, s.p90, s.shortfall_probability, s.median_lowest_cents, s.paths,
            [(i.day, i.cents, i.series_key) for i in f.items],
            [(d.day, d.balance_cents) for d in f.days],
            f.arrivals.in_rate, f.arrivals.out_rate)  # fmt: skip


def leakage(frame: pd.DataFrame, account: int, origin: date, work: Path) -> bool:
    """True if the forecast at `origin` is identical whether or not the database holds the rows
    after it (the rows after the origin are cut off in the second database)."""
    line = dip_line_cents(frame, origin_dates(frame)[0])
    full = build_db(frame, account, work / f"{account}-full.db", line)
    cut = build_db(frame[frame["day"] <= pd.Timestamp(origin)], account,
                   work / f"{account}-cut.db", line)  # fmt: skip
    try:
        return forecast_fingerprint(full, account, origin) == forecast_fingerprint(
            cut, account, origin
        )
    finally:
        full.close()
        cut.close()


def leakage_run(accounts: int, directory: Path) -> int:
    ids = development_accounts("primary")[:accounts]
    assert_development_only(ids)
    t = load(directory)
    t = t[t["account_id"].isin(ids)]
    work = DATA / "runs" / "leakage"
    work.mkdir(parents=True, exist_ok=True)
    checked = same = 0
    for a, g in t.groupby("account_id"):
        frame = g.reset_index(drop=True)
        origins = origin_dates(frame)
        for origin in (origins[0], origins[len(origins) // 2], origins[-1]):
            checked += 1
            same += leakage(frame, int(a), origin, work)
    print(f"truncated-future test: {same} of {checked} forecasts identical "
          f"({accounts} development accounts, 3 origins each)")  # fmt: skip
    return 0 if same == checked else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.berka",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=["sanity", "leakage"])
    parser.add_argument("--group", choices=["primary", "sensitivity"], default="primary")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--dir", type=Path, default=DATA)
    args = parser.parse_args(argv)
    if args.command == "leakage":
        return leakage_run(args.limit or 10, args.dir)
    return sanity(args.group, args.limit, args.jobs, args.dir)


if __name__ == "__main__":
    sys.exit(main())
