"""Merchant extraction: raw bank text -> clean merchant name (rules only, see DECISIONS.md).

Extraction removes noise; it does not decide which names mean the same merchant.
"Uber Canada/Ubereats" stays "Uber Canada/Ubereats" and "Stm-Opus" stays "Stm-Opus";
mapping those to "Uber Eats" and "STM" is merchant resolution (the next step).

Scotiabank card text is fixed width: "{Apos|Opos} {merchant:20}{location:5} " (31 chars),
where location is a city cut to 5 letters or a phone prefix like "+1877". Other rows hold
a payee or sender name, a fixed bank text (e-Transfers, own-account transfers) or a single
space. Rules for other formats (payment words in French and English, full phone numbers,
cities and provinces) make the function usable on other banks' text too.

Counts-only check on a database (prints no names, amounts or text):
    python -m lookahead.merchants report --db data/private/db/lookahead.db
Brands whose digits belong to the name go in data/private/merchant_allowlist.txt;
leftover names you have looked at and accepted go in
data/private/merchant_accepted_leftovers.txt (then RESULT says "PASS (N accepted ...)").
Add --write-leftovers to save the flagged names to data/private/eval/merchant_leftovers.txt
for the owner to read; they are never printed.
"""

import argparse
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Channel = Literal["online", "in_person"]
Kind = Literal["card", "named", "transfer", "blank"]


@dataclass(frozen=True)
class Extracted:
    """What extraction found in one row's text.

    merchant: the clean name, or None when the text names no merchant.
    channel: card rows only: "online" (Opos) or "in_person" (Apos); None otherwise.
    kind: "card" (Apos/Opos), "named" (payee, sender or employer), "transfer"
          (e-Transfer or own-account transfer text, no counterparty), "blank".
    via_restaurant: delivery-platform orders only: the restaurant text after the platform
          name (the merchant is the platform); None otherwise.
    """

    merchant: str | None
    channel: Channel | None
    kind: Kind
    via_restaurant: str | None = None


_MERCHANT_WIDTH = 20
_LOCATION_WIDTH = 5

_CARD = re.compile(r"([AO]pos) (.*)", re.IGNORECASE | re.DOTALL)
_CHANNELS: dict[str, Channel] = {"apos": "in_person", "opos": "online"}

_TRANSFER_TEXTS = {"mb-transfer", "transfer", "transfert"}
_TRANSFER_WORDS = ("e-transfer", "etransfer", "virement")

_MOBILE_BANKING = re.compile(r"^mb-", re.IGNORECASE)
_MEMO_PREFIX = re.compile(r"^\d+/")  # credit memos: "1/Sender Name"
_UNDERSCORE_STORE = re.compile(r"^\d+_")  # "4821_Velo_Shop": store number joined by "_"
_COUNTRY = re.compile(r"[A-Za-z]{2}")  # "Us 2" at the end: country code + lone digit
_LONE_DIGIT = re.compile(r"\d")
_CITY_FRAGMENT_MIN = 5  # "Montr", "Toron": the bank's own 5-letter cut; "Mont" is too short
_FOREIGN_AMOUNT = re.compile(r"^\d{1,3}(?:,\d{3})*\.\d{2}\s+")  # "7.33 Steamgames.Com"
_PHONE = re.compile(r"(?:\+?1[\s-]?)?\(?\d{3}\)?[\s-]\d{3}[\s-]\d{4}|\+\d{2,}")
_STORE = re.compile(r"#\s*[A-Za-z0-9-]*|\bno\.?\s*\d+\b", re.IGNORECASE)
_CODE = re.compile(r"\d{3,}|(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z0-9]{4,}")

# Payment processors whose code comes before the shop's name ("Sq *Cafe Velo", "Ls Cafe Velo").
_PROCESSORS = {"sq", "sp", "pp", "ls", "iz", "tst", "paypal"}

# Delivery platforms: their name (letters only, lowercase) -> the merchant. Bank text may glue
# a different restaurant onto the platform name each time ("Doordashpizzaroma"), sometimes
# after a short prefix ("Xx/"); the restaurant is kept as a detail, not as the merchant.
_PLATFORMS = {
    "doordash": "DoorDash", "ubereats": "Uber Eats", "skipthedishes": "SkipTheDishes",
    "fantuan": "Fantuan", "grubhub": "Grubhub", "instacart": "Instacart",
}  # fmt: skip
# A platform's own subscription is a separate merchant (a subscription, not a meal).
_PLATFORM_PRODUCTS = {
    "DoorDash": {"dashpass": "DoorDash DashPass"},
    "Uber Eats": {"eatspass": "Uber Eats Pass", "uberone": "Uber One"},
    "SkipTheDishes": {"skipplus": "Skip+"},
    "Instacart": {"instacartplus": "Instacart+", "plus": "Instacart+"},
}
# Merchant names extraction gives to platform orders and to platform subscriptions.
PLATFORM_MERCHANTS = frozenset(_PLATFORMS.values())
PLATFORM_SUBSCRIPTIONS = frozenset(n for d in _PLATFORM_PRODUCTS.values() for n in d.values())
_PLATFORM_PREFIX = re.compile(r"^[A-Za-z]{2,3}/")

# "Uber* <service>": the word after the star says which Uber service it was. Without it the
# service is unknown, and the row must not join rides or delivery (owner's real-data find).
UBER_PENDING = "Uber pending, unknown service"
UBER_UNKNOWN = "Uber, unknown service"
_NOT_A_RESTAURANT = {"pending"}  # "Uber* Eats Pending" is a hold, not a restaurant
_PRODUCT_MIN = 4  # a subscription name cut to 4+ letters still counts ("dashp")

# Compared without case or accents; a "?" in bank text matches any one letter.
_PAYMENT_WORDS = {
    "pos", "apos", "opos", "purchase", "payment", "pmt", "pymt", "debit", "interac",
    "achat", "paiement", "retail",
}  # fmt: skip
_PLACES = {
    "montreal", "mtl", "laval", "longueuil", "brossard", "gatineau", "quebec", "toronto",
    "ottawa", "vancouver", "calgary", "qc", "que", "on", "ont", "bc",
}  # fmt: skip


def _key(word: str) -> str:
    """Lowercase, accents removed, surrounding punctuation dropped: "Montréal," -> "montreal"."""
    decomposed = unicodedata.normalize("NFKD", word.casefold())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return plain.strip(".,:;-/")


def _is_one_of(word: str, words: set[str]) -> bool:
    """True if `word` is in `words`, letting each "?" (the bank's lost accent) match any letter."""
    key = _key(word)
    if "?" not in key:
        return key in words
    return any(
        len(w) == len(key) and all(a == b or a == "?" for a, b in zip(key, w, strict=True))
        for w in words
    )


def _is_place(word: str) -> bool:
    """A city or province, or a city cut short to 5+ letters ("Montr", "Montrea")."""
    if _is_one_of(word, _PLACES):
        return True
    key = _key(word)
    if len(key) < _CITY_FRAGMENT_MIN:
        return False
    return any(
        len(city) > len(key) and all(a == b or a == "?" for a, b in zip(key, city, strict=False))
        for city in _PLACES
    )


def _drop_star_code(name: str) -> str:
    """ "Doordash*Ngbs2" -> "Doordash"; "Sq *Cafe Velo" -> "Cafe Velo" (processor first)."""
    before, star, after = name.partition("*")
    if not star:
        return name
    before, after = before.strip(), after.strip()
    if _key(before) in _PROCESSORS and after:
        return after
    return before or after


def _platform_order(name: str) -> tuple[str, str | None] | None:
    """(platform merchant, restaurant or None) if `name` starts with a delivery platform."""
    match = _PLATFORM_PREFIX.match(name)
    for text in (name, name[match.end() :]) if match else (name,):
        letters = [(i, c.lower()) for i, c in enumerate(text) if c.isalnum()]
        compact = "".join(c for _, c in letters)
        for word, platform in _PLATFORMS.items():
            if not compact.startswith(word):
                continue
            rest_compact = compact[len(word) :]
            for product_word, product in _PLATFORM_PRODUCTS.get(platform, {}).items():
                if rest_compact.startswith(product_word) or (
                    len(rest_compact) >= _PRODUCT_MIN and product_word.startswith(rest_compact)
                ):
                    return product, None
            rest = text[letters[len(word) - 1][0] + 1 :]
            return platform, _tidy(rest) if rest_compact else None
    return None


def _clean(name: str) -> tuple[str | None, str | None]:
    """(merchant, restaurant): remove amounts, codes, store numbers, phones, payment words and
    places; a delivery-platform order becomes the platform, with the restaurant as a detail."""
    name = _UNDERSCORE_STORE.sub("", " ".join(name.split()))
    name = " ".join(name.replace("_", " ").split())
    name = _FOREIGN_AMOUNT.sub("", name)
    uber = _uber_service(name)
    if uber is not None:
        return uber
    name = _drop_star_code(name)
    order = _platform_order(name)
    if order is not None:
        return order
    cleaned = _tidy(name)
    if cleaned is not None and _key(cleaned) == "uber":  # "Uber" alone: which service?
        return UBER_UNKNOWN, None
    return cleaned, None


def _uber_service(name: str) -> tuple[str, str | None] | None:
    """ "Uber* Eats ..." -> Uber Eats (delivery); "Uber* One" -> Uber One; "Uber* Trip" ->
    Uber Trip (a ride); "Uber* Pending" or anything else -> its own unknown-service merchant.
    None if the name is not "Uber*"."""
    before, star, after = name.partition("*")
    if not star or _key(before.strip()) != "uber":
        return None
    words = after.split()
    service = _key(words[0]) if words else ""
    rest = " ".join(words[1:])
    if service == "eats":
        if _key(rest).replace(" ", "") in ("pass", "eatspass"):
            return "Uber Eats Pass", None
        restaurant = None if not rest or _key(rest) in _NOT_A_RESTAURANT else _tidy(rest)
        return "Uber Eats", restaurant
    if service == "one":
        return "Uber One", None
    if service == "trip":
        return "Uber Trip", None
    if service == "pending":
        return UBER_PENDING, None
    return UBER_UNKNOWN, None


def _tidy(name: str) -> str | None:
    name = _PHONE.sub(" ", name)
    name = _STORE.sub(" ", name)
    words = name.split()
    changed = True
    while changed and len(words) > 1:  # never strip a name down to nothing
        changed = False
        if _is_one_of(words[0], _PAYMENT_WORDS) or _key(words[0]) in _PROCESSORS:
            words.pop(0)
            changed = True
        elif _is_one_of(words[-1], _PAYMENT_WORDS) or _is_place(words[-1]):
            words.pop()
            changed = True
        elif _CODE.fullmatch(words[-1]):
            words.pop()
            changed = True
        elif len(words) > 2 and _LONE_DIGIT.fullmatch(words[-1]) and _COUNTRY.fullmatch(words[-2]):
            del words[-2:]
            changed = True
    cleaned = " ".join(words).strip(" -*/#.,")
    return cleaned or None


def extract_merchant(sub_description: str) -> Extracted:
    """Turn one row's raw merchant text (the export's Sub-description) into an `Extracted`."""
    text = sub_description.strip()
    if not text:
        return Extracted(None, None, "blank")

    folded = " ".join(text.casefold().split())
    if folded in _TRANSFER_TEXTS or any(w in folded for w in _TRANSFER_WORDS):
        return Extracted(None, None, "transfer")

    card = _CARD.fullmatch(sub_description.lstrip())
    if card:
        channel = _CHANNELS[card.group(1).casefold()]
        body = card.group(2)
        if len(body) >= _MERCHANT_WIDTH + _LOCATION_WIDTH:  # the full fixed-width layout
            body = body[:_MERCHANT_WIDTH]  # drops the 5-character city or phone prefix
        merchant, restaurant = _clean(body)
        return Extracted(merchant, channel, "card", restaurant)

    text = _MEMO_PREFIX.sub("", _MOBILE_BANKING.sub("", text))
    merchant, restaurant = _clean(text)
    return Extracted(merchant, None, "named", restaurant)


# --- Counts-only report on a database ---

# Signs that a rule missed something, checked separately so the report can count each.
LEFTOVER_PATTERNS: dict[str, re.Pattern[str]] = {
    "'#'": re.compile(r"#"),
    "'*'": re.compile(r"\*"),
    "'+digits'": re.compile(r"\+\d"),
    "3+ digits in a row": re.compile(r"\d{3,}"),
}
DEFAULT_LEFTOVERS_FILE = Path("data/private/eval/merchant_leftovers.txt")
# Brands whose real name contains digits (e.g. a leading number). Private: comes from real data.
DEFAULT_BRAND_ALLOWLIST = Path("data/private/merchant_allowlist.txt")
# Leftover names the owner has looked at and accepted (handed to resolution). Private.
DEFAULT_ACCEPTED_LEFTOVERS = Path("data/private/merchant_accepted_leftovers.txt")


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def load_private_list(path: Path) -> set[str]:
    """Names, one per line (case and spacing ignored; # comments). Used for the brand
    allowlist and the accepted leftovers. A missing file is an empty list. The contents
    come from real data, so they live in data/private/ and are never printed."""
    if not path.exists():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {_normalized(ln) for ln in lines if ln.strip() and not ln.strip().startswith("#")}


load_brand_allowlist = load_private_list


def _without_brands(name: str, brands: set[str]) -> str:
    """`name` with any allowlisted brand removed, so only the remaining text is checked."""
    rest = _normalized(name)
    for brand in brands:
        rest = rest.replace(brand, " ")
    return rest


def leftover_patterns(name: str) -> list[str]:
    """Labels of the leftover patterns found in `name` (empty list = looks clean)."""
    return [label for label, pattern in LEFTOVER_PATTERNS.items() if pattern.search(name)]


def has_leftovers(name: str) -> bool:
    """Signs that a rule missed something: '#', '*', '+digits' or 3+ digits in a row."""
    return bool(leftover_patterns(name))


@dataclass
class ExtractionCounts:
    rows: int = 0
    named: int = 0
    empty: Counter[str] = field(default_factory=Counter)  # why: blank, transfer, nothing left
    channels: Counter[str] = field(default_factory=Counter)  # card rows: online, in_person
    distinct: int = 0
    delivery: int = 0  # delivery-platform orders (merchant = platform, restaurant as a detail)
    leftovers: int = 0  # rows whose name hits at least one leftover pattern
    allowlisted: int = 0  # rows whose only digits belong to an allowlisted brand
    accepted: int = 0  # leftover rows whose name the owner accepted
    accepted_names: set[str] = field(default_factory=set)
    leftover_hits: Counter[str] = field(default_factory=Counter)  # rows per pattern
    # Flagged names -> rows. Kept in memory for the private file; never printed.
    leftover_names: Counter[str] = field(default_factory=Counter)

    @property
    def ok(self) -> bool:
        """No row lost its name to the rules, and no name still looks like it holds noise."""
        return self.empty["nothing left"] == 0 and self.leftovers == 0


def count_extraction(
    texts: Iterable[str],
    brand_allowlist: set[str] | None = None,
    accepted: set[str] | None = None,
) -> ExtractionCounts:
    """Extract every text and keep only counts (plus the flagged names, for the private file).

    Digits inside an allowlisted brand do not count as leftovers; anything else does.
    Leftover names on the `accepted` list are counted separately and do not fail the check.
    """
    brands = brand_allowlist or set()
    known = accepted or set()
    result = ExtractionCounts()
    names: set[str] = set()
    for text in texts:
        got = extract_merchant(text)
        result.rows += 1
        if got.channel:
            result.channels[got.channel] += 1
        if got.merchant is None:
            reason = got.kind if got.kind in ("blank", "transfer") else "nothing left"
            result.empty[reason] += 1
            continue
        result.named += 1
        names.add(got.merchant.casefold())
        if got.via_restaurant is not None:
            result.delivery += 1
        hits = leftover_patterns(got.merchant)
        if hits and brands:
            hits = leftover_patterns(_without_brands(got.merchant, brands))
            if not hits:
                result.allowlisted += 1
        if hits and _normalized(got.merchant) in known:
            result.accepted += 1
            result.accepted_names.add(_normalized(got.merchant))
        elif hits:
            result.leftovers += 1
            result.leftover_hits.update(hits)
            result.leftover_names[got.merchant] += 1
    result.distinct = len(names)
    return result


def is_private_path(path: Path) -> bool:
    """True if `path` lies inside a data/private/ folder (git-ignored)."""
    parts = [p.casefold() for p in path.resolve().parts]
    return any(a == "data" and b == "private" for a, b in zip(parts, parts[1:], strict=False))


def write_leftovers(counts: ExtractionCounts, path: Path) -> None:
    """Write the flagged names for the owner to read on their own machine.

    One line per distinct name: rows, patterns hit, name. Refuses any path outside
    data/private/, so the names cannot end up somewhere that gets committed.
    """
    if not is_private_path(path):
        raise ValueError(f"refusing to write merchant names outside data/private/: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# rows\tpatterns\tname (private: never commit, never paste)"]
    for name, n in sorted(counts.leftover_names.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"{n}\t{', '.join(leftover_patterns(name))}\t{name}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _report(
    db_path: Path,
    account: str,
    leftovers_file: Path | None,
    allowlist_path: Path,
    accepted_path: Path,
) -> int:
    """Print counts only: no merchant names, amounts or bank text."""
    if not db_path.exists():
        print(f"database not found: {db_path}")
        return 2
    if leftovers_file is not None and not is_private_path(leftovers_file):
        print("--write-leftovers must point inside data/private/ (names are private)")
        return 2
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    texts = [
        r[0]
        for r in conn.execute(
            "SELECT sub_description FROM transactions WHERE account = ?", (account,)
        )
    ]
    conn.close()

    c = count_extraction(texts, load_private_list(allowlist_path), load_private_list(accepted_path))
    empty, channels = c.empty, c.channels
    print(f"Merchant extraction: {c.rows} rows, account {account}")
    print(f"with a merchant name: {c.named} rows")
    print(
        f"empty: {sum(empty.values())} rows (blank text: {empty['blank']}, "
        f"transfers: {empty['transfer']}, nothing left after the rules: {empty['nothing left']})"
    )
    print(f"card rows: online {channels['online']}, in person {channels['in_person']}")
    print(f"distinct merchant names: {c.distinct} (ignoring case)")
    print(f"delivery-platform orders with a restaurant detail: {c.delivery} rows")
    print(f"allowlisted brand numbers: {c.allowlisted} rows")
    print(
        f"possible leftovers in names: {c.leftovers} rows "
        f"from {len(c.leftover_names)} distinct names"
    )
    if c.leftovers:
        by_pattern = ", ".join(f"{label}: {c.leftover_hits[label]}" for label in LEFTOVER_PATTERNS)
        print(f"  rows per pattern (a row can hit more than one): {by_pattern}")
    print(f"accepted leftovers: {c.accepted} rows from {len(c.accepted_names)} distinct names")
    if leftovers_file is not None:
        write_leftovers(c, leftovers_file)
        print(f"wrote {len(c.leftover_names)} flagged names to {leftovers_file} (not shown here)")
    if not c.ok:
        print("RESULT: CHECK")
    elif c.accepted:
        plural = "" if c.accepted == 1 else "s"
        print(f"RESULT: PASS ({c.accepted} accepted leftover{plural})")
    else:
        print("RESULT: PASS")
    return 0 if c.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.merchants")
    commands = parser.add_subparsers(dest="command", required=True)
    rep = commands.add_parser("report", help="counts-only check of merchant extraction")
    rep.add_argument("--db", type=Path, required=True)
    rep.add_argument("--account", default="scotia-chequing")
    rep.add_argument(
        "--write-leftovers",
        type=Path,
        nargs="?",
        const=DEFAULT_LEFTOVERS_FILE,
        default=None,
        metavar="PATH",
        help=f"write flagged names to a private file (default {DEFAULT_LEFTOVERS_FILE});"
        " they are never printed",
    )
    rep.add_argument(
        "--allowlist",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"brands whose digits are part of the name (default {DEFAULT_BRAND_ALLOWLIST};"
        " git-ignored, never printed)",
    )
    rep.add_argument(
        "--accepted",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"leftover names you have accepted (default {DEFAULT_ACCEPTED_LEFTOVERS};"
        " git-ignored, never printed)",
    )
    args = parser.parse_args(argv)
    # Defaults are read here, not at import, so tests can point them at temporary files.
    allowlist = args.allowlist if args.allowlist is not None else DEFAULT_BRAND_ALLOWLIST
    accepted = args.accepted if args.accepted is not None else DEFAULT_ACCEPTED_LEFTOVERS
    return _report(args.db, args.account, args.write_leftovers, allowlist, accepted)


if __name__ == "__main__":
    sys.exit(main())
