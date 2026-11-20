"""The LLM planner (spec docs/specs/2026-11-20-ask.md, step 8): Claude Haiku 4.5 turns a question
into a plan. Opt-in, off by default.

What is sent, and nothing else (definition of done 7): my question as I typed it, the fixed
instructions below (the plan format and the category list), and, on the one retry, the problems
the plan check found (`PlanError.problems`: positions and field names, never values). No rows,
amounts, balances, dates or merchant names from my data are ever sent, and nothing computed is
sent back. `plan_for` takes only the question: it has no way to reach the data.

The key is read from .env by the code at run time (`key_from_env`), never printed.
"""

import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lookahead.ask.plan import PlanError, check_phrases, parse
from lookahead.config import load_env
from lookahead.taxonomy import CATEGORIES

MODEL = "claude-haiku-4-5-20251001"  # the dated snapshot the categorizer uses: it does not change
TEMPERATURE = 0.0
MAX_TOKENS = 1500
KEY_NAME = "ANTHROPIC_API_KEY"
# List prices for this model, dollars per million tokens (input, output).
PRICE_IN, PRICE_OUT = 1.00, 5.00
TOOL_NAME = "submit_plan"

SYSTEM = (
    """You turn one question about the user's own bank account into a plan for a local program.
You never see the user's data and never answer the question yourself: you only write the plan.
Call the tool submit_plan exactly once with {"steps": [...]}.

A plan is a list of named steps (name: lowercase letters, digits, underscore; unique). A later
step may use an earlier step's result by its name. At most 10 steps. Use only these blocks and
fields; any other field is refused.

rows: {"op":"rows","name":..., "categories":[...], "merchants":[...], "period":{...},
  "direction":"out"|"in"|"both", "kinds":[...], "min_dollars":n, "max_dollars":n}
  Transactions matching every filter given. direction "out" (default) is money out (spending);
  "in" is money in. kinds: "income","bill","everyday","transfer","refund". Transfers between
  the user's accounts are left out unless kinds or categories ask for them. merchants: names
  copied exactly as the user wrote them in the question (the program matches them to its own
  merchant names). No period: all the data. Gives rows; never the last step.
group: {"op":"group","name":...,"source":<rows>,"by":"month"|"week"|"category"|"merchant"|
  "weekday","measure":"sum"|"average"|"count"|"max"|"min"} (measure default "sum"). Gives a
  table. Months and weeks with nothing count as 0.
aggregate: {"op":"aggregate","name":...,"source":<rows or table>,"measure":"sum"|"average"|
  "count"|"max"|"min"}. Gives a number. "Average per month" = group by month, then average.
  count works on rows only.
top: {"op":"top","name":...,"source":<table>,"n":1-25,"order":"highest"|"lowest"}.
compare: {"op":"compare","name":...,"a":<number>,"b":<number>,"how":"difference"|
  "percent_change"|"share"}. difference = a - b; percent_change = change from b to a;
  share = a as a percent of b.
list: {"op":"list","name":...,"source":<rows>,"order":"largest"|"smallest"|"latest"|
  "earliest","limit":1-200}.
forecast: {"op":"forecast","name":...,"measure":"balance_on"|"lowest"|"chance_below",
  "on":<day> (balance_on only), "until":<day> (optional), "below":{"dollars":n} or
  {"ref":<name>} (chance_below only; omitted = the user's safety buffer)}. The forecast covers
  the 30 days after the data ends. Without "until": all 30 days.
whatif: {"op":"whatif","name":...,"changes":[<change>, ...up to 5]}. Compares the lowest
  balance and the chance of going below the buffer over the forecast's 30 days, now and with
  the changes. A change is one of:
  {"kind":"category","category":C,"direction":"more"|"less","amount":{"dollars":n} (a month)
    or "percent":p}
  {"kind":"everyday","direction":"more"|"less","amount":{"dollars":n} or "percent":p}
  {"kind":"cancel","target":<a recurring payment's name as the user wrote it>}
  {"kind":"swap","target":<name as written>,"amount":{"dollars":n} (new amount each time)}
  {"kind":"one_off","direction":"in"|"out","amount":{"dollars":n},"day":<day> (optional)}
  amount may be {"ref":<name of an earlier money number>}.
budget: {"op":"budget","name":...,"category":C (optional; omitted = every budget)}. This
  month's budget pace.
safe_to_spend: {"op":"safe_to_spend","name":...}. How much a day is safe to spend.
recurring: {"op":"recurring","name":...,"find":<name as written> (optional),
  "direction":"out"|"in"|"both"}. Recurring payments, their usual amount and next date.
cannot_answer: {"op":"cannot_answer","reason":"advice"|"not_in_data"|"beyond_horizon"|
  "unsupported"} as the only step. advice: opinions, recommendations, "should I", "is it worth
  it", "too much". not_in_data: anything a single chequing account's transactions cannot show
  (prices, rates, other accounts or people, credit cards, investments, net worth).
  beyond_horizon: the future past the forecast's 30 days, or future spending totals.
  unsupported: anything else that is not a question about this account.
clarify: {"op":"clarify","about":"subject"|"period"|"amount"|"which_one"} as the only step,
  when the question has no clear subject ("that", "it"), a vague period ("recently"), a missing
  amount, or a word that could mean several categories ("food").

Periods (past): {"kind": K} with K one of "all","today","yesterday","this_week","last_week",
  "this_month","last_month","this_year","last_year"; or {"kind":"last_n_days"|"last_n_weeks"|
  "last_n_months","n":N} (full weeks/months before the current one); {"kind":"month",
  "year":Y,"month":M}; {"kind":"named_month","month":M} (a month named without a year);
  {"kind":"year","year":Y}; {"kind":"range","start":"YYYY-MM-DD","end":"YYYY-MM-DD"};
  {"kind":"since","start":"YYYY-MM-DD"}; {"kind":"since_named","month":M,"day":D} ("since
  July 1" with no year). You do not know today's date or the year: never write a year the user
  did not write; use these words, the program turns them into dates.
Days (future): {"kind":"tomorrow"}, {"kind":"in_days","n":N}, {"kind":"date","date":"YYYY-MM-DD"},
  {"kind":"day_of_month","day":D} ("on the 15th"), {"kind":"named_date","month":M,"day":D},
  {"kind":"end_of_week"}, {"kind":"end_of_month"} (this month's end). A named day without a
  year ("October 1", "the end of October") is named_date with that month and day (the month's
  last day for its end), never "date". "Next 2 weeks" = in_days 14; "next month" or "next 30
  days" = no "until"; "this month" for a forecast = until end_of_month.

Written defaults: "spending" is money out without transfers; "last month" is the previous
calendar month; a word that is neither a merchant name nor a category goes to the closest
category (coffee -> Restaurants & cafés). Questions may be in English or French.

Categories (use exactly these): """
    + ", ".join(CATEGORIES)
    + """.

Examples:
Q: "How much did I spend at Fernwood Deli in April?" ->
{"steps":[{"op":"rows","name":"r","merchants":["Fernwood Deli"],"period":{"kind":"named_month",
"month":4}},{"op":"aggregate","name":"total","source":"r","measure":"sum"}]}
Q: "Combien je dépense en transport par mois en moyenne cette année ?" ->
{"steps":[{"op":"rows","name":"r","categories":["Transport"],"period":{"kind":"this_year"}},
{"op":"group","name":"m","source":"r","by":"month"},{"op":"aggregate","name":"avg",
"source":"m","measure":"average"}]}
Q: "What if I drop my gym membership and buy a $300 bike on the 10th?" ->
{"steps":[{"op":"whatif","name":"w","changes":[{"kind":"cancel","target":"gym"},
{"kind":"one_off","direction":"out","amount":{"dollars":300},"day":{"kind":"day_of_month",
"day":10}}]}]}
Q: "What if I spend 15% less on shopping?" ->
{"steps":[{"op":"whatif","name":"w","changes":[{"kind":"category","category":"Shopping",
"direction":"less","percent":15}]}]}
Q: "Is my phone plan too expensive?" -> {"steps":[{"op":"cannot_answer","reason":"advice"}]}
"""
)

TOOL: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": "Submit the plan for the question.",
    "input_schema": {
        "type": "object",
        "properties": {"steps": {"type": "array", "items": {"type": "object"}}},
        "required": ["steps"],
    },
}


def build_request(question: str, problems: list[str] | None = None,
                  first_plan: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """The whole request, as arguments of the SDK's `messages.create`. Nothing else is sent: the
    fixed instructions, the question as typed and, on the retry, the plan it wrote and the
    problems found in it (positions and field names only)."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    if problems is not None:
        messages += [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "plan_1",
                                               "name": TOOL_NAME, "input": first_plan or {}}]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "plan_1", "is_error": True,
                "content": "The plan failed its checks: " + "; ".join(problems)
                + ". Call submit_plan again with a corrected plan.",
            }]},
        ]  # fmt: skip
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM,
        "tools": [TOOL],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
        "messages": messages,
        "extra_body": {"temperature": TEMPERATURE},
    }


@dataclass
class Spend:
    """Tokens and dollars spent, and the cap that stops more calls."""

    cap_usd: float
    spent_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, usage: Any) -> None:
        tin = int(getattr(usage, "input_tokens", 0) or 0)
        tout = int(getattr(usage, "output_tokens", 0) or 0)
        self.input_tokens += tin
        self.output_tokens += tout
        self.calls += 1
        self.spent_usd += (tin * PRICE_IN + tout * PRICE_OUT) / 1e6

    def room_for_one(self) -> bool:
        """A call costs at most about 5,000 tokens in and MAX_TOKENS out."""
        worst = (5_000 * PRICE_IN + MAX_TOKENS * PRICE_OUT) / 1e6
        return self.spent_usd + worst <= self.cap_usd


class SpendCapReached(RuntimeError):
    pass


class ApiFailed(RuntimeError):
    """The API still failed after the SDK's retries."""


@dataclass
class Planned:
    plan: dict[str, Any] | None  # None: the check still failed after the retry
    attempts: int
    problems: list[str] = field(default_factory=list)


Send = Callable[[dict[str, Any]], Any]


def _tool_input(response: Any) -> dict[str, Any]:
    for block in getattr(response, "content", []):
        if getattr(block, "type", "") == "tool_use":
            return dict(block.input)
    return {}


def plan_for(question: str, send: Send, spend: Spend) -> Planned:
    """A checked plan for the question: one call, and one retry with the check's problems."""
    problems: list[str] | None = None
    first: dict[str, Any] | None = None
    for attempt in (1, 2):
        if not spend.room_for_one():
            raise SpendCapReached(f"spent {spend.spent_usd:.4f} of {spend.cap_usd:.2f} USD")
        try:
            response = send(build_request(question, problems, first))
        except Exception as e:  # the SDK already retried; record it as an API failure
            status = getattr(e, "status_code", "")  # the type and status only, never the text
            raise ApiFailed(f"{type(e).__name__} {status}".strip()) from e
        spend.add(getattr(response, "usage", None))
        data = _tool_input(response)
        try:
            check_phrases(parse(data), question)
        except PlanError as e:
            problems, first = e.problems, data
            continue
        return Planned(data, attempt)
    return Planned(None, 2, problems or [])


def main_checkout() -> Path:
    """The main repository folder (where .env lives), also when run from a worktree."""
    try:
        out = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
        return Path(out).parent
    except (OSError, subprocess.CalledProcessError):
        return Path.cwd()


def key_from_env() -> str:
    """The API key from the environment or the main checkout's .env (never printed)."""
    key = os.environ.get(KEY_NAME) or load_env(main_checkout() / ".env").get(KEY_NAME, "")
    if not key:
        raise RuntimeError(f"{KEY_NAME} is not set")
    return key


def make_sender() -> Send:
    """A function that sends one request. Never in demo mode."""
    from lookahead.demo_mode import is_demo

    if is_demo():
        raise RuntimeError("the LLM is off in demo mode")
    import anthropic

    client = anthropic.Anthropic(api_key=key_from_env())

    def send(request: dict[str, Any]) -> Any:
        start = time.perf_counter()
        response = client.messages.create(**request)
        response._latency_s = time.perf_counter() - start  # type: ignore[attr-defined]
        return response

    return send


def request_text(request: dict[str, Any]) -> str:
    """Everything a request carries, as one string (for the test of what is sent)."""
    return json.dumps(request, ensure_ascii=False, default=str)
