"""The Time machine page and the snapshot pieces behind it. Synthetic personas only, temporary
folders only."""

from datetime import date, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lookahead.forecast.scenario import Scenario
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.timemachine_snapshots import (
    alert_view,
    available_personas,
    build_persona_snapshots,
    closing_balances,
    evidence_lines,
    history_frame,
    load_balances,
    load_steps,
    moved,
    persona_label,
    realized_frame,
    scenario_at,
    step_chart,
    step_low,
    whatif_extra,
)

PAGE = Path(__file__).resolve().parents[1] / "app" / "pages" / "6_Time_machine.py"
D0 = date(2026, 3, 2)


def _step(condition: bool = False, notified: bool = True, chance: float = 0.0) -> dict:
    days = [(D0 + timedelta(days=k + 1)).isoformat() for k in range(30)]
    p50 = [50_000 - 1_000 * k for k in range(30)]
    evidence = {
        "low_cents": p50[-1], "low_day": days[-1], "buffer_cents": 20_000, "probability": chance,
        "probability_without_deposits": chance, "futures": 100, "median_lowest_cents": -1_000,
        "largest_payments": [{"name": "Rent <b>", "day": days[3], "cents": -90_000,
                              "basis": "detected", "series_key": "k"}],
        "assumptions": {"detected": 2}, "big_events": [], "spending_per_day_cents": 3_000,
        "method": "weekday", "rule": "provisional",
    }  # fmt: skip
    return {
        "as_of": D0.isoformat(), "data_date": D0.isoformat(), "futures": 100,
        "buffer_cents": 20_000, "start_balance_cents": 51_000, "days": days,
        "p10": [p - 5_000 for p in p50], "p50": p50, "p90": [p + 5_000 for p in p50],
        "shortfall_probability": chance, "median_lowest_cents": -1_000, "n_items": 2,
        "history_days": 90, "n_series": 2,
        "alert": {"condition": condition, "notified": notified, "reason": "new alert",
                  "episode_id": 1 if condition else None, "active": condition,
                  "message": "Possible shortfall: ..." if condition else None,
                  "evidence": evidence if condition else None},
    }  # fmt: skip


def test_moving_by_days_lands_on_saved_dates_and_stays_inside() -> None:
    dates = [D0 + timedelta(days=k) for k in range(0, 60, 3)]  # every third day
    assert moved(dates, D0, 7) == D0 + timedelta(days=9)  # the first saved date past a week
    assert moved(dates, D0, 1) == D0 + timedelta(days=3)  # at least one step
    assert moved(dates, D0, -1) == D0  # nothing earlier: stays
    assert moved(dates, dates[-1], 7) == dates[-1]
    assert moved(dates, D0 + timedelta(days=30), -7) == D0 + timedelta(days=21)


def test_labels_name_the_kind_of_persona() -> None:
    assert persona_label("synthetic-C") == "synthetic-C (demo persona)"
    assert "tight budget" in persona_label("v2-TIGHT-03")


def test_alert_view_follows_the_alert_record() -> None:
    calm = alert_view(_step(chance=0.0))
    assert calm.kind == "ok" and calm.tag == "On track" and "No simulated future" in calm.sentence
    few = alert_view(_step(chance=0.08))
    assert "Only 8%" in few.sentence
    loud = alert_view(_step(condition=True, chance=0.6))
    assert loud.kind == "warning" and loud.status == "Alert shown: new alert."
    quiet = alert_view(_step(condition=True, notified=False, chance=0.6))
    assert "stays quiet" in quiet.status


def test_evidence_lines_say_what_stands_behind_an_alert() -> None:
    assert evidence_lines(_step()) == []
    lines = evidence_lines(_step(condition=True, chance=0.6))
    text = "\n".join(lines)
    assert "Lowest balance in the middle future" in text and "60%" in text
    assert "Rent <b>" in text  # escaped by the page, not here
    assert "Placed payments: 2 detected." in text


def test_history_never_shows_days_after_the_date_and_realized_starts_at_it() -> None:
    balances = {D0 + timedelta(days=k): 100 * k for k in range(-80, 40)}
    history = history_frame(balances, D0)
    assert history["date"].max() == D0 and len(history) == 60
    realized = realized_frame(balances, D0)
    assert realized["date"].min() == D0 and realized["date"].max() == D0 + timedelta(days=30)


def test_the_chart_has_the_band_the_middle_future_and_optionally_what_happened() -> None:
    step = _step()
    balances = {D0 + timedelta(days=k): 50_000 for k in range(-60, 31)}
    fig = step_chart(step, history_frame(balances, D0))
    assert [t.meta for t in fig.data] == ["recent", "band_edge", "band", "median"]
    assert fig.data[3].x[0] == D0 and fig.data[3].y[0] == 510.0  # starts at the closing balance
    fig = step_chart(step, history_frame(balances, D0), realized_frame(balances, D0))
    assert fig.data[-1].meta == "realized"
    assert step_low(step) == (date.fromisoformat(step["days"][-1]), step["p50"][-1])


@pytest.fixture(scope="module")
def snapshots(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tm")
    synthetic = root / "synthetic"
    write_persona(PERSONAS["C"], synthetic, seed=42, start=date(2026, 6, 1),
                  export_date=date(2026, 9, 30))  # fmt: skip
    out = root / "timemachine"
    assert build_persona_snapshots("synthetic-C", out, synthetic, every=10, futures=40) > 3
    return out


def test_persona_files_and_balances(snapshots: Path) -> None:
    assert available_personas(snapshots) == ["synthetic-C"]
    steps = load_steps(snapshots, "synthetic-C")
    balances = load_balances(snapshots, "synthetic-C")
    for s in steps:  # each step starts from that day's closing balance
        assert s["start_balance_cents"] == balances[date.fromisoformat(s["as_of"])]
    days = sorted(balances)
    assert days == [days[0] + timedelta(days=k) for k in range(len(days))]  # every day


def test_a_scenario_with_no_change_gives_the_saved_step(snapshots: Path) -> None:
    step = load_steps(snapshots, "synthetic-C")[2]
    result = scenario_at(snapshots / "synthetic-C.db", "synthetic-C",
                         date.fromisoformat(step["as_of"]), Scenario(), futures=40)  # fmt: skip
    assert list(result.original.p50) == step["p50"] == list(result.modified.p50)


def test_held_out_personas_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="held-out"):
        build_persona_snapshots("v2-SAL-04", tmp_path, tmp_path)


def test_closing_balances_fill_quiet_days(snapshots: Path) -> None:
    from lookahead.db import connect_readonly

    conn = connect_readonly(snapshots / "synthetic-C.db")
    try:
        balances = closing_balances(conn, "synthetic-C")
    finally:
        conn.close()
    assert balances == load_balances(snapshots, "synthetic-C")


@pytest.mark.slow
def test_the_page_without_snapshots_says_how_to_make_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # fmt: skip
    monkeypatch.setenv("LOOKAHEAD_TIMEMACHINE_DIR", str(tmp_path / "none"))
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception, at.exception
    assert "python -m lookahead.timemachine_snapshots" in at.info[0].value


@pytest.mark.slow
def test_the_page_steps_through_dates_and_runs_a_what_if(
    snapshots: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # fmt: skip
    monkeypatch.setenv("LOOKAHEAD_TIMEMACHINE_DIR", str(snapshots))
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception, at.exception
    text = "\n".join(str(m.value) for m in at.markdown)
    assert "Synthetic data" in text and "not anyone" in text
    dates = [date.fromisoformat(s["as_of"]) for s in load_steps(snapshots, "synthetic-C")]
    assert at.select_slider[0].value == dates[0]
    at.button(key="tm_next7").click().run()
    assert at.select_slider[0].value == dates[1]  # saved every 10 days: the next one
    at.button(key="tm_back1").click().run()
    assert at.select_slider[0].value == dates[0]
    at.toggle(key="tm_show_real").set_value(True).run()
    assert not at.exception, at.exception
    at.number_input(key="tm_cut").set_value(20)
    at.button[-1].click().run()
    assert not at.exception, at.exception
    assert any("Conditional scenario" in str(m.value) for m in at.markdown)


def test_the_what_if_item_follows_the_apps_sign_rule() -> None:
    day = date(2026, 3, 9)
    (deposit,) = whatif_extra(50.0, day)
    assert (deposit.cents, deposit.description) == (5000, "a deposit")  # positive = money in
    (purchase,) = whatif_extra(-60.25, day)
    assert (purchase.cents, purchase.description) == (-6025, "a purchase")  # negative = out
    assert whatif_extra(0.0, day) == ()
