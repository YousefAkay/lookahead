"""The futures store (eval/futures_store.py): exact paths, atomic files, the recompute check."""

from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest

from eval import futures_store as fs
from eval.anchor import anchor_origin
from eval.backtest import run_origin_full
from lookahead.db import connect
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents

PID = "v2-SAL-01"
ORIGINS = [date(2026, 3, 2), date(2026, 5, 4)]


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    tmp = tmp_path_factory.mktemp("store")
    p = build_persona(PID)
    export, _ = write_persona(p.persona, tmp, 42, START, END)
    c = connect(tmp / "x.db")
    store_exports(c, PID, [(export.name, parse_export(export))], datetime(2026, 10, 1))
    set_safety_buffer_cents(c, p.buffer_cents)
    yield c
    c.close()


def test_the_nearest_rank_percentile_is_the_simulators() -> None:
    values = np.arange(500)[::-1].copy()  # 499 .. 0, unsorted
    assert int(fs.nearest_rank(values, 0.5)) == 250  # round(249.5) = 250, as in the simulator
    assert int(fs.nearest_rank(values, 0.1)) == 50  # round(49.9)
    assert int(fs.nearest_rank(values, 0.9)) == 449  # round(449.1)


def test_the_default_recomputed_from_saved_paths_equals_the_record(conn, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    records, paths = [], []
    for origin in ORIGINS:
        rec = anchor_origin(conn, PID, origin, 60, "SAL")  # the experiment 3 record
        _, p, _ = run_origin_full(conn, PID, "synthetic", origin, 60, "SAL", False)
        records.append(rec)
        paths.append(np.asarray(p, dtype=np.int64))
    arrays = fs.pack(records, paths, {"archetype": np.array("SAL"), "account": np.array(PID)})
    path = tmp_path / "SAL.npz"
    fs.write_atomic(path, arrays)
    assert not (tmp_path / "SAL.npz.tmp").exists()  # the temporary file is renamed away
    data = fs.load(path)
    assert data["paths"].dtype == np.int32 and data["paths"].shape == (2, 60, 30)
    assert np.array_equal(data["paths"], np.stack(paths))  # exact: cents, nothing rounded
    checked, equal = fs.check_file(path, records)
    assert (checked, equal) == (2, 2)  # p10, p50, p90, dip chance, median lowest, CRPS


def test_a_changed_path_is_noticed_by_the_check(conn, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    rec = anchor_origin(conn, PID, ORIGINS[0], 40, "SAL")
    _, p, _ = run_origin_full(conn, PID, "synthetic", ORIGINS[0], 40, "SAL", False)
    paths = np.asarray(p, dtype=np.int64).copy()
    paths[:, 5] += 1000  # one day moved by $10 in every future
    arrays = fs.pack([rec], [paths], {"archetype": np.array("SAL"), "account": np.array(PID)})
    fs.write_atomic(tmp_path / "bad.npz", arrays)
    assert fs.check_file(tmp_path / "bad.npz", [rec]) == (1, 0)


def test_held_out_personas_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        fs.save_persona("v2-ITIGHT-04", tmp_path)
