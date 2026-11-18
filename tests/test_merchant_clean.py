"""The merchant-text cleaner (categorizer v2). Every string here is made up in the style of
bank text; none comes from real data."""

import pytest

from lookahead.merchant_clean import clean_merchant, match_key

STRIPPED = [
    # (input, cleaned name)
    ("OPOS TIM HORTONS #2341 MONTREAL QC", "TIM HORTONS"),
    ("Opos Cafe Velorin #12", "Cafe Velorin"),
    ("FPOS DOLLARAMA 1234 LAVAL", "DOLLARAMA"),
    ("APOS SUPER C 5139", "SUPER C"),
    ("POS PURCHASE PHARMAPRIX 0442", "PHARMAPRIX"),
    ("IDP ACHAT MAXI 8812 MTL", "MAXI"),
    ("SQ *CAFE VELORIN", "CAFE VELORIN"),
    ("Sq * Boulangerie Tardive", "Boulangerie Tardive"),
    ("TST* Bistro Fanfaron", "Bistro Fanfaron"),
    ("TST-Bistro Fanfaron", "Bistro Fanfaron"),
    ("PAYPAL *STEAMGAMES", "STEAMGAMES"),
    ("PP*Boutique Zephyrine", "Boutique Zephyrine"),
    ("METRO PLUS REF 882314 MONTREAL", "METRO PLUS"),
    ("ULTRAMAR AUTH 00A913", "ULTRAMAR"),
    ("PHARMAPRIX 123456789", "PHARMAPRIX"),
    ("IGA EXTRA 08123 MTL", "IGA EXTRA"),
    ("SAQ 23045 ST-LAURENT", "SAQ"),
    ("COSTCO WHOLESALE W512 POINTE CLAIRE QC", "COSTCO WHOLESALE"),
    ("STARBUCKS TORONTO ON", "STARBUCKS"),
    ("Rona Laval", "Rona"),
    ("Épicerie Lunaire Montréal", "Épicerie Lunaire"),
    ("EPICERIE LUNAIRE MONTREAL QC", "EPICERIE LUNAIRE"),
    ("Librairie Ostrogothe STORE 17", "Librairie Ostrogothe"),
    ("Quincaillerie Brume Magasin 22", "Quincaillerie Brume"),
    ("AMZN Mktp CA*2K4X9", "AMZN Mktp CA"),
    ("7-Eleven 34567", "7-Eleven"),
    ("Boulangerie Tardive QC", "Boulangerie Tardive"),
    ("  OPOS   Couche-Tard   #0091   Longueuil  ", "Couche-Tard"),
    ("Café Velorin Gatineau QC", "Café Velorin"),
]

KEPT = [
    # near-misses: these must come out unchanged
    "Forever 21",
    "Studio 54",
    "Route 66",
    "Université Laval",
    "Ville de Montréal",
    "Hydro Quebec",
    "Hydro-Québec",
    "Le Montreal",
    "Casino de Montréal",
    "Posh Nails",
    "Spa Nordik",
    "Lsd Bar",
    "Square One Cafe",
    "Toast Bistro",
    "Pay Plus Laverie",
    "B12 Vitamines",
    "Ontario Pizza",
    "Galeries Laval",
    "Quebec",
    "Montreal",
    "Toronto On",
    "Ma Cabane Online",
    "Café Dépôt",
    "Caf? Quorvel",
    "Netflix.Com",
    "Uber Eats",
]


@pytest.mark.parametrize(("text", "name"), STRIPPED)
def test_bank_noise_is_stripped(text: str, name: str) -> None:
    got = clean_merchant(text)
    assert got.name == name
    assert got.removed  # something was removed, and it is listed


@pytest.mark.parametrize("text", KEPT)
def test_near_misses_are_not_stripped(text: str) -> None:
    got = clean_merchant(text)
    assert got.name == text
    assert got.removed == ()


@pytest.mark.parametrize(
    "text", ["#1234", "POS", "OPOS", "SQ *", "MONTREAL QC", "123456789", "REF 88231", "QC", "*"]
)
def test_never_strips_a_name_to_empty(text: str) -> None:
    got = clean_merchant(text)
    assert got.name.strip()
    assert got.key.strip() or not any(c.isalnum() for c in text)
    if not any(c.isalpha() for c in text):  # no letters at all: kept as it came
        assert got.name == " ".join(text.split())
    else:
        assert any(c.isalpha() for c in got.name)


def test_display_is_never_changed() -> None:
    text = "  OPOS Café Velorin #12  MONTRÉAL QC "
    got = clean_merchant(text)
    assert got.display == text
    assert got.name == "Café Velorin"


def test_removed_pieces_are_listed_in_order() -> None:
    got = clean_merchant("OPOS TIM HORTONS #2341 MONTREAL QC")
    assert got.removed == ("OPOS", "2341", "MONTREAL QC")


def test_match_key_normalizes_case_spacing_accents_and_punctuation() -> None:
    assert match_key("  Épicerie   LUNAIRE ") == "epicerie lunaire"
    assert match_key("Couche-Tard") == "couche tard"
    assert match_key("McDonald's") == match_key("MCDONALDS") == "mcdonalds"
    assert match_key("L’Échoppe") == "lechoppe"
    assert match_key("Caf? Quorvel") == "caf? quorvel"  # the bank's lost accent is kept


def test_key_matches_across_bank_spellings() -> None:
    a = clean_merchant("OPOS PHARMAPRIX #0442 MONTREAL QC").key
    b = clean_merchant("Pharmaprix").key
    c = clean_merchant("FPOS PHARMAPRIX 123456789").key
    assert a == b == c == "pharmaprix"


def test_cleaning_twice_changes_nothing_more() -> None:
    for text, _ in STRIPPED:
        once = clean_merchant(text).name
        assert clean_merchant(once).name == once
