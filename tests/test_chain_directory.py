"""The public Canadian chain directory and its lookup (categorizer v2)."""

import csv
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from lookahead import categorizer_v2 as v2
from lookahead.chain_directory import (
    DEFAULT_PATH,
    REGIONS,
    SIMILAR_AT,
    ChainDirectory,
    Entry,
    choose_threshold,
    default_directory,
    load_entries,
    synthetic_non_chains,
)
from lookahead.merchant_clean import match_key
from lookahead.taxonomy import CATEGORIES, check_labels

ROOT = Path(__file__).resolve().parents[1]


def _rows() -> list[dict[str, str]]:
    with DEFAULT_PATH.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_the_file_is_committed_and_not_git_ignored() -> None:
    relative = DEFAULT_PATH.relative_to(ROOT)
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(relative)], cwd=ROOT, check=False
    ).returncode
    assert ignored == 1  # 1 = not ignored


def test_columns_and_size() -> None:
    rows = _rows()
    assert list(rows[0]) == ["name", "aliases", "category", "region", "verified"]
    assert 230 <= len(rows) <= 300  # "about 250"


def test_every_category_is_one_of_the_17() -> None:
    for row in _rows():
        check_labels(row["category"], "everyday")  # raises on an unknown category
        assert row["category"] in CATEGORIES


def test_no_empty_names_and_known_regions_and_unverified() -> None:
    for row in _rows():
        assert row["name"].strip()
        assert row["region"] in REGIONS
        assert row["verified"] == "no"  # I spot-check before any entry says yes
        assert all(a.strip() for a in row["aliases"].split("|") if row["aliases"])


def test_no_alias_belongs_to_two_entries() -> None:
    owner: dict[str, str] = {}
    for row in _rows():
        spellings = {match_key(row["name"])} | {
            match_key(a) for a in row["aliases"].split("|") if a.strip()
        }
        for key in spellings:
            assert key not in owner, f"{key!r} in {owner.get(key)!r} and {row['name']!r}"
            owner[key] = row["name"]


def test_both_regions_are_present() -> None:
    regions = {row["region"] for row in _rows()}
    assert regions == {"national", "QC"}


def test_loading_rejects_a_bad_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("name,aliases,category,region,verified\nX,,Groceriez,QC,no\n", encoding="utf-8")
    with pytest.raises(ValueError, match="category"):
        load_entries(bad)
    bad.write_text("name,aliases,category,region,verified\n ,,Groceries,QC,no\n", encoding="utf-8")
    with pytest.raises(ValueError, match="name"):
        load_entries(bad)


TOY = ChainDirectory(
    [
        Entry("D001", "Épicerie Lunaire", ("Lunaire", "Epicerie Lunaire Express"), "Groceries",
              "QC", False),
        Entry("D002", "Velorin Café", ("Cafe Velorin",), "Restaurants & cafés", "national",
              False),
        Entry("D003", "Pharmacie Zéphyr", (), "Health & pharmacy", "QC", False),
    ]
)  # fmt: skip


def test_lookup_exact_then_alias() -> None:
    m = TOY.lookup("EPICERIE LUNAIRE")
    assert m is not None and m.how == "exact" and m.entry.id == "D001" and m.score == 1.0
    m = TOY.lookup("Cafe Velorin")
    assert m is not None and m.how == "alias" and m.entry.id == "D002"


def test_lookup_cleans_bank_noise_first() -> None:
    m = TOY.lookup("OPOS PHARMACIE ZEPHYR #0442 MONTREAL QC")
    assert m is not None and m.entry.id == "D003" and m.how == "exact"


def test_lookup_lets_the_banks_lost_accent_match() -> None:
    m = TOY.lookup("Pharmacie Z?phyr")
    assert m is not None and m.entry.id == "D003"


def test_similarity_is_off_unless_asked() -> None:
    assert TOY.lookup("Epicerie Lunair") is None
    m = TOY.lookup("Epicerie Lunair", similar=True)
    assert m is not None and m.how == "similar" and m.entry.id == "D001"
    assert SIMILAR_AT <= m.score < 1.0


def test_similarity_below_the_threshold_is_no_match() -> None:
    assert TOY.lookup("Boulangerie Tardive", similar=True) is None
    assert TOY.lookup("", similar=True) is None


def test_similar_ranks_entries_best_first() -> None:
    top = TOY.similar("Epicerie Lunaire Expres", k=2)
    assert [m.entry.id for m in top][0] == "D001"
    assert top[0].score >= top[1].score


def test_the_threshold_comes_from_synthetic_non_chains_only() -> None:
    non_chains = synthetic_non_chains()
    assert non_chains  # the generator's made-up local places
    directory = default_directory()
    assert all(directory.lookup(n) is None for n in non_chains)
    assert choose_threshold(directory, non_chains) == SIMILAR_AT
    # and at that threshold, none of them is taken for a chain
    assert all(directory.lookup(n, similar=True) is None for n in non_chains)


def test_choose_threshold_rule() -> None:
    # best non-chain score 0.0 -> the floor; otherwise the next 0.05 step plus one
    assert choose_threshold(TOY, []) == 0.5
    assert choose_threshold(TOY, ["Epicerie Lunair"]) <= 1.1


def _predictions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "fingerprint": ["a", "b", "c", "d"],
            "merchant": ["Lunaire", "Lunaire", "Quincaillerie Brume", "Cafe Velorin"],
            "flow_type": ["everyday"] * 4,
            "category": ["Shopping", "Shopping", "Shopping", "Groceries"],
            "source": ["model", "model", "model", "known merchant"],
            "detail": ["new merchant", "new merchant", "new merchant", "known merchant"],
            "flagged": [True, False, True, False],
            "confidence": [0.3, 0.9, 0.2, 1.0],
        }
    )


def test_directory_off_changes_nothing() -> None:
    df = _predictions()
    assert v2.apply_directory(df, v2.Options(), TOY) is df


def test_directory_on_answers_only_flagged_new_merchants() -> None:
    df = _predictions()
    got = v2.apply_directory(df, v2.Options(directory=True), TOY)
    assert list(got["category"]) == ["Groceries", "Shopping", "Shopping", "Groceries"]
    assert list(got["source"]) == ["directory", "model", "model", "known merchant"]
    assert got.loc[0, "flagged"]  # still worth a look: the directory is unverified
    assert "Épicerie Lunaire" in got.loc[0, "detail"]
    assert list(df["source"])[0] == "model"  # the input is not changed


def test_directory_similarity_needs_its_own_switch() -> None:
    df = _predictions()
    df.loc[0, "merchant"] = "Epicerie Lunair"
    off = v2.apply_directory(df, v2.Options(directory=True), TOY)
    assert off.loc[0, "source"] == "model"
    on = v2.apply_directory(df, v2.Options(directory=True, directory_similar=True), TOY)
    assert on.loc[0, "source"] == "directory"


def test_directory_switch_is_read_by_categorize(tmp_path: Path) -> None:
    from lookahead.db import connect

    conn = connect(tmp_path / "t.db")
    assert v2.load_options(conn).directory is False
    v2.set_option(conn, v2.DIRECTORY, True)
    v2.set_option(conn, v2.DIRECTORY_SIMILAR, True)
    assert v2.load_options(conn) == v2.Options(directory=True, directory_similar=True)
    conn.close()


# --- Short names and aliases (5 letters or fewer) match only exactly ---

SHORT = ChainDirectory(
    [
        Entry("D001", "Gap", (), "Shopping", "national", False),
        Entry("D002", "Bell", (), "Phone, internet & utilities", "national", False),
        Entry("D003", "Shell", (), "Transport", "national", False),
        Entry("D004", "Cora", (), "Restaurants & cafés", "national", False),
    ]
)
LOCAL = ["Gapetto Pizzeria", "Bella Notte Trattoria", "Shellys Deli", "Coralie Fleuriste",
         "GAPS COUTURE", "BELLE PROVINCE", "SHELLA NAILS", "CORAL SUSHI"]  # fmt: skip


@pytest.mark.parametrize("name", LOCAL)
def test_short_keys_never_match_by_similarity(name: str) -> None:
    assert SHORT.lookup(name, similar=True, threshold=0.0) is None
    assert SHORT.similar(name) == []


@pytest.mark.parametrize(("text", "name"), [("GAP", "Gap"), ("Bell", "Bell"),
                                            ("SHELL", "Shell"), ("cora", "Cora")])  # fmt: skip
def test_short_keys_still_match_exactly(text: str, name: str) -> None:
    match = SHORT.lookup(text)
    assert match is not None and match.entry.name == name and match.how == "exact"


def test_short_keys_take_no_wildcard_match() -> None:
    assert SHORT.lookup("G?P") is None  # a "?" (an accent the bank dropped) is not exact


@pytest.mark.parametrize("name", LOCAL)
def test_the_real_directory_gives_no_short_chain_to_local_names(name: str) -> None:
    match = default_directory().lookup(name, similar=True)
    assert match is None or match.entry.name not in {"Gap", "Bell", "Shell", "Cora"}
