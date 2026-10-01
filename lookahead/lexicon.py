"""French/English category keywords, used as extra categorizer features (experiment 2).

FROZEN on 2026-10-01, before any run: written from general knowledge, without looking at
the holdout or any real merchant name. A test pins the exact contents; changing them means
a new, logged experiment (DECISIONS.md).

Matching (also frozen): names are compared without case or accents, and the bank's "?"
matches any letter. A keyword of 4+ letters matches the START of a word ("sushi" matches
"Sushishop"); a shorter keyword must match a WHOLE word ("bar", "pub", "spa").
"""

import unicodedata

import numpy as np

LEXICON: dict[str, tuple[str, ...]] = {
    "Restaurants & cafés": (
        "restaurant", "resto", "bistro", "brasserie", "cafe", "coffee", "espresso", "pizza",
        "pizzeria", "sushi", "ramen", "pho", "burger", "grill", "taverne", "tavern", "cantine",
        "boulangerie", "patisserie", "bakery", "bagel", "deli", "diner", "cuisine", "kitchen",
        "shawarma", "poutine", "creperie", "dumpling", "noodle", "taqueria", "thai", "bar",
        "pub", "brewery", "microbrasserie",
    ),
    "Groceries": (
        "epicerie", "marche", "market", "supermarche", "supermarket", "grocery", "fruiterie",
        "boucherie", "butcher", "poissonnerie", "fromagerie", "depanneur", "dep",
        "alimentation", "aliments", "provisions", "foods",
    ),
    "Health & pharmacy": (
        "pharmacie", "pharmacy", "clinique", "clinic", "dentiste", "dental", "dentaire",
        "medical", "medicale", "optometriste", "optometrist", "lunetterie", "physio",
        "sante", "health", "laboratoire", "psychologue",
    ),
    "Personal care": (
        "coiffure", "coiffeur", "barbier", "barber", "salon", "spa", "esthetique",
        "beauty", "beaute", "nails", "ongles", "cosmetique", "cosmetics", "massage",
    ),
    "Transport": (
        "parking", "stationnement", "taxi", "essence", "petro", "station", "gas", "fuel",
        "communauto", "train", "autobus", "bus", "peage", "toll", "car", "auto",
    ),
    "Entertainment": (
        "cinema", "cineplex", "theatre", "theater", "concert", "billet", "billets", "tickets",
        "club", "bowling", "musee", "museum", "jeux", "games", "arcade", "karaoke", "festival",
    ),
    "Shopping": (
        "boutique", "store", "magasin", "shop", "outlet", "mode", "fashion", "vetements",
        "clothing", "chaussures", "shoes", "librairie", "bookstore", "quincaillerie",
        "hardware", "electronique", "electronics", "meubles", "furniture",
    ),
    "Subscriptions": ("abonnement", "subscription", "membership", "premium"),
    "Phone, internet & utilities": (
        "telecom", "mobile", "wireless", "internet", "telephone", "cellulaire", "hydro",
        "energie", "energy", "electricite",
    ),
    "Education": (
        "universite", "university", "college", "cegep", "ecole", "school", "academie",
        "academy", "cours", "tutorat", "tuition",
    ),
    "Gifts & donations": (
        "fondation", "foundation", "charity", "charite", "donation", "don", "eglise",
        "church", "mosquee", "mosque", "synagogue", "temple", "paroisse", "parish",
        "centraide", "croix", "unicef", "fleuriste", "florist", "cadeau", "gift",
    ),
    "Rent & housing": (
        "gestion", "immobilier", "immobiliere", "property", "properties", "apartments",
        "appartements", "logement", "condo", "loyer", "rent", "residence",
    ),
    "Food delivery": ("livraison", "delivery"),
}  # fmt: skip

LEXICON_CATEGORIES: tuple[str, ...] = tuple(LEXICON)
_WHOLE_WORD_BELOW = 4  # keywords shorter than this must match a whole word


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _words(name: str) -> list[str]:
    return "".join(c if c.isalnum() or c == "?" else " " for c in _plain(name)).split()


def _same(word: str, keyword: str) -> bool:
    return len(word) == len(keyword) and all(
        a == b or a == "?" for a, b in zip(word, keyword, strict=True)
    )


def _matches(word: str, keyword: str) -> bool:
    if len(keyword) < _WHOLE_WORD_BELOW:
        return _same(word, keyword)
    return len(word) >= len(keyword) and _same(word[: len(keyword)], keyword)


def lexicon_hits(name: str) -> list[str]:
    """Categories whose keywords appear in `name`, in LEXICON order."""
    words = _words(name)
    return [
        category
        for category, keywords in LEXICON.items()
        if any(_matches(w, k) for w in words for k in keywords)
    ]


def lexicon_matrix(names: object) -> np.ndarray:
    """One 0/1 column per lexicon category, one row per name (a scikit-learn feature)."""
    values = list(np.asarray(names).ravel())
    out = np.zeros((len(values), len(LEXICON_CATEGORIES)))
    for i, name in enumerate(values):
        for category in lexicon_hits(str(name)):
            out[i, LEXICON_CATEGORIES.index(category)] = 1.0
    return out
