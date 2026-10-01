"""Tests for merchant extraction (rules). Synthetic-style examples only, never real rows.

Card text layout (see DECISIONS.md): "{Apos|Opos} {merchant:20}{location:5} ", 31 chars.
"""

import pytest

from lookahead.merchants import Extracted, extract_merchant


def card(prefix: str, merchant: str, location: str) -> str:
    """Build card text exactly as the bank lays it out (merchant cut to 20 characters)."""
    return f"{prefix} {merchant[:20]:<20}{location:<5} "


# --- Card rows: prefix, channel, fixed-width fields ---


def test_in_person_card_purchase() -> None:
    assert extract_merchant(card("Apos", "Maxi #350", "Montr")) == Extracted(
        merchant="Maxi", channel="in_person", kind="card"
    )


def test_online_card_purchase() -> None:
    got = extract_merchant(card("Opos", "Fizz", "Montr"))
    assert got.merchant == "Fizz" and got.channel == "online" and got.kind == "card"


def test_merchant_that_fills_all_20_characters_touches_the_city() -> None:
    # No space between merchant and city: only the fixed width tells them apart.
    text = "Opos Uber Canada/Ubereatstoron "
    assert len(text) == 31
    assert extract_merchant(text).merchant == "Uber Canada/Ubereats"


@pytest.mark.parametrize(
    ("merchant", "location", "expected"),
    [
        ("Tim Hortons #4043", "Montr", "Tim Hortons"),
        ("Couche-Tard #496", "Montr", "Couche-Tard"),
        ("Stm-Opus #7876", "Montr", "Stm-Opus"),
        ("Super C #5139", "Montr", "Super C"),
        ("Caf? Velisaro", "Montr", "Caf? Velisaro"),  # the bank's "?" for an accent stays
    ],
)
def test_store_numbers_and_city_are_removed(merchant: str, location: str, expected: str) -> None:
    assert extract_merchant(card("Apos", merchant, location)).merchant == expected


@pytest.mark.parametrize("phone", ["+1855", "+1877", "+1425"])
def test_phone_prefix_in_the_location_slot_is_removed(phone: str) -> None:
    assert extract_merchant(card("Opos", "Ticketmaster Canada", phone)).merchant == (
        "Ticketmaster Canada"
    )


def test_city_inside_the_merchant_field_is_removed() -> None:
    assert extract_merchant(card("Opos", "Bixi Montr?al", "Montr")).merchant == "Bixi"


# --- Truncation at about 31 characters ---


def test_name_cut_at_20_characters_is_kept_as_cut() -> None:
    # "Restaurant Le Petit Orme" does not fit; extraction cannot recover the rest.
    assert extract_merchant(card("Apos", "Restaurant Le Petit Orme", "Montr")).merchant == (
        "Restaurant Le Petit"
    )


def test_store_number_cut_in_the_middle_is_still_removed() -> None:
    text = card("Apos", "Pharmaprix Fleury #1234", "Montr")  # field ends in "#"
    assert text[5:25] == "Pharmaprix Fleury #1"
    assert extract_merchant(text).merchant == "Pharmaprix Fleury"


def test_store_number_cut_to_a_lone_hash() -> None:
    text = card("Apos", "Jean Coutu Lasalle #77", "Montr")
    assert text[5:25] == "Jean Coutu Lasalle #"
    assert extract_merchant(text).merchant == "Jean Coutu Lasalle"


# --- Codes after a star, and reference codes ---


@pytest.mark.parametrize(
    ("merchant", "location", "expected"),
    [
        ("Doordash*Ngbs2", "+1855", "Doordash"),
        ("Amazon.Ca*7Efhd", "+1877", "Amazon.Ca"),
        ("Lyft *Ride Y1Qkq", "Vanco", "Lyft"),
    ],
)
def test_order_code_after_a_star_is_removed(merchant: str, location: str, expected: str) -> None:
    assert extract_merchant(card("Opos", merchant, location)).merchant == expected


def test_payment_processor_before_the_star_keeps_the_real_merchant() -> None:
    # "SQ *" (Square) and similar: the shop's name is after the star.
    assert extract_merchant(card("Apos", "Sq *Cafe Velo", "Montr")).merchant == "Cafe Velo"


def test_trailing_reference_code_is_removed() -> None:
    assert extract_merchant(card("Opos", "Spotify P4Xucq", "Stock")).merchant == "Spotify"


# --- Foreign-currency purchases ---


def test_foreign_amount_before_the_name_is_removed() -> None:
    text = card("Opos", "7.33 Steamgames.Com", "+1425")
    assert extract_merchant(text).merchant == "Steamgames.Com"


def test_foreign_amount_with_thousands_and_a_cut_name() -> None:
    text = card("Opos", "1,250.00 Grandhotel Lisboa", "+3512")
    assert extract_merchant(text).merchant == "Grandhotel"


# --- Pre-authorization holds and corrections ---


def test_uber_hold_charge_and_correction_give_the_same_merchant() -> None:
    text = "Opos Uber Canada/Ubertriptoron "  # same text on all three rows
    names = {extract_merchant(text).merchant for _ in range(3)}
    assert names == {"Uber Canada/Ubertrip"}


def test_lyft_temp_auth_hold_is_lyft() -> None:
    hold = card("Opos", "Lyft *Temp Auth Hold", "Vanco")
    ride = card("Opos", "Lyft *Ride Y1Qkq", "Vanco")
    assert extract_merchant(hold).merchant == extract_merchant(ride).merchant == "Lyft"


# --- Rows with no merchant ---


@pytest.mark.parametrize(
    "text",
    [
        "Free Interac E-Transfer ",
        "Interac E-Transfer Rtn ",
        "Mb-Transfer ",
        "Virement Interac ",  # French
    ],
)
def test_transfers_have_no_merchant(text: str) -> None:
    assert extract_merchant(text) == Extracted(merchant=None, channel=None, kind="transfer")


@pytest.mark.parametrize("text", [" ", "", "   "])
def test_blank_text_has_no_merchant(text: str) -> None:
    assert extract_merchant(text) == Extracted(merchant=None, channel=None, kind="blank")


# --- Bill payments, credit memos, deposits ---


def test_mobile_banking_bill_payment() -> None:
    assert extract_merchant("Mb-Hydro-Quebec 55231204") == Extracted(
        merchant="Hydro-Quebec", channel=None, kind="named"
    )


def test_bill_payment_without_mobile_banking() -> None:
    assert extract_merchant("Gestion Immobiliere ").merchant == "Gestion Immobiliere"


def test_credit_memo_sender() -> None:
    assert extract_merchant("1/Nadia Haddad ").merchant == "Nadia Haddad"


def test_credit_memo_without_the_prefix() -> None:
    assert extract_merchant("Nadia Haddad ").merchant == "Nadia Haddad"


def test_payroll_deposit_employer() -> None:
    assert extract_merchant("Boutique Mile End ").merchant == "Boutique Mile End"


# --- French and English payment words (other banks, other formats) ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Achat Metro #123 ", "Metro"),
        ("Paiement Videotron ", "Videotron"),
        ("Pos Purchase Iga #42 ", "Iga"),
        ("Payment Bell Canada ", "Bell Canada"),
        ("Achat Pmt Dollarama ", "Dollarama"),
        ("Saq #5751 Achat ", "Saq"),
    ],
)
def test_payment_words_in_both_languages(text: str, expected: str) -> None:
    assert extract_merchant(text).merchant == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Jean Coutu #12 Laval Qc ", "Jean Coutu"),
        ("Provigo Montreal Qc ", "Provigo"),
        ("Pizza Pizza 1-800-555-0199 ", "Pizza Pizza"),
        ("Netflix.Com 866-579-7172 ", "Netflix.Com"),
        ("Petro-Canada 04512 ", "Petro-Canada"),
    ],
)
def test_cities_provinces_phones_and_bare_store_numbers(text: str, expected: str) -> None:
    assert extract_merchant(text).merchant == expected


# --- Things that must not be stripped ---


@pytest.mark.parametrize(
    "name",
    ["Super C", "Uber Canada/Ubereats", "Concordia Bookstore", "7-Eleven", "Laval Sushi"],
)
def test_ordinary_names_survive(name: str) -> None:
    assert extract_merchant(card("Apos", name, "Montr")).merchant == name


def test_a_name_that_is_only_noise_comes_out_empty_not_crashing() -> None:
    got = extract_merchant(card("Apos", "#1234", "Montr"))
    assert got.merchant is None and got.kind == "card" and got.channel == "in_person"


def test_spacing_is_collapsed() -> None:
    assert extract_merchant("Mb-Hydro-Quebec    55231204").merchant == "Hydro-Quebec"
    assert extract_merchant("Gestion   Immobiliere  ").merchant == "Gestion Immobiliere"


# --- Shapes found by the owner's real-data check (made-up names, same shapes) ---


def test_trailing_country_code_and_lone_digit_are_removed() -> None:
    # Brand starting with a 3-digit number, then "<2-letter country code> <digit>".
    assert extract_merchant(card("Opos", "747 Burger Bar Us 2", "+1855")).merchant == (
        "747 Burger Bar"
    )


def test_leading_number_of_a_brand_is_kept() -> None:
    assert extract_merchant(card("Apos", "747 Burger Bar", "Montr")).merchant == "747 Burger Bar"


@pytest.mark.parametrize("name", ["Studio Ab 2 Plus", "Bar Le 9 Jazz", "Super C 2"])
def test_country_code_rule_only_applies_at_the_very_end(name: str) -> None:
    # Mid-name, or a 1-letter word before the digit: left alone.
    assert extract_merchant(card("Apos", name, "Montr")).merchant == name


def test_store_number_joined_by_underscore_and_underscores_as_spaces() -> None:
    text = card("Opos", "4821_Velo_Shop_Plus", "Montr")
    assert extract_merchant(text).merchant == "Velo Shop Plus"


def test_underscores_alone_become_spaces() -> None:
    assert extract_merchant(card("Opos", "Velo_Shop", "Montr")).merchant == "Velo Shop"


def test_digits_glued_to_a_word_then_an_abbreviation_are_left_for_resolution() -> None:
    # Too risky to strip in general (the digits may belong to the name).
    assert extract_merchant(card("Apos", "Deli505 Ste", "Montr")).merchant == "Deli505 Ste"


@pytest.mark.parametrize("fragment", ["Montr", "Montre", "Montrea", "Toron", "Vanco"])
def test_city_cut_short_inside_the_merchant_field_is_removed(fragment: str) -> None:
    assert extract_merchant(card("Apos", f"Stm Lnd {fragment}", "Montr")).merchant == "Stm Lnd"


def test_transit_machine_code_and_city_fragment() -> None:
    # Station name, a letters+digits machine code, then a city fragment.
    assert extract_merchant(card("Apos", "Stm Lnd Kt456 Montr", "Montr")).merchant == "Stm Lnd"


@pytest.mark.parametrize("name", ["Cafe Du Mont", "Bar Tor", "Chez Mon"])
def test_short_words_that_start_a_city_name_are_kept(name: str) -> None:
    # Only fragments of 5+ letters count as a cut city ("Mont" could be a real word).
    assert extract_merchant(card("Apos", name, "Montr")).merchant == name
