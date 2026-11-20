"""The offline parser: simple questions in English or French turned into a plan with keyword
patterns, no LLM (spec docs/specs/2026-11-20-ask.md, "LLM off"). It builds the same plans the
LLM writes, so the answers and 'How I worked it out' look the same; it is also the baseline the
LLM must beat by 20 points.

The shapes it knows:
- spending (or income) in a category or at a merchant over a period: total, average a month or a
  week, how many times, the largest one;
- top N categories or merchants over a period;
- the balance on a future day;
- the chance of going below the buffer, zero or an amount; the lowest balance;
- safe to spend;
- a budget (one category or all);
- recurring payments;
- advice and investments are refused with the reason.

Anything else: None, and the page says "I can't answer that yet" with examples.
"""

import re
from collections.abc import Iterable
from typing import Any

from lookahead.ask.match import plain

# --- Words ---

NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "un": 1, "une": 1, "deux": 2, "trois": 3,
    "quatre": 4, "cinq": 5, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "douze": 12,
}  # fmt: skip
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
    "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
}  # fmt: skip
# Words for each category, English and French, longest first when matched.
CATEGORY_WORDS: dict[str, tuple[str, ...]] = {
    "Groceries": ("groceries", "grocery", "grocery store", "supermarket", "epicerie",
                  "epiceries", "marche", "food shopping"),
    "Restaurants & cafés": ("restaurants", "restaurant", "eating out", "dining out", "dining",
                            "cafes", "cafe", "coffee", "coffee shops", "restos", "resto",
                            "cafes et restaurants"),
    "Food delivery": ("food delivery", "delivery", "takeout", "take out", "livraison",
                      "livraison de repas"),
    "Transport": ("transport", "transportation", "transit", "public transit", "bus", "metro",
                  "taxi", "gas", "essence", "transports"),
    "Phone, internet & utilities": ("phone", "internet", "utilities", "hydro", "electricity",
                                    "phone bill", "cell", "telephone", "electricite"),
    "Subscriptions": ("subscriptions", "subscription", "streaming", "abonnements",
                      "abonnement"),
    "Shopping": ("shopping", "clothes", "clothing", "magasinage", "vetements"),
    "Health & pharmacy": ("health", "pharmacy", "pharmacie", "sante", "medication",
                          "medicaments"),
    "Personal care": ("personal care", "haircut", "haircuts", "soins personnels", "coiffure"),
    "Education": ("education", "school", "tuition", "textbooks", "frais de scolarite",
                  "scolarite", "etudes", "universite"),
    "Entertainment": ("entertainment", "fun", "movies", "games", "divertissement", "cinema",
                      "loisirs"),
    "Gifts & donations": ("gifts", "gift", "donations", "charity", "cadeaux", "dons"),
    "Transfers": ("transfers", "virements"),
    "Fees & interest": ("bank fees", "fees", "interest", "frais bancaires", "frais",
                        "interets"),
    "Cash": ("cash", "atm", "withdrawals", "retraits", "argent comptant"),
    "Rent & housing": ("rent", "housing", "loyer", "logement"),
    "Income": ("income", "salary", "paycheque", "paycheck", "revenu", "revenus", "salaire",
               "paie"),
}  # fmt: skip
_CATEGORY_OF = sorted(((w, c) for c, ws in CATEGORY_WORDS.items() for w in ws),
                      key=lambda x: -len(x[0]))  # fmt: skip

ADVICE = (
    r"\bshould i\b", r"\bshould we\b", r"\bis it (a )?(good|bad|smart|wise)\b", r"\bworth it\b",
    r"\brecommend", r"\badvice\b", r"\bdevrais\b", r"\bdois je\b",
    r"\b(un|des|tes|vos|ton|votre) conseils?\b", r"\bme conseill", r"\bvaut il\b",
    r"\bbonne idee\b", r"\btoo much\b", r"\btrop\b", r"\bsmartest\b", r"\bbest way\b",
    r"\bmeilleure facon\b", r"\bcan i afford\b", r"\bai je les moyens\b",
)  # fmt: skip
NOT_IN_DATA = (
    r"\bstocks?\b", r"\bcrypto", r"\bbitcoin\b", r"\binvest", r"\bmortgage rates?\b",
    r"\binflation\b", r"\bcredit score\b", r"\bnet worth\b", r"\bactions?\b en bourse",
    r"\bbourse\b", r"\bcote de credit\b", r"\bwhat time\b", r"\bweather\b", r"\bmeteo\b",
    r"\bcredit card\b", r"\bcarte de credit\b", r"\bowe\b", r"\bdebt\b", r"\bdette\b",
    r"\broommate\b", r"\bcoloc", r"\bmy (partner|friend|wife|husband|mom|dad)s?\b",
    r"\btfsa\b", r"\bcelis?\b", r"\brrsp\b", r"\breer\b", r"\bother bank\b",
    r"\bautre banque\b",
)  # fmt: skip
# Shapes the offline parser does not do: it says "I can't answer that yet" rather than guess.
NOT_OFFLINE = (
    r"\bwhat if\b", r"\bet si\b", r"\bsuppose\b", r"\bimagine\b", r"\bthan\b", r"\bvs\b",
    r"\bversus\b", r"\bcompar", r"\bup or down\b", r"\bhigher\b", r"\blower\b", r"\bshare\b",
    r"\bpercent", r"\bpourcent", r"\bqu en\b", r"\bque\b", r"\bde plus\b", r"\bde moins\b",
    r"\bpar rapport\b", r"\bsince\b", r"\bdepuis\b", r"\bwill i spend\b", r"\bnext year\b",
    r"\ban prochain\b", r"\bannee prochaine\b", r"\bpoem\b", r"\bjoke\b", r"\bstory\b",
    r"\btranslate\b", r"\btraduire\b", r"\bsend \$|\bsend \d", r"\benvoie", r"\bpay off\b",
)  # fmt: skip
VAGUE_SUBJECTS = frozenset("that it this those them ca cela ceci".split())
VAGUE_PERIODS = r"\b(recently|lately|dernierement|recemment)\b"
VAGUE_WORDS = r"\b(food|nourriture|bouffe)\b"  # groceries, restaurants or delivery: which one?


def _num(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    return NUMBERS.get(word)


NUM = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|twelve|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|douze)"  # noqa: E501
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))


# --- Periods and days ---


def period_of(q: str) -> tuple[dict[str, Any] | None, str]:
    """A past period in the question, and the question without it."""
    rules: list[tuple[str, Any]] = [
        (r"\b(last|past|previous) month\b|\ble mois (dernier|passe|precedent)\b",
         {"kind": "last_month"}),
        (r"\bthis month\b|\bce mois( ci)?\b", {"kind": "this_month"}),
        (r"\b(last|past|previous) week\b|\bla semaine (derniere|passee)\b",
         {"kind": "last_week"}),
        (r"\bthis week\b|\bcette semaine\b", {"kind": "this_week"}),
        (r"\b(last|past|previous) year\b|\bl an (dernier|passe)\b|\bl annee (derniere|passee)\b",
         {"kind": "last_year"}),
        (r"\bthis (year|yr)\b|\bcette annee\b", {"kind": "this_year"}),
        (r"\btoday\b|\baujourd hui\b", {"kind": "today"}),
        (r"\byesterday\b|\bhier\b", {"kind": "yesterday"}),
    ]  # fmt: skip
    for pattern, period in rules:
        m = re.search(pattern, q)
        if m:
            return period, q[: m.start()] + q[m.end() :]
    m = re.search(rf"\b(?:last|past) {NUM} (day|week|month)s?\b", q) or re.search(
        rf"\b(?:les|ces) {NUM} (jour|semaine|mois)s? (?:derniers|dernieres)\b", q
    )
    if m:
        n = _num(m.group(1)) or 1
        unit = {"day": "days", "jour": "days", "week": "weeks", "semaine": "weeks",
                "month": "months", "mois": "months"}[m.group(2)]  # fmt: skip
        return {"kind": f"last_n_{unit}", "n": n}, q[: m.start()] + q[m.end() :]
    m = re.search(rf"\b(?:in|en|during|for|pour|of|de|d)? ?({MONTH_RE})(?: (\d{{4}}))?\b", q)
    if m and not (m.group(1) == "may" and not m.group(2) and not re.search(r"\bin may\b", q)):
        month = MONTHS[m.group(1)]
        period = ({"kind": "month", "year": int(m.group(2)), "month": month} if m.group(2)
                  else {"kind": "named_month", "month": month})  # fmt: skip
        return period, q[: m.start()] + q[m.end() :]
    m = re.search(r"\b(?:in|en|for|pour|during) (20\d\d)\b", q)
    if m:
        return {"kind": "year", "year": int(m.group(1))}, q[: m.start()] + q[m.end() :]
    return None, q


def day_of(q: str) -> dict[str, Any] | None:
    """A future day in the question."""
    if re.search(r"\btomorrow\b|\bdemain\b", q):
        return {"kind": "tomorrow"}
    m = re.search(rf"\b(?:in|within|dans|over) (?:the next |the coming |les prochains? )?{NUM} "
                  r"(day|week|jour|semaine)s?\b", q) or re.search(
        rf"\b(?:next|prochains?) {NUM} (day|week|jour|semaine)s?\b", q)  # fmt: skip
    if m:
        n = (_num(m.group(1)) or 1) * (7 if m.group(2) in ("week", "semaine") else 1)
        return {"kind": "in_days", "n": n}
    if re.search(r"\bnext week\b|\bla semaine prochaine\b", q):
        return {"kind": "in_days", "n": 7}
    if re.search(r"\bend of (the |this )?month\b|\bfin (du|de ce) mois\b|\bmonth end\b", q):
        return {"kind": "end_of_month"}
    if re.search(r"\bend of (the |this )?week\b|\bfin de (la )?semaine\b|\bweekend\b", q):
        return {"kind": "end_of_week"}
    m = re.search(rf"\b(?:on )?({MONTH_RE}) (\d{{1,2}})(?:st|nd|rd|th)?\b", q) or re.search(
        rf"\b(?:le )?(\d{{1,2}}) ({MONTH_RE})\b", q
    )
    if m:
        a, b = m.group(1), m.group(2)
        month, day = (MONTHS[a], int(b)) if a in MONTHS else (MONTHS[b], int(a))
        if 1 <= day <= 31:
            return {"kind": "named_date", "month": month, "day": day}
    m = re.search(r"\b(?:on the|the|le) (\d{1,2})(?:st|nd|rd|th|er)?\b", q)
    if m and 1 <= int(m.group(1)) <= 31:
        return {"kind": "day_of_month", "day": int(m.group(1))}
    return None


def dollars_of(raw: str) -> float | None:
    """A dollar amount in the question as written ("$200", "$1,000", "200 $", "12,50 $")."""
    q = re.sub(r"(?<=\d)[ \u00a0,](?=\d{3}\b)", "", raw)  # thousands separators
    m = re.search(r"\$ ?(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?) ?(?:\$|dollars?\b)", q)
    if not m:
        return None
    return float((m.group(1) or m.group(2)).replace(",", "."))


# --- Subjects ---


def category_of(text: str) -> str | None:
    for word, category in _CATEGORY_OF:
        if re.search(rf"\b{re.escape(word)}\b", text):
            return category
    return None


EDGE_WORDS = frozenset(
    "the my a an le la les mon ma mes du de des d l au aux a total money argent spending spend "
    "spent purchases purchase payments payment depenses depense achats achat so far overall "
    "per month a month monthly par mois per week a week weekly par semaine on average en "
    "moyenne average moyenne budget in altogether en tout jusqu ici did i do have was were "
    "for on at ai je j".split()
)
WORD_CATEGORIES = {w: c for w, c in _CATEGORY_OF}


def _trim(text: str) -> str:
    """The phrase without filler words at either end (inner words stay, so the phrase is still
    a piece of the question as I wrote it)."""
    words = text.split()
    while words and words[0] in EDGE_WORDS:
        words.pop(0)
    while words and words[-1] in EDGE_WORDS:
        words.pop()
    return " ".join(words)


def subject_of(rest: str) -> tuple[str | None, str | None]:
    """(category, merchant phrase) from the words after 'at / chez / on / en / ...'. A phrase that
    is exactly a category word is that category; after 'at' or 'chez' anything else is a
    merchant; after 'on', 'in', 'en' or 'pour' a category word anywhere wins."""
    m = re.search(r"\b(at|to|chez|from|with|au|aux|on|in|for|en|pour|sur|dans)\b (.+)$", rest)
    if not m:
        return None, None
    phrase = _trim(m.group(2))
    if not phrase:
        return None, None
    if phrase in WORD_CATEGORIES:
        return WORD_CATEGORIES[phrase], None
    if m.group(1) in ("at", "to", "chez", "from", "with"):
        return None, phrase
    category = category_of(phrase)
    return (category, None) if category is not None else (None, phrase)


# --- Merchants already in my data ---

# Words that say nothing about which merchant is meant (or are everyday English or French).
NAME_STOPWORDS = frozenset(
    "plus prime premium bank banque inc ltd ltee store stores shop market marche group groupe "
    "student etudiant health sante insurance assurance the and pour from with more moins "
    "montreal canada quebec plateau".split()
)
# Words a question may have besides its subject and period. Anything else, with no subject found,
# means a name the parser does not know (a typo): it says it can't answer rather than guess.
FUNCTION_WORDS = frozenset(
    "how much many what whats did do does i me my mine was were is are be the a an in on at to "
    "for of this that last past so far total in total altogether overall everything all spend "
    "spent spending cost costs paid pay money purchases purchase transactions times often "
    "largest biggest smallest average monthly weekly per month week year yr day days months "
    "weeks the s ve have has get got earned earn received receive made income go went "
    "combien j ai je de du des le la les mon ma mes en au aux ce cet cette ci mois annee "
    "semaine depense depenses depenser paye coute fois plus grosse gros petite moyenne par "
    "total tout dernier derniere passe passee jour jours by each show break down breakdown "
    "category categories categorie categories merchant merchants store stores order orders "
    "expense expenses spending's whats s par chaque".split()
)


def known_merchant(q: str, names: Iterable[str]) -> str | None:
    """A merchant of my data named in the question (offline only: nothing is sent): a full name,
    or a word of 4 letters or more that only one name has. Longest first."""
    found = known_merchant_name(q, names)
    return found[0] if found else None


def known_merchant_name(q: str, names: Iterable[str]) -> tuple[str, str] | None:
    """(the phrase as found in the question, the merchant's full name made plain)."""
    named = {plain(n) for n in names if plain(n)}
    found = [n for n in named if re.search(rf"\b{re.escape(n)}\b", q)]
    if found:
        best = max(found, key=len)
        return best, best
    words: dict[str, set[str]] = {}
    for n in named:
        for w in n.split():
            if (len(w) >= 4 and w not in WORD_CATEGORIES and w not in EDGE_WORDS
                    and w not in NAME_STOPWORDS):  # fmt: skip
                words.setdefault(w, set()).add(n)
    unique = [w for w, owners in words.items() if len(owners) == 1 and re.search(rf"\b{w}\b", q)]
    if not unique:
        return None
    word = max(unique, key=len)
    return word, next(iter(words[word]))


# --- Plans ---


def _rows(category: str | None, merchant: str | None, period: dict[str, Any] | None,
          direction: str = "out") -> dict[str, Any]:  # fmt: skip
    step: dict[str, Any] = {"op": "rows", "name": "rows", "direction": direction}
    if category is not None:
        step["categories"] = [category]
        if category == "Income":
            step["direction"] = "in"
    if merchant is not None:
        step["merchants"] = [merchant]
    if period is not None:
        step["period"] = period
    return step


def _ask(about: str) -> dict[str, Any]:
    return {"steps": [{"op": "clarify", "about": about}]}


SPEND = (
    r"\b(spen[dt]|spending|cost|paid|pay|depense[sr]?|depense|paye|coute|how many times|"
    r"combien de fois|how often|largest|biggest|plus grosse?|plus gros|smallest|plus petite?|"
    r"earn(ed)?|receive[d]?|got paid|get paid|made|gagne|recu|average|moyenne|how much|combien|"
    r"in one go|charged?|how many|went to)\b"
)
INCOME = (r"\b(earn(ed)?|receive[d]?|got paid|get paid|made|gagne|recu|income|revenus?|salaire|"
          r"did i get|i got)\b")  # fmt: skip
MAX_WORDS = r"\b(largest|biggest|plus grosse?|plus gros|most .* in one go|in one go)\b"
MIN_WORDS = r"\b(smallest|plus petite?)\b"
BY = {
    "month": "month",
    "mois": "month",
    "week": "week",
    "semaine": "week",
    "category": "category",
    "categorie": "category",
    "merchant": "merchant",
    "store": "merchant",
    "commerce": "merchant",
}
GROUP = (r"\b(?:by|each|per|par|chaque) (month|mois|week|semaine|category|categorie|merchant|"
         r"store|commerce)s?\b|\bbreak ?down\b")  # fmt: skip


def _subject(q: str, rest: str, names: Iterable[str]) -> tuple[str | None, str | None]:
    """(category, merchant phrase). A merchant of my data named in the question wins, with a
    category word said beside it ("groceries at Maxi")."""
    known = known_merchant_name(rest, names)
    if known is not None:
        phrase, full = known
        # words of the merchant's name (and their plurals) are not a category ("e-Transfers")
        left = " ".join(w for w in rest.split()
                        if not any(w.startswith(n) for n in full.split()))  # fmt: skip
        return category_of(left), phrase
    spend = re.search(SPEND, rest)
    category, merchant = subject_of(rest[spend.end() :] if spend else rest)
    if category is None and merchant is None:
        category = category_of(rest)
    return category, merchant


def _spending(q: str, raw: str, names: Iterable[str]) -> dict[str, Any] | None:
    if not re.search(SPEND, q) and "$" not in raw:
        return None
    if re.search(VAGUE_PERIODS, q):
        return _ask("period")
    if re.search(VAGUE_WORDS, q) and category_of(q) is None:
        return _ask("which_one")
    period, rest = period_of(q)
    category, merchant = _subject(q, rest, names)
    if merchant is not None and merchant in VAGUE_SUBJECTS:
        return _ask("subject")
    if category is None and merchant is None and re.search(r"\b(it|that|ca)\b", q):
        return _ask("subject")
    income = re.search(INCOME, q) and category in (None, "Income") and merchant is None
    rows = _rows(category, merchant, period, "in" if income else "out")
    if income and category is None:
        rows["kinds"] = ["income"]
    if category is None and merchant is None and not income:
        unknown = [w for w in rest.split() if w not in FUNCTION_WORDS and not w.isdigit()]
        if unknown:
            return None  # a word it does not know, likely a name: never answered for everything
    if category is None and merchant is None and not income and period is None:
        if not re.search(r"\b(in total|altogether|en tout|overall|total|everything|average|"
                         r"moyenne)\b|" + MAX_WORDS + "|" + MIN_WORDS, q):  # fmt: skip
            return None
    g = re.search(GROUP, q)
    if re.search(r"\b(average|moyenne)\b", q):
        by = "week" if re.search(r"\b(week|weekly|semaine)\b", q) else "month"
        if not re.search(r"\b(month|monthly|mois|week|weekly|semaine)\b", q):
            return {"steps": [rows, {"op": "aggregate", "name": "answer", "source": "rows",
                                     "measure": "average"}]}  # fmt: skip
        return {"steps": [rows, {"op": "group", "name": "by_period", "source": "rows", "by": by},
                          {"op": "aggregate", "name": "answer", "source": "by_period",
                           "measure": "average"}]}  # fmt: skip
    which = re.search(r"\b(which|quel|quelle) (month|day|week|mois|jour|semaine)\b", q)
    if which or re.search(r"\bday of the week\b|\bjour de la semaine\b", q):
        unit = which.group(2) if which else "day"
        by = {"month": "month", "mois": "month", "week": "week", "semaine": "week"}.get(
            unit, "weekday"
        )
        order = "lowest" if re.search(r"\b(least|moins)\b", q) else "highest"
        return {"steps": [rows, {"op": "group", "name": "by_" + by, "source": "rows", "by": by},
                          {"op": "top", "name": "answer", "source": "by_" + by, "n": 1,
                           "order": order}]}  # fmt: skip
    if g:
        by = BY.get(g.group(1) or "month", "month")
        measure = "count" if re.search(r"\bhow many\b|\bcombien de\b", q) else "sum"
        return {"steps": [rows, {"op": "group", "name": "answer", "source": "rows", "by": by,
                                 "measure": measure}]}  # fmt: skip
    measure = "sum"
    if re.search(r"\bhow many\b|\bcombien de fois\b|\bhow often\b", q):
        measure = "count"
    elif re.search(MAX_WORDS, q):
        measure = "max"
    elif re.search(MIN_WORDS, q):
        measure = "min"
    return {"steps": [rows, {"op": "aggregate", "name": "answer", "source": "rows",
                             "measure": measure}]}  # fmt: skip


def _list(q: str, raw: str, names: Iterable[str]) -> dict[str, Any] | None:
    listing = re.search(r"\b(list|show)\b", q) and re.search(
        r"\b(purchases|transactions|orders|payments|achats|commandes)\b", q
    )
    several = re.search(rf"\b{NUM} (largest|biggest|smallest|latest|last|plus gros)\b", q)
    if not (listing or several):
        return None
    period, rest = period_of(q)
    category, merchant = _subject(q, rest, names)
    rows = _rows(category, merchant, period)
    amount = dollars_of(raw)
    if amount is not None and re.search(r"\b(under|below|less than|moins de)\b", q):
        rows["max_dollars"] = amount
    elif amount is not None and re.search(r"\b(over|above|more than|plus de)\b", q):
        rows["min_dollars"] = amount
    order = "latest"
    if re.search(r"\b(largest|biggest|plus gros)\b", q):
        order = "largest"
    elif re.search(r"\bsmallest\b", q):
        order = "smallest"
    limit = 20
    n = re.search(rf"\b{NUM}\b", q)
    if several or (n and n.group(1).isdigit() and int(n.group(1)) <= 200 and "$" not in raw):
        limit = _num((several or n).group(1)) or 20  # type: ignore[union-attr]
    return {"steps": [rows, {"op": "list", "name": "answer", "source": "rows", "order": order,
                             "limit": max(1, min(limit, 200))}]}  # fmt: skip


def _count_over(q: str, raw: str, names: Iterable[str]) -> dict[str, Any] | None:
    """How many purchases over / under an amount."""
    if not re.search(r"\bhow many\b|\bcombien de\b", q):
        return None
    amount = dollars_of(raw)
    if amount is None:
        return None
    period, rest = period_of(q)
    category, merchant = _subject(q, rest, names)
    rows = _rows(category, merchant, period)
    if re.search(r"\b(under|below|less than|moins de)\b", q):
        rows["max_dollars"] = amount
    else:
        rows["min_dollars"] = amount
    return {"steps": [rows, {"op": "aggregate", "name": "answer", "source": "rows",
                             "measure": "count"}]}  # fmt: skip


def _top(q: str, raw: str, names: Iterable[str]) -> dict[str, Any] | None:
    m = re.search(rf"\btop {NUM}?|\b(biggest|largest|main|principales?|plus grosses?|plus gros) "
                  r"(spending )?(categories|categorie|categories de depenses|merchants|"
                  r"marchands|commerces)\b|\bwhere did (most of )?my money go\b|"
                  r"\bou est alle mon argent\b|\b(merchants|stores) did i spend the most\b",
                  q)  # fmt: skip
    if not m:
        return None
    n_match = re.search(rf"\btop {NUM}\b|\b{NUM} (?:biggest|largest|principales?|top|plus "
                        r"gros|merchants|stores)\b", q)  # fmt: skip
    n = 5
    if n_match:
        n = _num(next(g for g in n_match.groups() if g)) or 5
    by = "merchant" if re.search(r"\b(merchants?|marchands?|commerces?|stores?|shops?)\b", q) \
        else "category"  # fmt: skip
    period, _ = period_of(q)
    return {"steps": [
        _rows(None, None, period),
        {"op": "group", "name": "by_" + by, "source": "rows", "by": by},
        {"op": "top", "name": "answer", "source": "by_" + by, "n": min(n, 25)},
    ]}  # fmt: skip


def _window(q: str) -> dict[str, Any] | None:
    """The end of a forecast window; the next 30 days or the next month is the whole forecast."""
    if re.search(r"\bnext (30 days|month)\b|\b(30|trente) (prochains )?jours\b|\bmois prochain\b",
                 q):  # fmt: skip
        return None
    until = day_of(q)
    if until is None and re.search(r"\bthis month\b|\bce mois\b", q):
        until = {"kind": "end_of_month"}
    return until


def _forecast(q: str, raw: str, names: Iterable[str]) -> dict[str, Any] | None:
    dip = re.search(r"\b(chance|risk|risque|likely|probabilit[ey]|odds|will i (go|dip|fall|drop)"
                    r"|am i going to (go|dip)|vais je (passer|tomber))\b", q)  # fmt: skip
    below = re.search(r"\b(below|under|sous|en dessous|negative|overdraft|negatif|dip|short)\b", q)
    if dip and below:
        step: dict[str, Any] = {"op": "forecast", "name": "answer", "measure": "chance_below"}
        if re.search(r"\b(zero|0|negative|overdraft|negatif|zero dollars?)\b", q):
            step["below"] = {"dollars": 0}
        else:
            amount = dollars_of(raw)
            if amount:
                step["below"] = {"dollars": amount}
        until = _window(q)
        if until is not None:
            step["until"] = until
        return {"steps": [step]}
    if re.search(r"\b(lowest|minimum|plus bas|how low)\b", q) and re.search(
        r"\b(balance|solde)\b", q
    ):
        step = {"op": "forecast", "name": "answer", "measure": "lowest"}
        until = _window(q)
        if until is not None:
            step["until"] = until
        return {"steps": [step]}
    if re.search(r"\b(balance|solde|will i have|have left|aurai)\b", q):
        day = day_of(q)
        if day is None:
            return None
        return {"steps": [{"op": "forecast", "name": "answer", "measure": "balance_on",
                           "on": day}]}  # fmt: skip
    return None


def _budget(q: str, raw: str, names: Iterable[str]) -> dict[str, Any] | None:
    if not re.search(r"\bbudgets?\b", q):
        return None
    step: dict[str, Any] = {"op": "budget", "name": "answer"}
    category = category_of(q)
    if category is not None:
        step["category"] = category
    return {"steps": [step]}


def _safe(q: str, raw: str, names: Iterable[str]) -> dict[str, Any] | None:
    if re.search(r"\bsafe(ly)? to spend\b|\bhow much can i (safely )?spend\b|\bcombien (puis je|"
                 r"je peux) depenser\b|\bdepenser sans risque\b", q):  # fmt: skip
        return {"steps": [{"op": "safe_to_spend", "name": "answer"}]}
    return None


def _recurring(q: str, raw: str, names: Iterable[str]) -> dict[str, Any] | None:
    listing = re.search(r"\b(recurring|regular|repeating) (payments?|bills?|charges?|deposits?)\b|"
                        r"\bmy (subscriptions|bills)\b|\bsubscriptions do i have\b|"
                        r"\bpaiements? recurrents?\b|\bmes abonnements\b|\bprelevements?\b",
                        q)  # fmt: skip
    if listing:
        if re.search(r"\b(how much|combien|total)\b", q):
            return None
        direction = "in" if re.search(r"\b(deposits?|income|revenus?)\b", q) else "out"
        return {"steps": [{"op": "recurring", "name": "answer", "direction": direction}]}
    if re.search(r"\b(next|when|usually|usual|prochain|quand)\b", q):
        merchant = known_merchant(q, names)
        if merchant is not None:
            return {"steps": [{"op": "recurring", "name": "answer", "find": merchant}]}
    return None


def parse_question(question: str, merchants: Iterable[str] = ()) -> dict[str, Any] | None:
    """A plan as JSON-like data, or None when no shape fits. `merchants`: the merchant names in
    my data (offline only, so they may be used; nothing is sent)."""
    q = plain(question)
    names = list(merchants)
    if not q:
        return None
    if any(re.search(p, q) for p in ADVICE):
        return {"steps": [{"op": "cannot_answer", "reason": "advice"}]}
    if any(re.search(p, q) for p in NOT_IN_DATA):
        return {"steps": [{"op": "cannot_answer", "reason": "not_in_data"}]}
    if any(re.search(p, q) for p in NOT_OFFLINE):
        return None
    for shape in (_safe, _budget, _recurring, _forecast, _top, _list, _count_over, _spending):
        plan = shape(q, question, names)
        if plan is not None:
            return plan
    return None
