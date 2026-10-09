"""The per-persona report of generated histories (no forecasts)."""

from datetime import date

from eval.backtest import origins
from eval.persona_report import balances_by_day, format_line, persona_line
from lookahead.generator.archetypes import END, PERSONA_IDS, START, build_persona
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


def test_the_line_names_the_persona_and_says_whether_it_is_held_out() -> None:
    assert "dev" in format_line(persona_line("v2-SAL-01"))
    assert "held-out" in format_line(persona_line("v2-SAL-04"))
    assert "of 40 origins start below the buffer" in format_line(persona_line(PID))


def test_every_persona_has_a_line() -> None:
    lines = [persona_line(pid) for pid in PERSONA_IDS]
    assert len(lines) == 30 and {ln.persona_id for ln in lines} == set(PERSONA_IDS)
    assert all(ln.origins == 40 for ln in lines)
    assert date(2025, 12, 1) in origins()
