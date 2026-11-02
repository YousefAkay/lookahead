"""Experiment 4, the combination itself (eval/combination.py), on made-up futures."""

import json
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
             "crps_stays": [0.0] * 30} for o in origins]  # fmt: skip
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
        cb.load_account(account, tmp_path / "nowhere")
