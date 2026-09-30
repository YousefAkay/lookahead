"""Tests for bank-style description text (stage 4 of the generator)."""

import re
from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from lookahead.generator.descriptions import describe
from lookahead.generator.schedules import GeneratedTxn

D = date(2026, 6, 10)


def txn(txn_type: str, merchant: str, **extra: object) -> GeneratedTxn:
    fields: dict[str, object] = {
        "posted_date": D,
        "amount_cents": -1_000,
        "txn_type": txn_type,
        "merchant": merchant,
        "category": "Groceries",
        "flow_type": "everyday",
    }
    fields.update(extra)
    return GeneratedTxn(**fields)  # type: ignore[arg-type]


def card(merchant: str, online: bool = False, **extra: object) -> GeneratedTxn:
    return txn("pos purchase", merchant, online=online, **extra)


def sub(row: GeneratedTxn, seed: int = 42) -> str:
    [out] = describe([row], np.random.default_rng(seed))
    return out.sub_description


# --- Card purchases: "{Apos|Opos} {merchant:20}{location:5} " ---


def test_card_text_is_31_chars_with_trailing_space() -> None:
    for row in (card("Maxi"), card("Uber Eats", online=True), card("Some New Shop")):
        text = sub(row)
        assert len(text) == 31 and text.endswith(" "), repr(text)


def test_prefix_follows_the_channel() -> None:
    assert sub(card("Maxi", online=False)).startswith("Apos ")
    assert sub(card("Uber Eats", online=True)).startswith("Opos ")


def test_title_case_like_the_real_export() -> None:
    assert sub(card("Uber Eats", online=True)) == "Opos Uber Canada/Ubereatstoron "


def test_accents_become_question_marks_on_card_text() -> None:
    assert "Caf? Velisaro" in sub(card("Café Velisaro"))


def test_store_numbers_stay_few_per_merchant() -> None:
    rows = describe([card("Tim Hortons") for _ in range(200)], np.random.default_rng(1))
    stores = {re.search(r"#(\d+)", r.sub_description).group(1) for r in rows}  # type: ignore[union-attr]
    assert 1 <= len(stores) <= 3


def test_foreign_amount_is_shown_in_the_original_currency() -> None:
    row = card(
        "Steam", online=True, amount_cents=-1_690, currency="USD", original_amount_cents=-1_199
    )
    assert sub(row).startswith("Opos 11.99 Steamgames.Com")


def test_pos_return_uses_the_card_format() -> None:
    ret = txn("pos return", "Amazon.ca", online=True, amount_cents=2_500, flow_type="refund")
    text = sub(ret)
    assert text.startswith("Opos Amazon.Ca*") and len(text) == 31


# --- Non-card rows ---


ET, FEES, FREE = "Interac e-Transfer", "Fees & interest", "Free Interac E-Transfer "


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (txn("withdrawal", ET, category="Rent & housing"), FREE),
        (txn("deposit", ET, category="Income", flow_type="income"), FREE),
        (txn("deposit", ET, category="Transfers", flow_type="refund"), "Interac E-Transfer Rtn "),
        (
            txn("customer transfer dr.", "Savings", category="Transfers", flow_type="transfer"),
            "Mb-Transfer ",
        ),
        (txn("abm withdrawal", "ABM", category="Cash"), " "),
        (txn("service charge", "Scotiabank", category=FEES), " "),
        (txn("overdraft interest", "Scotiabank", category=FEES), " "),
        (txn("overdrawn handling chg.", "Scotiabank", category=FEES), " "),
        (
            txn("deposit", "Boutique Mile End", category="Income", flow_type="income"),
            "Boutique Mile End ",
        ),
    ],
)
def test_fixed_texts(row: GeneratedTxn, expected: str) -> None:
    assert sub(row) == expected


def test_credit_memo_is_1_slash_sender_name_mostly() -> None:
    memo = txn(
        "credit memo", "Nadia Haddad", amount_cents=300_000, category="Income", flow_type="income"
    )
    texts = [r.sub_description for r in describe([memo] * 300, np.random.default_rng(3))]
    assert set(texts) <= {"1/Nadia Haddad ", "Nadia Haddad "}
    assert 0.85 <= texts.count("1/Nadia Haddad ") / len(texts) < 1.0  # a few lack the prefix


def test_bill_payment_payee_without_accents_or_mobile_banking_reference() -> None:
    bill = txn("bill payment", "Hydro-Québec", category="Phone, internet & utilities",
               flow_type="recurring_outflow")  # fmt: skip
    texts = {r.sub_description for r in describe([bill] * 50, np.random.default_rng(5))}
    assert len(texts) == 1  # same channel and reference every month for one payee
    [text] = texts
    assert text == "Hydro-Quebec " or re.fullmatch(r"Mb-Hydro-Quebec \d{8}", text), text


def test_both_bill_payment_channels_occur_across_payees() -> None:
    payees = [f"Payee {i}" for i in range(40)]
    rows = [
        txn("bill payment", p, category="Education", flow_type="recurring_outflow") for p in payees
    ]
    texts = [r.sub_description for r in describe(rows, np.random.default_rng(0))]
    assert any(t.startswith("Mb-") for t in texts) and any(not t.startswith("Mb-") for t in texts)


# --- General properties ---


def test_description_column_is_the_transaction_type() -> None:
    [out] = describe([card("Maxi")], np.random.default_rng(0))
    assert out.description == "pos purchase"


def test_text_does_not_change_the_answer_key() -> None:
    rows = [card("Maxi"), txn("credit memo", "Nadia Haddad", category="Income", flow_type="income")]
    out = describe(rows, np.random.default_rng(0))
    assert [replace(r, description="", sub_description="") for r in out] == rows


def test_same_seed_same_text() -> None:
    rows = [card("Tim Hortons") for _ in range(20)]
    assert describe(rows, np.random.default_rng(8)) == describe(rows, np.random.default_rng(8))


def test_unknown_transaction_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="txn_type"):
        describe([txn("mystery", "X")], np.random.default_rng(0))
