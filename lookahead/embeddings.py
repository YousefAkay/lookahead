"""Sentence embeddings of merchant names (step 5c): a small multilingual MiniLM, run locally.

An embedding is a list of numbers (384 here) that places names with a similar meaning close
together, so "Boulangerie X" can land near "Bakery Y" without sharing letters. The model is
downloaded once, then everything runs on this machine: no name leaves it.

Each distinct name is embedded once and remembered (in memory, and on disk if a cache file
is given). The cache holds real merchant names, so it may only live inside data/private/.
Needs the optional `sentence-transformers` library (not needed by the rest of the app).
"""

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from lookahead.merchants import is_private_path

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_CACHE = Path("data/private/cache/minilm_embeddings.npz")

_model: object | None = None
_vectors: dict[str, np.ndarray] = {}  # name -> embedding, for this run


def _encode(names: list[str]) -> np.ndarray:
    """One unit-length vector per name, from the model (loaded on first use)."""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "step 5c needs sentence-transformers: see the install steps in DECISIONS.md"
            ) from e
        _model = SentenceTransformer(MODEL_NAME, device="cpu")
    vectors = _model.encode(  # type: ignore[attr-defined]
        names, batch_size=32, normalize_embeddings=True, show_progress_bar=False
    )
    return np.asarray(vectors, dtype=np.float32)


def embed_names(names: object) -> np.ndarray:
    """Embeddings for `names`, one row each (a scikit-learn feature). New names are encoded
    once; repeats come from memory."""
    values = [str(n) for n in np.asarray(names, dtype=object).ravel()]
    missing = sorted({n for n in values if n not in _vectors})
    if missing:
        for name, vector in zip(missing, _encode(missing), strict=True):
            _vectors[name] = vector
    if not values:
        return np.zeros((0, 0), dtype=np.float32)
    return np.vstack([_vectors[n] for n in values])


def load_cache(path: Path = DEFAULT_CACHE) -> int:
    """Read embeddings saved by an earlier run; returns how many names were loaded."""
    if not path.exists():
        return 0
    with np.load(path, allow_pickle=False) as saved:
        if str(saved["model"]) != MODEL_NAME:
            return 0  # saved by another model: ignore it
        for name, vector in zip(saved["names"], saved["vectors"], strict=True):
            _vectors[str(name)] = vector
        return len(saved["names"])


def save_cache(path: Path = DEFAULT_CACHE) -> int:
    """Save every embedding known in this run; refuses to write outside data/private/."""
    if not is_private_path(path):
        raise ValueError("the embedding cache holds merchant names: keep it in data/private/")
    path.parent.mkdir(parents=True, exist_ok=True)
    names = sorted(_vectors)
    vectors = np.vstack([_vectors[n] for n in names]) if names else np.zeros((0, 0))
    np.savez(path, model=np.array(MODEL_NAME), names=np.array(names, dtype=str), vectors=vectors)
    return len(names)


def warm(names: Sequence[str], path: Path | None = DEFAULT_CACHE) -> tuple[int, int]:
    """Embed all `names` up front, using and updating the disk cache. Returns
    (names read from disk, names newly encoded)."""
    loaded = load_cache(path) if path is not None else 0
    before = len(_vectors)
    embed_names(list(names))
    if path is not None:
        save_cache(path)
    return loaded, len(_vectors) - before
