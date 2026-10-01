"""Tests for the frozen category keyword lexicon (experiment 2). Made-up names only."""

import hashlib
import json

import numpy as np
import pytest

from lookahead.lexicon import LEXICON, LEXICON_CATEGORIES, lexicon_hits, lexicon_matrix

FROZEN_SHA256 = "d0ac32a291eb9c54dafc56697a716f39e50b368405377b7205d6765aeeaa807d"


def test_lexicon_is_frozen() -> None:
    # Changing the lexicon after a run would let the results shape it. Any change must be a
    # new, logged experiment: update this hash only together with a DECISIONS.md entry.
    dumped = json.dumps(LEXICON, sort_keys=True, ensure_ascii=False).encode()
    assert hashlib.sha256(dumped).hexdigest() == FROZEN_SHA256


def test_every_lexicon_category_is_a_real_category() -> None:
    from lookahead.taxonomy import CATEGORIES

    assert set(LEXICON_CATEGORIES) <= set(CATEGORIES)


@pytest.mark.parametrize(
    ("name", "categories"),
    [
        ("Resto Chez Lune", ["Restaurants & cafés"]),
        ("Caf? Soleil", ["Restaurants & cafés"]),  # the bank's "?" for an accent
        ("Sushishop Centre", ["Restaurants & cafés"]),  # glued: prefix of a word
        ("Bar Le Coin", ["Restaurants & cafés"]),
        ("Barbier Royal", ["Personal care"]),  # "bar" must be a whole word
        ("Pharmacie Lune", ["Health & pharmacy"]),
        ("Depanneur Soleil", ["Groceries"]),
        ("Fondation Lune", ["Gifts & donations"]),
        ("Stationnement Ville", ["Transport"]),
        ("Galerie Lune", []),
    ],
)
def test_lexicon_hits(name: str, categories: list[str]) -> None:
    assert lexicon_hits(name) == categories


def test_short_keywords_need_a_whole_word() -> None:
    assert "Personal care" not in lexicon_hits("Spaghetti House")  # "spa" is whole-word only
    assert "Personal care" in lexicon_hits("Spa Nordique")


def test_lexicon_matrix_has_one_column_per_category() -> None:
    m = lexicon_matrix(np.array(["Resto Lune", "Galerie Lune"], dtype=object))
    assert m.shape == (2, len(LEXICON_CATEGORIES))
    assert m[0].sum() == 1 and m[1].sum() == 0
