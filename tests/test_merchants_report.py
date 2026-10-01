"""Tests for the counts-only extraction report and the answer-key evaluation.

Every database and export here is generated in a temporary folder: never data/private/.
"""

import re
from datetime import date, datetime
from pathlib import Path

import pytest

from eval.merchant_extraction import is_match, score_persona
from lookahead.db import connect, insert_transactions
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.merchants import (
    DEFAULT_LEFTOVERS_FILE,
    count_extraction,
    extract_merchant,
    is_private_path,
    load_brand_allowlist,
    main,
)

START, EXPORT = date(2026, 6, 1), date(2026, 9, 30)


@pytest.fixture(autouse=True)
def no_private_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read the real default allowlist in data/private/."""
    monkeypatch.setattr(
        "lookahead.merchants.DEFAULT_BRAND_ALLOWLIST", tmp_path / "no_allowlist.txt"
    )


@pytest.fixture
def persona_c(tmp_path: Path) -> tuple[Path, Path]:
    return write_persona(PERSONAS["C"], tmp_path / "synthetic", 42, START, EXPORT)


@pytest.fixture
def db(tmp_path: Path, persona_c: tuple[Path, Path]) -> Path:
    path = tmp_path / "db" / "t.db"
    conn = connect(path)
    export = persona_c[0]
    store_exports(conn, "synthetic-C", [(export.name, parse_export(export))], datetime(2026, 10, 2))
    conn.commit()
    conn.close()
    return path


def texts_of(export: Path) -> list[str]:
    return [r.sub_description for r in parse_export(export)]


# --- Counting ---


def test_counts_add_up(persona_c: tuple[Path, Path]) -> None:
    texts = texts_of(persona_c[0])
    c = count_extraction(texts)
    assert c.rows == len(texts) == c.named + sum(c.empty.values())
    assert c.empty["blank"] > 0 and c.empty["transfer"] > 0
    assert c.ok  # no row lost its name, no leftover noise
    assert c.channels["online"] > 0 and c.channels["in_person"] > 0
    assert 0 < c.distinct < c.named


def test_a_card_row_with_nothing_left_fails_the_check() -> None:
    c = count_extraction(["Apos #1234               Montr "])
    assert c.empty["nothing left"] == 1 and not c.ok


def test_leftover_noise_fails_the_check() -> None:
    text = "Apos Shop 4567 Express   Montr "  # "4567" is mid-name, so no rule removes it
    assert len(text) == 31
    assert extract_merchant(text).merchant == "Shop 4567 Express"
    c = count_extraction([text])
    assert c.leftovers == 1 and not c.ok


# --- The report command prints counts only ---


def test_report_prints_counts_and_passes(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["report", "--db", str(db), "--account", "synthetic-C"])
    out = capsys.readouterr().out
    assert code == 0 and out.rstrip().endswith("RESULT: PASS")
    assert re.search(r"Merchant extraction: \d+ rows, account synthetic-C", out)
    assert re.search(r"distinct merchant names: \d+", out)


def test_report_prints_no_names_amounts_or_text(
    db: Path, persona_c: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    main(["report", "--db", str(db), "--account", "synthetic-C"])
    out = capsys.readouterr().out
    assert not re.search(r"\d+\.\d{2}", out), out  # no money
    assert "$" not in out
    rows = parse_export(persona_c[0])
    names = {extract_merchant(r.sub_description).merchant for r in rows} - {None}
    texts = {r.sub_description.strip() for r in rows} - {""}
    types = {r.description for r in rows}
    for secret in names | texts | types:
        assert secret not in out, secret


def test_report_on_a_missing_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["report", "--db", str(tmp_path / "none.db")]) == 2
    assert not (tmp_path / "none.db").exists()  # opened read-only, never created


# --- Evaluation against the answer key ---


def test_match_rules() -> None:
    assert is_match("Caf? Velisaro", "Café Velisaro")  # card text: accent as "?"
    assert is_match("Hydro-Quebec", "Hydro-Québec")  # payee: accent dropped
    assert is_match("Iga", "IGA")  # case ignored
    assert is_match(None, "Interac e-Transfer")  # no merchant in the text: empty is right
    assert not is_match("Uber Canada/Ubereats", "Uber Eats")  # an alias: resolution's job
    assert not is_match(None, "Maxi")


@pytest.mark.parametrize("pid", ["A", "B", "C"])
def test_every_miss_is_a_naming_difference_not_noise(pid: str, tmp_path: Path) -> None:
    export, key = write_persona(PERSONAS[pid], tmp_path, 42, START, EXPORT)
    s = score_persona(export, key)
    assert s.noisy == 0
    # The bank's text names these differently from the answer key, or cuts them short.
    aliases = {"Uber Eats", "Uber", "STM", "Restaurant Le Petit Orme", "Netflix", "Steam",
               "Ticketmaster", "Etsy", "Qavo"}  # fmt: skip
    assert {canonical for canonical, _ in s.misses} <= aliases


# --- Leftover breakdown and the private leftovers file ---

# Made-up texts whose names keep noise the rules do not remove (mid-name, not at the end).
NOISY = [
    "Apos Shop 4567 Express   Montr ",  # 3+ digits
    "Apos Shop 4567 Express   Montr ",  # same name again: 2 rows, 1 distinct name
    "Apos Bar #2 Velo Club    Montr ",  # cleaned: the store-number rule removes "#2"
    "Opos Pay+1 Thing Shop    +1877 ",  # '+digits' (too short for the phone rule)
    "Apos Sq *Cafe*Velo       Montr ",  # '*': a second star after a processor
]


@pytest.fixture
def noisy_db(tmp_path: Path) -> Path:
    path = tmp_path / "db" / "noisy.db"
    conn = connect(path)
    rows = [
        ("acct", "2026-09-01", -100, "pos purchase", text, 1000 - i, f"fp{i}", "f", "2026-10-02")
        for i, text in enumerate([*NOISY, "Apos Maxi #350           Montr "])
    ]
    insert_transactions(conn, rows)
    conn.commit()
    conn.close()
    return path


def test_leftovers_are_counted_per_pattern_and_per_distinct_name() -> None:
    c = count_extraction(NOISY)
    assert c.leftovers == 4  # the "#2" row is cleaned by the store-number rule
    assert c.leftover_hits["3+ digits in a row"] == 2
    assert c.leftover_hits["'+digits'"] == 1
    assert c.leftover_hits["'*'"] == 1
    assert c.leftover_hits["'#'"] == 0
    assert len(c.leftover_names) == 3  # "Shop 4567 Express" counts once


def test_report_breakdown_is_counts_only(
    noisy_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["report", "--db", str(noisy_db), "--account", "acct"])
    out = capsys.readouterr().out
    assert code == 1 and out.rstrip().endswith("RESULT: CHECK")
    assert "possible leftovers in names: 4 rows from 3 distinct names" in out
    assert "'#': 0, '*': 1, '+digits': 1, 3+ digits in a row: 2" in out
    for name in ("Shop", "Express", "Pay+1", "Thing", "Cafe", "Velo", "Maxi"):
        assert name not in out, name


def test_write_leftovers_saves_names_to_the_file_only(
    noisy_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "data" / "private" / "eval" / "leftovers.txt"
    main(["report", "--db", str(noisy_db), "--account", "acct", "--write-leftovers", str(target)])
    out = capsys.readouterr().out
    assert "wrote 3 flagged names" in out
    for name in ("Shop", "Pay+1", "Cafe"):
        assert name not in out  # never on screen
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[1:] == [
        "2	3+ digits in a row	Shop 4567 Express",
        "1	'*'	Cafe*Velo",
        "1	'+digits'	Pay+1 Thing Shop",
    ]


def test_write_leftovers_refuses_a_path_outside_data_private(
    noisy_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "eval" / "leftovers.txt"
    args = ["report", "--db", str(noisy_db), "--account", "acct", "--write-leftovers", str(target)]
    code = main(args)
    assert code == 2 and not target.exists()
    assert "Shop" not in capsys.readouterr().out


def test_default_allowlist_is_private() -> None:
    import lookahead.merchants as m

    assert m.DEFAULT_BRAND_ALLOWLIST.name == "no_allowlist.txt"  # patched in tests
    assert is_private_path(Path("data/private/merchant_allowlist.txt"))


def test_default_leftovers_file_is_private() -> None:
    assert DEFAULT_LEFTOVERS_FILE == Path("data/private/eval/merchant_leftovers.txt")
    assert is_private_path(DEFAULT_LEFTOVERS_FILE)
    assert not is_private_path(Path("data/synthetic/leftovers.txt"))
    assert not is_private_path(Path("private/data/x.txt"))


# --- Private allowlist of brands whose name contains digits ---


def test_allowlisted_brand_digits_are_not_leftovers() -> None:
    texts = [card_text("747 Burger Bar Us 2"), card_text("Deli505 Ste")]
    assert count_extraction(texts).leftovers == 2
    c = count_extraction(texts, brand_allowlist={"747 burger bar"})
    assert c.leftovers == 1 and c.allowlisted == 1
    assert list(c.leftover_names) == ["Deli505 Ste"]


def test_allowlist_does_not_hide_other_digits_in_the_same_name() -> None:
    c = count_extraction([card_text("747 Burger 1234 Bar")], brand_allowlist={"747 burger"})
    assert c.leftovers == 1  # "1234" is not part of the allowlisted brand


def test_brand_allowlist_file(tmp_path: Path) -> None:
    path = tmp_path / "brands.txt"
    path.write_text("# comment\n  747   BURGER Bar \n\n", encoding="utf-8")
    assert load_brand_allowlist(path) == {"747 burger bar"}
    assert load_brand_allowlist(tmp_path / "missing.txt") == set()


def test_report_counts_allowlisted_rows_and_never_prints_the_list(
    noisy_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    allow = tmp_path / "brands.txt"
    allow.write_text("Shop 4567 Express\n", encoding="utf-8")
    main(["report", "--db", str(noisy_db), "--account", "acct", "--allowlist", str(allow)])
    out = capsys.readouterr().out
    assert "allowlisted brand numbers: 2 rows" in out
    assert "possible leftovers in names: 2 rows from 2 distinct names" in out
    assert "Shop" not in out and "Express" not in out


def card_text(merchant: str) -> str:
    return f"Apos {merchant[:20]:<20}Montr "
