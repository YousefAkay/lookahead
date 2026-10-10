"""Tests for the 24-month persona set `v2L` (DECISIONS.md, 2026-10-11, step A). Synthetic
development personas only; held-out ids are only checked for refusal, never generated."""

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from eval import metrics
from eval.backtest import FORMAT, FORMAT_LONG, run_persona, select_personas
from lookahead.generator.archetypes import (
    END,
    HELD_OUT,
    LONG_HELD_OUT,
    LONG_IDS,
    LONG_START,
    PERSONA_IDS,
    PERSONA_SPECS,
    START,
    assert_development_only,
    base_id,
    build_persona,
    coverage,
    development_ids,
    is_long,
)
from lookahead.generator.writer import export_rows, write_persona

# --- Ids and the held-out list ---


def test_the_long_ids_are_the_30_specs_with_the_v2l_prefix() -> None:
    assert len(LONG_IDS) == 30 and all(pid.startswith("v2L-") for pid in LONG_IDS)
    assert [base_id(pid) for pid in LONG_IDS] == list(PERSONA_IDS)
    assert is_long("v2L-SAL-01") and not is_long("v2-SAL-01")
    assert base_id("v2-SAL-01") == "v2-SAL-01"


def test_the_held_out_ids_are_the_same_ten() -> None:
    assert LONG_HELD_OUT == {pid.replace("v2-", "v2L-") for pid in HELD_OUT}
    assert len(development_ids(long=True)) == 20
    assert not set(development_ids(long=True)) & LONG_HELD_OUT
    assert development_ids() == [pid for pid in PERSONA_IDS if pid not in HELD_OUT]


def test_the_development_only_guard_refuses_a_long_held_out_persona() -> None:
    assert_development_only(["v2L-SAL-01", "v2L-ITIGHT-03"])
    with pytest.raises(ValueError, match="held-out"):
        assert_development_only(["v2L-SAL-01", "v2L-ITIGHT-04"])
    with pytest.raises(ValueError, match="held-out"):
        select_personas(["v2L-TIGHT-04"], include_held_out=False, long=True)


def test_the_runner_refuses_a_long_held_out_persona_before_generating_it(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="held-out"):
        run_persona("v2L-IFAM-04", tmp_path, [date(2026, 3, 2)], 10, 42, tmp_path / "w")
    assert not list(tmp_path.glob("*.jsonl"))


def test_the_two_sets_are_never_mixed_in_one_run() -> None:
    assert select_personas(None, False, long=True) == development_ids(long=True)
    with pytest.raises(ValueError, match="never mixed"):
        select_personas(["v2-SAL-01", "v2L-SAL-01"], include_held_out=False, long=True)
    with pytest.raises(ValueError, match="never mixed"):
        select_personas(["v2L-SAL-01"], include_held_out=False, long=False)


def test_the_metrics_refuse_to_pool_the_two_sets(tmp_path: Path) -> None:
    def write(pid: str, fmt: int | str) -> None:
        row = {"persona": pid, "dataset": "synthetic", "format": fmt, "logic": metrics.LOGIC,
               "held_out": False, "origin": "2026-03-02"}  # fmt: skip
        (tmp_path / f"{pid}.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    write("v2L-SAL-01", FORMAT_LONG)
    assert len(metrics.load_records(tmp_path)) == 1
    write("v2L-SAL-02", FORMAT)  # a long persona with the 12-month marker: refused
    with pytest.raises(ValueError, match="older version"):
        metrics.load_records(tmp_path)
    write("v2L-SAL-02", FORMAT_LONG)
    write("v2-SAL-01", FORMAT)
    with pytest.raises(ValueError, match="never pooled"):
        metrics.load_records(tmp_path)


# --- The 12-month set is unchanged, byte for byte ---

# Taken before the long option was written (commit 328bd10): the sha256 of each development
# persona's export followed by its answer key, seed 42, START to END.
CRLF, LF = bytes([13, 10]), bytes([10])  # Windows writes the answer key with CRLF
BEFORE = {
    "v2-IFAM-01": "6957834ef1e9d986", "v2-IFAM-02": "f725244ac8f2c508",
    "v2-IFAM-03": "e8e5bf28079fbc5d", "v2-ITIGHT-01": "0752ac189e2bc700",
    "v2-ITIGHT-02": "72e311eb1178d9ab", "v2-ITIGHT-03": "3dc1f6b4fc04e382",
    "v2-DHOME-01": "a2d031a7c7b13fe1", "v2-DHOME-02": "69707b6f287be3e3",
    "v2-DHOME-03": "1a9109b884c83be8", "v2-DRENT-01": "2eeae9cfc4d27968",
    "v2-DRENT-02": "bd85d7eddc91694f", "v2-DRENT-03": "4c576611eb093ac4",
    "v2-SAL-01": "4ac20a3787d871d7", "v2-SAL-02": "df072637a2b010d8",
    "v2-SAL-03": "b91ab7c7be39fcc3", "v2-SEM-01": "a1a370006361e8ff",
    "v2-TIGHT-01": "8c0afd4ce30ad4ed", "v2-TIGHT-02": "ba2929daf0cc49e2",
    "v2-TIGHT-03": "13f5cb27cddfd2be", "v2-FREE-01": "613650fed49f8714",
}  # fmt: skip


def test_the_12_month_exports_and_answer_keys_are_byte_for_byte_unchanged(tmp_path: Path) -> None:
    assert set(BEFORE) == set(development_ids())
    for pid, expected in BEFORE.items():
        export, key = write_persona(build_persona(pid).persona, tmp_path, 42, START, END)
        # the answer key is text: compare it with LF line endings, as on the machine that took it
        key_bytes = key.read_bytes().replace(CRLF, LF)
        digest = hashlib.sha256(export.read_bytes() + key_bytes).hexdigest()[:16]
        assert digest == expected, pid
        assert build_persona(pid).start == START


# --- What a long persona is ---


@pytest.mark.parametrize("pid", ["v2L-SAL-01", "v2L-IFAM-02", "v2L-ITIGHT-01", "v2L-DRENT-01",
                                 "v2L-DHOME-03", "v2L-FREE-01"])  # fmt: skip
def test_a_long_persona_has_its_namesakes_parameters_and_24_months_of_rows(pid: str) -> None:
    long, short = build_persona(pid), build_persona(base_id(pid))
    assert long.start == LONG_START and long.persona.persona_id == pid
    assert (long.buffer_cents, long.archetype) == (short.buffer_cents, short.archetype)
    assert long.persona.opening_balance_cents == short.persona.opening_balance_cents
    assert long.persona.spending == short.persona.spending
    assert coverage(short.persona).ratio > 0
    rows = [t for t in export_rows(long.persona, LONG_START, END, 42) if t.posted_date <= END]
    months = {t.posted_date.strftime("%Y-%m") for t in rows}
    assert len(months) == 24  # every month of the two years has rows
    assert all((t.series_id or pid).startswith(pid) for t in rows)  # series carry the long id
    assert build_persona(pid) == long  # the same id gives the same persona


def test_the_daily_simulation_differs_from_the_12_month_persona() -> None:
    long = export_rows(build_persona("v2L-SAL-01").persona, LONG_START, END, 42)
    short = export_rows(build_persona("v2-SAL-01").persona, START, END, 42)
    overlap = [(t.posted_date, t.amount_cents) for t in long if t.posted_date >= START]
    assert overlap != [(t.posted_date, t.amount_cents) for t in short]


@pytest.mark.parametrize("pid", [p for p in development_ids(long=True)
                                 if PERSONA_SPECS.get(base_id(p)) is not None
                                 and PERSONA_SPECS[base_id(p)].from_chequing
                                 and PERSONA_SPECS[base_id(p)].tuition_dollars[1] > 0])  # fmt: skip
def test_tuition_is_paid_in_both_years_at_the_same_amount_to_one_payee(pid: str) -> None:
    spec = PERSONA_SPECS[base_id(pid)]
    rows = export_rows(build_persona(pid).persona, LONG_START, END, 42)
    paid = [t for t in rows if (t.series_id or "").endswith("-tuition") and t.posted_date <= END]
    assert len({t.merchant for t in paid}) == 1
    assert len({t.amount_cents for t in paid}) <= 2  # one amount (two halves if installments)
    by_term_start = Counter(
        (t.link_id or "").rsplit("-", 1)[1].startswith("p") for t in paid
    )  # "p": a term of the year before
    terms = 3 if spec.summer else 2
    per_term = spec.installments
    # The earlier year: Winter, (Summer), Fall 2025, every installment inside the window.
    assert by_term_start[True] == terms * per_term
    # The base year: the payments that fall by September 30 2026 (a Fall second half may not).
    assert by_term_start[False] >= (terms - 1) * per_term + 1
    years = Counter(t.posted_date.year for t in paid)
    assert years[2025] >= terms and years[2026] >= terms - 1 + (per_term > 1)


def test_the_earlier_year_repeats_breaks_exams_and_months_away() -> None:
    away = build_persona("v2L-DRENT-02").persona
    pay = [
        t for t in export_rows(away, LONG_START, END, 42) if (t.series_id or "").endswith("-pay")
    ]
    for summer in (2025, 2026):  # no wages paid in July or August, in both years
        assert not [
            t for t in pay if t.posted_date.year == summer and t.posted_date.month in (7, 8)
        ]
    assert [t for t in pay if t.posted_date.year == 2024]  # wages in the first months too
