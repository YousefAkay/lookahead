"""Tests for the pseudonymization rules applied to bank text."""

import csv
import re
from datetime import date
from pathlib import Path

import pytest

from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import write_persona
from lookahead.pseudonymize import pseudonymize_sub_description, token

KEY = bytes(range(32))
TOKEN = r"[A-Z]+_[0-9a-f]{12}"


def pseudo(description: str, sub: str) -> tuple[str, tuple[str, ...]]:
    result = pseudonymize_sub_description(description, sub, KEY)
    return result.text, result.rules


# --- Credit memo sender names ---


def test_credit_memo_name_becomes_a_person_token_keeping_prefix_and_space() -> None:
    text, rules = pseudo("credit memo", "1/Nadia Haddad ")
    assert text == f"1/{token('PERSON', 'Nadia Haddad', KEY)} "
    assert rules == ("person",)


def test_credit_memo_without_prefix() -> None:
    text, _ = pseudo("credit memo", "Nadia Haddad ")
    assert text == f"{token('PERSON', 'Nadia Haddad', KEY)} "


def test_same_sender_same_token_regardless_of_case_or_prefix() -> None:
    a, _ = pseudo("credit memo", "1/Nadia Haddad ")
    b, _ = pseudo("credit memo", "NADIA  HADDAD ")
    assert a.removeprefix("1/") == b


# --- Mobile-banking bill payment references ---


def test_mobile_banking_reference_becomes_a_ref_token_payee_kept() -> None:
    text, rules = pseudo("bill payment", "Mb-Concordia University 12345678")
    assert text == f"Mb-Concordia University {token('REF', '12345678', KEY)}"  # no trailing space
    assert rules == ("reference",)


def test_bill_payment_without_mobile_banking_is_unchanged() -> None:
    assert pseudo("bill payment", "Hydro-Quebec ") == ("Hydro-Quebec ", ())


# --- Long digit runs anywhere ---


def test_seven_or_more_digits_become_a_num_token() -> None:
    text, rules = pseudo("deposit", "Payroll 1234567 ")
    assert text == f"Payroll {token('NUM', '1234567', KEY)} "
    assert rules == ("number",)


@pytest.mark.parametrize(
    "sub",
    [
        "Apos Tim Hortons #7662   Montr ",  # store number: short
        "Opos 130.97 Qavo.Com     Dubai ",  # foreign amount
        "Opos Amazon.Ca*2X4K9     +1877 ",  # order code, phone prefix
        "Ref 123456 ",  # 6 digits: not a long run
        "Free Interac E-Transfer ",
        "Interac E-Transfer Rtn ",
        "Mb-Transfer ",
        " ",
    ],
)
def test_ordinary_text_is_unchanged(sub: str) -> None:
    assert pseudo("pos purchase", sub) == (sub, ())


def test_tokens_are_never_tokenized_again() -> None:
    # A credit memo name whose token happens to contain a digit run stays one token.
    for name in (f"Person {i}" for i in range(300)):
        text, rules = pseudo("credit memo", f"1/{name} ")
        assert re.fullmatch(rf"1/{TOKEN} ", text) and rules == ("person",)


def test_digits_inside_a_mobile_banking_payee_are_also_caught() -> None:
    text, rules = pseudo("bill payment", "Mb-Landlord 9876543 12345678")
    assert text == f"Mb-Landlord {token('NUM', '9876543', KEY)} {token('REF', '12345678', KEY)}"
    assert sorted(rules) == ["number", "reference"]


# --- On a whole synthetic export ---


def test_synthetic_export_has_no_identifier_left(tmp_path: Path) -> None:
    export, _ = write_persona(
        PERSONAS["C"], tmp_path, seed=42, start=date(2026, 6, 1), export_date=date(2026, 9, 30)
    )
    with export.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))[1:]
    fired = 0
    for r in rows:
        text, rules = pseudo(r[2], r[3])
        assert "Nadia" not in text and "Haddad" not in text
        assert not re.search(r"\d{7,}", re.sub(TOKEN, "", text))
        if rules:
            fired += 1
        else:
            assert text == r[3]  # rows without identifiers are untouched
    assert fired == sum(
        r[2] == "credit memo" or r[3].startswith("Mb-") and r[3] != "Mb-Transfer " for r in rows
    )
