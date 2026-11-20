"""Ask's development question set: 200 questions on six synthetic development personas, each
with a gold plan (spec docs/specs/2026-11-20-ask.md, "Test set"). Written by Claude Code;
development only (prompt work and checks happen on it; it is never the scored set).

Every question is asked on TODAY (2026-09-15, a Tuesday) about data that ends the day before.
A gold plan is the steps that should run; the expected answer is whatever the gold plan computes
on the persona's database, so an equivalent but different plan also counts as correct.

`expect` is the gold's outcome: "answer", "refused", "clarify" or "not_found". The kinds the
scorer reports come from it: answerable (answer or not_found), should-refuse (refused) and
ambiguous (`ambiguous=True`: the gold asks back, or answers from a written default).

Tags (for coverage): lookup, group, top, compare, multi, typo, informal, forecast, whatif,
budget, safe, recurring, ambiguous, advice, outside, horizon, unsupported.
"""

from dataclasses import dataclass, field
from typing import Any

SAL, FREE, TIGHT = "v2-SAL-01", "v2-FREE-01", "v2-TIGHT-01"
DRENT, DHOME, IFAM = "v2-DRENT-01", "v2-DHOME-01", "v2-IFAM-01"

GRO, RES, DEL, SHOP = "Groceries", "Restaurants & cafés", "Food delivery", "Shopping"
TRA, SUB, HEA = "Transport", "Subscriptions", "Health & pharmacy"
UTI = "Phone, internet & utilities"
RENT, INC, CASH, FEES = "Rent & housing", "Income", "Cash", "Fees & interest"

Step = dict[str, Any]


@dataclass(frozen=True)
class Question:
    id: str
    persona: str
    text: str
    steps: tuple[Step, ...]
    expect: str = "answer"
    tags: tuple[str, ...] = ()
    lang: str = "en"
    ambiguous: bool = False
    note: str = field(default="", compare=False)

    @property
    def gold(self) -> dict[str, Any]:
        return {"steps": [dict(s) for s in self.steps]}


# --- Periods and days ---

LM = {"kind": "last_month"}
TM = {"kind": "this_month"}
TY = {"kind": "this_year"}
LY = {"kind": "last_year"}
LW = {"kind": "last_week"}
TW = {"kind": "this_week"}


def ND(n: int) -> dict[str, Any]:  # noqa: N802
    return {"kind": "last_n_days", "n": n}


def NW(n: int) -> dict[str, Any]:  # noqa: N802
    return {"kind": "last_n_weeks", "n": n}


def NM(n: int) -> dict[str, Any]:  # noqa: N802
    return {"kind": "last_n_months", "n": n}


def MON(m: int) -> dict[str, Any]:  # noqa: N802
    return {"kind": "named_month", "month": m}


def MY(y: int, m: int) -> dict[str, Any]:  # noqa: N802
    return {"kind": "month", "year": y, "month": m}


def IN(n: int) -> dict[str, Any]:  # noqa: N802
    return {"kind": "in_days", "n": n}


EOM = {"kind": "end_of_month"}
EOW = {"kind": "end_of_week"}
TMRW = {"kind": "tomorrow"}


def DOM(d: int) -> dict[str, Any]:  # noqa: N802
    return {"kind": "day_of_month", "day": d}


def ON(m: int, d: int) -> dict[str, Any]:  # noqa: N802
    return {"kind": "named_date", "month": m, "day": d}


# --- Blocks ---


def R(name: str = "r", cat: str | list[str] | None = None, m: str | list[str] | None = None,  # noqa: N802
      p: dict[str, Any] | None = None, d: str = "out", k: list[str] | None = None,
      mn: float | None = None, mx: float | None = None) -> Step:  # fmt: skip
    s: Step = {"op": "rows", "name": name, "direction": d}
    if cat:
        s["categories"] = [cat] if isinstance(cat, str) else cat
    if m:
        s["merchants"] = [m] if isinstance(m, str) else m
    if p is not None:
        s["period"] = p
    if k:
        s["kinds"] = k
    if mn is not None:
        s["min_dollars"] = mn
    if mx is not None:
        s["max_dollars"] = mx
    return s


def AGG(measure: str, src: str = "r", name: str = "a") -> Step:  # noqa: N802
    return {"op": "aggregate", "name": name, "source": src, "measure": measure}


def SUM(src: str = "r", name: str = "a") -> Step:  # noqa: N802
    return AGG("sum", src, name)


def CNT(src: str = "r", name: str = "a") -> Step:  # noqa: N802
    return AGG("count", src, name)


def MAX(src: str = "r", name: str = "a") -> Step:  # noqa: N802
    return AGG("max", src, name)


def G(by: str, src: str = "r", name: str = "g", measure: str = "sum") -> Step:  # noqa: N802
    return {"op": "group", "name": name, "source": src, "by": by, "measure": measure}


def AVG_BY(by: str, src: str = "r") -> tuple[Step, Step]:  # noqa: N802
    return G(by, src), AGG("average", "g")


def TOP(n: int, src: str = "g", order: str = "highest", name: str = "t") -> Step:  # noqa: N802
    return {"op": "top", "name": name, "source": src, "n": n, "order": order}


def CMP(a: str, b: str, how: str, name: str = "c") -> Step:  # noqa: N802
    return {"op": "compare", "name": name, "a": a, "b": b, "how": how}


def L(order: str = "latest", limit: int = 20, src: str = "r") -> Step:  # noqa: N802
    return {"op": "list", "name": "l", "source": src, "order": order, "limit": limit}


def FB(day: dict[str, Any]) -> Step:  # noqa: N802
    return {"op": "forecast", "name": "f", "measure": "balance_on", "on": day}


def FL(until: dict[str, Any] | None = None) -> Step:  # noqa: N802
    s: Step = {"op": "forecast", "name": "f", "measure": "lowest"}
    if until:
        s["until"] = until
    return s


def FC(below: float | None = None, until: dict[str, Any] | None = None) -> Step:  # noqa: N802
    s: Step = {"op": "forecast", "name": "f", "measure": "chance_below"}
    if below is not None:
        s["below"] = {"dollars": below}
    if until:
        s["until"] = until
    return s


def WI(*changes: dict[str, Any]) -> Step:  # noqa: N802
    return {"op": "whatif", "name": "w", "changes": list(changes)}


def cat(category: str, direction: str, dollars: float | None = None,
        pct: float | None = None) -> dict[str, Any]:  # fmt: skip
    c: dict[str, Any] = {"kind": "category", "category": category, "direction": direction}
    if dollars is not None:
        c["amount"] = {"dollars": dollars}
    if pct is not None:
        c["percent"] = pct
    return c


def every(direction: str, dollars: float | None = None, pct: float | None = None
          ) -> dict[str, Any]:  # fmt: skip
    c: dict[str, Any] = {"kind": "everyday", "direction": direction}
    if dollars is not None:
        c["amount"] = {"dollars": dollars}
    if pct is not None:
        c["percent"] = pct
    return c


def cancel(target: str) -> dict[str, Any]:
    return {"kind": "cancel", "target": target}


def swap(target: str, dollars: float) -> dict[str, Any]:
    return {"kind": "swap", "target": target, "amount": {"dollars": dollars}}


def once(direction: str, dollars: float, day: dict[str, Any] | None = None) -> dict[str, Any]:
    c: dict[str, Any] = {"kind": "one_off", "direction": direction, "amount": {"dollars": dollars}}
    if day:
        c["day"] = day
    return c


def BUD(category: str | None = None) -> Step:  # noqa: N802
    s: Step = {"op": "budget", "name": "b"}
    if category:
        s["category"] = category
    return s


SAFE: Step = {"op": "safe_to_spend", "name": "s"}


def REC(find: str | None = None, d: str = "out") -> Step:  # noqa: N802
    s: Step = {"op": "recurring", "name": "rec", "direction": d}
    if find:
        s["find"] = find
    return s


def NO(reason: str) -> Step:  # noqa: N802
    return {"op": "cannot_answer", "reason": reason}


def ASK(about: str) -> Step:  # noqa: N802
    return {"op": "clarify", "about": about}


# --- The questions ---

_Q: list[Question] = []


def q(persona: str, text: str, *steps: Step, expect: str = "answer", tags: tuple[str, ...] = (),
      lang: str = "en", ambiguous: bool = False) -> None:  # fmt: skip
    _Q.append(Question(f"dev-{len(_Q) + 1:03d}", persona, text, tuple(steps), expect, tags,
                       lang, ambiguous))  # fmt: skip


LOOK = ("lookup",)
FR = "fr"

# fmt: off

# Category and merchant totals over a period (lookups)
q(SAL, "How much did I spend on groceries last month?", R(cat=GRO, p=LM), SUM(), tags=LOOK)
q(SAL, "What did I spend at IGA in August?", R(m="IGA", p=MON(8)), SUM(), tags=LOOK)
q(SAL, "how much have i spent on restaurants this month", R(cat=RES, p=TM), SUM(), tags=LOOK)
q(SAL, "Total spent at Café Velisaro this year?", R(m="Café Velisaro", p=TY), SUM(), tags=LOOK)
q(SAL, "How much did I spend on transport in the last 30 days?", R(cat=TRA, p=ND(30)), SUM(),
  tags=LOOK)
q(SAL, "How much did Amazon cost me in July?", R(m="Amazon", p=MON(7)), SUM(), tags=LOOK)
q(SAL, "What did I spend at the SAQ last month?", R(m="SAQ", p=LM), SUM(), tags=LOOK)
q(SAL, "How much did I pay Hydro-Québec in the last 6 months?", R(m="Hydro-Québec", p=NM(6)),
  SUM(), tags=LOOK)
q(SAL, "How much have I spent on shopping so far this month?", R(cat=SHOP, p=TM), SUM(),
  tags=LOOK)
q(SAL, "How much did I spend in total last week?", R(p=LW), SUM(), tags=LOOK)
q(SAL, "How much did I earn last month?", R(d="in", k=["income"], p=LM), SUM(), tags=LOOK)
q(SAL, "Combien j'ai dépensé en épicerie le mois dernier ?", R(cat=GRO, p=LM), SUM(), tags=LOOK,
  lang=FR)
q(SAL, "Combien ai-je dépensé chez Pharmaprix cette année ?", R(m="Pharmaprix", p=TY), SUM(),
  tags=LOOK, lang=FR)
q(SAL, "Combien j'ai payé à Vidéotron en juin ?", R(m="Vidéotron", p=MON(6)), SUM(), tags=LOOK,
  lang=FR)
q(FREE, "How much did I spend on groceries in March?", R(cat=GRO, p=MON(3)), SUM(), tags=LOOK)
q(FREE, "What did Bell Internet charge me this year?", R(m="Bell Internet", p=TY), SUM(),
  tags=LOOK)
q(FREE, "How much did I get paid in August?", R(d="in", k=["income"], p=MON(8)), SUM(), tags=LOOK)
q(FREE, "How much did I spend at Winners in 2026?",
  R(m="Winners", p={"kind": "year", "year": 2026}), SUM(), tags=LOOK)
q(FREE, "How much have I spent on restaurants since July 1?",
  R(cat=RES, p={"kind": "since", "start": "2026-07-01"}), SUM(), tags=LOOK)
q(FREE, "How much did the bank charge me in fees this year?", R(cat=FEES, p=TY), SUM(), tags=LOOK)
q(FREE, "Combien j'ai dépensé au restaurant ce mois-ci ?", R(cat=RES, p=TM), SUM(), tags=LOOK,
  lang=FR)
q(FREE, "Combien ai-je reçu en revenus le mois dernier ?", R(d="in", k=["income"], p=LM), SUM(),
  tags=LOOK, lang=FR)
q(TIGHT, "How much has Netflix cost me this year?", R(m="Netflix", p=TY), SUM(), tags=LOOK)
q(TIGHT, "How much did I spend at Concordia Bookstore?", R(m="Concordia Bookstore"), SUM(),
  tags=LOOK)
q(TIGHT, "How much went to Groupe Conseil Plateau this year?", R(m="Groupe Conseil Plateau", p=TY),
  SUM(), tags=LOOK)
q(TIGHT, "Combien j'ai dépensé en transport la semaine dernière ?", R(cat=TRA, p=LW), SUM(),
  tags=LOOK, lang=FR)
q(DRENT, "How much did I spend on food delivery last month?", R(cat=DEL, p=LM), SUM(), tags=LOOK)
q(DRENT, "How much did I spend on Uber Eats this month?", R(m="Uber Eats", p=TM), SUM(), tags=LOOK)
q(DRENT, "What did DoorDash cost me this year?", R(m="DoorDash", p=TY), SUM(), tags=LOOK)
q(DRENT, "How much did I spend at Couche-Tard in the last 2 weeks?", R(m="Couche-Tard", p=NW(2)),
  SUM(), tags=LOOK)
q(DRENT, "How much did I spend on groceries at Maxi last month?", R(cat=GRO, m="Maxi", p=LM),
  SUM(), tags=LOOK)
q(DRENT, "Combien j'ai dépensé en livraison de repas ce mois-ci ?", R(cat=DEL, p=TM), SUM(),
  tags=LOOK, lang=FR)
q(DRENT, "Combien ai-je dépensé chez Super C le mois dernier ?", R(m="Super C", p=LM), SUM(),
  tags=LOOK, lang=FR)
q(DHOME, "How much did I spend at Tim Hortons last month?", R(m="Tim Hortons", p=LM), SUM(),
  tags=LOOK)
q(DHOME, "What did Amazon Prime cost me in the last 6 months?", R(m="Amazon Prime", p=NM(6)),
  SUM(), tags=LOOK)
q(DHOME, "Combien j'ai dépensé chez Couche-Tard cette semaine ?", R(m="Couche-Tard", p=TW), SUM(),
  tags=LOOK, lang=FR)
q(IFAM, "How much did I send to Nadia Haddad this year?", R(m="Nadia Haddad", p=TY), SUM(),
  tags=LOOK)
q(IFAM, "How much did I pay Intact Insurance in the last 3 months?",
  R(m="Intact Insurance", p=NM(3)), SUM(), tags=LOOK)
q(IFAM, "How much did I spend at Concordia University?", R(m="Concordia University"), SUM(),
  tags=LOOK)
q(IFAM, "Combien m'a coûté YouTube Premium cette année ?", R(m="YouTube Premium", p=TY), SUM(),
  tags=LOOK, lang=FR)

# Counts, largest, lists, amount filters
q(SAL, "How many times did I go to Café Velisaro last month?", R(m="Café Velisaro", p=LM), CNT(),
  tags=("lookup", "count"))
q(SAL, "What was my biggest purchase last month?", R(p=LM), MAX(), tags=("lookup",))
q(SAL, "Show me my 5 largest purchases this year", R(p=TY), L("largest", 5), tags=("lookup",))
q(SAL, "List my purchases at Winners this year", R(m="Winners", p=TY), L("latest", 20),
  tags=("lookup",))
q(SAL, "How many purchases over $100 did I make last month?", R(p=LM, mn=100), CNT(),
  tags=("lookup",))
q(FREE, "How many Interac e-Transfers did I get this year?",
  R(m="Interac e-Transfer", p=TY, d="in"), CNT(), tags=("lookup",))
q(FREE, "What's the most I've spent at IGA in one go?", R(m="IGA"), MAX(), tags=("lookup",))
q(TIGHT, "What was my smallest grocery purchase last month?", R(cat=GRO, p=LM),
  AGG("min"), tags=("lookup",))
q(TIGHT, "Show my purchases under $10 last week", R(p=LW, mx=10), L("latest", 20),
  tags=("lookup",))
q(DRENT, "What was my biggest Uber Eats order this year?", R(m="Uber Eats", p=TY), MAX(),
  tags=("lookup",))
q(DHOME, "List my DoorDash orders from last month", R(m="DoorDash", p=LM), L("latest", 20),
  tags=("lookup",))
q(IFAM, "What's the average Uber Eats order this year?", R(m="Uber Eats", p=TY), AGG("average"),
  tags=("lookup",))
q(IFAM, "Combien de fois suis-je allé chez Tim Hortons le mois dernier ?",
  R(m="Tim Hortons", p=LM), CNT(), tags=("lookup",), lang=FR)
q(IFAM, "Ma plus grosse dépense ce mois-ci ?", R(p=TM), MAX(), tags=("lookup",), lang=FR)

# Averages and groupings
q(SAL, "What's my average monthly grocery spending this year?", R(cat=GRO, p=TY), *AVG_BY("month"),
  tags=("group",))
q(SAL, "How much do I spend on restaurants per month on average over the last 6 months?",
  R(cat=RES, p=NM(6)), *AVG_BY("month"), tags=("group",))
q(SAL, "Break down my spending by month this year", R(p=TY), G("month"), tags=("group",))
q(SAL, "What did I spend each week in August at Café Velisaro?", R(m="Café Velisaro", p=MON(8)),
  G("week"), tags=("group",))
q(FREE, "Show my spending by category last month", R(p=LM), G("category"), tags=("group",))
q(FREE, "What's my average weekly grocery bill over the last 12 weeks?", R(cat=GRO, p=NW(12)),
  *AVG_BY("week"), tags=("group",))
q(FREE, "Which day of the week do I spend the most on restaurants?", R(cat=RES), G("weekday"),
  TOP(1), tags=("group",))
q(TIGHT, "How much do I spend on transport a month on average?", R(cat=TRA), *AVG_BY("month"),
  tags=("group",))
q(DRENT, "Average monthly food delivery spending over the last 3 months", R(cat=DEL, p=NM(3)),
  *AVG_BY("month"), tags=("group",))
q(DHOME, "How many Uber Eats orders per month this year?", R(m="Uber Eats", p=TY),
  G("month", measure="count"), tags=("group",))
q(DHOME, "Dépenses par catégorie ce mois-ci", R(p=TM), G("category"), tags=("group",), lang=FR)
q(IFAM, "En moyenne, combien je dépense en épicerie par mois ?", R(cat=GRO), *AVG_BY("month"),
  tags=("group",), lang=FR)

# Top N
q(SAL, "What were my top 3 spending categories last month?", R(p=LM), G("category"), TOP(3),
  tags=("top",))
q(FREE, "Where did most of my money go last month?", R(p=LM), G("category"), TOP(5),
  tags=("top",))
q(FREE, "Which 3 merchants did I spend the most at in August?", R(p=MON(8)), G("merchant"), TOP(3),
  tags=("top",))
q(TIGHT, "Which month did I spend the most on groceries this year?", R(cat=GRO, p=TY), G("month"),
  TOP(1), tags=("top",))
q(DRENT, "What are my 5 biggest categories in the last 3 months?", R(p=NM(3)), G("category"),
  TOP(5), tags=("top",))
q(DRENT, "Which month did I spend the least on food delivery this year?", R(cat=DEL, p=TY),
  G("month"), TOP(1, order="lowest"), tags=("top",))
q(IFAM, "Quelles sont mes 3 principales catégories de dépenses cette année ?", R(p=TY),
  G("category"), TOP(3), tags=("top",), lang=FR)
q(IFAM, "Mes 5 plus gros commerces le mois dernier", R(p=LM), G("merchant"), TOP(5),
  tags=("top",), lang=FR)

# Comparisons and multi-step
_TWO = (R("now", cat=GRO, p=TM), SUM("now", "a"), R("then", cat=GRO, p=LM), SUM("then", "b"))
q(SAL, "Did I spend more on groceries this month than last month? By how much?", *_TWO,
  CMP("a", "b", "difference"), tags=("compare",))
q(SAL, "How did my restaurant spending in August compare to July, in percent?",
  R("aug", cat=RES, p=MON(8)), SUM("aug", "a"), R("jul", cat=RES, p=MON(7)), SUM("jul", "b"),
  CMP("a", "b", "percent_change"), tags=("compare",))
q(SAL, "What share of last month's spending went to groceries?", R("gro", cat=GRO, p=LM),
  SUM("gro", "a"), R("all", p=LM), SUM("all", "b"), CMP("a", "b", "share"), tags=("compare",))
q(FREE, "How much more did I spend on restaurants than groceries last month?",
  R("res", cat=RES, p=LM), SUM("res", "a"), R("gro", cat=GRO, p=LM), SUM("gro", "b"),
  CMP("a", "b", "difference"), tags=("compare",))
q(FREE, "What percent of my income went to rent this year?", R("rent", cat=RENT, p=TY),
  SUM("rent", "a"), R("inc", d="in", k=["income"], p=TY), SUM("inc", "b"), CMP("a", "b", "share"),
  tags=("compare",))
q(FREE, "Compare my spending at IGA and Metro this year", R("iga", m="IGA", p=TY),
  SUM("iga", "a"), R("metro", m="Metro", p=TY), SUM("metro", "b"), CMP("a", "b", "difference"),
  tags=("compare",))
q(TIGHT, "Is my transport spending up or down this month vs last month, in %?",
  R("now", cat=TRA, p=TM), SUM("now", "a"), R("then", cat=TRA, p=LM), SUM("then", "b"),
  CMP("a", "b", "percent_change"), tags=("compare",))
q(DRENT, "What share of my food spending is delivery, this year?", R("del", cat=DEL, p=TY),
  SUM("del", "a"), R("food", cat=[GRO, RES, DEL], p=TY), SUM("food", "b"), CMP("a", "b", "share"),
  tags=("compare", "multi"))
q(DRENT, "How much more did I spend on Uber Eats than DoorDash this year?",
  R("u", m="Uber Eats", p=TY), SUM("u", "a"), R("d", m="DoorDash", p=TY), SUM("d", "b"),
  CMP("a", "b", "difference"), tags=("compare",))
q(DHOME, "Is my average monthly delivery spending this year higher than last year?",
  R("ty", cat=DEL, p=TY), G("month", "ty", "gty"), AGG("average", "gty", "a"),
  R("ly", cat=DEL, p=LY), G("month", "ly", "gly"), AGG("average", "gly", "b"),
  CMP("a", "b", "difference"), tags=("compare", "multi"))
q(DHOME, "How does this month's Tim Hortons spending compare with my monthly average?",
  R("now", m="Tim Hortons", p=TM), SUM("now", "a"), R("all", m="Tim Hortons"),
  G("month", "all", "gm"), AGG("average", "gm", "b"), CMP("a", "b", "difference"),
  tags=("compare", "multi"))
q(IFAM, "J'ai dépensé combien de plus en livraison qu'en épicerie le mois dernier ?",
  R("del", cat=DEL, p=LM), SUM("del", "a"), R("gro", cat=GRO, p=LM), SUM("gro", "b"),
  CMP("a", "b", "difference"), tags=("compare",), lang=FR)
q(SAL, "What if I cut restaurants by what I spent there last month?", R(cat=RES, p=LM), SUM(),
  WI({"kind": "category", "category": RES, "direction": "less", "amount": {"ref": "a"}}),
  tags=("whatif", "multi"))
q(DRENT, "What if I spent my monthly average on delivery less each month?", R(cat=DEL),
  *AVG_BY("month"),
  WI({"kind": "category", "category": DEL, "direction": "less", "amount": {"ref": "a"}}),
  tags=("whatif", "multi"))

# Typos and informal wording
q(SAL, "how much at velisarro last month", R(m="velisarro", p=LM), SUM(), tags=("typo",))
q(SAL, "pharmaprixx spending this yr", R(m="pharmaprixx", p=TY), SUM(), tags=("typo", "informal"))
q(SAL, "grocery $$ last month??", R(cat=GRO, p=LM), SUM(), tags=("informal",))
q(FREE, "how much did i blow on restos last month lol", R(cat=RES, p=LM), SUM(),
  tags=("informal",))
q(FREE, "amazn this year", R(m="amazn", p=TY), SUM(), tags=("typo", "informal"))
q(TIGHT, "netflx cost this year?", R(m="netflx", p=TY), SUM(), tags=("typo",))
q(DRENT, "uber eets last month", R(m="uber eets", p=LM), SUM(), tags=("typo", "informal"))
q(DRENT, "how much on dolarama this year", R(m="dolarama", p=TY), SUM(), tags=("typo",))
q(DHOME, "timhortons this month", R(m="timhortons", p=TM), SUM(), tags=("typo", "informal"))
q(IFAM, "dorrdash last month how much", R(m="dorrdash", p=LM), SUM(), tags=("typo", "informal"))
q(IFAM, "combien chez tim hortns ce mois", R(m="tim hortns", p=TM), SUM(), tags=("typo",),
  lang=FR)

# Not found (an honest "no merchant like ...")
q(SAL, "How much did I spend at Costco last month?", R(m="Costco", p=LM), SUM(),
  expect="not_found", tags=("lookup",))
q(IFAM, "Combien chez Provigo le mois dernier ?", R(m="Provigo", p=LM), SUM(),
  expect="not_found", tags=("lookup",), lang=FR)

# Recurring payments
q(SAL, "What are my recurring payments?", REC(), tags=("recurring",))
q(SAL, "When is my next Hydro bill?", REC("Hydro"), tags=("recurring",))
q(TIGHT, "What subscriptions do I have?", REC(), tags=("recurring",))
q(DRENT, "When does Netflix charge me next?", REC("Netflix"), tags=("recurring",))
q(DHOME, "Quels sont mes paiements récurrents ?", REC(), tags=("recurring",), lang=FR)
q(IFAM, "How much is my Bell Internet bill usually?", REC("Bell Internet"), tags=("recurring",))
q(FREE, "What regular deposits do I get?", REC(d="in"), tags=("recurring",))

# Forecast
q(SAL, "What will my balance be in 2 weeks?", FB(IN(14)), tags=("forecast",))
q(SAL, "What's my balance going to be at the end of the month?", FB(EOM), tags=("forecast",))
q(FREE, "What will my balance be on October 1?", FB(ON(10, 1)), tags=("forecast",))
q(FREE, "How low will my balance get in the next month?", FL(), tags=("forecast",))
q(TIGHT, "What's the chance I go below my buffer this month?", FC(until=EOM), tags=("forecast",))
q(TIGHT, "Will I go below zero in the next 30 days?", FC(below=0), tags=("forecast",))
q(TIGHT, "What's my lowest balance going to be before the end of the month?", FL(EOM),
  tags=("forecast",))
q(DRENT, "Balance tomorrow?", FB(TMRW), tags=("forecast",))
q(DRENT, "What are the odds I dip under $200 in the next 2 weeks?", FC(below=200, until=IN(14)),
  tags=("forecast",))
q(DHOME, "What will I have on the 1st?", FB(DOM(1)), tags=("forecast",))
q(IFAM, "Quel sera mon solde à la fin du mois ?", FB(EOM), tags=("forecast",), lang=FR)
q(IFAM, "Quel est le risque de passer sous zéro ce mois-ci ?", FC(below=0, until=EOM),
  tags=("forecast",), lang=FR)
q(SAL, "Chance my balance goes under $1,000 by the end of the week?", FC(below=1000, until=EOW),
  tags=("forecast",))
q(FREE, "What will my balance be on the 25th?", FB(DOM(25)), tags=("forecast",))
q(DHOME, "Lowest point of my balance over the next 30 days?", FL(), tags=("forecast",))

# What-ifs
q(TIGHT, "What if I cancel Netflix?", WI(cancel("Netflix")), tags=("whatif",))
q(SAL, "What if I cancel Spotify?", WI(cancel("Spotify")), tags=("whatif",))
q(SAL, "What if I spend $100 less on restaurants a month?", WI(cat(RES, "less", 100)),
  tags=("whatif",))
q(FREE, "What if I buy a $600 laptop next week?", WI(once("out", 600, IN(7))), tags=("whatif",))
q(FREE, "What if I spend 20% less on groceries?", WI(cat(GRO, "less", pct=20)), tags=("whatif",))
q(DRENT, "What if I stop ordering delivery altogether?", WI(cat(DEL, "less", pct=100)),
  tags=("whatif",))
q(DRENT, "What if Netflix and Disney Plus were both cancelled?",
  WI(cancel("Netflix"), cancel("Disney Plus")), tags=("whatif",))
q(DHOME, "What if I spend $50 more on groceries each month?", WI(cat(GRO, "more", 50)),
  tags=("whatif",))
q(DHOME, "What if I get a $500 gift on the 20th?", WI(once("in", 500, DOM(20))), tags=("whatif",))
q(IFAM, "What if Bell Internet cost $40 instead?", WI(swap("Bell Internet", 40)), tags=("whatif",))
q(IFAM, "Et si je dépensais 10 % de moins au quotidien ?", WI(every("less", pct=10)),
  tags=("whatif",), lang=FR)
q(TIGHT, "What if I cut my everyday spending by $200 a month?", WI(every("less", 200)),
  tags=("whatif",))
q(SAL, "Et si j'annulais Vidéotron ?", WI(cancel("Vidéotron")), tags=("whatif",), lang=FR)

# Budgets and safe to spend
q(SAL, "How am I doing on my restaurant budget?", BUD(RES), tags=("budget",))
q(SAL, "Am I on track with my budgets?", BUD(), tags=("budget",))
q(FREE, "Will I go over my shopping budget this month?", BUD(SHOP), tags=("budget",))
q(TIGHT, "How's my shopping budget looking?", BUD(SHOP), tags=("budget",))
q(DRENT, "Am I going to blow my delivery budget?", BUD(DEL), tags=("budget",))
q(DHOME, "Où en est mon budget restaurants ?", BUD(RES), tags=("budget",), lang=FR)
q(IFAM, "Budgets status", BUD(), tags=("budget",))
q(SAL, "How much is safe to spend?", SAFE, tags=("safe",))
q(FREE, "How much can I spend per day without going under my buffer?", SAFE, tags=("safe",))
q(TIGHT, "What's my safe to spend?", SAFE, tags=("safe",))
q(DRENT, "Combien puis-je dépenser par jour ?", SAFE, tags=("safe",), lang=FR)
q(DHOME, "How much can I safely spend until payday?", SAFE, tags=("safe",))
q(IFAM, "safe to spend?", SAFE, tags=("safe",))

# Ambiguous: asking back is right, or a written default applies
AMB = ("ambiguous",)
q(SAL, "How much did I spend on that?", ASK("subject"), expect="clarify", tags=AMB,
  ambiguous=True)
q(FREE, "How much was it?", ASK("subject"), expect="clarify", tags=AMB, ambiguous=True)
q(TIGHT, "Is it more than before?", ASK("subject"), expect="clarify", tags=AMB, ambiguous=True)
q(DHOME, "How much did I spend at the café last month?", R(m="café", p=LM), SUM(),
  expect="clarify", tags=AMB, ambiguous=True)
q(DRENT, "How much did I spend at Concordia this year?", R(m="Concordia", p=TY), SUM(),
  expect="clarify", tags=AMB, ambiguous=True)
q(IFAM, "How much did I send to Haddad this year?", R(m="Haddad", p=TY), SUM(),
  expect="clarify", tags=AMB, ambiguous=True)
q(SAL, "How much did I spend recently?", ASK("period"), expect="clarify", tags=AMB,
  ambiguous=True)
q(FREE, "What did I spend on food?", ASK("which_one"), expect="clarify", tags=AMB, ambiguous=True)
q(TIGHT, "Combien ça m'a coûté ?", ASK("subject"), expect="clarify", tags=AMB, lang=FR,
  ambiguous=True)
q(DRENT, "What if I spend less?", ASK("amount"), expect="clarify", tags=AMB, ambiguous=True)
q(SAL, "How much did I spend last month?", R(p=LM), SUM(), tags=AMB, ambiguous=True)
q(FREE, "How much did I spend on coffee this year?", R(cat=RES, p=TY), SUM(), tags=AMB,
  ambiguous=True)
q(TIGHT, "How much did I spend in March?", R(p=MON(3)), SUM(), tags=AMB, ambiguous=True)
q(DRENT, "What's my biggest expense?", R(), MAX(), tags=AMB, ambiguous=True)
q(DHOME, "How much did I spend this week?", R(p=TW), SUM(), tags=AMB, ambiguous=True)
q(IFAM, "Combien j'ai dépensé le mois passé ?", R(p=LM), SUM(), tags=AMB, lang=FR,
  ambiguous=True)
q(SAL, "Average spending per month?", R(), *AVG_BY("month"), tags=AMB, ambiguous=True)
q(FREE, "How much did I spend in the last 3 months?", R(p=NM(3)), SUM(), tags=AMB,
  ambiguous=True)
q(TIGHT, "My grocery spending this year", R(cat=GRO, p=TY), SUM(), tags=AMB, ambiguous=True)
q(DHOME, "How much on eating out last month?", R(cat=RES, p=LM), SUM(), tags=AMB, ambiguous=True)

# Should refuse: advice, outside the data, past the horizon, not supported
ADV = ("advice",)
q(SAL, "Should I buy a car?", NO("advice"), expect="refused", tags=ADV)
q(FREE, "Is it a good idea to move to a cheaper apartment?", NO("advice"), expect="refused",
  tags=ADV)
q(TIGHT, "Should I cancel Netflix?", NO("advice"), expect="refused", tags=ADV)
q(DRENT, "What budget would you recommend for food delivery?", NO("advice"), expect="refused",
  tags=ADV)
q(DHOME, "Am I spending too much on Uber Eats?", NO("advice"), expect="refused", tags=ADV)
q(IFAM, "Est-ce que je devrais épargner plus ?", NO("advice"), expect="refused", tags=ADV,
  lang=FR)
q(SAL, "Should I invest in an ETF?", NO("advice"), expect="refused", tags=ADV)
q(FREE, "Can I afford a trip to Mexico?", NO("advice"), expect="refused", tags=ADV)
q(TIGHT, "What's the smartest way to pay off debt?", NO("advice"), expect="refused", tags=ADV)
q(DHOME, "Is it worth it to get Amazon Prime?", NO("advice"), expect="refused", tags=ADV)
OUT = ("outside",)
q(SAL, "What's the price of bitcoin?", NO("not_in_data"), expect="refused", tags=OUT)
q(FREE, "What's my credit score?", NO("not_in_data"), expect="refused", tags=OUT)
q(TIGHT, "How much do I owe on my credit card?", NO("not_in_data"), expect="refused", tags=OUT)
q(DRENT, "What's my TFSA balance?", NO("not_in_data"), expect="refused", tags=OUT)
q(DHOME, "What are mortgage rates right now?", NO("not_in_data"), expect="refused", tags=OUT)
q(IFAM, "Quel est le taux d'intérêt de la Banque du Canada ?", NO("not_in_data"),
  expect="refused", tags=OUT, lang=FR)
q(SAL, "How much does my roommate spend on groceries?", NO("not_in_data"), expect="refused",
  tags=OUT)
q(FREE, "What's my net worth?", NO("not_in_data"), expect="refused", tags=OUT)
q(TIGHT, "How much did Netflix cost in 2019?", R(m="Netflix", p={"kind": "year", "year": 2019}),
  SUM(), tags=("lookup",))
HOR = ("horizon",)
q(SAL, "What will my balance be at Christmas?", NO("beyond_horizon"), expect="refused", tags=HOR)
q(FREE, "What will my balance be next March?", NO("beyond_horizon"), expect="refused", tags=HOR)
q(TIGHT, "How much will I spend on groceries next year?", NO("beyond_horizon"), expect="refused",
  tags=HOR)
q(DRENT, "What will my balance be in 2 months?", FB(IN(61)), expect="refused", tags=HOR)
q(DHOME, "What's my balance going to be at the end of October?", FB(ON(10, 31)), expect="refused",
  tags=HOR)
q(IFAM, "Quel sera mon solde dans six mois ?", NO("beyond_horizon"), expect="refused", tags=HOR,
  lang=FR)
q(DRENT, "What's my balance at my other bank?", NO("not_in_data"), expect="refused", tags=OUT)
q(DHOME, "Devrais-je annuler Disney Plus ?", NO("advice"), expect="refused", tags=ADV, lang=FR)
UNS = ("unsupported",)
q(SAL, "Tell me a joke", NO("unsupported"), expect="refused", tags=UNS)
q(FREE, "Write a poem about my budget", NO("unsupported"), expect="refused", tags=UNS)
q(TIGHT, "Translate 'groceries' into Spanish", NO("unsupported"), expect="refused", tags=UNS)
q(DHOME, "Send $50 to my friend", NO("unsupported"), expect="refused", tags=UNS)

# fmt: on

QUESTIONS: tuple[Question, ...] = tuple(_Q)
