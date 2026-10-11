"""The alert-threshold analysis: the objective, the leave-one-persona-out choice, adding up."""

from eval.thresholds import choose, meets, pool


def _point(t: float, recall: float | None, precision: float | None, false: float) -> dict:
    return {"threshold": t, "recall": recall, "precision": precision, "false_per_30_days": false}


def test_both_constraints_must_hold() -> None:
    assert meets(_point(0.3, 0.5, 0.70, 0.10))
    assert not meets(_point(0.3, 0.5, 0.69, 0.05))
    assert not meets(_point(0.3, 0.5, 0.90, 0.11))
    assert not meets(_point(0.3, 0.5, None, 0.0))  # no alert at all: precision unknown


def test_highest_recall_then_precision_then_threshold() -> None:
    points = [
        _point(0.2, 0.9, 0.5, 0.05),  # recall best but precision too low
        _point(0.3, 0.6, 0.8, 0.05),
        _point(0.4, 0.6, 0.9, 0.05),  # same recall, better precision
        _point(0.5, 0.6, 0.9, 0.05),  # same again: the higher threshold
        _point(0.6, 0.4, 1.0, 0.0),
    ]
    best = choose(points)
    assert best is not None and best["threshold"] == 0.5
    assert choose([_point(0.2, 0.9, 0.5, 0.05)]) is None


def test_pool_adds_counts_not_rates() -> None:
    a = {"shortfall_episodes": 4, "caught": 1, "alert_episodes": 2, "false_alerts": 1,
         "ordinary_origins": 30, "lead_times": [5], "n_personas": 1}  # fmt: skip
    b = {"shortfall_episodes": 0, "caught": 0, "alert_episodes": 2, "false_alerts": 0,
         "ordinary_origins": 40, "lead_times": [], "n_personas": 1}  # fmt: skip
    m = pool([a, b])
    assert m["recall"] == 0.25 and m["precision"] == 0.75
    assert m["false_per_30_days"] == 1 / (70 * 7) * 30
    assert m["n_personas"] == 2 and m["lead_times"] == [5]


# --- Raw chances, both caught definitions (DECISIONS.md 2026-10-11, step 1) ---


def test_the_raw_choice_uses_the_new_recall_and_the_same_constraints() -> None:
    from eval.thresholds_raw import choose as choose_raw

    points = [
        {**_point(0.2, 0.9, 0.8, 0.05), "recall_on": 0.5},
        {**_point(0.5, 0.6, 0.8, 0.05), "recall_on": 0.7},  # best by the new recall
        {**_point(0.6, 0.6, 0.6, 0.05), "recall_on": 0.9},  # precision too low
    ]
    best = choose_raw(points)
    assert best is not None and best["threshold"] == 0.5


def test_the_stop_conditions_fixed_before_computing() -> None:
    from eval.thresholds_raw import high_rise, stop_conditions

    def point(t: float, old: float, new: float, a: list, b: list) -> dict:  # type: ignore[type-arg]
        return {"threshold": t, "recall": old, "recall_on": new, "caught_episodes": a,
                "caught_on_episodes": b}  # fmt: skip

    fine = [point(0.1, 0.5, 0.6, [1], [1, 2]), point(0.9, 0.5, 0.52, [1], [1])]
    assert stop_conditions(fine, {"a": 0.5, "b": 0.6, "c": 0.5}) == []
    assert stop_conditions([point(0.1, 0.5, 0.4, [1], [1])], {})  # lower: a bug
    assert stop_conditions([point(0.1, 0.5, 0.5, [1], [2])], {})  # not a superset
    high = [point(0.85, 0.5, 0.6, [1], [1, 2])]  # the high threshold moved by 10 points
    assert stop_conditions(high, {}) == []  # not one of the plan's three checks (2026-10-12)
    assert stop_conditions(high, {}, with_high_rise=True)  # the proposals run's fourth
    assert high_rise(high) and not high_rise(fine)
    assert stop_conditions([], {"a": 0.1, "b": 0.2, "c": 0.3, "d": 0.4})  # four thresholds


def test_pooling_keeps_both_definitions() -> None:
    from eval.thresholds_raw import pool as pool_raw

    a = {"shortfall_episodes": 4, "caught": 1, "caught_on": 2, "alert_episodes": 2,
         "false_alerts": 1, "ordinary_origins": 30, "lead_times": [5], "lead_times_on": [5, 9],
         "n_personas": 1}  # fmt: skip
    m = pool_raw([a, {**a, "caught_on": 1, "lead_times_on": [5]}])
    assert m["recall"] == 0.25 and m["recall_on"] == 3 / 8 and m["lead_times_on"] == [5, 5, 9]
