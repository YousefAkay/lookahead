"""Tests for the synthetic resolution test set (built from freshly generated personas)."""

from datetime import date
from pathlib import Path

import pytest

from eval.resolution_variants import CUT, HARD_NEGATIVES, VariantSet, build_variants
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona


@pytest.fixture(scope="module")
def variants(tmp_path_factory: pytest.TempPathFactory) -> VariantSet:
    out = tmp_path_factory.mktemp("synthetic")
    for pid in "ABC":
        write_persona(PERSONAS[pid], out, 42, date(2026, 6, 1), date(2026, 9, 30))
    return build_variants(out)


def test_dev_and_test_share_no_merchant(variants: VariantSet) -> None:
    dev = {v.merchant for v in variants.dev}
    test = {v.merchant for v in variants.test}
    assert dev and test and not dev & test


def test_each_look_alike_is_in_the_same_half_as_its_partner(variants: VariantSet) -> None:
    for half in (variants.dev, variants.test):
        merchants = {v.merchant for v in half}
        for fake, real in HARD_NEGATIVES.items():
            if fake in merchants:
                assert real in merchants


def test_cut_flag_means_the_name_fills_the_field(variants: VariantSet) -> None:
    for v in variants.dev + variants.test:
        if v.cut:
            assert len(v.name) == CUT and v.kind in ("bank", "location_cut")
        if v.kind == "location_cut":
            assert v.cut == (len(v.name) == CUT)
    # A 20-character payee is not cut: only card text has the 20-character field.
    payee = [v for v in variants.dev + variants.test if v.name == "Concordia University"]
    assert payee and not payee[0].cut


def test_no_name_belongs_to_two_merchants(variants: VariantSet) -> None:
    for half in (variants.dev, variants.test):
        names = [v.name.casefold() for v in half]
        assert len(names) == len(set(names))


def test_same_seed_same_set(variants: VariantSet, tmp_path: Path) -> None:
    for pid in "ABC":
        write_persona(PERSONAS[pid], tmp_path, 42, date(2026, 6, 1), date(2026, 9, 30))
    assert build_variants(tmp_path) == variants
