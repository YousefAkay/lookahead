"""Experiment 4, the combination itself (eval/combination.py), on made-up futures."""

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from eval import combination as cb
from eval import futures_store as fs
from eval.berka_split import load_split
from lookahead.generator.archetypes import ALL_HELD_OUT, development_ids

START = 100_000
BUFFER = 50_000


def made_up_paths(
    seed: int = 1, futures: int = 200, days: int = 30, drift: int = -800
) -> np.ndarray:
    """Futures that fall by about `drift` cents a day, with noise; int32 cents like the store."""
    rng = np.random.default_rng(seed)
    steps = drift + rng.normal(0, 1500, size=(futures, days))
    return (START + np.cumsum(steps, axis=1)).round().astype(np.int32)


def median(paths: np.ndarray) -> np.ndarray:
    return fs.nearest_rank(paths, 0.5)


def test_the_grid_is_0_to_1_in_steps_of_a_tenth() -> None:
    assert cb.GRID == (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


@pytest.mark.parametrize("w", cb.GRID)
def test_the_combined_median_is_w_m_plus_1_minus_w_s_and_the_band_keeps_its_width(w: float) -> None:
    paths = made_up_paths()
    m = median(paths)
    x = cb.combined(paths, START, m, w)
    assert np.allclose(fs.nearest_rank(x, 0.5), w * m + (1 - w) * START)
    width = fs.nearest_rank(paths, 0.9) - fs.nearest_rank(paths, 0.1)
    assert np.allclose(fs.nearest_rank(x, 0.9) - fs.nearest_rank(x, 0.1), width)


def test_weight_0_is_balance_stays_in_the_middle() -> None:
    paths = made_up_paths()
    v = cb.view(paths, START, BUFFER, median(paths), paths[0], 0.0)
    assert v["p50"] == [START] * 30


def test_weight_1_gives_the_default_to_the_digit() -> None:
    paths = made_up_paths()
    realized = made_up_paths(seed=9, futures=1)[0]
    default = fs.recompute(paths, BUFFER, realized)
    v = cb.view(paths, START, BUFFER, median(paths), realized, 1.0)
    for key in ("p10", "p50", "p90", "shortfall_probability", "median_lowest_cents"):
        assert v[key] == default[key]
    assert v["crps"] == default["crps_default"]
    assert v["moved"] is False


def test_a_falling_forecast_gets_a_lower_dip_chance_and_a_rising_one_a_higher() -> None:
    falling = made_up_paths(drift=-1500)
    rising = made_up_paths(drift=+1500)
    for paths, lower in ((falling, True), (rising, False)):
        m = median(paths)
        line = START - (45_000 if lower else 1_000)  # a line some futures cross
        d = cb.view(paths, START, line, m, paths[0], 1.0)["shortfall_probability"]
        c = cb.view(paths, START, line, m, paths[0], 0.5)["shortfall_probability"]
        assert 0 < d < 1
        assert (c < d) if lower else (c > d)


def test_moved_needs_a_weight_below_1_and_a_median_away_from_the_start() -> None:
    paths = made_up_paths()
    m = median(paths)
    assert cb.view(paths, START, BUFFER, m, paths[0], 0.9)["moved"] is True
    flat = np.full_like(m, START)
    assert cb.view(paths, START, BUFFER, flat, paths[0], 0.0)["moved"] is False


def test_the_loss_of_weight_1_is_the_default_crps_over_the_30_days() -> None:
    paths = made_up_paths()
    realized = made_up_paths(seed=5, futures=1)[0]
    loss = cb.losses(paths, START, median(paths), realized)
    assert loss.shape == (11,)
    assert loss[-1] == pytest.approx(np.mean(fs.recompute(paths, BUFFER, realized)["crps_default"]),
                                     abs=0.05)  # fmt: skip


def test_when_what_happened_stayed_flat_the_loss_prefers_balance_stays() -> None:
    paths = made_up_paths(drift=-2000)
    loss = cb.losses(paths, START, median(paths), np.full(30, START))
    assert cb.best_weight(loss) == 0.0


def test_the_lowest_loss_wins_and_a_tie_goes_to_the_larger_weight() -> None:
    loss = np.array([5.0, 4.0, 3.0, 3.0, 9, 9, 9, 9, 9, 9, 9])
    assert cb.best_weight(loss) == 0.3
    assert cb.best_weight(np.full(11, 2.0)) == 1.0
    assert cb.best_weight(np.array([1.0, *[2.0] * 10])) == 0.0


# --- Reading the saved futures ---


def write_store(folder: Path, pid: str, paths: np.ndarray, origins: list[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    np.savez(folder / f"{pid}.npz", paths=paths, account=np.array(pid),
             origin=np.array(origins), start_cents=np.full(len(origins), START, dtype=np.int64),
             buffer_cents=np.full(len(origins), BUFFER, dtype=np.int64))  # fmt: skip


def write_records(folder: Path, pid: str, origins: list[str], held_out: bool = False) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    rows = [{"persona": pid, "archetype": pid.split("-")[1], "origin": o, "held_out": held_out,
             "days": [o] * 30, "start_balance_cents": START, "buffer_cents": BUFFER,
             "point": [START] * 30, "realized": [START] * 30, "realized_lowest_cents": START,
             "realized_first_below_buffer": None, "in_shortfall_at_origin": False,
             "crps_stays": [0.0] * 30, "everyday": 1,
             "history_days": 200} for o in origins]  # fmt: skip
    (folder / f"{pid}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", "utf-8")


def test_a_persona_is_read_oldest_first_with_its_outcomes(tmp_path: Path) -> None:
    pid = development_ids()[0]
    origins = ["2026-03-09", "2026-03-02"]
    paths = np.stack([made_up_paths(seed=1, futures=10), made_up_paths(seed=2, futures=10)])
    write_store(tmp_path / "store", pid, paths, origins)
    write_records(tmp_path / "rec", pid, origins)
    rows = cb.load_persona(pid, tmp_path / "store", tmp_path / "rec")
    assert [r["origin"] for r in rows] == ["2026-03-02", "2026-03-09"]
    assert np.array_equal(rows[0]["paths"], paths[1])
    assert rows[0]["realized"] == [START] * 30 and rows[0]["account"] == pid


def test_a_held_out_persona_is_refused_before_any_file_is_opened(tmp_path: Path) -> None:
    pid = sorted(ALL_HELD_OUT)[0]
    with pytest.raises(ValueError, match="held-out"):
        cb.load_persona(pid, tmp_path / "nowhere", tmp_path / "nowhere")


def test_a_record_marked_held_out_is_refused(tmp_path: Path) -> None:
    pid = development_ids()[0]
    write_store(tmp_path / "store", pid, made_up_paths(futures=10)[None], ["2026-03-02"])
    write_records(tmp_path / "rec", pid, ["2026-03-02"], held_out=True)
    with pytest.raises(ValueError, match="held-out"):
        cb.load_persona(pid, tmp_path / "store", tmp_path / "rec")


def test_a_held_out_berka_account_is_refused_before_any_file_is_opened(tmp_path: Path) -> None:
    account = int(load_split()["primary_held_out"][0])  # type: ignore[index]
    with pytest.raises(ValueError, match="held-out"):
        cb.load_account(account, tmp_path / "nowhere", tmp_path / "nowhere")


# --- Learning the weights: only from origins whose 30 days are over ---

FIRST = date(2025, 10, 6)


def made_up_rows(
    accounts: dict[str, str], n: int = 40, seed: int = 3, futures: int = 60
) -> list[cb.Record]:
    """Weekly origins for each account (name -> archetype), with made-up futures and outcomes."""
    rng = np.random.default_rng(seed)
    rows = []
    for name, archetype in accounts.items():
        # One daily balance series per account, so overlapping windows agree on every day.
        series = made_up_paths(seed=int(rng.integers(1e6)), futures=1, days=7 * n + 31, drift=0)[0]
        for k in range(n):
            origin = FIRST + timedelta(days=7 * k)
            paths = made_up_paths(seed=int(rng.integers(1e6)), futures=futures,
                                  drift=int(rng.integers(-1500, 1500)))  # fmt: skip
            real = series[7 * k + 1 : 7 * k + 31]
            days = [(origin + timedelta(days=h)).isoformat() for h in range(1, 31)]
            below = next((d for d, b in zip(days, real, strict=True) if b < BUFFER), None)
            rows.append({
                "persona": name, "account": name, "archetype": archetype,
                "origin": origin.isoformat(), "days": days, "start_balance_cents": START,
                "buffer_cents": BUFFER, "point": [START] * 30, "realized": real.tolist(),
                "realized_lowest_cents": int(real.min()), "realized_first_below_buffer": below,
                "in_shortfall_at_origin": False,
                "crps_stays": np.abs(START - real.astype(float)).tolist(), "everyday": 1,
                "history_days": 200,
                "paths": paths,
            })  # fmt: skip
    return rows


def test_an_account_gets_its_own_weight_at_28_of_40_weekly_origins() -> None:
    rows = made_up_rows({"a": "X"})
    ok = cb.eligibility(rows, "archetype")["iii"]
    assert sum(ok) == 28 and ok.index(True) == 12  # 8 resolved: origins 0..7, each <= t - 35


def test_an_origin_exactly_35_days_back_counts_and_34_does_not() -> None:
    rows = made_up_rows({"a": "X"}, n=2)
    rows[1]["origin"] = (FIRST + timedelta(days=35)).isoformat()
    d = cb._dates(rows)
    assert d[0] <= d[1] - np.timedelta64(cb.LAG.days, "D")
    rows[1]["origin"] = (FIRST + timedelta(days=34)).isoformat()
    d = cb._dates(rows)
    assert not d[0] <= d[1] - np.timedelta64(cb.LAG.days, "D")


def set_losses(rows: list[cb.Record], value: np.ndarray) -> None:
    for r in rows:
        r["loss"] = value.copy()
        r["median"] = fs.nearest_rank(r["paths"], 0.5)


def test_a_weight_never_learns_from_origins_whose_30_days_are_not_over() -> None:
    rows = made_up_rows({f"p{i}": f"A{i}" for i in range(7)})
    favour_0 = np.array([1.0, *[2.0] * 10])  # every resolved origin says w = 0
    set_losses(rows, favour_0)
    t = 20  # origin index of account p0
    before = cb.weights(rows, "archetype")
    # What is not yet resolved at t (less than 35 days back, t itself, later) now says w = 1.
    cutoff = date.fromisoformat(rows[t]["origin"]) - timedelta(days=35)
    for r in rows:
        if date.fromisoformat(r["origin"]) > cutoff:
            r["loss"] = np.array([*[2.0] * 10, 1.0])
    after = cb.weights(rows, "archetype")
    for k in ("ii", "iii"):
        assert before[k][t] == after[k][t] == 0.0


def test_the_global_weight_leaves_out_the_whole_archetype() -> None:
    accounts = {"same1": "S", "same2": "S", **{f"o{i}": f"O{i}" for i in range(5)}}
    rows = made_up_rows(accounts)
    set_losses(rows, np.array([1.0, *[2.0] * 10]))  # the other archetypes say w = 0
    for r in rows:
        if r["archetype"] == "S":
            r["loss"] = np.array([*[2.0] * 10, 1.0])  # its own archetype (many origins) says w = 1
    ok = cb.eligibility(rows, "archetype")["ii"]
    w = cb.weights(rows, "archetype")["ii"]
    mine = [(x, e) for x, e, r in zip(w, ok, rows, strict=True) if r["account"] == "same1"]
    assert sum(e for _, e in mine) > 0
    assert all(x == (0.0 if e else 1.0) for x, e in mine)


def test_the_global_weight_needs_5_other_accounts() -> None:
    rows = made_up_rows({f"o{i}": f"O{i}" for i in range(5)})  # 4 others for each
    assert not any(cb.eligibility(rows, "archetype")["ii"])
    rows = made_up_rows({f"o{i}": f"O{i}" for i in range(6)})  # 5 others
    ok = cb.eligibility(rows, "archetype")["ii"]
    # 5 others x 8 resolved origins = 40 >= 30 first at the 13th origin (index 12); 30 needs 6
    # resolved each (index 10).
    assert ok.index(True) == 10


def test_an_origin_with_a_perfect_default_stops_the_run() -> None:
    rows = made_up_rows({f"o{i}": f"O{i}" for i in range(6)}, n=2)
    set_losses(rows, np.zeros(11))
    with pytest.raises(ValueError, match="undefined"):
        cb.weights(rows, "archetype")


def test_dips_caught_with_no_threshold_counts_0(monkeypatch: pytest.MonkeyPatch) -> None:
    picks = {"default": {"threshold": 0.3}, "variant": None}
    calls = iter(["default", "variant"])
    monkeypatch.setattr(cb, "curve", lambda rows, th: next(calls))
    monkeypatch.setattr(cb, "choose", lambda name: picks[name])
    monkeypatch.setattr(cb, "alert_metrics", lambda rows, t, median_rule: {
        "caught_on": 4, "shortfall_episodes": 9})  # fmt: skip
    out = cb.dips_caught([], [])
    assert out["variant"]["caught_on"] == 0 and out["pass"] is False
    picks["default"] = None
    calls = iter(["default", "variant"])
    out = cb.dips_caught([], [])
    assert out["pass"] is True and "neither" in out["note"]


def test_a_whole_set_scores_and_reports_end_to_end() -> None:
    accounts = {f"p{i}": f"A{i % 4}" for i in range(8)}
    rows = made_up_rows(accounts)
    cb.prepare(rows)
    w = cb.weights(rows, "archetype")
    v = cb.views(rows, w)
    res = {
        "rows": rows,
        "eligible": cb.eligible_counts(rows, "archetype"),
        "learned": {
            k: {
                a: [x for x, r in zip(w[k], rows, strict=True) if r["archetype"] == a]
                for a in sorted({r["archetype"] for r in rows})
            }
            for k in cb.LEARNED
        },
    }
    verdicts = {
        "12": {k: cb.synthetic_conditions(v, k, (len(rows), len(rows))) for k in cb.VARIANTS}
    }
    assert set(verdicts["12"]["i"]) == set(cb.SYNTHETIC_CONDITIONS)
    assert verdicts["12"]["i"]["1"]["n"] == 0  # (i) never uses w = 1
    assert (
        verdicts["12"]["iii"]["1"]["n"] >= 12 * 8 and verdicts["12"]["iii"]["1"]["differing"] == 0
    )
    text = cb.report({"12": res}, verdicts)
    assert "Variants counted: 3" in text and "| 11 synthetic and Berka agree |" in text
