"""The per-persona report of generated histories (no forecasts)."""

from datetime import date

import pytest

from eval.backtest import origins
from eval.persona_report import (
    PersonaLine,
    acceptance_checks,
    balances_by_day,
    format_line,
    persona_line,
)
from lookahead.generator.archetypes import END, PERSONA_IDS, START, build_persona, development_ids
from lookahead.generator.writer import export_rows

PID = "v2-TIGHT-01"


def test_the_lowest_and_closing_balance_are_the_running_balance_of_the_generated_rows() -> None:
    p = build_persona(PID)
    rows = [t for t in export_rows(p.persona, START, END, 42) if t.posted_date <= END]
    balance = low = p.persona.opening_balance_cents
    for t in rows:
        balance += t.amount_cents
        low = min(low, balance)
    line = persona_line(PID)
    assert line.lowest_cents == low and line.closing_cents == balance


def test_origins_below_the_buffer_are_counted_from_the_balance_at_each_origin() -> None:
    p = build_persona(PID)
    by_day, opening, _ = balances_by_day(PID)
    expected = 0
    for origin in origins():
        before = [d for d in by_day if d <= origin]
        start = by_day[max(before)] if before else opening
        expected += start < p.buffer_cents
    line = persona_line(PID)
    assert line.origins_below_buffer == expected and line.origins == 40
    assert 0 <= line.origins_below_buffer <= 40


def test_the_line_names_the_persona_and_a_held_out_persona_is_refused() -> None:
    assert "dev" in format_line(persona_line("v2-SAL-01"))
    with pytest.raises(ValueError, match="held-out"):
        persona_line("v2-SAL-04")
    assert "of 40 origins start below the buffer" in format_line(persona_line(PID))


def test_every_development_persona_has_a_line() -> None:
    lines = [persona_line(pid) for pid in development_ids()]
    assert len(lines) == 20 and {ln.persona_id for ln in lines} == set(development_ids())
    assert len(PERSONA_IDS) == 30
    assert all(ln.origins == 40 for ln in lines)
    assert date(2025, 12, 1) in origins()


# --- The acceptance checks fixed in DECISIONS.md before regenerating ---


def line(pid: str, lowest: int = 100_000, closing: int = 500_000, below: int = 0,
         buffer: int = 10_000) -> PersonaLine:  # fmt: skip
    code = pid.split("-")[1]
    return PersonaLine(pid, code, False, 1.0, buffer, lowest, closing, below, 40)


def verdict(results, name_start: str):  # type: ignore[no-untyped-def]
    return next(r for r in results if r.name.startswith(name_start))


def test_ifam_may_start_below_its_buffer_at_no_more_than_three_origins() -> None:
    ok = acceptance_checks([line("v2-IFAM-01", below=3)])
    assert verdict(ok, "IFAM").passed
    bad = acceptance_checks([line("v2-IFAM-02", below=4)])
    assert not verdict(bad, "IFAM").passed and "v2-IFAM-02" in " ".join(
        verdict(bad, "IFAM").details
    )


def test_itight_must_dip_near_its_buffer_but_not_run_away() -> None:
    fine = line("v2-ITIGHT-01", lowest=40_000, buffer=10_000)  # buffer plus $300 is the limit
    assert verdict(acceptance_checks([fine]), "ITIGHT").passed
    never_dips = line("v2-ITIGHT-02", lowest=40_001, buffer=10_000)
    assert not verdict(acceptance_checks([never_dips]), "ITIGHT").passed
    runaway = line("v2-ITIGHT-03", lowest=-300_001, buffer=10_000)
    result = verdict(acceptance_checks([runaway]), "ITIGHT")
    assert not result.passed and "v2-ITIGHT-03" in " ".join(result.details)
    edge = line("v2-ITIGHT-04", lowest=-300_000, buffer=10_000)
    assert verdict(acceptance_checks([edge]), "ITIGHT").passed


def test_the_other_archetypes_may_start_below_the_buffer_at_up_to_half_the_origins() -> None:
    for pid in ("v2-DHOME-01", "v2-DRENT-01", "v2-TIGHT-01", "v2-FREE-01"):
        assert verdict(acceptance_checks([line(pid, below=20)]), "DHOME").passed
        failed = verdict(acceptance_checks([line(pid, below=21)]), "DHOME")
        assert not failed.passed and pid in " ".join(failed.details)


def test_sal_and_sem_must_come_out_exactly_as_before() -> None:
    same = line("v2-SAL-01", lowest=295_000, closing=3_218_004)
    assert verdict(acceptance_checks([same]), "SAL").passed
    changed = line("v2-SAL-01", lowest=295_001, closing=3_218_004)
    result = verdict(acceptance_checks([changed]), "SAL")
    assert not result.passed and "v2-SAL-01" in " ".join(result.details)


def test_the_real_generated_personas_get_a_verdict_on_each_of_the_four_checks() -> None:
    results = acceptance_checks([persona_line(pid) for pid in development_ids()])
    assert [r.name.split()[0].rstrip(":,") for r in results] == ["IFAM", "ITIGHT", "DHOME", "SAL"]
    assert all(isinstance(r.passed, bool) for r in results)


# --- With --only, a check that did not see all its personas is skipped, not passed (R6) ---


def test_a_check_run_on_part_of_its_personas_says_it_was_skipped() -> None:
    from eval.persona_report import verdict_line

    every = development_ids()
    lines = [persona_line("v2-SAL-01")]
    by_name = {r.name.split(":")[0]: r for r in acceptance_checks(lines)}
    ifam = verdict_line(by_name["IFAM"], ["v2-SAL-01"], every)
    assert ifam.startswith("SKIPPED") and "v2-IFAM-01" in ifam  # it saw no IFAM persona at all
    sal = verdict_line(by_name["SAL, SEM"], ["v2-SAL-01"], every)
    assert sal.startswith("SKIPPED") and "v2-SEM-01" in sal and "v2-SAL-01," not in sal
    assert verdict_line(by_name["IFAM"], every, every).startswith("PASS")


def test_the_command_with_only_prints_skipped_checks(capsys) -> None:  # type: ignore[no-untyped-def]
    from eval.persona_report import main

    main(["--only", "v2-SAL-01", "--check"])
    text = capsys.readouterr().out
    assert "SKIPPED  IFAM" in text and "PASS  IFAM" not in text
