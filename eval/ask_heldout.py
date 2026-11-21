"""Ask's held-out question set (spec docs/specs/2026-11-20-ask.md, step 9): its files, their
formats, and commands that print only counts, ids and fingerprints.

The folder data/ask_heldout/ is git-ignored and blocked by a hook (tools/heldout_guard.py) for
the coding session, the reviewer and every agent except the writers, each to its own files.
It is committed only after the one scored run.

Files (all in the folder):
- questions_agent.jsonl, questions_agent_b.jsonl: 50 questions each, written by two separate
  fresh writer agents, one JSON object per line:
  {"id": "ha-001" (or "hb-001"), "persona": "v2-SAL-01", "text": "...", "lang": "en" | "fr"}.
- gold_a.jsonl, gold_b.jsonl: one gold plan per question, written independently:
  {"id": "...", "expect": "answer" | "refused" | "clarify" | "not_found",
   "ambiguous": true | false, "steps": [...]}.
- review.txt, answers.txt, review_cases.json: Yousef's review (written by `disagreements`).
  review.txt shows each case in plain words, the two plans side by side with what each computes;
  answers.txt has one line per case for his answer:
    d01: A        (disagreement: gold A is right)
    d02: B        (gold B is right)
    d03: X <one line: what the right plan does>
    s01: OK       (spot check of an agreed plan: right)
    s02: X <one line>
- gold_fixes.jsonl: corrected plans for the X cases, written by a fresh fixer agent from his
  one line; same format as the gold files.
- gold_final.jsonl: the gold used by the scored run (`apply-review`).

Commands (none prints a question or a plan):
    python -m eval.ask_heldout check-questions        # counts by file, persona, language
    python -m eval.ask_heldout check-gold --which a   # counts; plans that fail or run otherwise
    python -m eval.ask_heldout fingerprint            # SHA-256 of each file and of the set
    python -m eval.ask_heldout disagreements          # Yousef only: writes the review files;
                                                      # prints only how many cases
    python -m eval.ask_heldout apply-review           # reads answers.txt, writes gold_final;
                                                      # prints counts and case ids
    python -m eval.ask_heldout check-fixes            # the fixer's plans: counts and ids
    python -m eval.ask_heldout check-final            # gold_final: counts and ids
"""

import argparse
import hashlib
import json
import random
import re
import sys
import textwrap
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _main_checkout() -> Path:
    """The main repository folder, also from a worktree: the held-out files live in one place."""
    from lookahead.ask.llm import main_checkout

    return main_checkout()


FOLDER = _main_checkout() / "data" / "ask_heldout"
QUESTION_FILES = ("questions_agent.jsonl", "questions_agent_b.jsonl")
AGENT = QUESTION_FILES[0]
GOLD = {"a": "gold_a.jsonl", "b": "gold_b.jsonl"}
REVIEW, ANSWERS, CASES = "review.txt", "answers.txt", "review_cases.json"
FIXES, FINAL = "gold_fixes.jsonl", "gold_final.jsonl"
EXPECT = ("answer", "refused", "clarify", "not_found")
SPOT_SEED = 20261120  # fixed before anyone saw a gold plan (spec, step 9)
SPOT_N = 10
WIDTH = 58  # one column of the side-by-side view


@dataclass(frozen=True)
class HeldOutQuestion:
    id: str
    persona: str
    text: str
    lang: str = ""


def read_questions(path: Path) -> list[HeldOutQuestion]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out.append(HeldOutQuestion(d["id"], d["persona"], d["text"], d.get("lang", "")))
    return out


def questions(folder: Path = FOLDER) -> list[HeldOutQuestion]:
    found: list[HeldOutQuestion] = []
    for name in QUESTION_FILES:
        if (folder / name).exists():
            found += read_questions(folder / name)
    return found


def read_gold(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip())  # fmt: skip
    return {d["id"]: d for d in rows}


def _counts(counter: Counter[str]) -> str:
    return ", ".join(f"{k or '?'} {n}" for k, n in sorted(counter.items())) or "none"


def check_questions(folder: Path = FOLDER) -> list[str]:
    """Counts and problems, no question text."""
    from eval.ask_personas import PERSONAS

    qs = questions(folder)
    problems = [f"{q.id}: persona not on the sheet" for q in qs if q.persona not in PERSONAS]
    problems += [f"{q.id}: empty question" for q in qs if not q.text]
    ids = Counter(q.id for q in qs)
    problems += [f"{i}: id used twice" for i, n in ids.items() if n > 1]
    for name, prefix in zip(QUESTION_FILES, ("ha-", "hb-"), strict=True):
        if (folder / name).exists():
            wrong = [q.id for q in read_questions(folder / name) if not q.id.startswith(prefix)]
            problems += [f"{i}: id should start with {prefix} in {name}" for i in wrong]
    return [
        f"questions: {len(qs)} (ha: {sum(q.id.startswith('ha-') for q in qs)}, "
        f"hb: {sum(q.id.startswith('hb-') for q in qs)})",
        "by persona: " + _counts(Counter(q.persona for q in qs)),
        "by language: " + _counts(Counter(q.lang for q in qs)),
        *(problems or ["problems: none"]),
    ]


def _check_plans(gold: dict[str, dict[str, Any]], qs: dict[str, HeldOutQuestion],
                 label: str, need_all: bool = True) -> list[str]:  # fmt: skip
    """Run each plan on its persona; counts and the ids of plans that fail, no content."""
    from eval.ask_score import Runner
    from lookahead.ask.plan import PlanError, check_phrases, parse
    from lookahead.ask.run import run

    runner = Runner()
    kinds: Counter[str] = Counter()
    problems = []
    for qid, q in qs.items():
        g = gold.get(qid)
        if g is None:
            if need_all:
                problems.append(f"{qid}: no plan")
            continue
        if g.get("expect") not in EXPECT:
            problems.append(f"{qid}: expect is not one of {', '.join(EXPECT)}")
            continue
        try:
            plan = parse({"steps": g["steps"]})
            check_phrases(plan, q.text)
        except (PlanError, KeyError):
            problems.append(f"{qid}: the plan fails its checks")
            continue
        try:
            out = run(plan, runner.context(q.persona))
        except Exception as e:  # listed by id and error type only
            problems.append(f"{qid}: the plan could not run ({type(e).__name__})")
            continue
        if out.kind != g["expect"]:
            problems.append(f"{qid}: runs to {out.kind}, expect says {g['expect']}")
        kinds[f"{g['expect']}{' (ambiguous)' if g.get('ambiguous') else ''}"] += 1
    problems += [f"{i}: a plan for no question" for i in sorted(set(gold) - set(qs))]
    return [
        f"{label}: {len(gold)} plans for {len(qs)} questions",
        "by expected outcome: " + _counts(kinds),
        *(problems or ["problems: none"]),
    ]


def check_gold(which: str, folder: Path = FOLDER) -> list[str]:
    qs = {q.id: q for q in questions(folder)}
    return _check_plans(read_gold(folder / GOLD[which]), qs, f"gold {which}")


def check_fixes(folder: Path = FOLDER) -> list[str]:
    """The fixer's plans: each must be for a case marked X, and run to its expect."""
    qs = {q.id: q for q in questions(folder)}
    fixes = read_gold(folder / FIXES)
    x_ids = {c["question"] for c in _x_cases(folder)}
    lines = _check_plans(fixes, qs, "fixes", need_all=False)
    missing = sorted(x_ids - set(fixes))
    extra = sorted(set(fixes) - x_ids)
    lines += [f"{i}: marked X, no fix yet" for i in missing]
    lines += [f"{i}: a fix for a case not marked X" for i in extra]
    return lines


def check_final(folder: Path = FOLDER) -> list[str]:
    qs = {q.id: q for q in questions(folder)}
    return _check_plans(read_gold(folder / FINAL), qs, "final gold")


def fingerprint(folder: Path = FOLDER) -> list[str]:
    """SHA-256 of each file present, and one over all of them in a fixed order."""
    overall = hashlib.sha256()
    lines = []
    for name in (*QUESTION_FILES, *GOLD.values(), FINAL):
        path = folder / name
        if not path.exists():
            lines.append(f"{name}: absent")
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        overall.update(name.encode() + b"\0" + digest.encode() + b"\n")
        lines.append(f"{name}: {len(data.splitlines())} lines, sha256 {digest}")
    lines.append(f"set: sha256 {overall.hexdigest()}")
    return lines


# --- Yousef's review ---


def _gives(question: str, plan: Any, outcome: Any) -> list[str]:
    """What a plan computes, in a sentence or two (and a few table rows)."""
    from lookahead.ask.answer import Asked, headline
    from lookahead.ask.run import Listing, Table

    words = [f"gives: {headline(Asked(question, plan, outcome))}"]
    r = outcome.result
    if outcome.kind == "answer" and isinstance(r, Table):
        for label, value in r.pairs[:5]:
            shown = "-" if value is None else (
                f"{value:,}" if r.unit == "count" else f"${value / 100:,.2f}")  # fmt: skip
            words.append(f"  {label}: {shown}")
        if len(r.pairs) > 5:
            words.append(f"  ... {len(r.pairs) - 5} more")
    if outcome.kind == "answer" and isinstance(r, Listing):
        for _, row in r.rows.head(3).iterrows():
            words.append(f"  {row.get('date', '')} {row.get('merchant', '')} "
                         f"${abs(int(row.get('amount_cents', 0))) / 100:,.2f}")  # fmt: skip
    return words


def _side(gold: dict[str, Any], question: str, ran: tuple[Any, Any] | str) -> list[str]:
    """One plan's column: what it computes, its outcome and its steps in plain words."""
    from lookahead.ask.words import plan_words

    if isinstance(ran, str):
        return [f"could not run ({ran})"]
    plan, outcome = ran
    lines = _gives(question, plan, outcome)
    lines.append(f"outcome: {gold.get('expect')}"
                 f"{', marked ambiguous' if gold.get('ambiguous') else ''}")  # fmt: skip
    lines += [f"{i}. {w}" for i, w in enumerate(plan_words(plan), 1)]
    return lines


def _wrap(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        out += textwrap.wrap(line, WIDTH, subsequent_indent="   ") or [""]
    return out


def _columns(left: list[str], right: list[str]) -> list[str]:
    a, b = _wrap(left), _wrap(right)
    n = max(len(a), len(b))
    a, b = a + [""] * (n - len(a)), b + [""] * (n - len(b))
    return [f"   {x:<{WIDTH}} | {y}".rstrip() for x, y in zip(a, b, strict=True)]


def review_cases(folder: Path = FOLDER) -> tuple[list[dict[str, Any]], list[str]]:
    """The cases for Yousef and the review text: each question whose two gold plans compute
    different answers, disagree on ambiguity, or could not run (d01, d02, ...), then SPOT_N
    agreed ones chosen with SPOT_SEED (s01, ...)."""
    from eval.ask_score import Runner
    from lookahead.ask.plan import parse
    from lookahead.ask.run import run, same_answer

    golds = {"A": read_gold(folder / GOLD["a"]), "B": read_gold(folder / GOLD["b"])}
    runner = Runner()
    differ, agree = [], []
    for q in questions(folder):
        if q.id not in golds["A"] or q.id not in golds["B"]:
            continue
        ran: dict[str, tuple[Any, Any] | str] = {}
        for which, gold in golds.items():
            try:
                plan = parse({"steps": gold[q.id]["steps"]})
                ran[which] = (plan, run(plan, runner.context(q.persona)))
            except Exception as e:  # shown as "could not run"; the other side still shown
                ran[which] = type(e).__name__
        a, b = ran["A"], ran["B"]
        flags = golds["A"][q.id].get("ambiguous") == golds["B"][q.id].get("ambiguous")
        same = (not isinstance(a, str) and not isinstance(b, str)
                and same_answer(a[1], b[1]) and flags)  # fmt: skip
        (agree if same else differ).append((q, ran))
    spot = random.Random(SPOT_SEED).sample(agree, min(SPOT_N, len(agree)))
    cases: list[dict[str, Any]] = []
    text = [
        "Ask held-out review. For each case, write your answer in answers.txt on its line:",
        "  d-cases (the two gold plans disagree): A, B, or X followed by one line saying what",
        "  the right plan should do. s-cases (both agreed; a random spot check): OK, or X and",
        "  one line.",
        f"{len(differ)} disagreements of {len(differ) + len(agree)} questions; "
        f"{len(spot)} spot checks (seed {SPOT_SEED}).",
        "",
    ]
    for i, (q, ran) in enumerate(differ, 1):
        cid = f"d{i:02d}"
        cases.append({"case": cid, "question": q.id, "kind": "disagreement"})
        text += [f"=== {cid}   persona {q.persona}   question: {q.text}",
                 *_columns(["A"], ["B"]),
                 *_columns(_side(golds["A"][q.id], q.text, ran["A"]),
                           _side(golds["B"][q.id], q.text, ran["B"])),
                 f"   -> answers.txt  {cid}: A / B / X <one line>", ""]  # fmt: skip
    for i, (q, ran) in enumerate(spot, 1):
        cid = f"s{i:02d}"
        cases.append({"case": cid, "question": q.id, "kind": "spot"})
        text += [f"=== {cid}   persona {q.persona}   question: {q.text}",
                 *("   " + w for w in _wrap(_side(golds["A"][q.id], q.text, ran["A"]))),
                 f"   -> answers.txt  {cid}: OK / X <one line>", ""]  # fmt: skip
    return cases, text


def _filled(path: Path) -> bool:
    return path.exists() and any(
        re.match(r"^\s*[ds]\d+:\s*\S", line) for line in path.read_text("utf-8").splitlines()
    )


def write_review(folder: Path) -> list[str]:
    """Write the review files into the blocked folder and say only how many cases: whoever runs
    this, by whatever route, sees no question and no plan. Never overwrites answers given."""
    if _filled(folder / ANSWERS):
        return [f"{ANSWERS} already has answers: not overwritten (move it away to start again)"]
    cases, text = review_cases(folder)
    (folder / REVIEW).write_text("\n".join(text) + "\n", encoding="utf-8")
    (folder / CASES).write_text(json.dumps(cases, indent=1) + "\n", encoding="utf-8")
    template = [f"{c['case']}: " for c in cases]
    (folder / ANSWERS).write_text("\n".join(template) + "\n", encoding="utf-8")
    n_d = sum(c["kind"] == "disagreement" for c in cases)
    return [f"wrote {REVIEW}, {ANSWERS} and {CASES}: {n_d} disagreements, "
            f"{len(cases) - n_d} spot checks (open {REVIEW} yourself)"]  # fmt: skip


ANSWER = re.compile(r"^\s*(?P<case>[ds]\d+):\s*(?P<letter>OK|[ABX])\b\s*(?P<why>.*)$", re.I)


def read_answers(folder: Path) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Answers by case id (letter, explanation) and problems by case id (never the text)."""
    cases = {c["case"]: c for c in json.loads((folder / CASES).read_text("utf-8"))}
    answers: dict[str, tuple[str, str]] = {}
    problems = []
    for line in (folder / ANSWERS).read_text("utf-8").splitlines():
        if not line.strip():
            continue
        m = ANSWER.match(line)
        cid = line.split(":", 1)[0].strip()
        if m is None:
            problems.append(f"{cid}: no answer yet, or not A, B, OK or X")
            continue
        cid, letter, why = m["case"].lower(), m["letter"].upper(), m["why"].strip()
        kind = cases.get(cid, {}).get("kind")
        if kind is None:
            problems.append(f"{cid}: not a case")
        elif kind == "disagreement" and letter not in ("A", "B", "X"):
            problems.append(f"{cid}: a disagreement takes A, B or X")
        elif kind == "spot" and letter not in ("OK", "X"):
            problems.append(f"{cid}: a spot check takes OK or X")
        elif letter == "X" and not why:
            problems.append(f"{cid}: X needs one line saying what the right plan does")
        else:
            answers[cid] = (letter, why)
    problems += [f"{c}: no answer yet" for c in cases if c not in answers
                 and not any(p.startswith(f"{c}:") for p in problems)]  # fmt: skip
    return answers, problems


def _x_cases(folder: Path) -> list[dict[str, Any]]:
    if not (folder / CASES).exists() or not (folder / ANSWERS).exists():
        return []
    answers, _ = read_answers(folder)
    cases = json.loads((folder / CASES).read_text("utf-8"))
    return [c for c in cases if answers.get(c["case"], ("",))[0] == "X"]


def apply_review(folder: Path = FOLDER) -> list[str]:
    """gold_final from the answers: agreed plans (gold A), A or B where chosen, OK spot checks,
    and the fixer's plan for each X. Prints counts and case ids only."""
    answers, problems = read_answers(folder)
    cases = {c["question"]: c for c in json.loads((folder / CASES).read_text("utf-8"))}
    golds = {"A": read_gold(folder / GOLD["a"]), "B": read_gold(folder / GOLD["b"])}
    fixes = read_gold(folder / FIXES)
    final, waiting = [], []
    for q in questions(folder):
        case = cases.get(q.id)
        letter = answers.get(case["case"], ("",))[0] if case else "agreed"
        if letter in ("agreed", "OK", "A"):
            plan = golds["A"].get(q.id)
        elif letter == "B":
            plan = golds["B"].get(q.id)
        elif letter == "X":
            plan = fixes.get(q.id)
            if plan is None:
                waiting.append(case["case"])  # type: ignore[index]
        else:
            plan = None  # not answered yet
        if plan is not None:
            final.append(plan)
    if not problems and not waiting:
        (folder / FINAL).write_text("".join(json.dumps(p) + "\n" for p in final), "utf-8")
    tally = Counter(letter for letter, _ in answers.values())
    return [
        f"answers: {len(answers)} of {len(cases)} cases ({_counts(tally)})",
        f"X cases still waiting for the fixer: {len(waiting)}"
        + (f" ({', '.join(waiting)})" if waiting else ""),
        *problems,
        f"wrote {FINAL}: {len(final)} plans"
        if not problems and not waiting
        else f"{FINAL} not written: answer every case and fix every X first",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask's held-out set: counts and fingerprints.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check-questions", "fingerprint", "disagreements", "apply-review",
                 "check-fixes", "check-final"):  # fmt: skip
        sub.add_parser(name)
    gold = sub.add_parser("check-gold")
    gold.add_argument("--which", choices=sorted(GOLD), required=True)
    args = parser.parse_args(argv)
    commands = {"check-questions": lambda: check_questions(FOLDER),
                "fingerprint": lambda: fingerprint(FOLDER),
                "disagreements": lambda: write_review(FOLDER),
                "apply-review": lambda: apply_review(FOLDER),
                "check-fixes": lambda: check_fixes(FOLDER),
                "check-final": lambda: check_final(FOLDER)}  # fmt: skip
    lines = check_gold(args.which) if args.command == "check-gold" else commands[args.command]()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
