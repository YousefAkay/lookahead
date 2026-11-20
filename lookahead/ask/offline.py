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
    "Income": ("income", "salary", "pay", "paycheque", "paycheck", "revenu", "revenus",
               "salaire", "paie"),
}  # fmt: skip
_CATEGORY_OF = sorted(((w, c) for c, ws in CATEGORY_WORDS.items() for w in ws),
                      key=lambda x: -len(x[0]))  # fmt: skip

ADVICE = (
    r"\bshould i\b", r"\bshould we\b", r"\bis it (a )?(good|bad|smart|wise)\b", r"\bworth it\b",
    r"\brecommend", r"\badvice\b", r"\bdevrais\b", r"\bdois je\b", r"\bconseil", r"\bvaut il\b",
    r"\bbonne idee\b",
)  # fmt: skip
NOT_IN_DATA = (
    r"\bstocks?\b", r"\bcrypto", r"\bbitcoin\b", r"\binvest", r"\bmortgage rates?\b",
    r"\binflation\b", r"\bcredit score\b", r"\bnet worth\b", r"\bactions?\b en bourse",
    r"\bbourse\b", r"\bcote de credit\b", r"\bwhat time\b", r"\bweather\b", r"\bmeteo\b",
)  # fmt: skip


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
        (r"\bthis month\b|\bce mois( ci)?\b|\bdepuis le debut du mois\b", {"kind": "this_month"}),
        (r"\b(last|past|previous) week\b|\bla semaine (derniere|passee)\b",
         {"kind": "last_week"}),
        (r"\bthis week\b|\bcette semaine\b", {"kind": "this_week"}),
        (r"\b(last|past|previous) year\b|\bl an (dernier|passe)\b|\bl annee (derniere|passee)\b",
         {"kind": "last_year"}),
        (r"\bthis year\b|\bcette annee\b|\bdepuis le debut de l annee\b", {"kind": "this_year"}),
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
    """A dollar amount in the question as written ("$200", "200 $", "200 dollars")."""
    q = raw.replace("\u00a0", " ")
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


SPEND = (
    r"\b(spen[dt]|spending|cost|paid|pay|depense[sr]?|depense|paye|coute|how many times|"
    r"combien de fois|how often|largest|biggest|plus grosse?|plus gros|earn(ed)?|receive[d]?|"
    r"got paid|made|gagne|recu|average|moyenne|how much|combien)\b"
)
INCOME = r"\b(earn(ed)?|receive[d]?|got paid|made|gagne|recu)\b"


def _spending(q: str, raw: str) -> dict[str, Any] | None:
    if not re.search(SPEND, q):
        return None
    period, rest = period_of(q)
    spend = re.search(SPEND, rest)
    category, merchant = subject_of(rest[spend.end() :] if spend else rest)
    if category is None and merchant is None:
        category = category_of(rest)
    income = re.search(INCOME, q)
    if category is None and merchant is None and not income:
        if not re.search(
            r"\b(in total|altogether|en tout|overall|total|everything|largest|"
            r"biggest|plus grosse?|plus gros)\b",
            q,
        ):
            return None
    rows = _rows(category, merchant, period, "in" if income else "out")
    if income and category is None and merchant is None:
        rows["kinds"] = ["income"]
    measure = "sum"
    if re.search(r"\bhow many times\b|\bcombien de fois\b|\bhow often\b", q):
        measure = "count"
    elif re.search(r"\b(largest|biggest|plus grosse?|plus gros)\b", q):
        measure = "max"
    elif re.search(r"\b(average|moyenne)\b", q):
        by = "week" if re.search(r"\b(week|semaine)\b", q) else "month"
        return {"steps": [rows, {"op": "group", "name": "by_period", "source": "rows", "by": by},
                          {"op": "aggregate", "name": "answer", "source": "by_period",
                           "measure": "average"}]}  # fmt: skip
    return {"steps": [rows, {"op": "aggregate", "name": "answer", "source": "rows",
                             "measure": measure}]}  # fmt: skip


def _top(q: str, raw: str) -> dict[str, Any] | None:
    m = re.search(rf"\btop {NUM}?|\b(biggest|largest|main|principales?|plus grosses?) "
                  r"(spending )?(categories|categorie|categories de depenses|merchants|"
                  r"marchands|commerces)\b|\bwhere did (most of )?my money go\b|"
                  r"\bou est alle mon argent\b", q)  # fmt: skip
    if not m:
        return None
    n_match = re.search(rf"\btop {NUM}\b|\b{NUM} (?:biggest|largest|principales?|top)\b", q)
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


def _forecast(q: str, raw: str) -> dict[str, Any] | None:
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
        until = day_of(q)
        if until is None and re.search(r"\bthis month\b|\bce mois\b", q):
            until = {"kind": "end_of_month"}
        if until is not None:
            step["until"] = until
        return {"steps": [step]}
    if re.search(r"\b(lowest|minimum|plus bas)\b", q) and re.search(r"\b(balance|solde)\b", q):
        step = {"op": "forecast", "name": "answer", "measure": "lowest"}
        until = day_of(q)
        if until is not None:
            step["until"] = until
        return {"steps": [step]}
    if re.search(r"\b(balance|solde)\b", q):
        day = day_of(q)
        if day is None:
            return None
        return {"steps": [{"op": "forecast", "name": "answer", "measure": "balance_on",
                           "on": day}]}  # fmt: skip
    return None


def _budget(q: str, raw: str) -> dict[str, Any] | None:
    if not re.search(r"\bbudgets?\b", q):
        return None
    step: dict[str, Any] = {"op": "budget", "name": "answer"}
    category = category_of(q)
    if category is not None:
        step["category"] = category
    return {"steps": [step]}


def _safe(q: str, raw: str) -> dict[str, Any] | None:
    if re.search(r"\bsafe to spend\b|\bhow much can i (safely )?spend\b|\bcan i afford to "
                 r"spend\b|\bcombien (puis je|je peux) depenser\b|\bdepenser sans risque\b",
                 q):  # fmt: skip
        return {"steps": [{"op": "safe_to_spend", "name": "answer"}]}
    return None


def _recurring(q: str, raw: str) -> dict[str, Any] | None:
    if re.search(r"\b(recurring|regular|repeating) (payments?|bills?|charges?)\b|"
                 r"\bmy (subscriptions|bills)\b|\bpaiements? recurrents?\b|\bmes abonnements\b|"
                 r"\bprelevements?\b", q):  # fmt: skip
        if re.search(r"\b(how much|combien|total)\b", q):
            return None
        return {"steps": [{"op": "recurring", "name": "answer"}]}
    return None


def parse_question(question: str) -> dict[str, Any] | None:
    """A plan as JSON-like data, or None when no shape fits."""
    q = plain(question)
    if not q:
        return None
    if any(re.search(p, q) for p in ADVICE):
        return {"steps": [{"op": "cannot_answer", "reason": "advice"}]}
    if any(re.search(p, q) for p in NOT_IN_DATA):
        return {"steps": [{"op": "cannot_answer", "reason": "not_in_data"}]}
    for shape in (_safe, _budget, _recurring, _forecast, _top, _spending):
        plan = shape(q, question)
        if plan is not None:
            return plan
    return None
