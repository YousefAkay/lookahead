"""Tests for the recurring-placement breakdown. Made-up values and synthetic personas only."""

import re
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from eval.diagnose_real import match_recurring, origin_record
from eval.recurring_check import (
    _outcomes,
    collect,
    day_bucket,
    main,
    payment_kind,
    period_bucket,
    placement_kind,
    status_name,
    summary_lines,
)
from lookahead.db import connect, connect_readonly
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.recurring_store import series_as_of

ORIGINS = [date(2026, 3, 2), date(2026, 5, 25), date(2026, 8, 3)]


def test_the_buckets_and_kinds() -> None:
    assert [day_bucket(d) for d in (None, 0, 44, 45, 90, 91)] == [
        "no payment found", "under 45 days", "under 45 days", "45 to 90 days", "45 to 90 days",
        "over 90 days",
    ]  # fmt: skip
    assert period_bucket(14, "weekly") == "1.5 to 3 periods" and period_bucket(10, "monthly") == (
        "up to 1.5 periods"
    )  # fmt: skip
    assert period_bucket(100, "monthly") == "more than 3 periods"
    assert placement_kind("rent: the larger allowed amount, on the earliest day") == "rent window"
    assert placement_kind("rent: x not seen yet") == "rent window, overdue"
    assert placement_kind("due by the last day of the data and not seen yet: expected on the "
                          "first day").startswith("due and not seen yet")  # fmt: skip
    assert placement_kind("from its detected pattern") == "from its pattern"
    assert status_name(None) == "not in the list"


def test_the_outcome_is_the_same_matching_as_the_diagnosis_with_who_is_who() -> None:
    placed = [(5, -90000, "rent"), (12, -4000, "phone"), (20, -1500, "gym"), (25, -700, "x")]
    actual = [(7, -95000, "rent"), (13, -4000, "phone"), (29, -1500, "gym"), (40, -700, "x")]
    pairs, missed, extra = match_recurring(placed, actual)
    out = _outcomes(placed, actual)
    assert [p["result"] for p in out["placed"]] == ["matched", "matched", "matched", "missed"]
    # the last placed payment is missed and its series did pay, but 15 days off: "elsewhere"
    assert out["placed"][3]["elsewhere"] is True and out["extra"] == [3]
    assert len(pairs) == 3 and len(missed) == 1 and len(extra) == 1


@pytest.fixture(scope="module")
def synthetic_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("rc")
    export, _ = write_persona(build_persona("v2-TIGHT-01").persona, folder, 42, START, END)
    conn = connect(folder / "d.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    conn.close()
    return folder / "d.db"


@pytest.mark.slow
def test_the_totals_equal_the_diagnosis_and_every_breakdown_adds_up(synthetic_db: Path) -> None:
    conn = connect_readonly(synthetic_db)
    try:
        rows = collect(conn, "acct", ORIGINS)
        for row, origin in zip(rows, ORIGINS, strict=True):
            ref = origin_record(conn, "acct", origin, 20, keeps=None)
            assert len(row["placed"]) == ref["n_placed"]
            assert sum(p["result"] == "matched" for p in row["placed"]) == ref["n_matched"]
            assert sum(p["result"] == "missed" for p in row["placed"]) == ref["n_missed"]
            assert len(row["extra"]) == ref["n_extra"]
    finally:
        conn.close()
    placed = [p for r in rows for p in r["placed"]]
    extra = [e for r in rows for e in r["extra"]]
    assert placed, "the test persona has recurring payments"
    for name in placed[0]["attrs"]:
        total = sum(1 for p in placed if p["attrs"][name] is not None)
        assert total == len(placed)
    text = "\n".join(summary_lines(rows, "All origins"))
    assert f"{len(placed)} placed payments" in text and f"{len(extra)} extra real" in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)


@pytest.mark.slow
def test_what_is_known_at_the_origin_does_not_change_when_every_later_row_is_deleted(
    synthetic_db: Path, tmp_path: Path
) -> None:  # fmt: skip
    copy = tmp_path / "later.db"
    shutil.copy(synthetic_db, copy)
    origin = ORIGINS[1]
    raw = sqlite3.connect(copy)
    raw.execute("DELETE FROM transactions WHERE posted_date > ?", (origin.isoformat(),))
    raw.commit()
    raw.close()
    a, b = connect_readonly(synthetic_db), connect_readonly(copy)
    try:
        ra = collect(a, "acct", [origin])[0]["placed"]
        rb = collect(b, "acct", [origin])[0]["placed"]
    finally:
        a.close()
        b.close()
    assert len(ra) == len(rb)
    today_only = ("status today", "active or ended today")  # hindsight labels: they may differ

    def known(rows: list[dict]) -> list[dict]:  # type: ignore[type-arg]
        return [{k: v for k, v in p["attrs"].items() if k not in today_only} for p in rows]

    assert known(ra) == known(rb)  # the placed side, as known then


@pytest.mark.slow
def test_the_command_prints_counts_and_shares_and_no_names_or_dates(
    synthetic_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # fmt: skip
    assert main(["--db", str(synthetic_db), "--limit", "10"]) == 0
    out = capsys.readouterr().out
    assert "account-1" in out and "acct" not in out
    assert "placed payments by days since its last real payment" in out
    assert "extra real payments, by whether the series was known at the origin" in out
    assert not re.search(r"\d{4}-\d{2}-\d{2}", out)
    conn = connect_readonly(synthetic_db)
    try:
        names = [s.name for s in series_as_of(conn, "acct", None, None)[0] if s.name]
    finally:
        conn.close()
    assert not any(name in out for name in names)  # no series name anywhere


def test_a_series_with_no_payments_is_its_own_kind() -> None:
    class S:
        kind = "merchant"

    assert payment_kind(S(), []) == "no payment found"  # type: ignore[arg-type]
    assert payment_kind(S(), ["card purchase", "card purchase", "bill payment"]) == "card purchase"  # type: ignore[arg-type]
    assert payment_kind(S(), ["incoming e-Transfer"]) == "e-Transfer"  # type: ignore[arg-type]
    assert payment_kind(S(), ["credit memo"]) == "other"  # type: ignore[arg-type]
