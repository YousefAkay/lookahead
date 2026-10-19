"""Move my recurring decisions onto stable series ids (week 5, item 1).

    python -m lookahead.identity report  --db data/private/db/lookahead.db
    python -m lookahead.identity dry-run --db data/private/db/lookahead.db
    python -m lookahead.identity apply   --db data/private/db/lookahead.db [--reattach]
    python -m lookahead.identity check   --db data/private/db/lookahead.db --backup FILE

Everything printed is a count. `report` and `dry-run` write nothing (the dry run works on an
in-memory copy). `apply` first writes a byte copy of the database next to it
(`<name>.bak-YYYYMMDD-HHMMSS`), checks the copy, and stops without touching the database if that
fails; then it changes the database in ONE transaction, checks that no decision was lost, and
commits only if the check passes (otherwise it rolls back and the file is unchanged).

A decision made before ids existed names a series by its key. The migration:
- gives each such decision the id of the series that key names today, and remembers that
  series' payments with the id (`series_members`), so the id then survives a moving amount;
- keeps every decision whose key names no series today (an orphan), as it is, never deleting it;
- re-attaches orphans to a series of the same family only with `--reattach`, and only when
  exactly one series of that family exists today (more than one is ambiguous and is left).
"""

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lookahead.db import SCHEMA
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key
from lookahead.recurring_store import Decision, Stored, load_decisions, load_members, series_as_of
from lookahead.series_identity import family_of, is_id

MEMBERS_DDL = re.search(r"CREATE TABLE IF NOT EXISTS series_members \(.*?\);", SCHEMA, re.S).group(
    0
)  # type: ignore[union-attr]


class IdentityCheckError(Exception):
    """The before-and-after check failed: the change must not be kept."""


@dataclass
class Plan:
    """What the migration would write. `moves`: decision_id -> (new series_key, new details)."""

    moves: dict[int, tuple[str, str | None]] = field(default_factory=dict)
    members: list[tuple[str, str, str, str]] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)


def _accounts(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT account FROM recurring_decisions ORDER BY account")
    return [r[0] for r in rows]


def _is_new_style(key: str) -> bool:
    return is_id(key) or key.startswith("user|")


def _lookup(series: Sequence[Stored]) -> dict[str, Stored]:
    """A key that names a series today -> that series (a key absorbed into a longer history too)."""
    out: dict[str, Stored] = {}
    for s in series:
        if s.detected_now:
            out[s.key] = s
            for old in s.continues:
                out.setdefault(old, s)
    return out


def make_plan(conn: sqlite3.Connection, key: bytes | None, reattach: bool) -> Plan:
    """Read-only: what each decision row would become."""
    plan = Plan()
    planned: set[str] = set()  # fingerprints already given an id in this plan
    for account in _accounts(conn):
        plan.counts["accounts"] += 1
        series, _ = series_as_of(conn, account, None, key)
        today = _lookup(series)
        remembered = load_members(conn, account)
        targets: dict[str, str] = {}  # new id -> the old key that first moved to it
        for d in load_decisions(conn, account):
            plan.counts["decisions"] += 1
            previous = d.details.get("previous") if d.action == "continue" else None
            new_details = None
            if isinstance(previous, str) and not _is_new_style(previous) and previous in today:
                new_details = json.dumps({**d.details, "previous": today[previous].id})
            if _is_new_style(d.key):
                plan.counts["decisions_by_id_already"] += 1
                if new_details is not None:
                    plan.moves[d.decision_id] = (d.key, new_details)
                continue
            s = today.get(d.key)
            if s is None:
                family = family_of(d.key)
                same_family = {
                    x.id: x for x in series if x.detected_now and family_of(x.key) == family
                }
                if len(same_family) == 1:
                    plan.counts["orphans_reattachable"] += 1
                    s = next(iter(same_family.values())) if reattach else None
                    plan.counts["orphans_reattached"] += s is not None
                elif same_family:
                    plan.counts["orphans_ambiguous"] += 1
                else:
                    plan.counts["orphans_without_candidate"] += 1
            else:
                plan.counts["decisions_moved_to_id"] += 1
            if s is None:
                plan.counts["orphans_kept_as_they_are"] += 1
                if new_details is not None:
                    plan.moves[d.decision_id] = (d.key, new_details)
                continue
            if s.id in targets and targets[s.id] != d.key:
                plan.counts["collisions"] += 1  # two old keys now name one series
            targets.setdefault(s.id, d.key)
            plan.moves[d.decision_id] = (s.id, new_details)
            for fp in s.evidence:
                if fp not in remembered and fp not in planned:
                    planned.add(fp)
                    plan.members.append((account, fp, s.id, d.decided_at.isoformat()))
    plan.counts["members_added"] = len(plan.members)
    return plan


def _placed(s: Stored) -> bool:
    return s.status != "rejected" and s.active


def _signature(s: Stored) -> tuple[object, ...]:
    return (s.cadence, s.expected_cents, s.next_expected, s.anchor)


def _resolves(d: Decision, series: Sequence[Stored]) -> bool:
    if d.action == "add" or d.key.startswith("user|"):
        return True
    names = {s.key for s in series} | {s.id for s in series}
    names |= {k for s in series for k in s.continues}
    return d.key in names or (d.action == "confirm" and "cadence" in d.details)


def measure(conn: sqlite3.Connection, key: bytes | None) -> dict[str, object]:
    """The state the checks and the counts are read from: series and decisions that apply."""
    series: dict[str, Stored] = {}
    applying = 0
    for account in _accounts(conn):
        found, _ = series_as_of(conn, account, None, key)
        series |= {f"{account}|{s.key}": s for s in found}
        applying += sum(_resolves(d, found) for d in load_decisions(conn, account))
    return {"series": series, "applying": applying}


def _rows(conn: sqlite3.Connection) -> dict[int, tuple[str, str, str, str]]:
    rows = conn.execute(
        "SELECT decision_id, account, action, decided_at, details FROM recurring_decisions"
    )
    return {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}


def execute(conn: sqlite3.Connection, plan: Plan) -> None:
    """Write the plan. Inside the caller's transaction; commits nothing."""
    conn.execute(MEMBERS_DDL)
    conn.executemany(
        "INSERT OR IGNORE INTO series_members (account, fingerprint, series_id, added_on)"
        " VALUES (?, ?, ?, ?)", plan.members,
    )  # fmt: skip
    for decision_id, (series_key, details) in plan.moves.items():
        if details is None:
            conn.execute(
                "UPDATE recurring_decisions SET series_key = ? WHERE decision_id = ?",
                (series_key, decision_id),
            )
        else:
            conn.execute(
                "UPDATE recurring_decisions SET series_key = ?, details = ? WHERE decision_id = ?",
                (series_key, details, decision_id),
            )


def run_migration(
    conn: sqlite3.Connection, key: bytes | None, reattach: bool = False
) -> dict[str, int]:
    """Plan, write (inside the open transaction), check, and return the counts. Raises
    IdentityCheckError if a decision would be lost; the caller then rolls back."""
    before_rows = _rows(conn)
    before = measure(conn, key)
    plan = make_plan(conn, key, reattach)
    execute(conn, plan)
    after_rows = _rows(conn)
    after = measure(conn, key)
    if set(before_rows) != set(after_rows):
        raise IdentityCheckError("a decision row was added or removed")
    for decision_id, old in before_rows.items():
        new = after_rows[decision_id]
        same_details = old[3] == new[3] or (
            old[1] == "continue" and _without(old[3], "previous") == _without(new[3], "previous")
        )
        if old[:3] != new[:3] or not same_details:
            raise IdentityCheckError(f"decision {decision_id} changed more than its series key")
    if after["applying"] < before["applying"]:  # type: ignore[operator]
        raise IdentityCheckError(
            f"decisions that apply fell from {before['applying']} to {after['applying']}"
        )
    counts = dict(plan.counts)
    old_series, new_series = before["series"], after["series"]
    assert isinstance(old_series, dict) and isinstance(new_series, dict)
    counts["series_before"], counts["series_after"] = len(old_series), len(new_series)
    counts["decisions_applying_before"] = before["applying"]  # type: ignore[assignment]
    counts["decisions_applying_after"] = after["applying"]  # type: ignore[assignment]
    counts["forecast_series_removed"] = sum(
        _placed(s) and not (k in new_series and _placed(new_series[k]))
        for k, s in old_series.items()
    )
    counts["forecast_series_added"] = sum(
        _placed(s) and not (k in old_series and _placed(old_series[k]))
        for k, s in new_series.items()
    )
    counts["forecast_series_changed"] = sum(
        k in old_series and _placed(s) and _placed(old_series[k])
        and _signature(s) != _signature(old_series[k])
        for k, s in new_series.items()
    )  # fmt: skip
    for name in ("decisions", "decisions_by_id_already", "decisions_moved_to_id", "collisions",
                 "orphans_kept_as_they_are", "orphans_reattachable", "orphans_ambiguous",
                 "orphans_without_candidate", "orphans_reattached", "members_added"):  # fmt: skip
        counts.setdefault(name, 0)
    return {k: int(v) for k, v in sorted(counts.items())}


def _without(details: str, name: str) -> dict[str, object]:
    return {k: v for k, v in json.loads(details).items() if k != name}


# --- The commands ---


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _open_readonly(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _in_memory_copy(db: Path) -> sqlite3.Connection:
    source = _open_readonly(db)
    copy = sqlite3.connect(":memory:", isolation_level=None)
    copy.row_factory = sqlite3.Row
    source.backup(copy)
    source.close()
    return copy


def dry_run(db: Path, key: bytes | None, reattach: bool = False) -> dict[str, int]:
    """The counts `apply` would report, from an in-memory copy. Writes nothing."""
    copy = _in_memory_copy(db)
    try:
        copy.execute("BEGIN")
        counts = run_migration(copy, key, reattach)
        copy.execute("ROLLBACK")
    finally:
        copy.close()
    return counts


def write_backup(db: Path, when: datetime | None = None, folder: Path | None = None) -> Path:
    """A byte copy of the database, verified by its hash and an integrity check."""
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = (folder or db.parent) / f"{db.name}.bak-{stamp}"
    for side in ("-wal", "-journal"):
        if db.with_name(db.name + side).exists():
            raise OSError(f"{db.name}{side} exists: close the app and try again")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise OSError(f"{target.name} already exists")
    shutil.copyfile(db, target)
    if _sha256(target) != _sha256(db):
        target.unlink(missing_ok=True)
        raise OSError("the backup does not match the database")
    check = _open_readonly(target)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        check.close()
    if not ok:
        raise OSError("the backup failed its integrity check")
    return target


def apply(
    db: Path, key: bytes | None, reattach: bool = False, backup_folder: Path | None = None
) -> tuple[Path, dict[str, int]]:
    """Back up, then migrate in one transaction. Raises OSError (database untouched) if the
    backup cannot be written, IdentityCheckError (rolled back) if a decision would be lost."""
    backup = write_backup(db, folder=backup_folder)
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            counts = run_migration(conn, key, reattach)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()
    return backup, counts


def check_against_backup(db: Path, backup: Path, key: bytes | None) -> list[str]:
    """Problems found comparing the migrated database with its backup (empty: none)."""
    problems: list[str] = []
    now, then = _open_readonly(db), _open_readonly(backup)
    try:
        rows_now, rows_then = _rows(now), _rows(then)
        if set(rows_now) != set(rows_then):
            problems.append("the decision rows differ from the backup")
        for decision_id, old in rows_then.items():
            new = rows_now.get(decision_id)
            if new is not None and old[:3] != new[:3]:
                problems.append(f"decision {decision_id} changed")
        if measure(now, key)["applying"] < measure(then, key)["applying"]:  # type: ignore[operator]
            problems.append("fewer decisions apply than before")
    finally:
        now.close()
        then.close()
    return problems


def report(db: Path, key: bytes | None) -> dict[str, int]:
    """Counts only: decisions by action, how many already name an id, how many apply today."""
    conn = _open_readonly(db)
    try:
        counts: Counter[str] = Counter()
        for account in _accounts(conn):
            series, _ = series_as_of(conn, account, None, key)
            for d in load_decisions(conn, account):
                counts[f"action: {d.action}"] += 1
                counts["decisions"] += 1
                counts["by id"] += _is_new_style(d.key)
                counts["apply to a series today"] += _resolves(d, series)
                counts["apply to no series today"] += not _resolves(d, series)
            counts["series today"] += len(series)
            counts["confirmed without payments (kept copies)"] += sum(
                s.status == "confirmed" and not s.detected_now for s in series
            )
        return dict(sorted(counts.items()))
    finally:
        conn.close()


def _print(title: str, counts: dict[str, int]) -> None:
    print(title)
    for name, value in counts.items():
        print(f"  {name}: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m lookahead.identity",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=["report", "dry-run", "apply", "check"])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--reattach", action="store_true", help="also re-attach clear orphans")
    parser.add_argument("--backup", type=Path, help="for check: the backup file to compare with")
    args = parser.parse_args(argv)
    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 2
    try:
        key: bytes | None = pseudonym_key()
    except PseudonymKeyError:
        key = None
        print("note: no PSEUDONYM_KEY, e-Transfers that name a recipient are not detected")
    if args.command == "report":
        _print("decisions today (counts only)", report(args.db, key))
        return 0
    if args.command == "dry-run":
        try:
            _print("dry run: nothing written", dry_run(args.db, key, args.reattach))
        except IdentityCheckError as error:
            print(f"the check would FAIL: {error}")
            return 1
        return 0
    if args.command == "check":
        if args.backup is None:
            print("check needs --backup FILE")
            return 2
        problems = check_against_backup(args.db, args.backup, key)
        print("check: " + ("OK, no decision lost" if not problems else "; ".join(problems)))
        return 0 if not problems else 1
    try:
        backup, counts = apply(args.db, key, args.reattach)
    except OSError as error:
        print(f"nothing changed: {error}")
        return 2
    except IdentityCheckError as error:
        print(f"CHECK FAILED, rolled back, the database is unchanged: {error}")
        return 1
    print(f"backup: {backup}")
    _print("applied", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
