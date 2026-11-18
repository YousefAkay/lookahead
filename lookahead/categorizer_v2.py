"""Categorizer v2 switches (docs/specs/2026-11-18-categorizer-v2.md). Every one is OFF by
default, and with all of them off the categorizer behaves exactly as before v2
(tests/test_categorizer_v2_identity.py).

Settings, kept in `app_settings` ("on" / "off"; a missing row or table means off):
- `categorizer_v2_cleaner`:   the model learns and predicts from cleaned merchant names
                              (`lookahead.merchant_clean`);
- `categorizer_v2_directory`: a flagged new-merchant row that matches the public chain
                              directory by exact name or alias gets the directory's category
                              (source "directory"; it stays flagged: entries are unverified);
- `categorizer_v2_directory_similar`: the directory may also match by character similarity
                              (`chain_directory.SIMILAR_AT`); only used with the directory on;
- `categorizer_v2_rag`:       saved RAG answers (Haiku 4.5 with retrieved examples) are shown
                              for flagged new-merchant rows (source "RAG"). Set only by the
                              setup question; nothing is ever sent by retraining.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from lookahead.categorizer import HybridCategorizer, Prediction
from lookahead.chain_directory import ChainDirectory, default_directory
from lookahead.merchant_clean import clean_merchant

CLEANER = "categorizer_v2_cleaner"
DIRECTORY = "categorizer_v2_directory"
RAG = "categorizer_v2_rag"
DIRECTORY_SIMILAR = "categorizer_v2_directory_similar"
SETTINGS = (CLEANER, DIRECTORY, RAG, DIRECTORY_SIMILAR)
DIRECTORY_SOURCE = "directory"


@dataclass(frozen=True)
class Options:
    cleaner: bool = False
    directory: bool = False
    rag: bool = False
    directory_similar: bool = False

    @property
    def any_on(self) -> bool:
        return self.cleaner or self.directory or self.rag or self.directory_similar


def stored_answer(conn: sqlite3.Connection, setting: str) -> bool | None:
    """True / False as stored, None when never answered (or an older database)."""
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (setting,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None or row[0] not in ("on", "off"):
        return None
    return row[0] == "on"


def load_options(conn: sqlite3.Connection) -> Options:
    """The stored switches; anything missing or damaged is off."""
    return Options(*(stored_answer(conn, s) is True for s in SETTINGS))


def set_option(conn: sqlite3.Connection, setting: str, on: bool) -> None:
    if setting not in SETTINGS:
        raise ValueError(f"unknown categorizer v2 setting {setting!r}")
    with conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (setting, "on" if on else "off"),
        )


def with_clean_names(x: pd.DataFrame) -> pd.DataFrame:
    """A copy of categorizer features with each `name` cleaned. Groups are left as they are,
    so the known-merchant lookup and the evaluation splits do not move."""
    out = x.copy()
    out["name"] = [clean_merchant(n).name for n in out["name"]]
    return out


class CleanedHybrid(HybridCategorizer):
    """The same hybrid, trained on and predicting from cleaned merchant names."""

    def fit(
        self,
        x: pd.DataFrame,
        y: Sequence[str],
        groups: Sequence[str] | None = None,
        extra_x: pd.DataFrame | None = None,
        extra_y: Sequence[str] | None = None,
    ) -> "CleanedHybrid":
        extra = None if extra_x is None else with_clean_names(extra_x)
        super().fit(with_clean_names(x), y, groups, extra, extra_y)
        return self

    def predict_rows(self, x: pd.DataFrame) -> list[Prediction]:
        return super().predict_rows(with_clean_names(x))


def make_hybrid(options: Options, **kwargs: object) -> HybridCategorizer:
    """The model `lookahead.categorize.train` builds: today's hybrid unless the cleaner is on."""
    return (CleanedHybrid if options.cleaner else HybridCategorizer)(**kwargs)  # type: ignore[arg-type]


def apply_directory(
    predictions: pd.DataFrame, options: Options, directory: ChainDirectory | None = None
) -> pd.DataFrame:
    """`lookahead.categorize.predict`'s table with the directory's category on each flagged
    new-merchant row ("model" source) whose merchant is a directory chain. Every other row,
    and everything when the directory is off, is returned unchanged. A row it answers stays
    flagged (the directory is not verified yet)."""
    if not options.directory or predictions.empty:
        return predictions
    directory = directory or default_directory()
    out = predictions.copy()
    for i in out.index[(out["source"] == "model") & out["flagged"].astype(bool)]:
        merchant = out.at[i, "merchant"]
        if not isinstance(merchant, str) or not merchant:
            continue
        match = directory.lookup(merchant, similar=options.directory_similar)
        if match is None:
            continue
        out.at[i, "category"] = match.entry.category
        out.at[i, "source"] = DIRECTORY_SOURCE
        out.at[i, "detail"] = f"public chain directory ({match.how}): {match.entry.name}"
    return out
