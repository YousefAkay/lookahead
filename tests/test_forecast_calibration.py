"""The calibration of the simulated futures: widening, the probability map, the fitting rule, and
applying a saved calibration to a real backtest folder (a made-up one here) without fitting."""

import json
import random
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from eval.backtest_real import run_real
from eval.calibrate import (
    apply,
    apply_real,
    choose_variant,
    fit_multipliers,
    fit_recalibration,
    lows_array,
)
from lookahead.db import connect
from lookahead.forecast.calibration import (
    Calibration,
    Recalibration,
    calibrate_record,
    fit_multiplier,
    needed_multiplier,
    widen_band,
    widened_lows,
)
from lookahead.forecast.simulate import _percentile
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents


def _paths(n: int = 200, days: int = 30, seed: int = 1) -> list[list[int]]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        level, path = 100_000, []
        for _ in range(days):
            level += rng.randint(-5_000, 4_000)
            path.append(level)
        out.append(path)
    return out


def _band(paths: list[list[int]], q: float) -> list[int]:
    return [_percentile([p[k] for p in paths], q) for k in range(len(paths[0]))]


def test_needed_multiplier() -> None:
    assert needed_multiplier(80, 100, 120, 100) == 0.0
    assert needed_multiplier(80, 100, 120, 60) == 2.0  # twice the lower spread
    assert needed_multiplier(80, 100, 120, 110) == 0.5
    assert needed_multiplier(100, 100, 120, 90) == float("inf")  # no spread below


def test_fit_multiplier_covers_the_target_share() -> None:
    needed = [0.1 * k for k in range(1, 11)]  # 0.1 .. 1.0
    s = fit_multiplier(needed, 0.8)
    assert s == pytest.approx(0.8)
    assert sum(x <= s for x in needed) / len(needed) >= 0.8


def test_widening_the_futures_moves_the_percentiles_the_same_way() -> None:
    paths = _paths()
    p10, p50, p90 = _band(paths, 0.1), _band(paths, 0.5), _band(paths, 0.9)
    s = [1 + 0.05 * k for k in range(30)]
    widened = [[round(m + f * (x - m)) for x, m, f in zip(p, p50, s, strict=True)] for p in paths]
    lo, hi = widen_band(p10, p50, p90, s)
    assert all(abs(a - b) <= 1 for a, b in zip(lo, _band(widened, 0.1), strict=True))
    assert all(abs(a - b) <= 1 for a, b in zip(hi, _band(widened, 0.9), strict=True))
    assert _band(widened, 0.5) == p50


def test_array_and_plain_versions_of_the_lows_agree() -> None:
    paths = _paths()
    p50 = _band(paths, 0.5)
    s = [0.7 + 0.1 * k for k in range(30)]
    assert list(lows_array(np.array(paths), p50, s)) == widened_lows(paths, p50, s)


def test_multipliers_of_one_change_nothing() -> None:
    paths = _paths()
    record = {
        "p10": _band(paths, 0.1), "p50": _band(paths, 0.5), "p90": _band(paths, 0.9),
        "buffer_cents": 90_000, "lowest_cents": sorted(min(p) for p in paths),
    }  # fmt: skip
    record["shortfall_probability"] = sum(lo < 90_000 for lo in record["lowest_cents"]) / 200
    out = calibrate_record(record, paths, Calibration(tuple([1.0] * 30)))
    assert out["p10"] == record["p10"] and out["p90"] == record["p90"]
    assert out["lowest_cents"] == record["lowest_cents"]
    assert out["shortfall_probability"] == record["shortfall_probability"]


def test_wider_futures_dip_more_often() -> None:
    paths = _paths()
    p50 = _band(paths, 0.5)
    record = {"p10": _band(paths, 0.1), "p50": p50, "p90": _band(paths, 0.9),
              "buffer_cents": 90_000}  # fmt: skip
    narrow = calibrate_record(record, paths, Calibration(tuple([1.0] * 30)))
    wide = calibrate_record(record, paths, Calibration(tuple([2.0] * 30)))
    assert wide["shortfall_probability"] >= narrow["shortfall_probability"]
    assert wide["median_lowest_cents"] <= narrow["median_lowest_cents"]


def test_without_paths_only_the_band_changes() -> None:
    record = {"p10": [90] * 30, "p50": [100] * 30, "p90": [110] * 30,
              "shortfall_probability": 0.3, "buffer_cents": 0}  # fmt: skip
    out = calibrate_record(record, None, Calibration(tuple([2.0] * 30)))
    assert out["p10"] == [80] * 30 and out["p90"] == [120] * 30
    assert out["shortfall_probability"] == 0.3 and out["calibrated"] == "band only"


def test_probability_maps_are_monotone_and_survive_json() -> None:
    rng = random.Random(3)
    probs = [rng.random() for _ in range(400)]
    ys = [1 if rng.random() < min(1.0, p * 1.5) else 0 for p in probs]
    for kind in ("logistic", "isotonic"):
        recal = fit_recalibration(probs, ys, kind)
        grid = [k / 20 for k in range(21)]
        mapped = [recal(p) for p in grid]
        assert all(b >= a - 1e-12 for a, b in zip(mapped, mapped[1:], strict=False))
        assert all(0 <= m <= 1 for m in mapped)
        again = Calibration.from_json(Calibration((1.0,) * 30, recal).to_json()).recalibration
        assert [again(p) for p in grid] == pytest.approx(mapped)
    assert fit_recalibration(probs, [0] * 400, "logistic").kind == "none"  # one class: no map
    assert Recalibration("none")(0.37) == 0.37


def test_the_choice_rule() -> None:
    def rows(p: float, outcome: bool) -> list[dict]:
        return [{"shortfall_probability": p, "in_shortfall_at_origin": False,
                 "realized_first_below_buffer": "x" if outcome else None}]  # fmt: skip

    # widened 0.25 brier; logistic 0.16; isotonic 0.159 (not more than 0.002 better)
    lopo = {"widened": rows(0.5, True), "logistic": rows(0.6, True), "isotonic": rows(0.601, True)}
    assert choose_variant(lopo)[0] == "logistic"
    lopo["isotonic"] = rows(0.9, True)
    assert choose_variant(lopo)[0] == "isotonic"
    lopo = {"widened": rows(0.5, True), "logistic": rows(0.501, True),
            "isotonic": rows(0.5005, True)}  # fmt: skip
    assert choose_variant(lopo)[0] == "widened"  # no map lowers it by 0.002


def test_fit_multipliers_reaches_80_percent_on_its_own_records() -> None:
    rng = random.Random(5)
    records = []
    for _ in range(300):
        r = {"p10": [90] * 30, "p50": [100] * 30, "p90": [110] * 30}
        r["realized"] = [100 + round(rng.gauss(0, 20)) for _ in range(30)]
        records.append(r)
    s = fit_multipliers(records)
    for k in range(30):
        lo, hi = 100 - s[k] * 10, 100 + s[k] * 10
        assert sum(lo <= r["realized"][k] <= hi for r in records) / 300 >= 0.8


@pytest.fixture(scope="module")
def real_like_folder(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A made-up "real" backtest folder with paths, from a synthetic export."""
    folder = tmp_path_factory.mktemp("real")
    export, _ = write_persona(build_persona("v2-TIGHT-01").persona, folder, 42, START, END)
    conn = connect(folder / "made_up.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    set_safety_buffer_cents(conn, 10_000)
    conn.close()
    counts = run_real(folder / "made_up.db", folder / "paths", 40, quick=True, paths=True)
    assert counts["new_origins"] == 4
    return folder / "paths"


@pytest.mark.slow
def test_paths_are_saved_beside_the_records_and_a_rerun_skips(real_like_folder: Path) -> None:
    data = np.load(real_like_folder / "account-1.paths.npz")
    records = [json.loads(x) for x in (real_like_folder / "account-1.jsonl").read_text(
        encoding="utf-8").splitlines()]  # fmt: skip
    assert data["paths"].shape == (4, 40, 30)
    assert [str(o) for o in data["origins"]] == [r["origin"] for r in records]
    db = real_like_folder.parent / "made_up.db"
    assert run_real(db, real_like_folder, 40, quick=True, paths=True)["new_origins"] == 0


@pytest.mark.slow
def test_apply_to_a_real_folder_prints_numbers_and_writes_nothing(
    real_like_folder: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # fmt: skip
    fit = tmp_path / "cal.json"
    fit.write_text(Calibration(tuple([1.5] * 30), Recalibration("none"),
                               {"data": "test"}).to_json(), encoding="utf-8")  # fmt: skip
    before = sorted(p.name for p in real_like_folder.iterdir())
    assert apply_real(real_like_folder, fit) == 0
    printed = capsys.readouterr().out
    assert "After, own buffer" in printed and "After, buffer $0" in printed
    assert "4 with every future's path" in printed
    assert "acct" not in printed  # the account name is never printed
    assert sorted(p.name for p in real_like_folder.iterdir()) == before


@pytest.mark.slow
def test_apply_widens_the_band_of_a_saved_record(real_like_folder: Path) -> None:
    record = json.loads((real_like_folder / "account-1.jsonl").read_text(
        encoding="utf-8").splitlines()[0])  # fmt: skip
    paths = np.load(real_like_folder / "account-1.paths.npz")["paths"][0]
    out = apply(record, paths, Calibration(tuple([2.0] * 30)))
    assert all(a <= b for a, b in zip(out["p10"], record["p10"], strict=True))
    assert all(a >= b for a, b in zip(out["p90"], record["p90"], strict=True))
    assert out["calibrated"] == "full"
