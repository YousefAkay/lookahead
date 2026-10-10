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
