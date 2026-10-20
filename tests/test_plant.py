"""The planter (lookahead.generator.plant): planted copies of the development personas."""

import hashlib
from datetime import date, timedelta
from functools import cache
from pathlib import Path

import pytest

from lookahead.generator.archetypes import (
    END,
    HELD_OUT,
    LONG_HELD_OUT,
    build_persona,
    development_ids,
)
from lookahead.generator.plant import (
    NAMES,
    SEED,
    PlantedEvent,
    month_index,
    plant,
    read_answer_key,
    write_planted,
)
from lookahead.generator.schedules import GeneratedTxn
from lookahead.generator.writer import export_rows, write_export
from lookahead.ingest.scotiabank import parse_export

RENT_PERSONA = "v2-IFAM-03"  # pays rent by e-Transfer and has subscriptions
BILL_RENT_PERSONA = "v2-FREE-01"  # rent is a bill payment, no subscriptions
SEEDS = range(40)


@cache
def rows_of(persona_id: str) -> tuple[GeneratedTxn, ...]:
    persona = build_persona(persona_id)
    return tuple(export_rows(persona.persona, persona.start, END, 42))


def planted(persona_id: str, seed: int) -> tuple[list[GeneratedTxn], list[PlantedEvent]]:
    return plant(persona_id, rows_of(persona_id), seed)


def first_seed_with(persona_id: str, what: str) -> int:
    for seed in SEEDS:
        if any(e.what == what for e in planted(persona_id, seed)[1]):
            return seed
    raise AssertionError(f"no seed in {SEEDS} plants {what!r}")


def rent_of(rows: list[GeneratedTxn]) -> list[GeneratedTxn]:
    return [t for t in rows if t.series_id and t.series_id.endswith("-rent")]


def test_months_are_counted_from_october_2025() -> None:
    assert month_index(date(2025, 10, 1)) == 1 and month_index(date(2026, 9, 30)) == 12
    assert month_index(date(2025, 9, 30)) == 0 and month_index(date(2024, 10, 15)) == -11


@pytest.mark.parametrize("held_out", [sorted(HELD_OUT)[0], sorted(LONG_HELD_OUT)[0]])
def test_held_out_personas_are_refused_everywhere(held_out: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        plant(held_out, [])
    with pytest.raises(ValueError):
        write_planted(held_out, tmp_path)
    with pytest.raises(ValueError):
        read_answer_key(tmp_path, held_out)
    assert list(tmp_path.iterdir()) == []  # nothing was written


def test_the_input_rows_are_never_changed() -> None:
    rows = rows_of(RENT_PERSONA)
    before = list(rows)
    for seed in range(5):
        plant(RENT_PERSONA, rows, seed)
    assert list(rows) == before


def test_the_same_seed_gives_the_same_copy_and_other_seeds_differ() -> None:
    a, events_a = planted(RENT_PERSONA, SEED)
    b, events_b = planted(RENT_PERSONA, SEED)
    assert a == b and events_a == events_b
    assert len({tuple(e.event_id for e in planted(RENT_PERSONA, s)[1]) for s in SEEDS}) > 3


def test_a_rent_increase_is_five_to_twelve_percent_rounded_to_five_dollars() -> None:
    seed = first_seed_with(RENT_PERSONA, "increase")
    original, (event,) = (
        rows_of(RENT_PERSONA),
        [e for e in planted(RENT_PERSONA, seed)[1] if e.what == "increase"],
    )
    rows, _ = planted(RENT_PERSONA, seed)
    old, new, month = event.details["old_cents"], event.details["new_cents"], event.details["month"]
    assert new % 500 == 0 and new - old >= 500
    assert old * 1.05 - 250 <= new <= old * 1.12 + 250 and 5 <= month <= 9
    amounts = {month_index(t.posted_date) >= month: abs(t.amount_cents) for t in rent_of(rows)}
    assert amounts == {False: old, True: new}  # one amount before, one after
    assert {abs(t.amount_cents) for t in rent_of(list(original))} == {old}


def test_late_rent_moves_payments_two_to_eight_days_from_month_four_only() -> None:
    seed = first_seed_with(RENT_PERSONA, "late")
    rows, events = planted(RENT_PERSONA, seed)
    event = next(e for e in events if e.what == "late")
    before = sorted(t.posted_date for t in rent_of(list(rows_of(RENT_PERSONA))))
    after = sorted(t.posted_date for t in rent_of(rows))
    assert len(before) == len(after)
    moved = [(b, a) for b, a in zip(before, after, strict=True) if a != b]
    assert len(moved) == event.details["late_payments"] > 0
    for b, a in moved:
        assert month_index(b) >= 4 and a.weekday() < 5
        assert 2 <= (a - b).days <= 8 + 3  # up to eight days, then the next business day
    assert all(a == b for b, a in zip(before, after, strict=True) if month_index(b) < 4)
    assert event.details["send_days"] == sorted(d.day for d in after if month_index(d) >= 4)


def test_a_renamed_subscription_keeps_its_amount_and_dates_and_changes_its_text() -> None:
    seed = first_seed_with(RENT_PERSONA, "rename")
    original = list(rows_of(RENT_PERSONA))
    rows, events = planted(RENT_PERSONA, seed)
    event = next(e for e in events if e.what == "rename")
    d = event.details
    old = [t for t in original if t.series_id == d["series_id"]]
    new = [t for t in rows if t.series_id == d["series_id"]]
    assert [(t.posted_date, t.amount_cents) for t in old] == [
        (t.posted_date, t.amount_cents) for t in new
    ]
    changed = [t for t in new if t.merchant == d["new_name"]]
    assert len(changed) >= 2 and 4 <= d["month"] <= 9
    assert all(month_index(t.posted_date) >= d["month"] for t in changed)
    assert all(d["new_name"] in t.sub_description for t in changed)
    old_name, new_name = str(d["old_name"]).casefold(), str(d["new_name"]).casefold()
    assert not new_name.startswith(old_name) and not old_name.startswith(new_name)
    assert d["new_name"] in NAMES


def test_a_new_subscription_has_two_or_three_whole_dollar_monthly_charges() -> None:
    seed = first_seed_with(RENT_PERSONA, "new")
    rows, events = planted(RENT_PERSONA, seed)
    event = next(e for e in events if e.what == "new")
    planted_rows = [t for t in rows if t.series_id == f"{RENT_PERSONA}-sub-planted"]
    mine = sorted(planted_rows, key=lambda t: t.posted_date)
    assert 2 <= len(mine) <= 3 and event.details["first_month"] in (10, 11)
    assert len({t.amount_cents for t in mine}) == 1
    assert -3000 <= mine[0].amount_cents <= -500 and mine[0].amount_cents % 100 == 0
    assert event.observable_from == mine[1].posted_date
    gaps = [(b.posted_date - a.posted_date).days for a, b in zip(mine, mine[1:], strict=False)]
    assert all(25 <= g <= 38 for g in gaps)


def untouched(batch: tuple[GeneratedTxn, ...] | list[GeneratedTxn]) -> list[GeneratedTxn]:
    """The rows no event can change: not rent, not a subscription."""
    return [t for t in batch if not any(k in (t.series_id or "") for k in ("-rent", "-sub-"))]


def test_every_row_that_is_not_part_of_an_event_is_unchanged() -> None:
    for seed in range(8):
        rows, events = planted(RENT_PERSONA, seed)
        assert untouched(rows) == untouched(rows_of(RENT_PERSONA)) and len(events) <= 4


def test_a_persona_without_e_transfer_rent_gets_no_rent_event() -> None:
    kinds = {e.kind for s in SEEDS for e in planted(BILL_RENT_PERSONA, s)[1]}
    assert "rent" not in kinds and "link" not in kinds  # a bill-payment rent; no subscriptions
    assert "possible" in kinds  # but a new subscription can still be planted


def test_the_rows_stay_in_date_order() -> None:
    for seed in range(6):
        rows, _ = planted(RENT_PERSONA, seed)
        assert [t.posted_date for t in rows] == sorted(t.posted_date for t in rows)


def test_the_24_month_set_uses_the_same_calendar_months() -> None:
    long_id = "v2L-IFAM-03"
    seed = next(s for s in SEEDS if any(e.what == "increase" for e in planted(long_id, s)[1]))
    rows, events = planted(long_id, seed)
    event = next(e for e in events if e.what == "increase")
    first_new = date.fromisoformat(event.details["first_new_payment"])
    assert date(2026, 2, 1) <= first_new <= date(2026, 6, 30)
    older = [t for t in rent_of(rows) if t.posted_date < date(2025, 10, 1)]
    assert older and len({t.amount_cents for t in older}) == 1  # the year before is untouched


def csv_hash(persona_id: str, folder: Path) -> str:
    persona = build_persona(persona_id)
    path = folder / f"{persona_id}.csv"
    write_export(list(rows_of(persona_id)), persona.persona.opening_balance_cents, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_writing_planted_copies_leaves_the_existing_sets_byte_identical(tmp_path: Path) -> None:
    ids = [RENT_PERSONA, BILL_RENT_PERSONA, "v2L-IFAM-03"]
    before = {pid: csv_hash(pid, tmp_path) for pid in ids}
    out = tmp_path / "planted"
    for pid in ids:
        write_planted(pid, out / ("long" if pid.startswith("v2L") else "12"))
    # the generator is asked again, from scratch, after the planter ran
    rows_of.cache_clear()
    after = {pid: csv_hash(pid, tmp_path) for pid in ids}
    assert before == after
    written = {p.relative_to(tmp_path).parts[0] for p in tmp_path.rglob("*") if p.is_file()}
    assert written <= {"planted", *(f"{pid}.csv" for pid in ids)}  # nothing outside its folder


def test_a_planted_export_parses_and_the_answer_key_reads_back(tmp_path: Path) -> None:
    events = write_planted(RENT_PERSONA, tmp_path)
    export = tmp_path / RENT_PERSONA / "export.csv"
    parsed = parse_export(export)
    original = rows_of(RENT_PERSONA)
    extra = sum(len(e.details.get("charge_dates", [])) for e in events)
    assert len(parsed) == len(original) + extra
    assert read_answer_key(tmp_path, RENT_PERSONA) == events
    assert all(e.observable_from <= END for e in events)
    assert all(e.persona_id == RENT_PERSONA for e in events)
    assert all(e.event_id.startswith(RENT_PERSONA) for e in events)


def test_the_development_sets_have_twenty_personas_each() -> None:
    assert len(development_ids(long=False)) == len(development_ids(long=True)) == 20
    assert date(2025, 10, 1) + timedelta(days=364) == END
