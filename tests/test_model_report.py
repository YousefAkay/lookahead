"""The Model report: the numbers behind it and the page. Synthetic files and temporary folders
only: the "real" folder here is made from a synthetic export."""

import json
from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from eval.backtest_real import run_real
from eval.report_data import (
    ALERT_LABELS,
    FORECAST_LABELS,
    alert_values,
    breakdown_rows,
    forecast_values,
    real_summary,
    reliability_values,
    summarize,
)
from lookahead.db import connect
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "app" / "pages" / "7_Model_report.py"


@pytest.fixture(scope="module")
def real_like(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A made-up "real" backtest folder (quick run of a synthetic export)."""
    folder = tmp_path_factory.mktemp("real") / "data" / "private"  # real results stay private
    folder.mkdir(parents=True)
    export, _ = write_persona(build_persona("v2-TIGHT-01").persona, folder, 42, START, END)
    conn = connect(folder / "made_up.db")
    store_exports(conn, "acct", [(export.name, parse_export(export))], datetime(2026, 10, 1))
    set_safety_buffer_cents(conn, 10_000)
    conn.close()
    run_real(folder / "made_up.db", folder / "backtest" / "full", 40)
    return folder / "backtest"


def test_no_real_folder_means_no_real_summary(tmp_path: Path) -> None:
    assert real_summary(tmp_path / "missing") is None
    (tmp_path / "empty").mkdir()
    assert real_summary(tmp_path / "empty") is None


@pytest.mark.slow
def test_the_real_summary_reads_the_full_folder_and_writes_nothing(real_like: Path) -> None:
    before = sorted(p.name for p in real_like.rglob("*"))
    summary = real_summary(real_like)
    assert summary is not None
    assert summary["source"]["accounts"] == 1 and summary["source"]["origins"] > 30
    own, zero = summary["own_buffer"], summary["zero_buffer"]
    assert own["ordinary"]["n_origins"] + own["already"].get("n_origins", 0) == \
        summary["source"]["origins"]  # fmt: skip
    assert own["alerts"]["n_personas"] == zero["alerts"]["n_personas"] == 1
    assert sorted(p.name for p in real_like.rglob("*")) == before
    text = json.dumps(summary)
    assert "acct" not in text  # no account name, no rows


def test_rows_have_one_value_per_label_and_dashes_when_missing() -> None:
    assert forecast_values(None) == ["-"] * len(FORECAST_LABELS)
    assert alert_values(None) == ["-"] * len(ALERT_LABELS)
    assert reliability_values(None) == ["-"] * 5


@pytest.mark.slow
def test_rows_from_a_summary(real_like: Path) -> None:
    summary = real_summary(real_like)
    assert summary is not None
    own = summary["own_buffer"]
    values = forecast_values(own["ordinary"])
    assert len(values) == len(FORECAST_LABELS) and values[0].endswith("(1)")
    assert len(alert_values(own["alerts"])) == len(ALERT_LABELS)
    rows = breakdown_rows(own["by_history"], ("8-12 weeks", "12-26 weeks", "26+ weeks"))
    assert [r[0] for r in rows] == [b for b in ("8-12 weeks", "12-26 weeks", "26+ weeks")
                                    if b in own["by_history"]]  # fmt: skip


def test_summarize_counts_match_the_records() -> None:
    def record(origin: str, start: int, realized: list[int], prob: float) -> dict:
        return {
            "persona": "p", "origin": origin, "days": [f"2026-01-{d:02d}" for d in range(2, 32)],
            "p10": [start - 100] * 30, "p50": [start] * 30, "p90": [start + 100] * 30,
            "point": [start] * 30, "realized": realized, "start_balance_cents": start,
            "median_lowest_cents": start, "realized_lowest_cents": min(realized),
            "shortfall_probability": prob, "buffer_cents": 0, "history_days": 100,
            "in_shortfall_at_origin": start < 0,
            "realized_first_below_buffer": None if min(realized) >= 0 else "2026-01-05",
            "lowest_cents": [start] * 10,
        }  # fmt: skip

    records = [record("2026-01-01", 1_000, [1_000] * 30, 0.0)]
    s = summarize(records)
    assert s["ordinary"]["n_origins"] == 1 and s["ordinary"]["coverage"]["30"] == 1.0
    assert s["alerts"]["shortfall_episodes"] == 0


@pytest.mark.slow
def test_the_page_without_real_results_says_run_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # fmt: skip
    monkeypatch.setenv("LOOKAHEAD_REAL_BACKTEST", str(tmp_path / "missing"))
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception, at.exception
    frames = [df.value for df in at.dataframe]
    forecast = next(f for f in frames if "Synthetic, held out" in f.columns)
    assert set(forecast["Synthetic, held out"]) == {"not run yet"}
    assert set(forecast["Real"]) == {"run locally"}
    text = "\n".join(str(m.value) for m in at.markdown)
    assert "Known limitations" in text and "Categorizer" in text
    assert "Not financial advice" in text
    assert text.count("shelved: not used by the app") == 2  # reliability and threshold cards


@pytest.mark.slow
def test_the_page_reads_real_results_at_runtime(
    real_like: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # fmt: skip
    monkeypatch.setenv("LOOKAHEAD_REAL_BACKTEST", str(real_like))
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception, at.exception
    forecast = next(df.value for df in at.dataframe if "Real" in df.value.columns)
    assert "run locally" not in set(forecast["Real"])
    assert forecast["Real"].iloc[0].endswith("(1)")
