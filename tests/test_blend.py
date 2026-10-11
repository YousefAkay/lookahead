"""Step 6 (2026-10-12): blending with "the balance stays the same". Made-up records only."""

import json
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from eval.backtest import FORMAT, LOGIC
from eval.blend import (
    apply_real,
    blend_record,
    fit_weights,
    leave_one_out,
    non_increasing,
    raw_weights,
)

ORIGIN = date(2026, 3, 2)


def percentile(values: list[int], share: float) -> int:
    ordered = sorted(values)
    return ordered[round(share * (len(ordered) - 1))]


def record_from(paths: np.ndarray, start: int, realized: list[int], persona: str = "v2-SAL-01",
                buffer: int = 10_000, origin: date = ORIGIN, archetype: str = "SAL",
                dataset: str = "synthetic") -> dict:  # type: ignore[type-arg]  # fmt: skip
    """A record built from its futures the way the simulation reads them."""
    cols = [[int(x) for x in paths[:, k]] for k in range(30)]
    lows = sorted(int(x) for x in paths.min(axis=1))
    below = [origin + timedelta(days=k + 1) for k, b in enumerate(realized) if b < buffer]
    return {
        "format": FORMAT, "logic": LOGIC, "dataset": dataset, "persona": persona,
        "archetype": archetype, "held_out": False, "origin": origin.isoformat(),
        "buffer_cents": buffer, "start_balance_cents": start,
        "days": [(origin + timedelta(days=k + 1)).isoformat() for k in range(30)],
        "p10": [percentile(c, 0.1) for c in cols], "p50": [percentile(c, 0.5) for c in cols],
        "p90": [percentile(c, 0.9) for c in cols],
        "point": [percentile(c, 0.5) for c in cols], "lowest_cents": lows,
        "shortfall_probability": sum(x < buffer for x in lows) / len(lows),
        "median_lowest_cents": percentile(lows, 0.5), "realized": realized,
        "realized_lowest_cents": min(realized),
        "realized_first_below_buffer": below[0].isoformat() if below else None,
        "realized_below_zero": min(realized) < 0, "history_days": 200,
        "in_shortfall_at_origin": start < buffer, "futures": len(paths),
    }  # fmt: skip


def futures(start: int, drift: int, seed: int = 0, n: int = 50) -> np.ndarray:
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, 2_000, size=(n, 30)).round().astype(np.int64)
    return start + np.cumsum(steps, axis=1)


def test_a_weight_of_one_gives_the_forecast_back_exactly() -> None:
    paths = futures(50_000, -800)
    r = record_from(paths, 50_000, [50_000] * 30)
    b = blend_record(r, paths, [1.0] * 30)
    for key in ("p10", "p50", "p90", "point", "lowest_cents", "median_lowest_cents",
                "shortfall_probability"):  # fmt: skip
        assert b[key] == r[key], key


def test_a_weight_of_zero_is_the_balance_staying_the_same() -> None:
    paths = futures(50_000, -800)
    r = record_from(paths, 50_000, [50_000] * 30)
    b = blend_record(r, paths, [0.0] * 30)
    assert b["p10"] == b["p50"] == b["p90"] == [50_000] * 30
    assert b["shortfall_probability"] == 0.0 and b["median_lowest_cents"] == 50_000


def test_the_band_and_the_chance_come_from_the_blended_futures() -> None:
    paths = futures(30_000, -1_000)
    r = record_from(paths, 30_000, [30_000] * 30)
    w = [0.5] * 30
    b = blend_record(r, paths, w)
    blended = np.round(30_000 + 0.5 * (paths.astype(float) - 30_000)).astype(np.int64)
    direct = record_from(blended, 30_000, [30_000] * 30)
    for key in ("p10", "p50", "p90", "lowest_cents", "shortfall_probability"):
        assert b[key] == direct[key], key
    assert b["shortfall_probability"] < r["shortfall_probability"]  # pulled towards the start


def test_the_per_day_fit_finds_the_weight_that_made_the_outcome() -> None:
    records = []
    for seed in range(6):
        paths = futures(40_000, -600, seed)
        r0 = record_from(paths, 40_000, [40_000] * 30)
        realized = [round(40_000 + 0.3 * (m - 40_000)) for m in r0["p50"]]
        records.append(record_from(paths, 40_000, realized, origin=ORIGIN + timedelta(weeks=seed)))
    assert all(abs(w - 0.3) <= 0.011 for w in raw_weights(records))


def test_smoothing_is_non_increasing_and_inside_zero_and_one() -> None:
    assert non_increasing([0.2, 0.5, 0.4, 0.1]) == pytest.approx([11 / 30] * 3 + [0.1])
    assert non_increasing([1.0, 0.8, 0.8, 0.3]) == [1.0, 0.8, 0.8, 0.3]
    out = non_increasing([0.0, 0.0, 0.9] + [0.5] * 27)
    assert all(a >= b for a, b in zip(out, out[1:], strict=False))
    assert all(0.0 <= w <= 1.0 for w in out)


def test_only_ordinary_origins_are_fitted_on() -> None:
    paths = futures(40_000, -600)
    r = record_from(paths, 40_000, [40_000] * 30)
    already = record_from(paths, 5_000, [5_000] * 30)  # below the buffer: not used to fit
    assert raw_weights([r, already]) == raw_weights([r])


def test_leave_one_out_never_fits_on_the_held_back_persona() -> None:
    records, paths = [], {}
    for k, (pid, share) in enumerate((("v2-SAL-01", 1.0), ("v2-SAL-02", 1.0),
                                      ("v2-SAL-03", 0.0))):  # fmt: skip
        p = futures(40_000, -600, k)
        r0 = record_from(p, 40_000, [40_000] * 30, persona=pid)
        realized = [round(40_000 + share * (m - 40_000)) for m in r0["p50"]]
        r = record_from(p, 40_000, realized, persona=pid)
        records.append(r)
        paths[(pid, r["origin"])] = p
    held, folds = leave_one_out(records, paths)
    by = {f["persona"]: f["weights"] for f in folds}
    assert by["v2-SAL-03"] == [1.0] * 30  # fitted on the two personas whose outcome was w = 1
    assert len(held) == 3 and fit_weights(records)  # the full fit runs too


def test_held_out_personas_are_refused() -> None:
    p = futures(40_000, -600)
    r = record_from(p, 40_000, [40_000] * 30, persona="v2-SAL-04")
    with pytest.raises(ValueError, match="held-out"):
        leave_one_out([r], {(r["persona"], r["origin"]): p})


def real_folder(root: Path) -> Path:
    folder = root / "data" / "private" / "backtest" / "paths"
    folder.mkdir(parents=True)
    records, kept = [], []
    for k in range(3):
        p = futures(20_000, -500, k)
        origin = ORIGIN + timedelta(weeks=k)
        realized = [20_000 - 300 * (7 * k + d) for d in range(1, 31)]  # one daily series
        start = 20_000 - 300 * 7 * k
        r = record_from(p, start, realized, persona="account-0a1b", origin=origin,
                        archetype=None, dataset="real")  # type: ignore[arg-type]  # fmt: skip
        r["held_out"] = None
        records.append(r)
        kept.append(p)
    (folder / "account-0a1b.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records),
                                               encoding="utf-8")  # fmt: skip
    np.savez_compressed(folder / "account-0a1b.paths.npz",
                        origins=np.array([r["origin"] for r in records]),
                        paths=np.array(kept, dtype=np.int64))  # fmt: skip
    return folder


def test_apply_prints_summary_numbers_only_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = real_folder(tmp_path)
    weights = tmp_path / "weights.json"
    weights.write_text(json.dumps({"12": {"weights": [0.5] * 30},
                                   "long": {"weights": [0.7] * 30}}), encoding="utf-8")  # fmt: skip
    before = sorted(p.name for p in folder.iterdir())
    assert apply_real(folder, weights) == 0
    text = capsys.readouterr().out
    assert "Blended, 12-month weights" in text and "Blended, 24-month weights" in text
    assert "own buffer" in text and "buffer $0" in text
    assert "account-0a1b" not in text and not re.search(r"\d{4}-\d{2}-\d{2}", text)
    assert sorted(p.name for p in folder.iterdir()) == before


def test_apply_refuses_a_folder_outside_data_private(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    assert apply_real(folder, tmp_path / "w.json") == 1
    assert "outside data/private" in capsys.readouterr().out


def test_the_paths_run_must_equal_arm_0_record_for_record() -> None:
    from eval.blend import differs_from_arm0

    p = futures(40_000, -600)
    r = record_from(p, 40_000, [40_000] * 30)
    same = {**r, "version": "abc"}  # only the commit stamp differs
    moved = {**r, "p50": [1] * 30}
    assert differs_from_arm0([same], [{**r, "arm": 0, "confirmed": 0}]) == []
    assert differs_from_arm0([moved], [r]) == [f"v2-SAL-01 {r['origin']}"]
    assert differs_from_arm0([r], []) == [f"v2-SAL-01 {r['origin']}"]
