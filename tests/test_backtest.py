"""Tests for the backtest runner (week 4, step 1): origins, realized balances, rule zero, resuming.

Made-up personas from generator mix v2 only; nothing reads real data.
"""

import json
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from eval import backtest
from eval.backtest import (
    FIRST_ORIGIN,
    LAST_ORIGIN,
    origins,
    quick_origins,
    realized_balances,
    run_origin,
    run_persona,
    select_personas,
)
from lookahead.db import connect
from lookahead.generator.archetypes import (
    END,
    HELD_OUT,
    PERSONA_IDS,
    QUICK_IDS,
    START,
    build_persona,
)
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.recurring_store import reject, series_as_of
from lookahead.settings import set_safety_buffer_cents

PID = "v2-SAL-01"
ORIGIN = date(2026, 3, 2)
FUTURES = 60  # small, for speed


@pytest.fixture(scope="module")
def year_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A year of one persona, imported once; tests copy it before changing anything."""
    folder = tmp_path_factory.mktemp("year")
    persona = build_persona(PID)
    export, _ = write_persona(persona.persona, folder, 42, START, END)
    conn = connect(folder / "year.db")
    store_exports(conn, PID, [(export.name, parse_export(export))], datetime(2026, 10, 1))
    set_safety_buffer_cents(conn, persona.buffer_cents)
    conn.close()
    return folder / "year.db"


@pytest.fixture
def conn(year_db: Path, tmp_path: Path) -> sqlite3.Connection:
    copy = tmp_path / "copy.db"
    shutil.copy(year_db, copy)
    return connect(copy)


# --- Origins ---


def test_origins_are_the_recorded_weekly_mondays() -> None:
    days = origins()
    assert days[0] == FIRST_ORIGIN == date(2025, 12, 1)
    assert days[-1] == LAST_ORIGIN == date(2026, 8, 31)
    assert len(days) == 40
    assert all(d.weekday() == 0 for d in days)  # Mondays
    assert all(b - a == timedelta(days=7) for a, b in zip(days, days[1:], strict=False))


def test_every_origin_has_eight_weeks_behind_it_and_thirty_observed_days_after() -> None:
    assert FIRST_ORIGIN - START >= timedelta(weeks=8)
    assert LAST_ORIGIN + timedelta(days=30) <= END


def test_the_quick_origins_are_four_of_them() -> None:
    quick = quick_origins()
    assert len(quick) == 4 and set(quick) <= set(origins())


def test_origins_can_be_limited_to_a_range_for_real_data() -> None:
    days = origins(date(2026, 1, 1), date(2026, 2, 1))
    assert days[0] == date(2026, 1, 5) and days[-1] == date(2026, 1, 26)


# --- Choosing personas ---


def test_by_default_only_development_personas_are_run() -> None:
    chosen = select_personas(None, include_held_out=False)
    assert len(chosen) == 20 and not set(chosen) & HELD_OUT


def test_a_held_out_persona_needs_the_explicit_flag() -> None:
    with pytest.raises(ValueError, match="held-out"):
        select_personas(["v2-TIGHT-04"], include_held_out=False)
    assert select_personas(["v2-TIGHT-04"], include_held_out=True) == ["v2-TIGHT-04"]
    assert set(select_personas(None, include_held_out=True)) == set(PERSONA_IDS)


def test_quick_mode_uses_the_three_quick_personas() -> None:
    assert select_personas(None, include_held_out=False, quick=True) == list(QUICK_IDS)


# --- Realized balances ---


def put(conn: sqlite3.Connection, day: str, cents: int, balance: int, n: int) -> None:
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES ('a', ?, ?, 'pos purchase', 'x', ?, ?, 'f', '2026-10-01')",
            (day, cents, balance, f"fp{n}"),
        )


def test_realized_balances_are_the_closing_balance_of_each_day_carried_forward(
    tmp_path: Path,
) -> None:
    c = connect(tmp_path / "r.db")
    put(c, "2026-03-02", -500, 9500, 1)  # the origin day itself: not part of the answer
    put(c, "2026-03-03", -300, 9200, 2)  # a day's last posting is stored first (newest first)
    put(c, "2026-03-03", -200, 9400, 3)
    put(c, "2026-03-05", 1000, 10200, 4)
    got = realized_balances(c, "a", date(2026, 3, 2), horizon=4)
    assert got == [9200, 9200, 10200, 10200]  # Mar 3, 4 (carried), 5, 6 (carried)
    c.close()


def test_realized_balances_carry_the_origin_balance_over_quiet_days(tmp_path: Path) -> None:
    c = connect(tmp_path / "r.db")
    put(c, "2026-03-02", -500, 9500, 1)
    put(c, "2026-03-04", -100, 9400, 2)
    assert realized_balances(c, "a", date(2026, 3, 2), horizon=3) == [9500, 9400, 9400]
    c.close()


# --- One origin ---


def test_a_record_has_the_forecast_and_what_happened(conn: sqlite3.Connection) -> None:
    record = run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)
    assert record["origin"] == "2026-03-02" and record["persona"] == PID
    assert record["dataset"] == "synthetic" and record["held_out"] is False
    assert record["archetype"] == "SAL" and record["buffer_cents"] == 30_000
    for key in ("p10", "p50", "p90", "point", "realized", "days"):
        assert len(record[key]) == 30, key
    assert all(a <= b <= c for a, b, c in zip(record["p10"], record["p50"], record["p90"],
                                              strict=True))  # fmt: skip
    assert len(record["lowest_cents"]) == FUTURES
    assert record["realized_lowest_cents"] == min(record["realized"])
    assert record["shortfall_probability"] == pytest.approx(
        sum(low < record["buffer_cents"] for low in record["lowest_cents"]) / FUTURES
    )
    json.dumps(record)  # it must be saveable


def test_the_realized_balances_are_the_rows_after_the_origin(conn: sqlite3.Connection) -> None:
    record = run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)
    assert record["realized"] == realized_balances(conn, PID, ORIGIN)
    assert record["days"][0] == "2026-03-03" and record["days"][-1] == "2026-04-01"


# --- Rule zero ---


def forecast_part(record: dict) -> dict:  # type: ignore[type-arg]
    """Everything in a record that is a forecast (not what happened afterwards)."""
    skip = {"realized", "realized_lowest_cents", "realized_first_below_buffer",
            "realized_below_zero"}  # fmt: skip
    return {k: v for k, v in record.items() if k not in skip}


@pytest.mark.slow
def test_rows_posted_after_the_origin_cannot_change_the_forecast(
    conn: sqlite3.Connection,
) -> None:
    full = run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)
    with conn:  # the future vanishes
        conn.execute("DELETE FROM transactions WHERE posted_date > ?", (ORIGIN.isoformat(),))
    truncated = run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)
    assert forecast_part(full) == forecast_part(truncated)


@pytest.mark.slow
def test_changing_the_future_cannot_change_the_forecast(conn: sqlite3.Connection) -> None:
    before = run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)
    with conn:  # every later amount and balance is different, and new rows appear
        conn.execute(
            "UPDATE transactions SET amount_cents = amount_cents * 3,"
            " balance_after_cents = balance_after_cents + 777777 WHERE posted_date > ?",
            (ORIGIN.isoformat(),),
        )
    after = run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)
    assert forecast_part(before) == forecast_part(after)
    assert before["realized"] != after["realized"]  # but what happened is read from the rows


@pytest.mark.slow
def test_recurring_series_are_detected_again_from_the_rows_up_to_the_origin(
    conn: sqlite3.Connection,
) -> None:
    early, _ = series_as_of(conn, PID, date(2025, 12, 1))
    late, _ = series_as_of(conn, PID, date(2026, 8, 31))
    assert len(late) >= len(early)
    # the series known at an origin do not change when later rows exist or not
    with conn:
        conn.execute("DELETE FROM transactions WHERE posted_date > '2025-12-01'")
    again, _ = series_as_of(conn, PID, date(2025, 12, 1))
    assert [(s.key, s.expected_cents, s.next_expected) for s in early] == [
        (s.key, s.expected_cents, s.next_expected) for s in again
    ]


@pytest.mark.slow
def test_a_confirmation_made_after_the_origin_is_not_used_but_one_before_it_is(
    conn: sqlite3.Connection,
) -> None:
    base = run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)
    series, _ = series_as_of(conn, PID, ORIGIN)
    # rejecting the largest outflow series changes the forecast only if it is known by then
    biggest = min((s for s in series if s.direction == "out"), key=lambda s: s.expected_cents)
    reject(conn, PID, biggest.key, ORIGIN + timedelta(days=1))  # decided the day after
    assert forecast_part(run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)) == forecast_part(base)
    reject(conn, PID, biggest.key, ORIGIN - timedelta(days=1))  # decided the day before
    assert forecast_part(run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)) != forecast_part(base)


# --- A persona, saved and resumable ---


def calls(monkeypatch: pytest.MonkeyPatch) -> list[date]:
    seen: list[date] = []
    real = backtest.run_origin

    def counting(conn, persona_id, dataset, origin, futures, *args, **kw):  # type: ignore[no-untyped-def]
        seen.append(origin)
        return real(conn, persona_id, dataset, origin, futures, *args, **kw)

    monkeypatch.setattr(backtest, "run_origin", counting)
    return seen


def test_a_persona_run_saves_one_line_per_origin(tmp_path: Path) -> None:
    days = origins()[:3]
    done = run_persona(PID, tmp_path / "out", days, FUTURES, seed=42, work_dir=tmp_path / "work")
    assert done == 3
    lines = (tmp_path / "out" / f"{PID}.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["origin"] for line in lines] == [d.isoformat() for d in days]


def test_a_stopped_run_resumes_where_it_left_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    days = origins()[:4]
    run_persona(PID, tmp_path / "out", days[:2], FUTURES, seed=42, work_dir=tmp_path / "work")
    seen = calls(monkeypatch)
    done = run_persona(PID, tmp_path / "out", days, FUTURES, seed=42, work_dir=tmp_path / "work")
    assert seen == days[2:] and done == 2  # only the two new origins were computed
    lines = (tmp_path / "out" / f"{PID}.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["origin"] for line in lines] == [d.isoformat() for d in days]
    again = run_persona(PID, tmp_path / "out", days, FUTURES, seed=42,
                        work_dir=tmp_path / "work")  # fmt: skip
    assert again == 0 and len(lines) == 4  # nothing to do the third time


def test_a_run_with_other_settings_does_not_mix_with_saved_results(tmp_path: Path) -> None:
    days = origins()[:1]
    run_persona(PID, tmp_path / "out", days, FUTURES, seed=42, work_dir=tmp_path / "work")
    with pytest.raises(ValueError, match="different settings"):
        run_persona(PID, tmp_path / "out", days, FUTURES + 1, seed=42,
                    work_dir=tmp_path / "work")  # fmt: skip


def test_the_work_files_are_removed_after_a_persona(tmp_path: Path) -> None:
    run_persona(PID, tmp_path / "out", origins()[:1], FUTURES, seed=42,
                work_dir=tmp_path / "work")  # fmt: skip
    assert not list((tmp_path / "work").glob("*.db"))  # one persona at a time, nothing left


def test_a_held_out_persona_run_is_labelled_in_its_records(tmp_path: Path) -> None:
    run_persona("v2-SAL-04", tmp_path / "out", origins()[:1], FUTURES, seed=42,
                work_dir=tmp_path / "work", allow_held_out=True)  # fmt: skip
    record = json.loads((tmp_path / "out" / "v2-SAL-04.jsonl").read_text().splitlines()[0])
    assert record["held_out"] is True


def test_the_runner_refuses_a_held_out_persona_unless_told_to(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="held-out"):
        run_persona("v2-SAL-04", tmp_path / "out", origins()[:1], FUTURES, seed=42,
                    work_dir=tmp_path / "work")  # fmt: skip


# --- What it prints ---


def test_the_summary_has_counts_and_no_amounts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_persona(PID, tmp_path / "out", origins()[:2], FUTURES, seed=42, work_dir=tmp_path / "work")
    backtest.print_summary(tmp_path / "out")
    text = capsys.readouterr().out
    assert "1 persona" in text and "2 origins" in text
    assert "$" not in text and "cents" not in text  # counts only


# --- Rules for the metrics: already in shortfall, history length, early recurring detection ---


def test_a_record_says_how_much_history_stood_behind_it(conn: sqlite3.Connection) -> None:
    first = conn.execute(
        "SELECT MIN(posted_date) FROM transactions WHERE account = ?", (PID,)
    ).fetchone()[0]
    record = run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)
    assert record["history_days"] == (ORIGIN - date.fromisoformat(first)).days + 1
    early = run_origin(conn, PID, "synthetic", FIRST_ORIGIN, FUTURES)
    assert early["history_days"] < record["history_days"]
    assert early["history_days"] >= 8 * 7  # at least eight weeks behind the first origin


def test_history_buckets_are_short_medium_and_long() -> None:
    from eval.backtest import history_bucket

    assert history_bucket(56) == "8-12 weeks" and history_bucket(83) == "8-12 weeks"
    assert history_bucket(84) == "12-26 weeks" and history_bucket(181) == "12-26 weeks"
    assert history_bucket(182) == "26+ weeks" and history_bucket(400) == "26+ weeks"
    assert history_bucket(20) == "under 8 weeks"


def test_a_record_says_whether_the_account_was_already_below_the_buffer(
    conn: sqlite3.Connection,
) -> None:
    calm = run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)
    assert calm["in_shortfall_at_origin"] is (calm["start_balance_cents"] < calm["buffer_cents"])
    set_safety_buffer_cents(conn, calm["start_balance_cents"] + 50_000)  # far above the balance
    assert run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)["in_shortfall_at_origin"] is True
    set_safety_buffer_cents(conn, max(0, calm["start_balance_cents"] - 1))
    assert run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)["in_shortfall_at_origin"] is False


def test_a_record_counts_the_recurring_series_known_at_the_origin(
    conn: sqlite3.Connection,
) -> None:
    early = run_origin(conn, PID, "synthetic", FIRST_ORIGIN, FUTURES)
    late = run_origin(conn, PID, "synthetic", LAST_ORIGIN, FUTURES)
    assert early["n_series_detected"] <= late["n_series_detected"] + 3
    assert late["n_series_detected"] >= 3  # rent, pay, bills are found by then
    assert "series_keys" not in late  # no text from the data goes into the record


def test_records_split_into_ordinary_and_already_in_shortfall() -> None:
    from eval.backtest import split_by_shortfall

    records = [
        {"in_shortfall_at_origin": False, "origin": "a"},
        {"in_shortfall_at_origin": True, "origin": "b"},
        {"in_shortfall_at_origin": False, "origin": "c"},
    ]
    ordinary, already = split_by_shortfall(records)
    assert [r["origin"] for r in ordinary] == ["a", "c"]
    assert [r["origin"] for r in already] == ["b"]


def test_the_summary_counts_already_in_shortfall_origins_and_history_lengths_separately(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_persona(PID, tmp_path / "out", origins()[:3], FUTURES, seed=42, work_dir=tmp_path / "work")
    backtest.print_summary(tmp_path / "out")
    text = capsys.readouterr().out
    assert "already below the buffer at the origin" in text
    assert "8-12 weeks" in text  # the first origins have the shortest history


def test_the_synthetic_runner_refuses_to_mix_with_results_from_an_older_version(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / f"{PID}.meta.json").write_text(
        json.dumps({"futures": FUTURES, "seed": 42, "method": "weekday"}), encoding="utf-8"
    )  # a meta file from before the format was recorded
    with pytest.raises(ValueError, match="different settings"):
        run_persona(PID, out, origins()[:1], FUTURES, seed=42, work_dir=tmp_path / "work")


def test_every_record_carries_the_format_and_logic_version(conn: sqlite3.Connection) -> None:
    from eval.backtest import FORMAT, LOGIC

    record = run_origin(conn, PID, "synthetic", ORIGIN, FUTURES)
    assert record["format"] == FORMAT and record["logic"] == LOGIC


def test_a_held_out_run_saves_to_its_own_folder_apart_from_development_records() -> None:
    from eval.backtest import DEFAULT_OUT, LONG_OUT, default_out

    dev = default_out(False, "with-own", False, False)
    final = default_out(False, "with-own", False, True)
    assert dev == DEFAULT_OUT / "keeps-coming-with-own" / "full"
    assert final == DEFAULT_OUT / "held-out" / "keeps-coming-with-own" / "full"
    assert default_out(True, "off", True, True) == LONG_OUT / "held-out" / "quick"
    assert dev not in final.parents and final not in dev.parents


def test_every_reader_of_the_current_defaults_records_reads_where_the_backtest_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    from eval import blend, metrics, thresholds_raw
    from eval.arms import arm_dir
    from eval.backtest import default_out

    for long in (False, True):
        written = default_out(long, "with-own", False, False)  # what `python -m eval.backtest` does
        assert arm_dir(0, long) == written
        assert blend.arm0_folder(long) == written

    class Stop(Exception):
        pass

    seen: list[Path] = []

    def grab(path: Path, *args: object, **kwargs: object) -> None:
        seen.append(path)
        raise Stop

    for module in (metrics, thresholds_raw):
        seen.clear()
        monkeypatch.setattr(module, "load_records", grab)
        with pytest.raises(Stop):
            module.main([])
        assert seen == [default_out(False, "with-own", False, False)], module.__name__


def test_the_effective_forecaster_defaults_are_recorded_and_a_changed_default_refuses_a_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # fmt: skip
    from dataclasses import replace

    from eval.backtest import LOGIC

    assert LOGIC >= 3  # the version that records the forecaster's settings
    days = origins()[:1]
    run_persona(PID, tmp_path / "out", days, FUTURES, seed=42, work_dir=tmp_path / "work")
    meta = json.loads((tmp_path / "out" / f"{PID}.meta.json").read_text(encoding="utf-8"))
    assert meta["forecaster"]["recurring_fix"] == {"possibly_ended": True, "weekly_card": True}
    assert meta["forecaster"]["keeps"] == {"own_account": True}
    # the default fix changes (here: switched off), and the same folder is run again
    monkeypatch.setattr(
        backtest, "DEFAULT_OPTIONS", replace(backtest.DEFAULT_OPTIONS, recurring_fix=None)
    )
    with pytest.raises(ValueError, match=r"different settings.*recurring_fix") as error:
        run_persona(PID, tmp_path / "out", days, FUTURES, seed=42, work_dir=tmp_path / "work")
    assert "new folder" in str(error.value)  # it says what to do


def test_held_out_with_a_development_persona_saves_in_the_development_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # fmt: skip
    monkeypatch.setattr(backtest, "DEFAULT_OUT", tmp_path / "bt")
    few = backtest.origins()[:2]
    monkeypatch.setattr(backtest, "origins", lambda: few)  # two origins: this is about folders
    argv = ["--futures", "20", "--held-out", "--only", PID]
    assert backtest.main(argv) == 0
    assert (tmp_path / "bt" / "keeps-coming-with-own" / "full" / f"{PID}.jsonl").exists()
    assert not (tmp_path / "bt" / "held-out").exists()  # no development record among the held-out
    assert backtest.main([*argv[:-1], "v2-SAL-04"]) == 0
    held = tmp_path / "bt" / "held-out" / "keeps-coming-with-own" / "full"
    assert (held / "v2-SAL-04.jsonl").exists()
    assert not (held / f"{PID}.jsonl").exists()
