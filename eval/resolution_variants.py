"""Synthetic test set for merchant resolution: name variants with known merchants.

Generator v0 gives each merchant exactly one name after extraction, so it has no pairs
for resolution to join. This module builds variants of the answer-key merchants in the
ways real bank text differs. The variant types below were fixed (and committed) before
any resolution threshold was chosen. They are still written by the same person who
writes the rules, so scores on this set are optimistic; see DECISIONS.md.

Variant types:
- bank:            the name extraction gives on the synthetic export ("Uber Canada/Ubereats")
- plain:           the canonical name as another source might write it ("Uber Eats")
- accent_q:        accents as "?" (card text: "Caf? Velisaro")
- accent_dropped:  accents removed (payee text: "Cafe Velisaro")
- upper_punct:     capitals, hyphens and dots as spaces ("COUCHE TARD")
- location_full:   in-person card merchants: name + a neighbourhood ("Maxi Verdun")
- location_cut:    the same, cut to the bank's 20-character merchant field

Hard negatives: made-up merchants that look like real ones ("Restaurant Le Petit Bistro"
next to "Restaurant Le Petit Orme"), each with its own plain and location variants.

The merchants are split in two (seeded): `dev` for choosing thresholds, `test` for the
reported numbers. No merchant is in both.
"""

import csv
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from lookahead.merchants import extract_merchant

CUT = 20  # the bank's card merchant field
NO_MERCHANT = {"ABM", "Scotiabank", "Interac e-Transfer", "Own savings account"}
LOCATIONS = [
    "Verdun", "Lasalle", "Rosemont", "Plateau", "Centre-Ville", "Cote-Des-Neiges",
    "Saint-Laurent", "Villeray", "Hochelaga", "Mile-End", "Ndg", "Westmount",
]  # fmt: skip
# Made-up look-alike -> the answer-key merchant it imitates (kept in the same half).
HARD_NEGATIVES = {
    "Restaurant Le Petit Bistro": "Restaurant Le Petit Orme",  # same first 19 characters
    "Super Cafe": "Super C",  # starts with "Super C"
    "Pharmacie Brunet": "Pharmaprix",
    "Tim Burger": "Tim Hortons",
    "Uber Freight": "Uber",
    "Metro Pizza": "Metro",
    "Jean Talon Market": "Jean Coutu",
    "Maxi Dollar": "Maxi",
}


@dataclass(frozen=True)
class Variant:
    name: str
    merchant: str  # ground truth: the canonical merchant
    kind: str  # variant type
    cut: bool  # the name fills the bank's 20-character card field


def _question(text: str) -> str:
    return "".join(c if ord(c) < 128 else "?" for c in text)


def _dropped(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _bank_names(synthetic_dir: Path) -> dict[str, tuple[str, bool, bool]]:
    """canonical -> (extracted bank name, card merchant, in person), from the three personas."""
    out: dict[str, tuple[str, bool, bool]] = {}
    for pid in "ABC":
        export = synthetic_dir / f"persona_{pid}.csv"
        key = synthetic_dir / f"persona_{pid}_answer_key.csv"
        with export.open(encoding="utf-8-sig", newline="") as f:
            texts = [r["Sub-description"] for r in csv.DictReader(f)]
        with key.open(encoding="utf-8", newline="") as f:
            truth = [r["merchant"] for r in csv.DictReader(ln for ln in f if ln[0] != "#")]
        for text, canonical in zip(texts, truth, strict=True):
            got = extract_merchant(text)
            if canonical in NO_MERCHANT or got.merchant is None or canonical in out:
                continue
            out[canonical] = (got.merchant, got.kind == "card", got.channel == "in_person")
    return out


def _variants_of(
    canonical: str, bank: str | None, card: bool, in_person: bool, rng: np.random.Generator
) -> list[Variant]:
    found: dict[str, Variant] = {}

    def add(name: str, kind: str, cut: bool = False) -> None:
        name = " ".join(name.split())
        if name and name.casefold() not in {n.casefold() for n in found}:
            found[name] = Variant(name, canonical, kind, cut)

    if bank is not None:
        add(bank, "bank", cut=card and len(bank) == CUT)
    add(canonical, "plain")
    if _question(canonical) != canonical:
        add(_question(canonical), "accent_q")
        add(_dropped(canonical), "accent_dropped")
    add(_dropped(canonical).upper().replace("-", " ").replace(".", " "), "upper_punct")
    if in_person:
        full = f"{_question(canonical)} {LOCATIONS[int(rng.integers(len(LOCATIONS)))]}"
        add(full, "location_full")
        if len(full) > CUT:
            cut = full[:CUT].rstrip()
            add(cut, "location_cut", cut=len(cut) == CUT)
    return list(found.values())


@dataclass
class VariantSet:
    dev: list[Variant]  # for choosing thresholds
    test: list[Variant]  # for the reported numbers
    dropped: int  # names two merchants share; no rule can resolve them by name alone


def build_variants(synthetic_dir: Path, seed: int = 7) -> VariantSet:
    """Variants of two disjoint halves of the merchants (seeded, so always the same)."""
    rng = np.random.default_rng(seed)
    bank = _bank_names(synthetic_dir)
    merchants = sorted(bank)
    order = rng.permutation(len(merchants))
    halves = {
        "dev": [merchants[i] for i in order[::2]],
        "test": [merchants[i] for i in order[1::2]],
    }
    for names in halves.values():  # each look-alike joins the half of the merchant it imitates
        names += [fake for fake, real in HARD_NEGATIVES.items() if real in names]
    out: dict[str, list[Variant]] = {}
    dropped = 0
    for split, names in halves.items():
        variants: list[Variant] = []
        for canonical in sorted(names):
            # Look-alikes have no bank text; they act as in-person card merchants.
            name, card, in_person = bank.get(canonical, (None, True, True))
            variants += _variants_of(canonical, name, card, in_person, rng)
        out[split] = _drop_shared_names(variants)
        dropped += len(variants) - len(out[split])
    return VariantSet(out["dev"], out["test"], dropped)


def _drop_shared_names(variants: list[Variant]) -> list[Variant]:
    """Remove names that two merchants share ("Restaurant Le Petit" is both the Alep's bank
    name and the Bistro's cut name): no rule can tell them apart by the name alone."""
    owners: dict[str, set[str]] = {}
    for v in variants:
        owners.setdefault(v.name.casefold(), set()).add(v.merchant)
    return [v for v in variants if len(owners[v.name.casefold()]) == 1]
