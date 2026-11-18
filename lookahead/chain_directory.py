"""Public Canadian chain directory (categorizer v2): big national chains plus Quebec/Montreal
ones, written from general knowledge, nothing from my data (data/reference/canadian_chains.csv).

Columns: name, aliases (EN/FR names and common bank spellings, separated by "|"), category
(one of the 17), region ("national" or "QC"), verified ("no" until I spot-check an entry).

`ChainDirectory.lookup` matches a merchant name (cleaned by `lookahead.merchant_clean` first):
1. exact: the match key equals an entry's name;
2. alias: the match key equals one of an entry's aliases;
3. similar: character 3-gram TF-IDF cosine (fitted on the directory's own names and aliases;
   a name or alias of 5 letters or fewer is left out: it matches only an equal key)
   at or above `SIMILAR_AT`, only when asked for (`similar=True`). Off by default.
The bank's "?" (a lost accent) stands for any one letter in steps 1 and 2, except in a
short key.

How SIMILAR_AT was chosen (synthetic merchants only, never on a result): `choose_threshold`
takes the synthetic merchant names that are NOT chains (the generator's made-up local
places, e.g. its cafés and its landlord) and finds the highest similarity any of them has to
any directory name or alias. The threshold is the next step of 0.05 above that, plus one more
step as a margin, and never below 0.5. So no synthetic local merchant can be taken for a
chain. Nothing about how often a chain spelling is matched went into it.
tests/test_chain_directory.py recomputes it and fails if the constant drifts.
"""

import csv
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from lookahead.merchant_clean import clean_merchant, match_key
from lookahead.taxonomy import CATEGORIES

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "reference" / "canadian_chains.csv"
REGIONS = ("national", "QC")
SIMILAR_AT = 0.6  # from `choose_threshold` on the synthetic non-chain merchants (see above)
_STEP = 0.05
_FLOOR = 0.5


@dataclass(frozen=True)
class Entry:
    id: str  # "D001", ...: its row in the file, for citations
    name: str
    aliases: tuple[str, ...]
    category: str
    region: str
    verified: bool


@dataclass(frozen=True)
class Match:
    entry: Entry
    how: str  # "exact", "alias" or "similar"
    score: float  # 1.0 for exact and alias matches


def load_entries(path: Path = DEFAULT_PATH) -> list[Entry]:
    """Every row of the directory file, checked: a known category and region, a name."""
    entries = []
    with path.open(encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=1):
            name = row["name"].strip()
            if not name:
                raise ValueError(f"row {i}: empty name")
            if row["category"] not in CATEGORIES:
                raise ValueError(f"row {i}: unknown category {row['category']!r}")
            if row["region"] not in REGIONS:
                raise ValueError(f"row {i}: unknown region {row['region']!r}")
            aliases = tuple(a.strip() for a in row["aliases"].split("|") if a.strip())
            verified = row["verified"].strip().casefold() == "yes"
            entries.append(Entry(f"D{i:03d}", name, aliases, row["category"], row["region"],
                                 verified))  # fmt: skip
    return entries


def _grams(key: str) -> Counter[str]:
    grams: Counter[str] = Counter()
    for word in key.split():
        padded = f" {word} "
        grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return grams


def _same(a: str, b: str) -> bool:
    """Equal keys, where "?" on either side matches any one character."""
    return len(a) == len(b) and all(x == y or "?" in (x, y) for x, y in zip(a, b, strict=True))


SHORT_LETTERS = 5  # a key with this many letters or fewer matches only an equal key


def is_short(key: str) -> bool:
    """A short name or alias (Gap, Bell, Shell, Cora): matched only exactly, never by
    similarity or the "?" wildcard, so it cannot take an unrelated local merchant."""
    return sum(c.isalpha() for c in key) <= SHORT_LETTERS


class ChainDirectory:
    def __init__(self, entries: Iterable[Entry]) -> None:
        self.entries = list(entries)
        self.by_id = {e.id: e for e in self.entries}
        self._exact: dict[str, Entry] = {}
        self._alias: dict[str, Entry] = {}
        for e in self.entries:
            self._exact.setdefault(match_key(e.name), e)
        for e in self.entries:
            for a in e.aliases:
                self._alias.setdefault(match_key(a), e)
        # TF-IDF over every name and alias key that is not short; a key points at its entry
        self._keys = [(k, e) for e in self.entries for k in map(match_key, (e.name, *e.aliases))
                      if not is_short(k)]  # fmt: skip
        counts = [_grams(k) for k, _ in self._keys]
        df = Counter(g for c in counts for g in c)
        n = len(counts)
        self._idf = {g: math.log((1 + n) / (1 + d)) + 1 for g, d in df.items()}
        self._unseen_idf = math.log(1 + n) + 1
        self._vectors = [self._vector(c) for c in counts]

    def _vector(self, grams: Counter[str]) -> dict[str, float]:
        weights = {g: tf * self._idf.get(g, self._unseen_idf) for g, tf in grams.items()}
        norm = math.sqrt(sum(w * w for w in weights.values()))
        return {g: w / norm for g, w in weights.items()} if norm else {}

    def _find(self, key: str, table: dict[str, Entry]) -> Entry | None:
        if key in table:
            return table[key]
        if "?" in key:
            return next((e for k, e in table.items() if not is_short(k) and _same(key, k)), None)
        return None

    def similar(self, name: str, k: int = 3) -> list[Match]:
        """The k entries most similar to the cleaned `name` (best score per entry), best first."""
        query = self._vector(_grams(clean_merchant(name).key))
        best: dict[str, float] = {}
        for (_, entry), vector in zip(self._keys, self._vectors, strict=True):
            score = round(sum(w * vector.get(g, 0.0) for g, w in query.items()), 9)
            if score > best.get(entry.id, -1.0):
                best[entry.id] = score
        ranked = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [Match(self.by_id[i], "similar", s) for i, s in ranked]

    def lookup(
        self, name: str, similar: bool = False, threshold: float = SIMILAR_AT
    ) -> Match | None:
        """The directory entry for a merchant name: exact, then alias, then (only with
        `similar=True`) the most similar entry at or above `threshold`. None if no match."""
        key = clean_merchant(name).key
        if not key:
            return None
        for how, table in (("exact", self._exact), ("alias", self._alias)):
            entry = self._find(key, table)
            if entry is not None:
                return Match(entry, how, 1.0)
        if not similar:
            return None
        top = self.similar(name, k=1)
        return top[0] if top and top[0].score >= threshold else None


@cache
def default_directory() -> ChainDirectory:
    return ChainDirectory(load_entries())


def choose_threshold(directory: ChainDirectory, non_chains: Iterable[str]) -> float:
    """The similarity threshold: the next 0.05 step above the best score any non-chain name
    reaches against the directory, plus one step of margin, at least 0.5."""
    worst = max((m.score for n in non_chains for m in directory.similar(n, k=1)), default=0.0)
    steps = math.floor(worst / _STEP + 1e-9) + 2
    return round(max(_FLOOR, steps * _STEP), 2)


def synthetic_non_chains() -> list[str]:
    """Merchant names (as extraction gives them) of the synthetic training rows whose answer
    key merchant has no directory entry by exact or alias match: the generator's made-up
    local places. Used only to choose SIMILAR_AT."""
    from lookahead.synthetic_training import synthetic_examples

    directory = default_directory()
    rows = synthetic_examples()
    names: set[str] = set()
    for name, merchant in zip(rows["name"], rows["merchant"], strict=True):
        if directory.lookup(merchant) is None:
            names.update({name, merchant})
    return sorted(names)
