"""Categorizer v2 switches: all off by default, each one opt-in."""

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from lookahead import categorizer_v2 as v2
from lookahead.categorizer import HybridCategorizer
from lookahead.db import connect


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def test_every_switch_is_off_by_default(conn: sqlite3.Connection) -> None:
    assert v2.load_options(conn) == v2.Options(False, False, False)
    assert not v2.load_options(conn).any_on
    for setting in v2.SETTINGS:
        assert v2.stored_answer(conn, setting) is None


def test_an_older_database_without_settings_is_off() -> None:
    bare = sqlite3.connect(":memory:")
    assert v2.load_options(bare) == v2.Options()
    bare.close()


def test_a_damaged_value_is_off(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, 'yes please')", (v2.RAG,))
    assert v2.load_options(conn).rag is False
    assert v2.stored_answer(conn, v2.RAG) is None


def test_switches_are_stored_and_read_back(conn: sqlite3.Connection) -> None:
    v2.set_option(conn, v2.CLEANER, True)
    assert v2.load_options(conn) == v2.Options(cleaner=True)
    v2.set_option(conn, v2.CLEANER, False)
    assert v2.load_options(conn) == v2.Options()
    assert v2.stored_answer(conn, v2.CLEANER) is False  # answered "off", not "never asked"
    with pytest.raises(ValueError):
        v2.set_option(conn, "something_else", True)


def test_off_builds_todays_hybrid_and_on_builds_the_cleaned_one() -> None:
    off = v2.make_hybrid(v2.Options(), threshold=0.5)
    assert type(off) is HybridCategorizer
    on = v2.make_hybrid(v2.Options(cleaner=True), threshold=0.5)
    assert isinstance(on, v2.CleanedHybrid)


def test_train_uses_the_stored_cleaner_switch(conn: sqlite3.Connection) -> None:
    from lookahead.categorize import Settings, train

    settings = Settings(threshold=0.5, synthetic_months=2)
    model, _ = train(conn, "nobody", settings)
    assert type(model) is HybridCategorizer
    v2.set_option(conn, v2.CLEANER, True)
    model, _ = train(conn, "nobody", settings)
    assert isinstance(model, v2.CleanedHybrid)


def test_cleaner_changes_only_the_name_column() -> None:
    x = pd.DataFrame(
        {"name": ["Sq *Cafe Velorin", "Rona Laval"], "group": ["g1", "g2"], "channel": ["a", "b"]}
    )
    got = v2.with_clean_names(x)
    assert list(got["name"]) == ["Cafe Velorin", "Rona"]
    assert list(got["group"]) == ["g1", "g2"]
    assert list(x["name"]) == ["Sq *Cafe Velorin", "Rona Laval"]  # the input is not changed
