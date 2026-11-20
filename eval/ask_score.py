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
A plan that fails its checks is not correct, not a refusal and not silent wrong.

    python -m eval.ask_score --planner offline          # development set, writes a dated report
"""

import argparse
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
from lookahead.ask.offline import parse_question
from lookahead.ask.plan import PlanError, check_phrases, parse
from lookahead.ask.run import Context, Outcome, run, signature
from lookahead.db import connect_readonly
from lookahead.generator.archetypes import assert_development_only

BOOTSTRAP = 10_000
SEED = 20261120
LEDGER = Path("docs/results/ask_ledger.jsonl")
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
    data = planner(q.text, runner.context(q.persona))
    produced: str
    same = False
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
               produced == "answer" and not same, produced == "refused")  # fmt: skip


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
        "Development questions only (eval/ask_dev_set.py, written by Claude Code); synthetic "
        "development personas; not the scored held-out run. n = questions.",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Correct on answerable | {_pct(s.correct_answerable, s.answerable)} |",
        f"| Bootstrap 2.5th percentile of that rate | {s.bootstrap_low:.1%} |",
        f"| Refused, of should-refuse | {_pct(s.refused, s.should_refuse)} |",
        f"| Silent wrong, of all | {_pct(s.silent_wrong, s.n)} |",
        f"| Ambiguous handled like the gold | {_pct(s.correct_ambiguous, s.ambiguous)} |",
        f"| Plans that failed their checks | {s.invalid} |",
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


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def offline(question: str, ctx: Context) -> dict[str, Any] | None:
    return parse_question(question, ctx.merchant_names())


PLANNERS: dict[str, Planner] = {"offline": offline}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a planner on Ask's development set.")
    parser.add_argument("--planner", choices=sorted(PLANNERS), default="offline")
    parser.add_argument("--out", type=Path, default=Path("docs/results"))
    parser.add_argument("--no-ledger", action="store_true")
    args = parser.parse_args(argv)
    rows = score(PLANNERS[args.planner])
    text = report(args.planner, rows)
    path = args.out / f"ask_dev_{args.planner}_{date.today().isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    if not args.no_ledger:
        entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "set": "development",
                 "planner": args.planner, "code": _commit(), **asdict(summarize(rows))}  # fmt: skip
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
