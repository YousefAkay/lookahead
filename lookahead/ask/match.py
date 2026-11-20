"""Merchant names in a question, matched to the names in my data on this machine (never sent).

The phrase is what I wrote ("velisaro"). It is compared with each merchant name after both are
made plain (lowercase, no accents, punctuation as spaces), in this order:
1. same name;
2. the phrase is a whole word or words of exactly one name, or that name is inside the phrase.

More than one name at the same level: I am asked which one. None: "No merchant like ...".
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Match:
    phrase: str
    status: str  # one / many / none
    names: tuple[str, ...]  # one: the name; many: the candidates
    how: str = ""  # the rule that matched, in plain words


def plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    letters = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", letters).split())


def _contains(outer: str, inner: str) -> bool:
    """`inner` appears in `outer` as whole words."""
    return bool(inner) and f" {inner} " in f" {outer} "


def match(phrase: str, names: Iterable[str]) -> Match:
    unique = sorted(set(names))
    want = plain(phrase)
    if not want:
        return Match(phrase, "none", ())
    levels = (
        ("same name", lambda n: plain(n) == want),
        ("name contains your words", lambda n: _contains(plain(n), want)),
        ("your words contain the name", lambda n: _contains(want, plain(n))),
    )
    for how, test in levels:
        found = tuple(n for n in unique if test(n))
        if len(found) == 1:
            return Match(phrase, "one", found, how)
        if len(found) > 1:
            return Match(phrase, "many", found, how)
    return Match(phrase, "none", ())
