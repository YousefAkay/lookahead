"""Tests for the backtest metrics (week 4, step 4), on hand-made records whose answers are known.
Definitions are in DECISIONS.md (2026-10-09, "the backtest metrics, defined before anything is
computed")."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from eval import metrics
from eval.backtest import FORMAT, LOGIC
from eval.metrics import (
    alert_metrics,
    by_archetype,
    by_history_bucket,
    daily_realized,
    forecast_metrics,
    group_origins,
    load_records,
    report_text,
    restate,
    shortfall_episodes,
)

BUFFER = 10_000  # $100


def rec(origin: date = date(2026, 3, 2), persona: str = "v2-SAL-01", archetype: str = "SAL",
        dataset: str = "synthetic", start: int = 50_000, buffer: int = BUFFER,
        p10: list[int] | None = None, p50: list[int] | None = None, p90: list[int] | None = None,
        point: list[int] | None = None, realized: list[int] | None = None,
        lows: list[int] | None = None, prob: float | None = None, median_low: int | None = None,
        history_days: int = 100, held_out: bool = False) -> dict:  # type: ignore[type-arg]  # fmt: skip
    """A record with 30 flat days unless told otherwise."""
    flat = lambda v: [v] * 30  # noqa: E731
    p50 = p50 or flat(start)
    realized = realized or flat(start)
    lows = lows if lows is not None else [start] * 10
    if prob is None:
        prob = sum(low < buffer for low in lows) / len(lows)
    below = [origin + timedelta(days=k + 1) for k, b in enumerate(realized) if b < buffer]
    return {
        "format": FORMAT, "logic": LOGIC, "dataset": dataset, "persona": persona,
        "archetype": archetype,
        "held_out": held_out, "origin": origin.isoformat(), "buffer_cents": buffer,
        "start_balance_cents": start,
        "days": [(origin + timedelta(days=k + 1)).isoformat() for k in range(30)],
        "p10": p10 or [v - 5_000 for v in p50], "p50": p50, "p90": p90 or [v + 5_000 for v in p50],
        "point": point or p50, "lowest_cents": sorted(lows), "shortfall_probability": prob,
        "median_lowest_cents": (
            median_low if median_low is not None else sorted(lows)[len(lows) // 2]
        ),
        "realized": realized, "realized_lowest_cents": min(realized),
        "realized_first_below_buffer": below[0].isoformat() if below else None,
        "realized_below_zero": min(realized) < 0, "history_days": history_days,
        "in_shortfall_at_origin": start < buffer, "n_series_detected": 4, "has_income": True,
    }  # fmt: skip


# --- Balance error ---


def test_balance_error_at_7_14_and_30_days_is_the_mean_and_median_absolute_miss() -> None:
    def at(diff: int) -> dict:  # type: ignore[type-arg]
        p50 = [50_000] * 30
        realized = [50_000 + diff] * 30
        return rec(p50=p50, realized=realized)

    records = [at(1_000), at(-3_000), at(8_000)]  # misses of $10, $30, $80
    m = forecast_metrics(records)
    for h in (7, 14, 30):
        assert m["error"][h]["mean_p50"] == pytest.approx(40.0)
        assert m["error"][h]["median_p50"] == pytest.approx(30.0)


def test_the_single_path_estimate_and_the_naive_baseline_are_scored_the_same_way() -> None:
    r = rec(start=50_000, p50=[52_500] * 30, point=[50_500] * 30, realized=[51_000] * 30)
    m = forecast_metrics([r])
    assert m["error"][7]["mean_p50"] == pytest.approx(15.0)
    assert m["error"][7]["mean_point"] == pytest.approx(5.0)
    assert m["error"][7]["mean_naive"] == pytest.approx(10.0)  # "the balance stays as it was"
    assert m["error"][7]["skill_p50"] == pytest.approx(-0.5)  # worse than the baseline


def test_skill_is_one_minus_the_ratio_to_the_naive_error() -> None:
    r = rec(start=50_000, p50=[53_000] * 30, realized=[56_000] * 30)  # p50 off 30, naive off 60
    assert forecast_metrics([r])["error"][14]["skill_p50"] == pytest.approx(0.5)


def test_the_horizon_picks_the_right_day() -> None:
    p50 = [50_000 + 100 * k for k in range(30)]
    realized = [50_000] * 30
    m = forecast_metrics([rec(p50=p50, realized=realized)])
    assert m["error"][7]["mean_p50"] == pytest.approx(6.0)  # day 7 is index 6: 600 cents
    assert m["error"][30]["mean_p50"] == pytest.approx(29.0)


# --- Lowest balance ---


def test_lowest_balance_error_is_forecast_minus_observed_with_its_bias_and_typical_miss() -> None:
    a = rec(lows=[20_000] * 5, median_low=20_000, realized=[30_000] * 29 + [25_000])  # -$50
    b = rec(lows=[40_000] * 5, median_low=40_000, realized=[30_000] * 29 + [20_000])  # +$200
    m = forecast_metrics([a, b])
    assert m["lowest"]["bias"] == pytest.approx(75.0)  # (+200 - 50) / 2
    assert m["lowest"]["mae"] == pytest.approx(125.0)  # (200 + 50) / 2


# --- Band coverage, width, path coverage ---


def test_pointwise_coverage_counts_each_day_and_the_ends_of_the_band_count_as_inside() -> None:
    p50 = [50_000] * 30  # band 45,000 to 55,000
    realized = [50_000] * 20 + [55_000] * 5 + [55_001] * 5  # the last five are outside
    m = forecast_metrics([rec(p50=p50, realized=realized)])
    assert m["coverage"]["all"] == pytest.approx(25 / 30)  # 55,000 exactly is inside
    assert m["coverage"][7] == 1.0 and m["coverage"][30] == 0.0  # day 30 is outside
    assert m["width"][14] == pytest.approx(100.0)  # p90 - p10 = $100


def test_path_level_coverage_needs_every_day_inside() -> None:
    inside = rec(realized=[50_000] * 30)
    one_miss = rec(realized=[50_000] * 29 + [60_000])
    m = forecast_metrics([inside, one_miss, inside, one_miss])
    assert m["path_coverage"] == 0.5
    assert m["coverage"]["all"] > 0.9  # pointwise looks much better than whole-path


# --- Brier score ---


def test_brier_score_reference_skill_and_reliability_bins() -> None:
    def r(prob: float, happened: bool) -> dict:  # type: ignore[type-arg]
        realized = [5_000] * 30 if happened else [50_000] * 30  # below the $100 buffer or not
        return rec(prob=prob, realized=realized, lows=[50_000] * 10)

    records = [
        r(0.9, True),
        r(0.9, True),
        r(0.1, False),
        r(0.1, False),
        r(0.5, True),
        r(0.5, False),
    ]
    b = forecast_metrics(records)["brier"]
    # (0.9-1)^2 twice, (0.1-0)^2 twice, (0.5-1)^2 and (0.5-0)^2
    expected = (2 * 0.01 + 2 * 0.01 + 0.25 + 0.25) / 6
    assert b["brier"] == pytest.approx(expected)
    assert b["base_rate"] == pytest.approx(3 / 6)
    assert b["reference"] == pytest.approx(0.25)  # always saying 50 percent
    assert b["skill"] == pytest.approx(1 - expected / 0.25)
    bins = b["reliability"]
    assert [x["n"] for x in bins] == [2, 0, 2, 0, 2]  # 0-20, 20-40, 40-60, 60-80, 80-100
    assert bins[4]["observed"] == 1.0 and bins[0]["observed"] == 0.0
    assert bins[2]["mean_forecast"] == pytest.approx(0.5) and bins[2]["observed"] == 0.5


def test_a_forecast_of_exactly_100_percent_goes_in_the_last_bin() -> None:
    m = forecast_metrics([rec(prob=1.0, realized=[5_000] * 30)])
    assert m["brier"]["reliability"][4]["n"] == 1


def test_no_records_gives_zero_origins_and_no_numbers() -> None:
    assert forecast_metrics([]) == {"n_origins": 0, "n_personas": 0}


# --- Groups ---


def test_origins_split_into_all_ordinary_and_already_in_shortfall() -> None:
    ordinary = rec(start=50_000)
    already = rec(start=5_000, realized=[5_000] * 30)
    groups = group_origins([ordinary, already, ordinary])
    assert len(groups["all"]) == 3 and len(groups["ordinary"]) == 2
    assert len(groups["already in shortfall"]) == 1


def test_breakdowns_by_history_bucket_and_by_archetype() -> None:
    records = [rec(history_days=60), rec(history_days=100, archetype="TIGHT"),
               rec(history_days=150), rec(history_days=300, archetype="TIGHT")]  # fmt: skip
    buckets = by_history_bucket(records)
    assert {k: len(v) for k, v in buckets.items()} == {
        "8-12 weeks": 1, "12-26 weeks": 2, "26+ weeks": 1,
    }  # fmt: skip
    kinds = by_archetype(records)
    assert {k: len(v) for k, v in kinds.items()} == {"SAL": 2, "TIGHT": 2}


# --- Restating for another buffer (real data: $0) ---


def test_restate_recomputes_everything_that_depends_on_the_buffer() -> None:
    lows = [-5_000, 1_000, 2_000, 30_000, 60_000]
    realized = [20_000] * 10 + [-500] * 3 + [20_000] * 17
    r = rec(buffer=10_000, lows=lows, realized=realized, start=20_000)
    z = restate(r, 0)
    assert z["buffer_cents"] == 0
    assert z["shortfall_probability"] == pytest.approx(1 / 5)  # one low under $0
    assert z["realized_first_below_buffer"] == (date(2026, 3, 2) + timedelta(days=11)).isoformat()
    assert z["in_shortfall_at_origin"] is False
    assert r["shortfall_probability"] == pytest.approx(3 / 5)  # the original is untouched
    high = restate(r, 35_000)  # a buffer above the start balance and above four of five lows
    assert high["in_shortfall_at_origin"] is True
    assert high["shortfall_probability"] == pytest.approx(4 / 5)


# --- The observed daily balances and shortfall episodes ---


def series_records(balances: dict[date, int], first: date, n_origins: int,
                   probs: dict[date, float] | None = None, persona: str = "p") -> list[dict]:  # type: ignore[type-arg]  # fmt: skip
    """Weekly records for one persona from a known daily balance series."""
    out = []
    for k in range(n_origins):
        origin = first + timedelta(weeks=k)
        realized = [balances[origin + timedelta(days=d)] for d in range(1, 31)]
        prob = (probs or {}).get(origin, 0.0)
        out.append(rec(origin=origin, persona=persona, start=balances[origin], realized=realized,
                       p50=[balances[origin]] * 30, lows=[balances[origin]] * 10,
                       prob=prob, median_low=balances[origin]))  # fmt: skip
    return out


def make_series(start: date, days: int, below: list[tuple[date, date]]) -> dict[date, int]:
    balances = {}
    for k in range(days):
        day = start + timedelta(days=k)
        balances[day] = 5_000 if any(a <= day <= b for a, b in below) else 50_000
    return balances


def test_daily_balances_are_joined_from_the_overlapping_windows() -> None:
    first = date(2026, 1, 5)
    balances = make_series(first, 90, [(date(2026, 2, 10), date(2026, 2, 12))])
    daily = daily_realized(series_records(balances, first, 4))
    assert min(daily) == first + timedelta(days=1) and max(daily) == first + timedelta(days=21 + 30)
    assert all(daily[d] == balances[d] for d in daily)


def test_windows_that_disagree_are_an_error() -> None:
    first = date(2026, 1, 5)
    a = rec(origin=first, realized=[50_000] * 30)
    b = rec(origin=first + timedelta(days=7), realized=[40_000] * 30)
    with pytest.raises(ValueError, match="disagree"):
        daily_realized([a, b])


def test_shortfall_episodes_are_runs_of_days_below_the_buffer() -> None:
    first = date(2026, 1, 5)
    balances = make_series(first, 120, [(date(2026, 2, 10), date(2026, 2, 12)),
                                        (date(2026, 2, 14), date(2026, 2, 14)),
                                        (date(2026, 3, 20), date(2026, 3, 25))])  # fmt: skip
    records = series_records(balances, first, 10)
    episodes = shortfall_episodes(records)
    assert episodes == [date(2026, 2, 10), date(2026, 2, 14), date(2026, 3, 20)]


def test_an_episode_that_began_before_the_first_origin_is_not_counted() -> None:
    first = date(2026, 1, 5)
    early = (first - timedelta(days=2), first + timedelta(days=3))
    balances = make_series(first - timedelta(days=5), 120,
                           [early, (date(2026, 2, 20), date(2026, 2, 22))])  # fmt: skip
    records = series_records(balances, first, 5)
    assert shortfall_episodes(records) == [date(2026, 2, 20)]  # the early one began earlier


# --- Alerts ---


def scenario(alert_at: dict[date, float], dips: list[tuple[date, date]], n: int = 12,
             persona: str = "p") -> list[dict]:  # type: ignore[type-arg]  # fmt: skip
    first = date(2026, 1, 5)
    balances = make_series(first, n * 7 + 40, dips)
    return series_records(balances, first, n, probs=alert_at, persona=persona)


def test_an_alert_before_a_shortfall_catches_it_and_the_lead_time_is_counted() -> None:
    first = date(2026, 1, 5)
    dip = date(2026, 2, 18)  # a Wednesday; the origins are Mondays
    records = scenario({first + timedelta(weeks=3): 0.9, first + timedelta(weeks=4): 0.9},
                       [(dip, dip + timedelta(days=2))])  # fmt: skip
    m = alert_metrics(records)
    assert m["shortfall_episodes"] == 1 and m["caught"] == 1 and m["missed"] == 0
    opened = first + timedelta(weeks=3)  # Jan 26: the first alert
    assert m["lead_times"] == [(dip - opened).days]  # earliest alert gives the lead time
    assert m["alert_episodes"] == 1 and m["false_alerts"] == 0  # two looks, one episode
    assert m["recall"] == 1.0 and m["precision"] == 1.0


def test_a_shortfall_with_no_alert_is_missed_and_an_alert_with_no_shortfall_is_false() -> None:
    first = date(2026, 1, 5)
    dip = date(2026, 3, 18)
    records = scenario({first + timedelta(weeks=1): 0.9}, [(dip, dip + timedelta(days=1))],
                       n=12)  # fmt: skip
    m = alert_metrics(records)
    # the alert at Jan 12 looks at Jan 13 to Feb 11: no dip there, so it is false; the dip is missed
    assert m["alert_episodes"] == 1 and m["false_alerts"] == 1
    assert m["shortfall_episodes"] == 1 and m["caught"] == 0 and m["missed"] == 1
    assert m["recall"] == 0.0 and m["precision"] == 0.0


def test_an_alert_that_comes_after_the_shortfall_began_does_not_catch_it() -> None:
    dip = date(2026, 2, 10)  # begins on a Tuesday
    records = scenario({date(2026, 2, 16): 0.9}, [(dip, dip + timedelta(days=9))])
    m = alert_metrics(records)
    assert m["caught"] == 0 and m["missed"] == 1


def test_an_alert_more_than_30_days_ahead_does_not_count_as_catching_it() -> None:
    first = date(2026, 1, 5)
    dip = first + timedelta(days=60)
    records = scenario({first: 0.9}, [(dip, dip + timedelta(days=1))], n=10)
    assert alert_metrics(records)["caught"] == 0


def test_false_alerts_per_30_days_use_seven_days_for_each_ordinary_origin() -> None:
    first = date(2026, 1, 5)
    records = scenario({first: 0.9}, [], n=10)  # one false alert, ten calm origins
    m = alert_metrics(records)
    assert m["ordinary_origins"] == 10 and m["false_alerts"] == 1
    assert m["false_per_30_days"] == pytest.approx(1 / (10 * 7) * 30)


def test_alerts_raised_while_already_in_shortfall_have_their_own_row() -> None:
    # in shortfall from Jan 28 to Feb 20; an alert at the origin of Feb 2 (already below)
    records = scenario({date(2026, 2, 2): 0.9}, [(date(2026, 1, 28), date(2026, 2, 20))])
    m = alert_metrics(records)
    assert m["alerts_while_in_shortfall"] == 1
    assert m["alert_episodes"] == 0  # not in the ordinary count
    assert m["already_in_shortfall_origins"] >= 3
    assert m["false_alerts"] == 0 and m["precision"] is None  # nothing ordinary to score


def test_one_alert_can_catch_two_shortfall_episodes_within_30_days_of_it() -> None:
    # two dips a day apart; the alert at the ordinary origin of Jan 12 comes before both
    dips = [(date(2026, 1, 20), date(2026, 1, 21)), (date(2026, 1, 23), date(2026, 1, 24))]
    records = scenario({date(2026, 1, 12): 0.9}, dips)
    m = alert_metrics(records)
    assert m["shortfall_episodes"] == 2
    assert m["caught"] == 2  # the Jan 12 alert (ordinary origin) is within 30 days of both


def test_the_median_rule_alerts_even_when_the_probability_is_low() -> None:
    records = scenario({}, [], n=6)
    for r in records[:2]:
        r["median_lowest_cents"] = 5_000  # below the $100 buffer, probability stays 0
    assert alert_metrics(records)["alert_episodes"] == 1  # two looks merged into one episode
    assert alert_metrics(records, median_rule=False)["alert_episodes"] == 0


def test_a_higher_threshold_means_fewer_alerts() -> None:
    first = date(2026, 1, 5)
    records = scenario({first: 0.25, first + timedelta(weeks=5): 0.6}, [], n=10)
    assert alert_metrics(records, threshold=0.20)["alert_episodes"] == 2
    assert alert_metrics(records, threshold=0.50)["alert_episodes"] == 1
    assert alert_metrics(records, threshold=0.90)["alert_episodes"] == 0


def test_alerts_use_the_engines_suppression_so_a_repeat_look_does_not_make_a_new_episode() -> None:
    first = date(2026, 1, 5)
    records = scenario({first + timedelta(weeks=k): 0.9 for k in range(4)}, [], n=8)
    assert alert_metrics(records)["alert_episodes"] == 1


def test_counts_are_kept_for_personas_origins_and_episodes_across_several_personas() -> None:
    dip = date(2026, 2, 18)
    a = scenario({date(2026, 1, 26): 0.9}, [(dip, dip)], persona="a")
    b = scenario({}, [], persona="b")
    m = alert_metrics(a + b)
    assert m["n_personas"] == 2 and m["n_origins"] == 24
    assert m["shortfall_episodes"] == 1 and m["caught"] == 1


# --- Loading, held-out and real ---


def write(out: Path, records: list[dict]) -> None:  # type: ignore[type-arg]
    out.mkdir(parents=True, exist_ok=True)
    by_persona: dict[str, list[dict]] = {}  # type: ignore[type-arg]
    for r in records:
        by_persona.setdefault(r["persona"], []).append(r)
    for persona, rows in by_persona.items():
        (out / f"{persona}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows),
                                              encoding="utf-8")  # fmt: skip


def test_loading_refuses_a_held_out_persona_unless_it_is_the_final_test(tmp_path: Path) -> None:
    write(tmp_path, [rec(persona="v2-SAL-01"), rec(persona="v2-SAL-04", held_out=True)])
    with pytest.raises(ValueError, match="held-out"):
        load_records(tmp_path)
    assert len(load_records(tmp_path, final_test=True)) == 2


def test_loading_only_development_records_is_fine(tmp_path: Path) -> None:
    write(tmp_path, [rec(persona="v2-SAL-01"), rec(persona="v2-TIGHT-01", archetype="TIGHT")])
    assert len(load_records(tmp_path)) == 2


def test_loading_refuses_records_of_an_older_format(tmp_path: Path) -> None:
    old = rec()
    del old["format"]
    write(tmp_path, [old])
    with pytest.raises(ValueError, match="older version"):
        load_records(tmp_path)


def test_real_records_have_no_held_out_flag_and_load_without_the_flag(tmp_path: Path) -> None:
    real = rec(persona="account-1", dataset="real", archetype=None, held_out=None)  # type: ignore[arg-type]
    write(tmp_path, [real])
    assert len(load_records(tmp_path)) == 1


# --- The report ---


def test_the_report_has_separate_columns_and_n_beside_every_section() -> None:
    synthetic = scenario({date(2026, 1, 26): 0.9}, [(date(2026, 2, 18), date(2026, 2, 19))])
    real = scenario({}, [], persona="account-1")
    for r in real:
        r["dataset"] = "real"
        r["archetype"] = None
    text = report_text(synthetic, real)
    assert "Synthetic" in text and "Real" in text and "Real, buffer $0" in text
    assert "12 origins" in text and "1 persona" in text
    assert "shortfall episodes" in text and "already in shortfall" in text
    assert "Brier" in text and "coverage" in text and "lead time" in text.lower()


def test_the_report_without_real_data_says_there_is_none() -> None:
    text = report_text(scenario({}, []), [])
    assert "no real data" in text.lower()


def test_the_report_prints_no_persona_ids_for_real_data_and_no_dates() -> None:
    import re

    real = scenario({}, [], persona="my-account")
    for r in real:
        r["dataset"] = "real"
    text = report_text([], real)
    assert "my-account" not in text and not re.search(r"\d{4}-\d{2}-\d{2}", text)


def test_the_module_has_a_command_line() -> None:
    assert callable(metrics.main)


def test_the_30_day_window_is_inclusive_at_30_and_closed_at_31() -> None:
    first = date(2026, 1, 5)
    thirty = scenario(
        {first: 0.9}, [(first + timedelta(days=30), first + timedelta(days=31))], n=10
    )
    m = alert_metrics(thirty)
    assert m["caught"] == 1 and m["lead_times"] == [30] and m["false_alerts"] == 0
    late = scenario({first: 0.9}, [(first + timedelta(days=31), first + timedelta(days=32))], n=10)
    m = alert_metrics(late)
    assert m["caught"] == 0 and m["false_alerts"] == 1  # too far ahead to count either way


def test_negative_dollars_read_minus_dollar_not_dollar_minus() -> None:
    assert metrics._d(-158.8) == "-$158.80" and metrics._d(1882.84) == "$1,882.84"
    assert metrics._d(None) == "-"
