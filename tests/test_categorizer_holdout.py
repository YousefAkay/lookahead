"""Tests for step 7b (the holdout, scored once). Synthetic stand-in, fake LLM client,
temporary paths only."""

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

import lookahead.categorizer as categorizer
from eval.categorizer_holdout import in_synthetic_list, load_choice, main
from tests.test_llm_categorizer import FakeClient

ACCOUNT = "scotia-chequing"


def frame(names: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"name": names, "group": names})


def test_pre_taught_merchants_are_found_by_the_resolution_rules() -> None:
    synthetic = frame(["Maxi", "Caf? Velisaro", "Stm-Opus"])
    tested = frame(["MAXI", "Cafe Velisaro", "STM", "Bistro Lune", "Maxime Coiffure"])
    assert list(in_synthetic_list(tested, synthetic)) == [True, True, True, False, False]


def test_choice_file_is_read_as_saved(tmp_path: Path) -> None:
    path = tmp_path / "choice.json"
    path.write_text(json.dumps({
        "training": "real + synthetic", "synthetic_months": 12, "flag_threshold": None,
        "params": {"model__C": 100, "features__name__ngram_range": [2, 4]},
    }), encoding="utf-8")  # fmt: skip
    params, threshold, training, months = load_choice(path)
    assert params == {"model__C": 100, "features__name__ngram_range": (2, 4)}
    assert threshold == float("inf") and training == "real + synthetic" and months == 12


@pytest.fixture
def setup(tmp_path: Path) -> list[str]:
    private = tmp_path / "data" / "private"
    (private / "eval").mkdir(parents=True)
    (private / "eval" / "choice.json").write_text(json.dumps({
        "training": "real + synthetic", "synthetic_months": 4, "flag_threshold": 0.6,
        "params": {"model__C": 1.0, "features__name__ngram_range": [2, 5]},
    }), encoding="utf-8")  # fmt: skip
    env = tmp_path / ".env"
    env.write_text(f"PSEUDONYM_KEY={'ab' * 32}\n", encoding="utf-8")
    return ["--choice", str(private / "eval" / "choice.json"), "--out",
            str(private / "eval" / "holdout.json"), "--cache",
            str(private / "cache" / "llm.json"), "--env", str(env)]  # fmt: skip


def test_dry_run_sends_and_scores_nothing(
    db: Path, setup: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeClient()
    assert main(["--db", str(db), *setup, "--dry-run"], client) == 0
    out = capsys.readouterr().out
    assert client.requests == [] and "not scored yet" in out and "calls needed" in out
    assert "accuracy" not in out
    assert not (tmp_path / "data" / "private" / "eval" / "holdout.json").exists()


@pytest.mark.slow
def test_holdout_is_scored_once_with_the_saved_choice(
    db: Path, setup: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    from lookahead.labels import labelled_examples

    conn = sqlite3.connect(db)
    dev = set(labelled_examples(conn, ACCOUNT, ("dev",))["fingerprint"])
    holdout = labelled_examples(conn, ACCOUNT, ("test_a", "test_b"))
    conn.close()
    fits: list[tuple[set[str], dict, float | None]] = []
    real_fit = categorizer.HybridCategorizer.fit

    def spy(self, x, y, groups=None, extra_x=None, extra_y=None):  # type: ignore[no-untyped-def]
        fits.append((set(x["fingerprint"]), self.params, self.threshold))
        return real_fit(self, x, y, groups, extra_x, extra_y)

    monkeypatch.setattr(categorizer.HybridCategorizer, "fit", spy)
    client = FakeClient()
    assert main(["--db", str(db), *setup], client) == 0
    out = capsys.readouterr().out

    # trained once, on dev rows only, with exactly the saved settings (nothing tuned here)
    assert len(fits) == 1
    assert fits[0] == (dev, {"model__C": 1.0, "features__name__ngram_range": (2, 5)}, 0.6)
    for text in ("Test A (rows after the date cutoff)", "Test B (merchants never seen), all",
                 "in the synthetic list (pre-taught)", "not in the synthetic list",
                 "trained hybrid:", "hybrid + LLM fallback:", "extra: LLM for flagged",
                 "per-category recall", "curve", "per 1,000 merchants"):  # fmt: skip
        assert text in out
    assert "$" not in out
    for name in ("Tim Hortons", "Maxi", "Couche-Tard", "Nadia"):
        assert name not in out
    saved = json.loads(
        (tmp_path / "data" / "private" / "eval" / "holdout.json").read_text(encoding="utf-8")
    )
    assert saved["runs"] == 1
    a = saved["results"]["trained hybrid"]["Test A (rows after the date cutoff)"]
    b = saved["results"]["trained hybrid"]["Test B (merchants never seen), all"]
    assert a["rows"] + b["rows"] == len(holdout)  # every labelled holdout row scored once
    parts = saved["results"]["trained hybrid"]
    in_list = parts["Test B, merchants in the synthetic list (pre-taught)"]
    not_in = parts["Test B, merchants not in the synthetic list"]
    assert sum(p["rows"] for p in (in_list, not_in) if p) == b["rows"]
    sent = len(client.requests)
    assert sent == saved["llm"]["names"] > 0

    # a second run is refused; a dry run still works; --again is recorded
    again_client = FakeClient()
    assert main(["--db", str(db), *setup], again_client) == 1
    assert "already scored" in capsys.readouterr().out and again_client.requests == []
    assert main(["--db", str(db), *setup, "--dry-run"], again_client) == 0
    assert "already scored 1 time(s)" in capsys.readouterr().out
    assert main(["--db", str(db), *setup, "--again"], again_client) == 0
    assert again_client.requests == []  # names come from the cache
    saved = json.loads(
        (tmp_path / "data" / "private" / "eval" / "holdout.json").read_text(encoding="utf-8")
    )
    assert saved["runs"] == 2


def test_a_failed_llm_call_does_not_use_up_the_run(
    db: Path, setup: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Broken:
        class messages:  # noqa: N801
            @staticmethod
            def create(**_: object) -> object:
                raise TypeError("boom Cafe Secret")

    assert main(["--db", str(db), *setup], Broken()) == 1
    out = capsys.readouterr().out
    assert "TypeError" in out and "not scored" in out and "Cafe Secret" not in out
    assert not (tmp_path / "data" / "private" / "eval" / "holdout.json").exists()


def test_paths_outside_data_private_are_refused(db: Path, tmp_path: Path) -> None:
    assert main(["--db", str(db), "--out", str(tmp_path / "x.json")]) == 2
