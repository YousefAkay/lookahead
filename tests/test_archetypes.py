"""Generator v1: 30 personas from 7 archetypes (the mix is fixed in DECISIONS.md, 2026-10-08).

These tests pin the recorded mix and the recorded ranges, so the mix cannot drift unnoticed.
"""

from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from lookahead.generator.archetypes import (
    ARCHETYPES,
    END,
    HELD_OUT,
    PERSONA_IDS,
    QUICK_IDS,
    START,
    V1Persona,
    assert_development_only,
    build_persona,
    development_ids,
)
from lookahead.generator.schedules import DatedSeries, IrregularSeries, RecurringSchedule
from lookahead.generator.writer import export_rows

# The recorded mix (DECISIONS.md): archetype -> (n, held-out numbers, buffer in cents).
RECORDED = {
    "SAL": (5, (4, 5), 30_000),
    "SEM": (3, (3,), 30_000),
    "PTS": (4, (4,), 15_000),
    "TIGHT": (7, (6, 7), 10_000),
    "LUMP": (6, (5, 6), 50_000),
    "FREE": (3, (3,), 20_000),
    "AWAY": (2, (2,), 20_000),
}


def test_the_mix_is_the_recorded_one() -> None:
    assert len(PERSONA_IDS) == 30
    counts = Counter(pid.split("-")[1] for pid in PERSONA_IDS)
    assert dict(counts) == {k: v[0] for k, v in RECORDED.items()}
    assert set(ARCHETYPES) == set(RECORDED)
    for code, (_, _, buffer) in RECORDED.items():
        assert ARCHETYPES[code].buffer_cents == buffer


def test_the_held_out_personas_are_the_recorded_ten() -> None:
    expected = {f"v1-{code}-{n:02d}" for code, (_, held, _) in RECORDED.items() for n in held}
    assert HELD_OUT == expected and len(HELD_OUT) == 10
    assert HELD_OUT <= set(PERSONA_IDS)
    assert set(development_ids()) == set(PERSONA_IDS) - HELD_OUT


def test_ids_have_the_recorded_form() -> None:
    assert PERSONA_IDS[0] == "v1-SAL-01" and "v1-AWAY-02" in PERSONA_IDS
    assert len(set(PERSONA_IDS)) == 30


def test_tuning_code_refuses_a_held_out_persona() -> None:
    assert_development_only(["v1-SAL-01", "v1-TIGHT-01"])  # fine
    with pytest.raises(ValueError, match="held-out"):
        assert_development_only(["v1-SAL-01", "v1-TIGHT-07"])


def test_the_quick_set_is_three_development_personas_of_different_archetypes() -> None:
    assert len(QUICK_IDS) == 3 and not set(QUICK_IDS) & HELD_OUT
    assert len({pid.split("-")[1] for pid in QUICK_IDS}) == 3


def test_the_window_is_twelve_months() -> None:
    assert START == date(2025, 10, 1) and END == date(2026, 9, 30)


def test_a_persona_is_the_same_every_time_and_differs_between_ids() -> None:
    assert build_persona("v1-SAL-01") == build_persona("v1-SAL-01")
    assert build_persona("v1-SAL-01") != build_persona("v1-SAL-02")


def test_an_unknown_id_is_refused() -> None:
    with pytest.raises(ValueError):
        build_persona("v1-NOPE-01")
    with pytest.raises(ValueError):
        build_persona("v1-SAL-09")


def incomes(p: V1Persona) -> list:  # type: ignore[type-arg]
    return [s for s in p.persona.sources if getattr(s, "category", "") == "Income"]


def rent(p: V1Persona) -> RecurringSchedule:
    return next(
        s for s in p.persona.sources
        if isinstance(s, RecurringSchedule) and s.category == "Rent & housing"
    )  # fmt: skip


def monthly_pay_cents(p: V1Persona) -> int:
    pay = incomes(p)[0]
    per_year = {"biweekly": 26, "semimonthly": 24, "monthly": 12}[pay.cadence]
    return pay.amount.typical_cents * per_year // 12


@pytest.mark.parametrize("pid", PERSONA_IDS)
def test_every_persona_is_inside_its_archetypes_recorded_ranges(pid: str) -> None:
    p = build_persona(pid)
    code = pid.split("-")[1]
    dollars = lambda cents: cents / 100  # noqa: E731
    opening = dollars(p.persona.opening_balance_cents)
    rent_amount = dollars(-rent(p).amount.typical_cents)
    assert p.buffer_cents == RECORDED[code][2] and p.archetype == code
    assert p.held_out == (pid in HELD_OUT)
    ranges = {
        "SAL": ((1500, 2600), (900, 1500), (1500, 4000)),
        "SEM": ((1300, 2200), (800, 1400), (1200, 3500)),
        "PTS": ((450, 900), (500, 800), (300, 900)),
        "TIGHT": ((1100, 1700), None, (100, 500)),
        "FREE": (None, (700, 1200), (500, 2000)),
        "AWAY": ((900, 1400), (500, 800), (400, 1200)),
        "LUMP": (None, (600, 1100), (3000, 8000)),
    }[code]
    pay, rent_range, open_range = ranges
    if pay is not None:
        assert pay[0] <= dollars(incomes(p)[0].amount.typical_cents) <= pay[1]
    if rent_range is not None:
        assert rent_range[0] <= rent_amount <= rent_range[1]
    assert open_range[0] <= opening <= open_range[1]
    if code == "TIGHT":
        share = -rent(p).amount.typical_cents / monthly_pay_cents(p)
        assert 0.495 <= share <= 0.625  # 50-62 percent, rent rounded to $5


def test_lump_sum_personas_have_no_paycheque_and_one_deposit_per_term() -> None:
    for pid in PERSONA_IDS:
        if "-LUMP-" not in pid:
            continue
        p = build_persona(pid)
        assert not [s for s in p.persona.sources if isinstance(s, RecurringSchedule)
                    and s.category == "Income"]  # fmt: skip
        deposits = [s for s in p.persona.sources if isinstance(s, DatedSeries)
                    and s.category == "Income"]  # fmt: skip
        [series] = deposits
        assert [i.due.month for i in series.items] == [1, 5, 9]
        assert all(600_000 <= i.amount_cents <= 1_200_000 for i in series.items)


def test_freelancers_are_paid_irregularly_and_away_students_have_quiet_months() -> None:
    free = build_persona("v1-FREE-01")
    assert [s for s in free.persona.sources if isinstance(s, IrregularSeries)]
    away = build_persona("v1-AWAY-01")
    assert away.persona.quiet_months == {7, 8, 12} and away.persona.quiet_activity == 0.15


@pytest.mark.parametrize("pid", ["v1-SAL-01", "v1-TIGHT-01", "v1-LUMP-01", "v1-FREE-01"])
def test_a_year_of_history_is_generated_inside_the_window(pid: str) -> None:
    p = build_persona(pid)
    rows = export_rows(p.persona, START, END, seed=1)
    assert rows and rows[0].posted_date >= START
    in_window = [r for r in rows if r.posted_date <= END]
    assert len(in_window) >= 300  # a year of everyday spending and bills
    months = {r.posted_date.strftime("%Y-%m") for r in in_window}
    assert len(months) == 12  # every month has rows


def test_a_generated_year_imports_and_reconciles(tmp_path: Path) -> None:
    from datetime import datetime

    from lookahead.db import connect
    from lookahead.generator.writer import write_persona
    from lookahead.ingest.reconcile import reconcile
    from lookahead.ingest.scotiabank import parse_export
    from lookahead.ingest.store import store_exports

    p = build_persona("v1-TIGHT-01")
    export, _ = write_persona(p.persona, tmp_path, 1, START, END)
    conn = connect(tmp_path / "t.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    assert reconcile(conn, "acct").ok
    conn.close()
