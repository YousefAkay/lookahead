"""Score a planner on Ask's question set (spec docs/specs/2026-11-20-ask.md, "Scoring and pass
rule"). Development runs only here; the held-out run has its own harness (step 10).

For each question, the gold plan and the planner's plan are both run on the persona's database;
the planner is correct when the two outcomes have the same signature (`run.signature`: numbers to
the cent, same rows, same groups; refusal = refusal, ask-back = ask-back). Kinds come from the
gold: answerable (an answer, or an honest "no merchant like ..."), ambiguous, should-refuse.

- correct on answerable: the share of answerable questions answered like the gold, with the
  2.5th percentile of 10,000 bootstrap resamples of the questions (fixed seed);
- refused: the share of should-refuse questions the planner refused;
- silent wrong: answers shown (not a refusal, not an ask-back) that differ from the gold, over
  all questions;
- ambiguous: correct when the planner asks back where the gold asks back, or answers like the
  gold where the gold uses a written default (reported apart).

A planner that gives no plan (the offline parser's None) has refused ("I can't answer that yet").
A plan that fails its checks (the LLM's after its one retry) is not correct, not a refusal and not
silent wrong; so is an API call that still fails after its retries ("api_error").

    python -m eval.ask_score --planner offline          # development set, writes a dated report
"""

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from eval.ask_dev_set import QUESTIONS, Question
from eval.ask_personas import ACCOUNT, KEY, TODAY, build
from lookahead.ask.llm import (
    MODEL,
    SYSTEM,
    ApiFailed,
    Send,
    Spend,
    SpendCapReached,
    make_sender,
    plan_for,
)
from lookahead.ask.offline import parse_question
from lookahead.ask.plan import PlanError, check_phrases, parse
from lookahead.ask.run import Context, Outcome, run, signature
from lookahead.db import connect_readonly
from lookahead.generator.archetypes import assert_development_only

BOOTSTRAP = 10_000
SEED = 20261120
# Absolute, so a run from any worktree writes to the one ledger of this checkout's repository.
LEDGER = Path(__file__).resolve().parents[1] / "docs" / "results" / "ask_ledger.jsonl"
# A planner sees the question; the offline one may also read the merchant names in the data (it
# runs on this machine). An LLM planner must ignore the context: it sees only the question.
Planner = Callable[[str, Context], dict[str, Any] | None]


@dataclass(frozen=True)
class Row:
    id: str
    persona: str
    kind: str  # answerable / ambiguous / should_refuse
    lang: str
    tags: tuple[str, ...]
    produced: str  # answer / refused / clarify / not_found / invalid
    correct: bool
    silent_wrong: bool
    refused: bool
    no_plan: bool = False  # the planner gave no plan (shown as "I can't answer that yet")


def kind_of(q: Question) -> str:
    if q.ambiguous:
        return "ambiguous"
    return "should_refuse" if q.expect == "refused" else "answerable"


class Runner:
    """One read-only connection and context per persona, built once."""

    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder
        self.contexts: dict[str, Context] = {}

    def context(self, persona: str) -> Context:
        if persona not in self.contexts:
            assert_development_only([persona])
            path = build(persona) if self.folder is None else build(persona, self.folder)
            self.contexts[persona] = Context(connect_readonly(path), ACCOUNT, TODAY, KEY)
        ctx = self.contexts[persona]
        return Context(ctx.conn, ctx.account, ctx.today, ctx.key, _cache=ctx._cache)

    def outcome(self, persona: str, plan_data: dict[str, Any]) -> Outcome:
        return run(parse(plan_data), self.context(persona))


def gold_outcome(runner: Runner, q: Question) -> Outcome:
    out = runner.outcome(q.persona, q.gold)
    if out.kind != q.expect:
        raise AssertionError(f"{q.id}: the gold gives {out.kind}, the set says {q.expect}")
    return out


def score_one(runner: Runner, q: Question, planner: Planner) -> Row:
    gold = gold_outcome(runner, q)
    produced: str
    same = False
    try:
        data = planner(q.text, runner.context(q.persona))
    except ApiFailed:  # not correct, not a refusal, not silent wrong (spec, his reading)
        return Row(q.id, q.persona, kind_of(q), q.lang, q.tags, "api_error", False, False, False)
    if data is None:
        produced = "refused"
        same = gold.kind == "refused"
    else:
        try:
            plan = parse(data)
            check_phrases(plan, q.text)
        except PlanError:
            produced = "invalid"
        else:
            out = run(plan, runner.context(q.persona))
            produced = out.kind
            same = signature(out) == signature(gold)
    return Row(q.id, q.persona, kind_of(q), q.lang, q.tags, produced, same,
               produced == "answer" and not same, produced == "refused", data is None)  # fmt: skip


def bootstrap_low(hits: list[bool], seed: int = SEED, n: int = BOOTSTRAP) -> float:
    """The 2.5th percentile of the hit rate over `n` resamples of the questions."""
    if not hits:
        return float("nan")
    rng = np.random.default_rng(seed)
    values = np.array(hits, dtype=float)
    draws = rng.integers(0, len(values), size=(n, len(values)))
    return float(np.percentile(values[draws].mean(axis=1), 2.5))


@dataclass(frozen=True)
class Summary:
    n: int
    answerable: int
    correct_answerable: int
    bootstrap_low: float
    should_refuse: int
    refused: int
    silent_wrong: int
    ambiguous: int
    correct_ambiguous: int
    invalid: int
    refused_no_plan: int  # of the refusals on should-refuse questions: no plan at all

    @property
    def rate(self) -> float:
        return self.correct_answerable / self.answerable if self.answerable else float("nan")


def summarize(rows: list[Row]) -> Summary:
    ans = [r for r in rows if r.kind == "answerable"]
    ref = [r for r in rows if r.kind == "should_refuse"]
    amb = [r for r in rows if r.kind == "ambiguous"]
    return Summary(
        len(rows), len(ans), sum(r.correct for r in ans), bootstrap_low([r.correct for r in ans]),
        len(ref), sum(r.refused for r in ref), sum(r.silent_wrong for r in rows), len(amb),
        sum(r.correct for r in amb), sum(r.produced == "invalid" for r in rows),
        sum(r.refused and r.no_plan for r in ref),
    )  # fmt: skip


def score(planner: Planner, questions: Iterable[Question] = QUESTIONS,
          runner: Runner | None = None) -> list[Row]:  # fmt: skip
    runner = runner or Runner()
    return [score_one(runner, q, planner) for q in questions]


def _pct(a: int, b: int) -> str:
    return f"{a / b:.1%} ({a} of {b})" if b else "n/a"


def report(name: str, rows: list[Row], questions: Iterable[Question] = QUESTIONS) -> str:
    s = summarize(rows)
    text_of = {q.id: q for q in questions}
    lines = [
        f"# Ask, development set, planner `{name}` ({date.today().isoformat()})",
        "",
        "Development questions only (eval/ask_dev_set.py, written while building Ask, not by "
        "me); synthetic development personas; not the scored held-out run. n = questions.",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Correct on answerable | {_pct(s.correct_answerable, s.answerable)} |",
        f"| Bootstrap 2.5th percentile of that rate | {s.bootstrap_low:.1%} |",
        f"| Refused, of should-refuse | {_pct(s.refused, s.should_refuse)} |",
        f'| ... of which with no plan ("I can\'t answer that yet") | {s.refused_no_plan} |',
        f"| Silent wrong, of all | {_pct(s.silent_wrong, s.n)} |",
        f"| Ambiguous handled like the gold | {_pct(s.correct_ambiguous, s.ambiguous)} |",
        f"| Plans that failed their checks | {s.invalid} |",
        "",
        "The offline parser was tuned on these same 200 questions after its first score, so its "
        "figures here are fitted to them; only the held-out run measures it. Every development "
        "run is in docs/results/ask_ledger.jsonl:",
        "",
        *_history(),
        "",
        "## By tag (answerable and ambiguous questions: correct; should-refuse: refused)",
        "",
        "| Tag | Result |",
        "|---|---|",
    ]
    tags = sorted({t for r in rows for t in r.tags})
    for tag in tags:
        tagged = [r for r in rows if tag in r.tags]
        ok = sum(r.refused if r.kind == "should_refuse" else r.correct for r in tagged)
        lines.append(f"| {tag} | {_pct(ok, len(tagged))} |")
    lines += ["", "| Language | Correct on answerable |", "|---|---|"]
    for lang in sorted({r.lang for r in rows}):
        part = [r for r in rows if r.lang == lang and r.kind == "answerable"]
        lines.append(f"| {lang} | {_pct(sum(r.correct for r in part), len(part))} |")
    lines += ["", "## Questions not handled like the gold", "",
              "| Id | Kind | Produced | Question |", "|---|---|---|---|"]  # fmt: skip
    for r in rows:
        ok = r.refused if r.kind == "should_refuse" else r.correct
        if not ok:
            lines.append(f"| {r.id} | {r.kind} | {r.produced}{' (silent wrong)' * r.silent_wrong}"
                         f" | {text_of[r.id].text.replace('|', '/')} |")  # fmt: skip
    return "\n".join(lines) + "\n"


def _history() -> list[str]:
    if not LEDGER.exists():
        return ["- (no earlier runs)"]
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("set") != "development":
            continue
        out.append(f"- {e['planner']}, code {e['code']}: {e['correct_answerable']} of "
                   f"{e['answerable']} answerable correct, {e['refused']} of "
                   f"{e['should_refuse']} refused, {e['silent_wrong']} of {e['n']} silent wrong"
                   + (f" ({e['variant']})" if e.get("variant") else ""))  # fmt: skip
    return out


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def offline(question: str, ctx: Context) -> dict[str, Any] | None:
    return parse_question(question, ctx.merchant_names())


def llm_planner(send: Send, spend: Spend) -> Planner:
    """The LLM planner for the scorer. It gets the question only: the context (the data) is
    never passed on."""

    def planner(question: str, ctx: Context) -> dict[str, Any] | None:
        del ctx  # never sent
        planned = plan_for(question, send, spend)
        if SHOW_PLANS:
            print(f"PLAN {question!r}: {json.dumps(planned.plan, ensure_ascii=False)}")
        return planned.plan

    return planner


PLANNERS: dict[str, Planner] = {"offline": offline}
SHOW_PLANS = False
SPEND_CAP_USD = 5.00  # all LLM development runs together (his cap for step 8, 2026-11-20)


def spent_so_far() -> float:
    if not LEDGER.exists():
        return 0.0
    total = 0.0
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        total += float(json.loads(line).get("spent_usd", 0.0))
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a planner on Ask's development set.")
    parser.add_argument("--planner", choices=[*sorted(PLANNERS), "llm"], default="offline")
    parser.add_argument("--out", type=Path, default=Path("docs/results"))
    parser.add_argument("--no-ledger", action="store_true")
    parser.add_argument("--variant", default="", help="what changed since the last run")
    parser.add_argument("--limit", type=int, default=0, help="only the first N questions")
    parser.add_argument("--ids", default="", help="only these question ids (comma-separated)")
    parser.add_argument(
        "--show-plans",
        action="store_true",
        help="print each produced plan (development questions only)",
    )
    args = parser.parse_args(argv)
    questions = list(QUESTIONS[: args.limit] if args.limit else QUESTIONS)
    if args.ids:
        wanted = set(args.ids.split(","))
        questions = [q for q in questions if q.id in wanted]
    extra: dict[str, Any] = {}
    stopped = ""
    global SHOW_PLANS
    SHOW_PLANS = args.show_plans
    if args.planner == "llm":
        spend = Spend(cap_usd=SPEND_CAP_USD - spent_so_far())
        planner = llm_planner(make_sender(), spend)
        runner = Runner()
        rows = []
        for q in questions:
            try:
                rows.append(score_one(runner, q, planner))
            except SpendCapReached:
                stopped = f"spend cap reached after {len(rows)} questions"
                break
        extra = {"model": MODEL, "prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
                 "spent_usd": round(spend.spent_usd, 4), "calls": spend.calls,
                 "input_tokens": spend.input_tokens, "output_tokens": spend.output_tokens,
                 "api_errors": sum(r.produced == "api_error" for r in rows),
                 "questions_run": len(rows), "stopped": stopped}  # fmt: skip
        questions = questions[: len(rows)]
    else:
        rows = score(PLANNERS[args.planner], questions)
    text = report(args.planner, rows, questions)
    if extra:
        text += (f"\nModel {MODEL}, temperature 0. Spent {extra['spent_usd']:.4f} USD on "
                 f"{extra['calls']} calls ({extra['input_tokens']} tokens in, "
                 f"{extra['output_tokens']} out); API failures {extra['api_errors']}."
                 + (f" Stopped: {stopped}." if stopped else "") + "\n")  # fmt: skip
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")  # one file per run, never overwritten
    path = args.out / f"ask_dev_{args.planner}_{stamp}.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    if not args.no_ledger:
        entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "set": "development",
                 "planner": args.planner, "code": _commit(), "variant": args.variant,
                 **asdict(summarize(rows)), **extra}  # fmt: skip
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
