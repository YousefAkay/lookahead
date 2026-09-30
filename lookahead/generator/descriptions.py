"""Bank-style description text: stage 4 of the generator.

Copies the Scotiabank chequing export format (see DECISIONS.md):
- `Description` column = the transaction type ("pos purchase", "credit memo", ...).
- `Sub-description` for card rows = "{Apos|Opos} {merchant:20}{location:5} " (31 chars):
  Apos = in person, Opos = online (observed pattern). Text is built in capitals, as a
  terminal sends it, then title-cased; non-ASCII characters (accents) become "?".
- Other rows: fixed texts, payee names (accents stripped, not "?"), or a single space.
"""

import unicodedata
from dataclasses import dataclass, replace

import numpy as np

from lookahead.generator.schedules import GeneratedTxn

_MERCHANT_WIDTH = 20
_LOCATION_WIDTH = 5
_MISSING_MEMO_PREFIX = 0.05  # share of credit memos without "1/"
_MOBILE_BANKING_SHARE = 0.5  # share of payees paid through mobile banking ("Mb-")

_CARD_TYPES = {"pos purchase", "pos return", "correction"}
_BLANK_TYPES = {"abm withdrawal", "service charge", "overdraft interest", "overdrawn handling chg."}


@dataclass(frozen=True)
class _Template:
    raw: str  # capitals; may contain {store} (stable per persona) or {code} (random)
    location: str  # 5 chars: a city cut to 5 letters, or a phone prefix


# Raw terminal text per canonical merchant. Unknown merchants: name in capitals, MONTR.
_TEMPLATES: dict[str, _Template] = {
    "Tim Hortons": _Template("TIM HORTONS #{store}", "MONTR"),
    "Café Velisaro": _Template("CAFÉ VELISARO", "MONTR"),
    "Café Quorvel": _Template("CAFÉ QUORVEL", "MONTR"),
    "Couche-Tard": _Template("COUCHE-TARD #{store}", "MONTR"),
    "Maxi": _Template("MAXI #{store}", "MONTR"),
    "Super C": _Template("SUPER C #{store}", "MONTR"),
    "IGA": _Template("IGA #{store}", "MONTR"),
    "Metro": _Template("METRO #{store}", "MONTR"),
    "SAQ": _Template("SAQ #{store}", "MONTR"),
    "Resto Pho Tavo": _Template("RESTO PHO TAVO", "MONTR"),
    "Restaurant Le Petit Orme": _Template("RESTAURANT LE PETIT ORME", "MONTR"),
    "Jean Coutu": _Template("JEAN COUTU #{store}", "MONTR"),
    "Pharmaprix": _Template("PHARMAPRIX #{store}", "MONTR"),
    "Dollarama": _Template("DOLLARAMA #{store}", "MONTR"),
    "Winners": _Template("WINNERS #{store}", "MONTR"),
    "Best Buy": _Template("BEST BUY #{store}", "MONTR"),
    "STM": _Template("STM-OPUS #{store}", "MONTR"),
    "Bixi": _Template("BIXI MONTRÉAL", "MONTR"),
    "Uber Eats": _Template("UBER CANADA/UBEREATS", "TORON"),
    "Uber": _Template("UBER CANADA/UBERTRIP", "TORON"),
    "Lyft": _Template("LYFT *RIDE {code}", "VANCO"),
    "DoorDash": _Template("DOORDASH*{code}", "+1855"),
    "Amazon.ca": _Template("AMAZON.CA*{code}", "+1877"),
    "Steam": _Template("STEAMGAMES.COM", "+1425"),
    "Etsy": _Template("ETSY.COM", "+1718"),
    "Qavo": _Template("QAVO.COM", "DUBAI"),
    "Spotify": _Template("SPOTIFY P{code}", "STOCK"),
    "Netflix": _Template("NETFLIX.COM", "+1866"),
    "Fizz": _Template("FIZZ", "MONTR"),
    "Concordia Bookstore": _Template("CONCORDIA BOOKSTORE", "MONTR"),
    "uBreakiFix": _Template("UBREAKIFIX #{store}", "MONTR"),
    "Ticketmaster": _Template("TICKETMASTER CANADA", "+1855"),
}

_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"


def _question_marks(text: str) -> str:
    """Card text: every non-ASCII character (accents) comes through as '?'."""
    return "".join(c if ord(c) < 128 else "?" for c in text)


def _strip_accents(text: str) -> str:
    """Payee names: accents are removed, not replaced ("Hydro-Québec" -> "Hydro-Quebec")."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


class _Describer:
    """Holds per-persona stable choices: store numbers, bill payment channels."""

    def __init__(self, rng: np.random.Generator) -> None:
        self.rng = rng
        self.stores: dict[str, list[str]] = {}
        self.bill_channel: dict[str, str] = {}  # payee -> full sub-description

    def _store(self, merchant: str) -> str:
        if merchant not in self.stores:
            count = int(self.rng.integers(1, 4))  # a persona frequents 1-3 locations
            self.stores[merchant] = [str(int(self.rng.integers(100, 10_000))) for _ in range(count)]
        options = self.stores[merchant]
        return options[int(self.rng.integers(len(options)))]

    def _code(self) -> str:
        return "".join(self.rng.choice(list(_CODE_CHARS), size=5))

    def card(self, t: GeneratedTxn) -> str:
        template = _TEMPLATES.get(t.merchant, _Template(t.merchant.upper(), "MONTR"))
        if t.merchant == "Lyft" and t.quirk == "preauth_hold":
            template = _Template("LYFT *TEMP AUTH HOLD", "VANCO")  # exactly 20 characters
        raw = template.raw.format(
            store=self._store(t.merchant) if "{store}" in template.raw else "",
            code=self._code() if "{code}" in template.raw else "",
        )
        if t.original_amount_cents is not None:  # foreign currency: show the original amount
            raw = f"{abs(t.original_amount_cents) / 100:.2f} {raw}"
        field = f"{raw[:_MERCHANT_WIDTH]:<{_MERCHANT_WIDTH}}{template.location:<{_LOCATION_WIDTH}}"
        prefix = "Opos" if t.online else "Apos"
        return f"{prefix} {_question_marks(field.title())} "

    def bill_payment(self, payee: str) -> str:
        if payee not in self.bill_channel:
            name = _strip_accents(payee).upper().title()
            if self.rng.random() < _MOBILE_BANKING_SHARE:
                reference = int(self.rng.integers(10_000_000, 100_000_000))  # 8 digits
                self.bill_channel[payee] = f"Mb-{name} {reference}"  # no trailing space
            else:
                self.bill_channel[payee] = f"{name} "
        return self.bill_channel[payee]

    def sub_description(self, t: GeneratedTxn) -> str:
        kind = t.txn_type
        if kind in _CARD_TYPES:
            return self.card(t)
        if kind in _BLANK_TYPES:
            return " "
        if kind == "bill payment":
            return self.bill_payment(t.merchant)
        if kind in ("customer transfer cr.", "customer transfer dr."):
            return "Mb-Transfer "
        if kind == "credit memo":
            prefix = "" if self.rng.random() < _MISSING_MEMO_PREFIX else "1/"
            return f"{prefix}{_strip_accents(t.merchant).upper().title()} "
        if kind in ("deposit", "withdrawal"):
            if t.merchant == "Interac e-Transfer":
                return (
                    "Interac E-Transfer Rtn "
                    if t.flow_type == "refund"
                    else "Free Interac E-Transfer "
                )
            return f"{_strip_accents(t.merchant).upper().title()} "  # e.g. payroll: employer name
        raise ValueError(f"no description rule for txn_type {kind!r}")


def describe(rows: list[GeneratedTxn], rng: np.random.Generator) -> list[GeneratedTxn]:
    """Copies of `rows` with the bank's Description and Sub-description filled in.

    Only the two text fields change; every ground-truth field stays as it was.
    """
    describer = _Describer(rng)
    return [
        replace(t, description=t.txn_type, sub_description=describer.sub_description(t))
        for t in rows
    ]
