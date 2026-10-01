"""Tests for step 5c (name embeddings). The real model is never loaded here: a small fake
encoder stands in, so these tests need neither sentence-transformers nor the internet."""

import json
import re
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import lookahead.categorizer as categorizer
import lookahead.embeddings as embeddings
from lookahead.categorizer import HybridCategorizer, make_embedding
from tests.test_categorizer_experiments import db  # noqa: F401  (shared synthetic database)


@pytest.fixture(autouse=True)
def fake_encoder(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Deterministic 8-number vectors from the name's first word; records every call."""
    calls: list[list[str]] = []

    def encode(names: list[str]) -> np.ndarray:
        calls.append(list(names))
        out = []
        for name in names:
            rng = np.random.default_rng(zlib.crc32(name.split()[0].casefold().encode()))
            v = rng.normal(size=8)
            out.append(v / np.linalg.norm(v))
        return np.asarray(out, dtype=np.float32)

    monkeypatch.setattr(embeddings, "_encode", encode)
    monkeypatch.setattr(embeddings, "_vectors", {})
    return calls


def rows(names: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"name": n, "group": n, "channel": "in_person", "txn_type": "pos purchase",
          "direction": "out", "amount_bucket": "10-30"} for n in names]
    )  # fmt: skip


def test_each_distinct_name_is_encoded_once(fake_encoder: list[list[str]]) -> None:
    got = embeddings.embed_names(["Cafe Velo", "Maxi", "Cafe Velo"])
    assert got.shape == (3, 8) and np.allclose(got[0], got[2])
    embeddings.embed_names(pd.Series(["Maxi", "Bistro Lune"]))
    assert fake_encoder == [["Cafe Velo", "Maxi"], ["Bistro Lune"]]


def test_cache_round_trip_and_reuse(tmp_path: Path, fake_encoder: list[list[str]]) -> None:
    cache = tmp_path / "data" / "private" / "cache" / "e.npz"
    assert embeddings.warm(["Maxi", "Cafe Velo"], cache) == (0, 2)
    first = embeddings.embed_names(["Maxi"])
    embeddings._vectors.clear()
    assert embeddings.warm(["Maxi", "Cafe Velo", "Stm"], cache) == (2, 1)
    assert fake_encoder[-1] == ["Stm"]  # the two saved names were not encoded again
    assert np.allclose(embeddings.embed_names(["Maxi"]), first)


def test_cache_is_refused_outside_data_private(tmp_path: Path) -> None:
    embeddings.embed_names(["Maxi"])
    with pytest.raises(ValueError, match="data/private"):
        embeddings.save_cache(tmp_path / "e.npz")


def test_a_cache_from_another_model_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "data" / "private" / "cache" / "e.npz"
    embeddings.warm(["Maxi"], cache)
    embeddings._vectors.clear()
    monkeypatch.setattr(embeddings, "MODEL_NAME", "another-model")
    assert embeddings.load_cache(cache) == 0


def test_embedding_models_have_the_declared_features() -> None:
    alone = [t[0] for t in make_embedding().named_steps["features"].transformers]
    both = [t[0] for t in make_embedding(with_current=True).named_steps["features"].transformers]
    assert alone == ["meta", "embedding"]
    assert both == ["name", "meta", "lexicon", "embedding"]


def test_embedding_model_trains_tunes_and_predicts_in_the_hybrid() -> None:
    names = ["Cafe Velo", "Cafe Lune", "Cafe Iris", "Maxi Plus", "Maxi Est", "Maxi Nord"]
    x = rows([n for n in names for _ in range(2)])
    y = [label for label in ["Restaurants & cafés"] * 3 + ["Groceries"] * 3 for _ in range(2)]
    model = HybridCategorizer(make=make_embedding).fit(x, y)  # tuning: C only, no n-grams
    assert set(model.params_) == {"model__C"}
    got = model.predict_rows(rows(["Cafe Soleil", "Maxi Sud"]))
    assert [p.category for p in got] == ["Restaurants & cafés", "Groceries"]
    assert all(p.route == "new merchant" for p in got)


def test_tune_still_tunes_n_grams_for_models_that_have_them() -> None:
    names = ["Cafe Velo", "Cafe Lune", "Cafe Iris", "Maxi Plus", "Maxi Est", "Maxi Nord"]
    x, y = rows(names), ["Restaurants & cafés"] * 3 + ["Groceries"] * 3
    params, _ = categorizer.tune(categorizer.make_current, x, y, names)
    assert set(params) == {"model__C", "features__name__ngram_range"}


@pytest.mark.parametrize(
    ("current", "minilm", "both", "chosen"),
    [(0.38, 0.50, 0.45, "minilm"), (0.38, 0.385, 0.42, "minilm + current"),
     (0.38, 0.385, 0.30, "current"), (0.38, 0.39, 0.39, "minilm")],
)  # fmt: skip
def test_the_minilm_rule_is_fixed_in_advance(
    current: float, minilm: float, both: float, chosen: str
) -> None:
    from eval.categorizer_minilm import choose

    assert choose({"current": current, "minilm": minilm, "minilm + current": both}) == chosen


@pytest.mark.slow
def test_command_runs_the_declared_variants_counts_only(
    db: Path,  # noqa: F811
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from eval.categorizer_minilm import main

    monkeypatch.setattr(
        categorizer, "PARAM_GRID", {"model__C": [1.0], "features__name__ngram_range": [(2, 5)]}
    )
    private = tmp_path / "data" / "private"
    out_file, cache = private / "eval" / "minilm.json", private / "cache" / "e.npz"
    code = main(["--db", str(db), "--out", str(out_file), "--cache", str(cache), "--months", "4"])
    out = capsys.readouterr().out
    assert code == 0 and cache.exists()
    for text in ("holdout untouched", "minilm + current", "dev candidate", "per-category recall"):
        assert text in out
    assert "$" not in out and not re.search(r"\d+\.\d{2}", out)
    for name in ("Tim Hortons", "Maxi", "Couche-Tard", "Nadia"):
        assert name not in out
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert set(saved["variants"]) == {"current", "minilm", "minilm + current"}
    assert main(["--db", str(db), "--out", str(tmp_path / "x.json")]) == 2  # not private
