"""Tests for the renewal draw's implied chance in the income-timing diagnostic (DECISIONS.md,
2026-10-12). Made-up deposits only."""

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from eval.income_timing import (
    Dep,
    chance_lines,
    main,
    observations,
    past_gaps,
    renewal_chance,
)
from lookahead.db import connect
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

FIRST = date(2025, 1, 6)


def monthly(n: int, start: date = date(2026, 1, 5), gap: int = 30) -> list[Dep]:
    return [Dep(start + timedelta(days=gap * i), i, "credit memo") for i in range(n)]


def test_gaps_are_between_distinct_days_in_the_last_year_only() -> None:
    deps = monthly(4) + [Dep(date(2026, 1, 5), 99, "credit memo")]  # a second deposit that day
    assert past_gaps(deps, date(2026, 6, 30)) == [30, 30, 30]
    old = [Dep(date(2024, 1, 1), 50, "x"), Dep(date(2024, 2, 1), 51, "x")]
    assert past_gaps(old + deps, date(2026, 6, 30)) == [30, 30, 30]  # over a year back: out
    assert past_gaps([], date(2026, 6, 30)) == []


def test_after_waiting_the_next_gap_comes_from_the_gaps_longer_than_the_wait() -> None:
    gaps = [28, 30, 31, 33, 29, 45]
    assert renewal_chance(gaps, 5, 0.1) == (0.0, False)  # a gap of 28 or more: not within 12 days
    assert renewal_chance(gaps, 25, 0.1) == (pytest.approx(4 / 6), False)  # 28, 30, 31, 29 <= 32
    # after 31 days only 33 and 45 are longer: 33 <= 38, 45 is not
    assert renewal_chance(gaps, 31, 0.1) == (pytest.approx(0.5), False)


def test_too_few_gaps_or_too_few_longer_ones_fall_back_to_the_constant_rate() -> None:
    assert renewal_chance([30] * 5, 20, 0.2) == (0.2, True)  # 5 gaps: under the minimum of 6
    assert renewal_chance([30] * 6, 25, 0.2) == (1.0, False)  # a gap of 30 is due within 7 days
    assert renewal_chance([30] * 5, 25, 0.2, min_gaps=4) == (1.0, False)  # settable (synthetic)
    assert renewal_chance([10, 12, 14, 16, 18, 40], 20, 0.2) == (0.2, True)  # one gap longer
    assert renewal_chance([10, 12, 14, 16, 40, 45], 20, 0.2) == (0.0, False)  # 40, 45 > 27


def test_a_regular_monthly_stream_is_followed_by_the_renewal_column_and_not_the_constant_one() -> (
    None
):
    deps = monthly(14)
    origins = [date(2026, 1, 5) + timedelta(days=7 * k) for k in range(40)]
    dates = sorted({*origins, *(o + timedelta(days=7) for o in origins)})
    pops = {d: {"keeps": [x for x in deps if x.day <= d]} for d in dates}
    obs, _ = observations(pops, origins, FIRST, "keeps")
    on_draw = [o for o in obs if not o.fallback]
    assert on_draw, "after 6 gaps the draw is in use"
    near_due = [o for o in on_draw if 23 <= o.days_since <= 29]
    early = [o for o in on_draw if 0 < o.days_since <= 14]
    assert near_due and early
    assert all(o.renewal == 1.0 and o.event for o in near_due)  # due within a week, and it was
    assert all(o.renewal == 0.0 and not o.event for o in early)  # not due, and it was not
    assert all(0.1 < o.implied < 0.3 for o in near_due)  # the constant rate is far from either
    text = "\n".join(chance_lines(obs, 0))
    assert "the renewal draw implies" in text and "origins on the renewal draw" in text
    assert "mean absolute difference from the real share" in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)  # no dates: only counts and shares


def test_the_chance_at_an_origin_uses_only_deposits_posted_by_then() -> None:
    deps = monthly(10)
    origin = date(2026, 5, 5)  # 120 days in: deposits on days 0, 30, 60, 90, 120
    now = [d for d in deps if d.day <= origin]
    more = deps  # the rows after the origin are in the list: they must change nothing
    assert past_gaps(now, origin) == past_gaps(more, origin)


@pytest.fixture(scope="module")
def synthetic_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("renewal")
    export, _ = write_persona(build_persona("v2-ITIGHT-01").persona, folder, 42, START, END)
    conn = connect(folder / "d.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    conn.close()
    return folder / "d.db"


@pytest.mark.slow
def test_a_synthetic_database_takes_other_minimums_and_a_private_one_does_not(
    synthetic_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # fmt: skip
    for minimum in ("4", "6", "8"):
        assert main(["--db", str(synthetic_db), "--limit", "4", "--min-gaps", minimum]) == 0
    out = capsys.readouterr().out
    assert out.count("the renewal draw implies") >= 6
    private = tmp_path / "data" / "private"
    private.mkdir(parents=True)
    copy = private / "d.db"
    copy.write_bytes(synthetic_db.read_bytes())
    assert main(["--db", str(copy), "--min-gaps", "4"]) == 1
    assert "synthetic databases only" in capsys.readouterr().out
    assert main(["--db", str(copy), "--limit", "6"]) == 0  # the default minimum is allowed
