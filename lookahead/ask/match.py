"""Merchant names in a question, matched to the names in my data on this machine (never sent).

The phrase is what I wrote ("starbux", "uber eats", "velisaro"). Both it and each merchant name
are made plain (lowercase, no accents, punctuation as spaces), then tried level by level; the
first level that finds anything decides:
1. same name;
2. alias: a known bank spelling of the name (`resolve.ALIASES`) or a name I merged on the
   merchants page, written the way I wrote it;
3. cleaned spelling: the same letters without spaces ("petitorme"), the phrase as whole words of
   a name ("velisaro" in "Cafe Velisaro"), or a name inside the phrase ("cafe velisaro latte");
4. close match: a typo (similarity of the letters 0.8 or more, against the name or one of its
   words), for phrases of 4 letters or more. Several names within 0.05 of the best are all
   offered.

One name at a level: matched. Several: I am asked which one. None at any level: "No merchant
like ...". Only the names already in my data can match, so nothing new is ever invented.
"""

import re
import sqlite3
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher

from lookahead.resolve import ALIAS_KEYS, name_key

CLOSE_AT = 0.8
CLOSE_BAND = 0.05
MIN_CLOSE_LETTERS = 4


@dataclass(frozen=True)
class Match:
    phrase: str
    status: str  # one / many / none
    names: tuple[str, ...]  # one: the name; many: the candidates
    how: str = ""  # the level that matched, in plain words


def plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    letters = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", letters).split())


def stored_aliases(conn: sqlite3.Connection) -> dict[str, str]:
    """Alias key -> merchant name: the built-in bank spellings, then the names stored on the
    merchants page (rules and my merges)."""
    out = dict(ALIAS_KEYS)
    try:
        rows = conn.execute(
            "SELECT a.alias_key, m.canonical_name FROM merchant_aliases a"
            " JOIN merchants m USING (merchant_id)"
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    out.update({key: name for key, name in rows})
    return out


def _contains(outer: str, inner: str) -> bool:
    return bool(inner) and f" {inner} " in f" {outer} "


def _closeness(want: str, name: str) -> float:
    whole = SequenceMatcher(None, want, name).ratio()
    words = name.split()
    if len(want.split()) == 1 and len(words) > 1:
        whole = max(whole, *(SequenceMatcher(None, want, w).ratio() for w in words))
    return whole


def match(phrase: str, names: Iterable[str], aliases: Mapping[str, str] | None = None) -> Match:
    unique = sorted(set(names))
    want = plain(phrase)
    if not want:
        return Match(phrase, "none", ())
    plain_of = {n: plain(n) for n in unique}

    def decide(found: Iterable[str], how: str) -> Match | None:
        found = tuple(sorted(set(found)))
        if not found:
            return None
        return Match(phrase, "one" if len(found) == 1 else "many", found, how)

    m = decide((n for n in unique if plain_of[n] == want), "same name")
    if m:
        return m
    if aliases:
        target = aliases.get(name_key(phrase)) or aliases.get(want)
        if target is not None:
            m = decide((n for n in unique if plain_of[n] == plain(target)), "known spelling")
            if m:
                return m
    squeezed = want.replace(" ", "")
    m = decide((n for n in unique if plain_of[n].replace(" ", "") == squeezed),
               "same letters")  # fmt: skip
    if m:
        return m
    m = decide((n for n in unique if _contains(plain_of[n], want)), "your words are in the name")
    if m:
        return m
    m = decide((n for n in unique if _contains(want, plain_of[n])), "the name is in your words")
    if m:
        return m
    if len(squeezed) < MIN_CLOSE_LETTERS:
        return Match(phrase, "none", ())
    scores = {n: _closeness(want, plain_of[n]) for n in unique}
    best = max(scores.values(), default=0.0)
    if best < CLOSE_AT:
        return Match(phrase, "none", ())
    m = decide((n for n, s in scores.items() if s >= best - CLOSE_BAND), "close spelling")
    assert m is not None
    return m
