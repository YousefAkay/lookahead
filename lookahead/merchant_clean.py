"""Merchant-text cleaner (categorizer v2): a conservative second pass for MATCHING.

Extraction (`lookahead.merchants`) already turns the bank's text into a merchant name. This
module strips the bank noise that can still be left, or that other banks' text carries, so
the same merchant matches across spellings: channel prefixes (OPOS, FPOS, APOS, POS, IDP),
payment-processor prefixes ("SQ *", "TST*", "PAYPAL *"), store numbers ("#1234", "STORE 12",
a trailing run of 3+ digits, "W512"), order codes after a star ("*2K4"), reference numbers
("REF 88231", any run of 6+ digits), and place names at the end ("MONTREAL QC", "MTL", "LAVAL").

It is pure (no I/O) and conservative:
- it never strips a name to empty, or to digits and punctuation only: such a step is skipped;
- a 1- or 2-digit number at the end stays ("Forever 21", "Studio 54");
- a trailing city stays when the word before it says it is part of the name ("Université
  Laval", "Ville de Montréal", "Hydro Québec");
- a province code other than QC is only removed right after a city ("TORONTO ON");
- the display name is never changed; `name` keeps the input's case, `key` is for matching.

Off by default: the categorizer uses it only when the `categorizer_v2_cleaner` setting is on
(`lookahead.categorizer_v2`).
"""

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Cleaned:
    display: str  # the input, unchanged
    name: str  # the cleaned name, in the input's case
    key: str  # match form: lowercase, no accents, no punctuation, single spaces
    removed: tuple[str, ...]  # the pieces removed, in the order they were removed


def match_key(text: str) -> str:
    """Lowercase, accents removed, apostrophes dropped, other punctuation as spaces:
    "McDonald's" -> "mcdonalds", "Couche-Tard" -> "couche tard". The bank's "?" (a lost
    accent) is kept, so a lookup can let it stand for any one letter."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    plain = re.sub(r"['’`]", "", plain)
    return " ".join(re.sub(r"[^\w?]+|_", " ", plain).split())


def _fold(word: str) -> str:
    return match_key(word).replace(" ", "")


_CHANNEL = re.compile(r"^(?:[AOF]POS|POS|IDP)(?:\s+(?:PURCHASE|ACHAT))?\s+", re.IGNORECASE)
_PROCESSOR = re.compile(
    r"^(?:SQ|TST|PAYPAL|PP|SP|LS|IZ)\s*\*\s*|^TST-\s*", re.IGNORECASE
)  # a star (or Toast's "TST-") is required: "Spa Nordik" and "Lsd Bar" keep their names
_REFERENCE = re.compile(
    r"\b(?:REF|REFERENCE|RÉF|AUTH|CONF|TXN)\b\.?\s*[#:]?\s*[A-Z0-9-]*\d[A-Z0-9-]*", re.IGNORECASE
)
_STORE_HASH = re.compile(r"#\s*[A-Z]?\d[\w-]*", re.IGNORECASE)
_STORE_WORD = re.compile(r"\b(?:STORE|STR|MAGASIN|SUCC|UNIT[EÉ]?)\s*\d+\b", re.IGNORECASE)
_LONG_DIGITS = re.compile(r"(?<![\w.])\d{6,}(?![\w.])")
_TRAILING_DIGITS = re.compile(r"\s[A-Z]?\d{3,}$", re.IGNORECASE)  # "... 2341", "... W512"
_STAR_CODE = re.compile(r"\s*\*\s*(?=[A-Z]*\d)[A-Z0-9]{2,12}$", re.IGNORECASE)  # "...*2K4"

_CITIES = {
    "montreal", "mtl", "laval", "longueuil", "brossard", "gatineau", "quebec", "sherbrooke",
    "levis", "terrebonne", "boucherville", "verdun", "lasalle", "anjou", "dorval", "pointe claire",
    "saint laurent", "st laurent", "toronto", "ottawa", "vancouver", "calgary", "edmonton",
    "winnipeg", "mississauga", "halifax",
}  # fmt: skip
_PROVINCES = {"qc", "que", "on", "ont", "bc", "ab", "mb", "sk", "ns", "nb", "nl", "pe"}
# A city right after one of these words is part of the name, not a location.
_KEEPS_CITY = {
    "de", "du", "des", "d", "of", "la", "le", "les", "universite", "university", "college",
    "cegep", "hydro", "ville", "city", "port", "place", "centre", "center", "carrefour",
    "musee", "museum", "gare", "aeroport", "airport", "galeries", "biodome", "hopital",
    "hospital", "casino", "complexe", "cite", "parc", "zoo", "canadiens", "alouettes",
}  # fmt: skip


def _tidy(text: str) -> str:
    return " ".join(text.split()).strip(" -*/#.,:;")


def _has_letters(text: str) -> bool:
    return any(c.isalpha() for c in text)


class _Work:
    def __init__(self, text: str) -> None:
        self.text = _tidy(text)
        self.removed: list[str] = []

    def apply(self, new: str, piece: str) -> bool:
        """Take `new` unless it would leave no letters (never strip a name to nothing)."""
        new = _tidy(new)
        if not _has_letters(new) or not piece.strip():
            return False
        self.text = new
        self.removed.append(_tidy(piece) or piece.strip())
        return True

    def sub(self, pattern: re.Pattern[str]) -> None:
        while True:
            match = pattern.search(self.text)
            if match is None:
                return
            new = self.text[: match.start()] + " " + self.text[match.end() :]
            if not self.apply(new, match.group(0)):
                return


def _trailing_place(work: _Work) -> bool:
    """Remove a place at the end (a province code after a city, QC alone, or a city that is
    not part of the name). True if something was removed."""
    words = work.text.split()
    if len(words) < 2:
        return False
    last = _fold(words[-1])
    for size in (2, 1):  # two-word cities first ("Pointe Claire")
        if len(words) <= size:
            continue
        city = " ".join(match_key(w) for w in words[-size:])
        if city in _CITIES:
            before = _fold(words[-size - 1])
            if before in _KEEPS_CITY:
                return False
            return work.apply(" ".join(words[:-size]), " ".join(words[-size:]))
    if last in _PROVINCES:
        for size in (2, 1):  # "MONTREAL QC", "POINTE CLAIRE QC": the city and the code go
            if len(words) <= size + 1:
                continue
            city = " ".join(match_key(w) for w in words[-size - 1 : -1])
            if city in _CITIES and _fold(words[-size - 2]) not in _KEEPS_CITY:
                return work.apply(" ".join(words[: -size - 1]), " ".join(words[-size - 1 :]))
        if last == "qc":  # unambiguous on its own; "ON", "BC" ... only after a city
            return work.apply(" ".join(words[:-1]), words[-1])
    return False


def clean_merchant(text: str) -> Cleaned:
    """The cleaned name of one merchant text, with what was removed."""
    work = _Work(text)
    for pattern in (_CHANNEL, _PROCESSOR):
        work.sub(pattern)
    work.sub(_CHANNEL)  # "SQ *POS ..." is rare, but a prefix may follow a processor
    for pattern in (_STAR_CODE, _REFERENCE, _STORE_HASH, _STORE_WORD, _LONG_DIGITS):
        work.sub(pattern)
    changed = True
    while changed:  # places and store numbers at the end, in any order
        changed = _trailing_place(work)
        match = _TRAILING_DIGITS.search(work.text)
        if match and work.apply(work.text[: match.start()], match.group(0)):
            changed = True
    name = work.text if _has_letters(work.text) else " ".join(text.split())
    return Cleaned(text, name, match_key(name), tuple(work.removed))
