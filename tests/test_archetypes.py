"""Generator, mix v2: 30 personas from 8 archetypes (fixed in DECISIONS.md, 2026-10-08, "Persona
mix revised: four student archetypes"). These tests pin the recorded mix, ranges and per-persona
assignments, so the mix cannot drift unnoticed.
"""

import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead.generator.archetypes import (
    ARCHETYPES,
    END,
    HELD_OUT,
    LOAN_SENDER,
    PERSONA_IDS,
    PERSONA_SPECS,
    QUICK_IDS,
    START,
    HourlyPay,
    MixPersona,
    assert_development_only,
    build_persona,
    development_ids,
)
from lookahead.generator.schedules import (
    DatedSeries,
    IrregularSeries,
    RecurringSchedule,
)
from lookahead.generator.writer import export_rows

# The recorded mix (DECISIONS.md): archetype -> (n, held-out numbers, buffer in cents).
RECORDED = {
    "IFAM": (4, (4,), 30_000),
    "ITIGHT": (5, (4, 5), 10_000),
    "DHOME": (4, (4,), 15_000),
    "DRENT": (5, (4, 5), 15_000),
    "SAL": (4, (4,), 30_000),
    "SEM": (2, (2,), 30_000),
    "TIGHT": (4, (4,), 10_000),
    "FREE": (2, (2,), 20_000),
}
STUDENT_CODES = ("IFAM", "ITIGHT", "DHOME", "DRENT")
STUDENT_IDS = [p for p in PERSONA_IDS if p.split("-")[1] in STUDENT_CODES]


def sources(p: MixPersona, kind: type) -> list:  # type: ignore[type-arg]
    return [s for s in p.persona.sources if isinstance(s, kind)]


def dollars(cents: int) -> float:
    return cents / 100


# --- The mix ---


def test_the_mix_is_the_recorded_one() -> None:
    assert len(PERSONA_IDS) == 30
    counts = Counter(pid.split("-")[1] for pid in PERSONA_IDS)
    assert dict(counts) == {k: v[0] for k, v in RECORDED.items()}
    assert set(ARCHETYPES) == set(RECORDED)
    for code, (_, _, buffer) in RECORDED.items():
        assert ARCHETYPES[code].buffer_cents == buffer


def test_the_held_out_personas_are_the_recorded_ten() -> None:
    expected = {f"v2-{code}-{n:02d}" for code, (_, held, _) in RECORDED.items() for n in held}
    assert HELD_OUT == expected and len(HELD_OUT) == 10
    assert HELD_OUT <= set(PERSONA_IDS)
    assert set(development_ids()) == set(PERSONA_IDS) - HELD_OUT


def test_every_student_archetype_has_a_held_out_persona() -> None:
    for code in STUDENT_CODES:
        assert [p for p in HELD_OUT if p.split("-")[1] == code], code


def test_ids_have_the_recorded_form_and_the_old_mix_is_gone() -> None:
    assert PERSONA_IDS[0] == "v2-IFAM-01" and "v2-FREE-02" in PERSONA_IDS
    assert len(set(PERSONA_IDS)) == 30 and not [p for p in PERSONA_IDS if p.startswith("v1")]
    with pytest.raises(ValueError):
        build_persona("v1-SAL-01")  # superseded


@pytest.mark.parametrize("bad", ["v2-SAL-1", "v2-SAL-001", "v2L-SAL-1", "v2-SAL-05", "v2L-SAL-05",
                                 "v2-sal-01", " v2-SAL-01"])  # fmt: skip
def test_an_id_that_is_not_in_the_recorded_lists_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="unknown persona id"):
        build_persona(bad)


def test_every_recorded_id_of_both_sets_builds() -> None:
    from lookahead.generator.archetypes import LONG_IDS

    assert build_persona(PERSONA_IDS[0]).persona and build_persona(LONG_IDS[-1]).persona


def test_tuning_code_refuses_a_held_out_persona() -> None:
    assert_development_only(["v2-SAL-01", "v2-TIGHT-01"])  # fine
    with pytest.raises(ValueError, match="held-out"):
        assert_development_only(["v2-SAL-01", "v2-TIGHT-04"])


def test_the_quick_set_is_three_development_personas_of_different_archetypes() -> None:
    assert len(QUICK_IDS) == 3 and not set(QUICK_IDS) & HELD_OUT
    assert len({pid.split("-")[1] for pid in QUICK_IDS}) == 3


def test_the_window_is_twelve_months() -> None:
    assert START == date(2025, 10, 1) and END == date(2026, 9, 30)


def test_a_persona_is_the_same_every_time_and_differs_between_ids() -> None:
    assert build_persona("v2-SAL-01") == build_persona("v2-SAL-01")
    assert build_persona("v2-SAL-01") != build_persona("v2-SAL-02")


def test_an_unknown_id_is_refused() -> None:
    for bad in ("v2-NOPE-01", "v2-SAL-09", "v2-SAL-00", "SAL-01", "v2-SAL-x"):
        with pytest.raises(ValueError):
            build_persona(bad)


# --- The recorded per-persona assignments ---


def test_the_tuition_paid_from_chequing_counts_are_the_recorded_ones() -> None:
    yes: Counter[str] = Counter()
    for pid, spec in PERSONA_SPECS.items():
        if spec.from_chequing:
            yes[pid.split("-")[1]] += 1
    assert yes == {"IFAM": 3, "ITIGHT": 4, "DHOME": 1, "DRENT": 3}
    assert not PERSONA_SPECS["v2-IFAM-03"].from_chequing  # the family pays the university
    assert not PERSONA_SPECS["v2-ITIGHT-02"].from_chequing


def test_a_third_of_the_student_personas_have_a_summer_term() -> None:
    summer = {pid for pid, spec in PERSONA_SPECS.items() if spec.summer}
    assert summer == {"v2-IFAM-02", "v2-ITIGHT-03", "v2-ITIGHT-04", "v2-DHOME-02",
                      "v2-DRENT-01", "v2-DRENT-04"}  # fmt: skip
    assert len(summer) * 3 == len(STUDENT_IDS)


def test_three_personas_are_away_in_summer_and_december_and_have_no_summer_term() -> None:
    away = {pid for pid, spec in PERSONA_SPECS.items() if spec.away}
    assert away == {"v2-DHOME-03", "v2-DRENT-02", "v2-DRENT-05"}
    assert not any(PERSONA_SPECS[p].summer for p in away)
    for pid in away:
        persona = build_persona(pid).persona
        assert persona.quiet_months == {7, 8, 12} and persona.quiet_activity == 0.15
        pay = sources(build_persona(pid), HourlyPay)[0]
        assert pay.hours_on(date(2026, 7, 17)) == 0 and pay.hours_on(date(2026, 8, 14)) == 0


def test_three_drent_personas_have_aid_and_both_patterns_are_used() -> None:
    aid = {pid: spec.aid for pid, spec in PERSONA_SPECS.items() if spec.aid}
    assert set(aid) == {"v2-DRENT-01", "v2-DRENT-03", "v2-DRENT-05"}
    assert set(aid.values()) == {"loans", "loans+bursary"}
    assert all(pid.split("-")[1] == "DRENT" for pid in aid)


def test_two_ifam_personas_pay_tuition_in_two_installments() -> None:
    assert {p for p, s in PERSONA_SPECS.items() if s.installments == 2} == {
        "v2-IFAM-02", "v2-IFAM-04",
    }  # fmt: skip


# --- Tuition, deposits and installments ---


def tuition_and_funding(pid: str) -> tuple[DatedSeries | None, DatedSeries | None]:
    p = build_persona(pid)
    tuition = [s for s in sources(p, DatedSeries) if s.series_id.endswith("-tuition")]
    funding = [s for s in sources(p, DatedSeries) if s.series_id.endswith("-tuition-funding")]
    return (tuition[0] if tuition else None, funding[0] if funding else None)


@pytest.mark.parametrize("pid", STUDENT_IDS)
def test_tuition_rows_exist_only_when_paid_from_chequing_and_not_free(pid: str) -> None:
    spec = PERSONA_SPECS[pid]
    tuition, funding = tuition_and_funding(pid)
    if not spec.from_chequing or spec.tuition_dollars[1] == 0:
        assert tuition is None and funding is None  # no tuition and no tuition deposit
        return
    assert tuition is not None
    terms = {i.link_id for i in tuition.items}
    assert len(terms) == (3 if spec.summer else 2)  # Winter and Fall, plus Summer
    per_term = [sum(-i.amount_cents for i in tuition.items if i.link_id == t) for t in terms]
    assert len({round(a, -3) for a in per_term}) == 1  # the same tuition every term
    low, high = spec.tuition_dollars
    assert low <= dollars(per_term[0]) <= high + 1


IFAM_FUNDED = [p for p in STUDENT_IDS if p.startswith("v2-IFAM") and PERSONA_SPECS[p].from_chequing]


@pytest.mark.parametrize("pid", IFAM_FUNDED)
def test_the_family_deposit_is_100_to_140_percent_of_tuition_1_to_30_days_before(pid: str) -> None:
    tuition, funding = tuition_and_funding(pid)
    assert funding is not None and tuition is not None
    for deposit in funding.items:
        paid = [i for i in tuition.items if i.link_id == deposit.link_id]
        total = sum(-i.amount_cents for i in paid)
        first = min(i.due for i in paid)
        assert 0.99 <= deposit.amount_cents / total <= 1.41
        assert 1 <= (first - deposit.due).days <= 30


@pytest.mark.parametrize("pid", ["v2-IFAM-02", "v2-IFAM-04"])
def test_installments_are_two_halves_30_to_45_days_apart(pid: str) -> None:
    tuition, _ = tuition_and_funding(pid)
    assert tuition is not None
    for term in {i.link_id for i in tuition.items}:
        first, second = sorted((i for i in tuition.items if i.link_id == term), key=lambda i: i.due)
        assert abs(first.amount_cents - second.amount_cents) <= 100  # halves, to the dollar
        assert 30 <= (second.due - first.due).days <= 45


def test_some_deposits_fall_outside_the_pass_through_window_so_the_rule_is_really_tested() -> None:
    """The pass-through rule pairs a deposit with outflows of 85-115 percent of it within 14 days.
    The data was not built to match it: some deposits are too large or too early."""
    outside = inside = 0
    for pid in IFAM_FUNDED:
        tuition, funding = tuition_and_funding(pid)
        assert tuition is not None and funding is not None
        for deposit in funding.items:
            paid = [i for i in tuition.items if i.link_id == deposit.link_id]
            within = sum(-i.amount_cents for i in paid if 0 <= (i.due - deposit.due).days <= 14)
            if 0.85 <= within / deposit.amount_cents <= 1.15:
                inside += 1
            else:
                outside += 1
    assert outside >= 2, (inside, outside)  # not every case is built to match the rule


def test_the_family_pays_the_university_directly_for_ifam_03_and_itight_02() -> None:
    for pid in ("v2-IFAM-03", "v2-ITIGHT-02"):
        ids = [getattr(s, "series_id", "") for s in build_persona(pid).persona.sources]
        assert not [i for i in ids if "tuition" in i], pid
    # but the family still sends money (an allowance, irregular transfers)
    assert sources(build_persona("v2-IFAM-03"), RecurringSchedule)
    assert sources(build_persona("v2-ITIGHT-02"), IrregularSeries)


def test_itight_family_money_is_often_partial_and_irregular() -> None:
    shares = []
    for pid in ("v2-ITIGHT-01", "v2-ITIGHT-03", "v2-ITIGHT-04", "v2-ITIGHT-05"):
        tuition, funding = tuition_and_funding(pid)
        assert tuition is not None and funding is not None
        for deposit in funding.items:
            total = sum(-i.amount_cents for i in tuition.items if i.link_id == deposit.link_id)
            shares.append(deposit.amount_cents / total)
        assert sources(build_persona(pid), IrregularSeries)  # plus irregular transfers
    assert all(0.59 <= s <= 1.06 for s in shares) and min(shares) < 0.9  # often partial


# --- Wages ---


def test_international_wages_are_16_to_17_an_hour_capped_at_24_hours_in_term() -> None:
    for pid in [p for p in PERSONA_IDS if "-ITIGHT-" in p]:
        pay = sources(build_persona(pid), HourlyPay)[0]
        assert 16.0 <= pay.net_cents_per_hour / 100 / 0.85 <= 17.01
        assert 0 < pay.term_hours <= 24  # the cap applies during term
        assert pay.break_hours >= 25 and pay.break_hours > pay.term_hours  # more in breaks
        assert pay.hours_on(date(2026, 3, 13)) == pay.term_hours
        assert pay.hours_on(date(2025, 12, 26)) == pay.break_hours  # winter break
        summer = pay.hours_on(date(2026, 6, 26))
        assert summer == (pay.term_hours if PERSONA_SPECS[pid].summer else pay.break_hours)


def test_domestic_wages_and_exam_dips() -> None:
    for pid in [p for p in PERSONA_IDS if "-DHOME-" in p]:
        pay = sources(build_persona(pid), HourlyPay)[0]
        assert 16.0 <= pay.net_cents_per_hour / 100 / 0.85 <= 20.01 and pay.term_hours > 0
        assert pay.dip_factor == 1.0
    for pid in [p for p in PERSONA_IDS if "-DRENT-" in p]:
        pay = sources(build_persona(pid), HourlyPay)[0]
        assert 16.0 <= pay.net_cents_per_hour / 100 / 0.85 <= 21.01 and pay.term_hours > 0
        assert 0.5 <= pay.dip_factor <= 0.7
        assert pay.hours_on(date(2025, 12, 12)) == pytest.approx(pay.term_hours * pay.dip_factor)
        assert pay.hours_on(date(2026, 4, 17)) == pytest.approx(pay.term_hours * pay.dip_factor)
        assert pay.hours_on(date(2026, 3, 13)) == pay.term_hours


def test_wages_are_paid_biweekly_and_vary_and_dip_in_exams() -> None:
    import numpy as np

    pay = sources(build_persona("v2-DRENT-01"), HourlyPay)[0]
    rows = pay.generate(START, END, np.random.default_rng(1))
    assert len(rows) >= 20 and len({r.amount_cents for r in rows}) > 5  # hours vary
    exam = [r for r in rows if date(2026, 4, 10) <= r.posted_date <= date(2026, 4, 30)]
    normal = [r for r in rows if date(2026, 2, 1) <= r.posted_date <= date(2026, 3, 31)]
    assert exam and normal
    mean_exam = sum(r.amount_cents for r in exam) / len(exam)
    mean_normal = sum(r.amount_cents for r in normal) / len(normal)
    assert mean_exam < mean_normal


# --- Aid ---


def aid_series(pid: str) -> DatedSeries:
    return next(s for s in sources(build_persona(pid), DatedSeries) if s.series_id.endswith("-aid"))


def test_loans_only_is_one_payment_at_the_start_of_each_semester() -> None:
    series = aid_series("v2-DRENT-03")
    assert [i.due for i in series.items] == [date(2026, 1, 6), date(2026, 9, 3)]
    assert all(2_500 <= dollars(i.amount_cents) <= 4_500 for i in series.items)


def test_loans_plus_bursaries_is_a_larger_start_payment_then_smaller_monthly_ones() -> None:
    series = aid_series("v2-DRENT-01")  # a summer-term persona: three semesters
    start_days = (date(2026, 1, 6), date(2026, 5, 5), date(2026, 9, 3))
    starts = [i for i in series.items if i.due in start_days]
    monthly = [i for i in series.items if i.due not in start_days]
    assert len(starts) == 3 and len(monthly) == 9  # Fall 2025, Winter and Summer 2026 months
    assert all(3_500 <= dollars(i.amount_cents) <= 6_000 for i in starts)
    assert all(300 <= dollars(i.amount_cents) <= 900 for i in monthly)
    assert all(i.due.day == 5 for i in monthly)
    assert min(i.amount_cents for i in starts) > max(i.amount_cents for i in monthly)
    other = aid_series("v2-DRENT-05")  # no summer term: six monthly parts
    assert len([i for i in other.items if i.due not in start_days]) == 6


def test_aid_looks_like_a_bank_transfer_not_a_government_name() -> None:
    for pid in ("v2-DRENT-01", "v2-DRENT-03", "v2-DRENT-05"):
        series = aid_series(pid)
        assert series.merchant == LOAN_SENDER == "BMO Bank of Montreal"
        assert series.txn_type == "deposit" and series.category == "Income"
        text = series.merchant.lower()
        assert not any(w in text for w in ("quebec", "government", "aide", "gouv"))


def test_aid_goes_only_to_the_three_drent_personas_that_have_it() -> None:
    for pid in PERSONA_IDS:
        has = any(s.series_id.endswith("-aid") for s in sources(build_persona(pid), DatedSeries))
        assert has == (pid in ("v2-DRENT-01", "v2-DRENT-03", "v2-DRENT-05")), pid


# --- Rent, bills and subscriptions ---


def rent_of(p: MixPersona) -> RecurringSchedule | None:
    found = [s for s in sources(p, RecurringSchedule) if s.series_id.endswith("-rent")]
    return found[0] if found else None


@pytest.mark.parametrize("pid", STUDENT_IDS)
def test_rent_is_in_the_recorded_range_or_absent(pid: str) -> None:
    spec = PERSONA_SPECS[pid]
    rent = rent_of(build_persona(pid))
    if spec.rent is None:
        assert rent is None
    else:
        assert rent is not None
        assert spec.rent[0] <= dollars(-rent.amount.typical_cents) <= spec.rent[1]


BILL_IDS = re.compile(r"-(fizz|phone|internet|transit|healthins|tenantins|hydro)$")
SUB_IDS = re.compile(r"-sub-")


def bills(p: MixPersona) -> list[RecurringSchedule]:
    return [s for s in sources(p, RecurringSchedule) if BILL_IDS.search(s.series_id)]


def subscriptions(p: MixPersona) -> list[RecurringSchedule]:
    return [s for s in sources(p, RecurringSchedule) if SUB_IDS.search(s.series_id)]


SHAPES = {
    "IFAM": ((4, 6), (2, 5), (20, 80)),
    "ITIGHT": ((3, 5), (1, 3), (10, 40)),
    "DHOME": ((2, 4), (3, 7), (30, 110)),
    "DRENT": ((3, 5), (2, 5), (20, 80)),
}


@pytest.mark.parametrize("pid", STUDENT_IDS)
def test_bill_series_and_subscriptions_are_in_range_and_kept_apart(pid: str) -> None:
    n_range, sub_range, cost_range = SHAPES[pid.split("-")[1]]
    p = build_persona(pid)
    paid_bills, subs = bills(p), subscriptions(p)
    assert n_range[0] <= len(paid_bills) <= n_range[1]
    assert sub_range[0] <= len(subs) <= sub_range[1]
    total = sum(-s.amount.typical_cents for s in subs) / 100
    assert cost_range[0] - 1 <= total <= cost_range[1] + len(subs) * 3  # each at least $2.99
    assert all(s.category == "Subscriptions" for s in subs)
    assert not any(s.category == "Subscriptions" for s in paid_bills)  # bills are not subs
    assert any("phone" in s.series_id or "fizz" in s.series_id for s in paid_bills)


def test_subscription_count_and_cost_are_drawn_separately() -> None:
    rows = []
    for pid in STUDENT_IDS:
        subs = subscriptions(build_persona(pid))
        rows.append((pid.split("-")[1], len(subs), sum(-s.amount.typical_cents for s in subs)))
    # a persona with more subscriptions can pay less in all than one with fewer
    assert any(a[1] > b[1] and a[2] < b[2] for a in rows for b in rows if a[0] == b[0])


# --- The non-student archetypes (ranges from the superseded entry) ---


def incomes(p: MixPersona) -> list:  # type: ignore[type-arg]
    return [s for s in p.persona.sources if getattr(s, "category", "") == "Income"]


@pytest.mark.parametrize("pid", [p for p in PERSONA_IDS if p not in STUDENT_IDS])
def test_non_student_personas_are_inside_their_recorded_ranges(pid: str) -> None:
    p = build_persona(pid)
    code = pid.split("-")[1]
    opening = dollars(p.persona.opening_balance_cents)
    rent = rent_of(p)
    assert rent is not None
    rent_amount = dollars(-rent.amount.typical_cents)
    ranges = {
        "SAL": ((1500, 2600), (900, 1500), (1500, 4000)),
        "SEM": ((1300, 2200), (800, 1400), (1200, 3500)),
        "TIGHT": ((1100, 1700), None, (100, 500)),
        "FREE": (None, (700, 1200), (500, 2000)),
    }[code]
    pay, rent_range, open_range = ranges
    if pay is not None:
        assert pay[0] <= dollars(incomes(p)[0].amount.typical_cents) <= pay[1]
    if rent_range is not None:
        assert rent_range[0] <= rent_amount <= rent_range[1]
    assert open_range[0] <= opening <= open_range[1]
    if code == "TIGHT":
        pay_cents = incomes(p)[0].amount.typical_cents
        share = -rent.amount.typical_cents / (pay_cents * 26 // 12)
        assert 0.495 <= share <= 0.625  # 50-62 percent of monthly pay, rent rounded to $5
    assert p.buffer_cents == RECORDED[code][2]


@pytest.mark.parametrize("pid", STUDENT_IDS)
def test_student_opening_balances_and_buffers(pid: str) -> None:
    code = pid.split("-")[1]
    p = build_persona(pid)
    low, high = {"IFAM": (2000, 8000), "ITIGHT": (300, 2000), "DHOME": (300, 1500),
                 "DRENT": (300, 2000)}[code]  # fmt: skip
    assert low <= dollars(p.persona.opening_balance_cents) <= high
    assert p.buffer_cents == RECORDED[code][2] and p.archetype == code
    assert p.held_out == (pid in HELD_OUT)


# --- A year of history ---


@pytest.mark.parametrize("pid", ["v2-SAL-01", "v2-IFAM-01", "v2-ITIGHT-01", "v2-DHOME-01",
                                 "v2-DRENT-01", "v2-FREE-01"])  # fmt: skip
def test_a_year_of_history_is_generated_inside_the_window(pid: str) -> None:
    p = build_persona(pid)
    rows = export_rows(p.persona, START, END, seed=1)
    assert rows and rows[0].posted_date >= START
    in_window = [r for r in rows if r.posted_date <= END]
    assert len(in_window) >= 300  # a year of everyday spending and bills
    months = {r.posted_date.strftime("%Y-%m") for r in in_window}
    assert len(months) == 12  # every month has rows


def test_the_pass_through_cases_exist_in_the_generated_data() -> None:
    """In a generated year an IFAM family deposit is followed by the tuition payment."""
    rows = export_rows(build_persona("v2-IFAM-01").persona, START, END, seed=1)
    tuition = [r for r in rows if r.series_id == "v2-IFAM-01-tuition"]
    deposits = [r for r in rows if r.series_id == "v2-IFAM-01-tuition-funding"]
    assert len(tuition) == 2 and len(deposits) == 2
    for d in deposits:
        assert any(0 < (t.posted_date - d.posted_date).days <= 31 for t in tuition)


@pytest.mark.parametrize("pid", ["v2-DRENT-01", "v2-IFAM-02"])
def test_a_generated_year_imports_and_reconciles(pid: str, tmp_path: Path) -> None:
    from datetime import datetime

    from lookahead.db import connect
    from lookahead.generator.writer import write_persona
    from lookahead.ingest.reconcile import reconcile
    from lookahead.ingest.scotiabank import parse_export
    from lookahead.ingest.store import store_exports

    p = build_persona(pid)
    export, _ = write_persona(p.persona, tmp_path, 1, START, END)
    conn = connect(tmp_path / "t.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    assert reconcile(conn, "acct").ok
    conn.close()


def test_the_aid_deposit_text_in_the_export_is_the_banks_not_a_governments(tmp_path: Path) -> None:
    from lookahead.generator.writer import write_persona

    p = build_persona("v2-DRENT-01")
    export, _ = write_persona(p.persona, tmp_path, 1, START, END)
    text = export.read_text(encoding="utf-8-sig")
    assert "Bmo Bank Of Montreal" in text
    assert "Aide Financi" not in text and "Gouvernement" not in text


def test_every_student_persona_has_a_spec_and_no_one_else_does() -> None:
    assert set(PERSONA_SPECS) == set(STUDENT_IDS)
    assert len(PERSONA_SPECS) == 18


def test_the_recorded_dates_are_in_the_window() -> None:
    for pid in STUDENT_IDS:
        for s in sources(build_persona(pid), DatedSeries):
            assert all(START <= i.due <= END + timedelta(days=45) for i in s.items), pid


# --- Regular income tied to costs: the coverage ratio (DECISIONS.md, "Mix v2 revised") ---

COVERAGE = {"IFAM": (1.05, 1.30), "ITIGHT": (0.90, 1.05), "DHOME": (1.00, 1.30),
            "DRENT": (0.95, 1.15)}  # fmt: skip


@pytest.mark.parametrize("pid", STUDENT_IDS)
def test_every_student_persona_has_its_archetypes_coverage_ratio(pid: str) -> None:
    from lookahead.generator.archetypes import coverage

    low, high = COVERAGE[pid.split("-")[1]]
    ratio = coverage(build_persona(pid).persona).ratio
    assert low - 0.02 <= ratio <= high + 0.02, (pid, ratio)  # rounding of the allowance to $10


def test_the_reliable_international_students_are_covered_and_the_stretched_ones_are_not() -> None:
    from lookahead.generator.archetypes import coverage

    ifam = [coverage(build_persona(p).persona).ratio for p in STUDENT_IDS if "-IFAM-" in p]
    itight = [coverage(build_persona(p).persona).ratio for p in STUDENT_IDS if "-ITIGHT-" in p]
    assert min(ifam) > max(itight)  # the contrast the archetypes exist for


def test_the_coverage_ratio_is_recomputed_from_the_persona_not_stored() -> None:
    from lookahead.generator.archetypes import coverage

    p = build_persona("v2-IFAM-01").persona
    assert coverage(p).income_cents > 0 and coverage(p).costs_cents > 0
    assert coverage(p).ratio == pytest.approx(coverage(p).income_cents / coverage(p).costs_cents)


def test_itight_wages_stay_under_the_cap_and_the_family_makes_up_the_rest() -> None:
    from lookahead.generator.archetypes import split_income

    assert split_income(1000, 300, 700, wage_cap=500) == (300, 700)  # in proportion, under the cap
    assert split_income(2000, 600, 400, wage_cap=900) == (900, 1100)  # capped: family makes up
    wages, family = split_income(1500, 100, 100, wage_cap=10_000)
    assert wages + family == 1500 and wages == family
    for pid in [p for p in PERSONA_IDS if "-ITIGHT-" in p]:
        pay = sources(build_persona(pid), HourlyPay)[0]
        assert pay.term_hours <= 24 + 1e-9, pid  # whatever the target, the cap holds


def test_the_expected_everyday_spending_matches_what_the_profile_really_spends() -> None:
    import numpy as np

    from lookahead.generator.archetypes import expected_monthly_spending

    profile = build_persona("v2-DRENT-01").persona.spending
    assert profile is not None
    rng = np.random.default_rng(7)
    days, total = 6_000, 0
    for k in range(days):
        made = date(2026, 1, 1) + timedelta(days=k)
        total += -sum(t.amount_cents for t in profile.day(made, rng, 1.0))
    simulated = total / days * 30.44
    assert expected_monthly_spending(profile) == pytest.approx(simulated, rel=0.06)


def test_tuition_and_the_deposits_that_fund_it_are_outside_the_ratio() -> None:
    """Dropping tuition and its funding from a persona does not change its coverage ratio."""
    from dataclasses import replace

    from lookahead.generator.archetypes import coverage

    persona = build_persona("v2-IFAM-01").persona
    plain = replace(
        persona,
        sources=tuple(
            s
            for s in persona.sources
            if not (isinstance(s, DatedSeries) and "tuition" in s.series_id)  # fmt: skip
        ),
    )
    assert coverage(plain) == coverage(persona)


def _aid_and_tuition(pid: str) -> tuple[list, int, int]:  # type: ignore[type-arg]
    srcs = list(build_persona(pid).persona.sources)
    aid = sum(i.amount_cents for s in srcs if isinstance(s, DatedSeries)
              and s.series_id.endswith("-aid") for i in s.items)  # fmt: skip
    tuition = -sum(i.amount_cents for s in srcs if isinstance(s, DatedSeries)
                   and s.series_id.endswith("-tuition") for i in s.items)  # fmt: skip
    return srcs, aid, tuition


def test_aid_that_funds_tuition_is_outside_the_ratio_and_the_rest_counts() -> None:
    from lookahead.generator.archetypes import _aid_regular_monthly

    srcs, aid, tuition = _aid_and_tuition("v2-DRENT-05")  # tuition not from chequing
    assert tuition == 0 and _aid_regular_monthly(srcs) == pytest.approx(aid / 12)  # all counts
    srcs, aid, tuition = _aid_and_tuition("v2-DRENT-03")  # aid funds the tuition
    assert tuition > 0
    assert _aid_regular_monthly(srcs) == pytest.approx(max(0, aid - tuition) / 12)
    assert _aid_regular_monthly(srcs) < aid / 12  # the tuition part is left out


# --- The last revision: ITIGHT second transfer and low opening balance, FREE scaled ---


def test_itight_second_transfer_is_15_to_45_days_after_tuition_and_part_of_the_gap() -> None:
    present = absent = 0
    for pid in [p for p in PERSONA_IDS if "-ITIGHT-" in p and PERSONA_SPECS[p].from_chequing]:
        p = build_persona(pid)
        tuition, funding = tuition_and_funding(pid)
        top = [s for s in sources(p, DatedSeries) if s.series_id.endswith("-tuition-topup")]
        topups = {i.link_id: i for s in top for i in s.items}
        assert tuition is not None and funding is not None
        for deposit in funding.items:
            term = deposit.link_id
            paid = sum(-i.amount_cents for i in tuition.items if i.link_id == term)
            gap = paid - deposit.amount_cents
            if term in topups:
                present += 1
                extra = topups[term]
                first = min(i.due for i in tuition.items if i.link_id == term)
                assert 15 <= (extra.due - first).days <= 45
                assert 0 < extra.amount_cents <= gap  # part of the gap, never more than it
                assert extra.amount_cents >= min(gap, 5_000)
            else:
                absent += 1
    assert present >= 3 and absent >= 1  # about 70 percent of the terms that have a gap


def test_no_second_transfer_when_the_family_pays_the_university_or_the_deposit_covers_it() -> None:
    for pid in PERSONA_IDS:
        has = any(
            isinstance(s, DatedSeries) and s.series_id.endswith("-tuition-topup")
            for s in build_persona(pid).persona.sources
        )
        if "-ITIGHT-" not in pid or not PERSONA_SPECS[pid].from_chequing:
            assert not has, pid  # only ITIGHT with tuition from chequing


def test_the_second_transfer_is_outside_the_coverage_ratio() -> None:
    from dataclasses import replace

    from lookahead.generator.archetypes import coverage

    persona = build_persona("v2-ITIGHT-03").persona
    plain = replace(
        persona,
        sources=tuple(
            s
            for s in persona.sources
            if not (
                isinstance(s, DatedSeries) and s.series_id.endswith("-tuition-topup")
            )  # fmt: skip
        ),
    )
    assert coverage(plain) == coverage(persona)


@pytest.mark.parametrize("pid", [p for p in PERSONA_IDS if "-FREE-" in p])
def test_freelancer_deposits_are_scaled_to_a_coverage_ratio_of_95_to_115_percent(pid: str) -> None:
    from lookahead.generator.archetypes import coverage

    assert 0.95 - 0.04 <= coverage(build_persona(pid).persona).ratio <= 1.15 + 0.04


def test_freelancer_deposits_keep_the_shape_of_the_old_range() -> None:
    for pid in [p for p in PERSONA_IDS if "-FREE-" in p]:
        series = sources(build_persona(pid), IrregularSeries)[0]
        low, high = series.amount_range_cents
        assert series.gap_days == (5, 20) and low >= 5_000 and 3 <= high / low <= 12


def test_the_generator_command_refuses_a_held_out_persona_before_writing(tmp_path: Path) -> None:
    from lookahead.generator.archetypes import main

    with pytest.raises(ValueError, match="held-out"):
        main(["--only", "v2-SAL-01", "v2-SAL-04", "--out", str(tmp_path)])
    assert not list(tmp_path.iterdir())
