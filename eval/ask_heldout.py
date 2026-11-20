"""Ask's held-out question set (spec docs/specs/2026-11-20-ask.md, step 9): its files, their
formats, and commands that print only counts and fingerprints.

The folder data/ask_heldout/ is git-ignored and blocked by a hook (tools/heldout_guard.py) for
the coding session, the reviewer and every agent except the writers, each to its own files.
It is committed only after the one scored run.

Files:
- questions_yousef.txt: Yousef's 50 questions, one per line, `persona<TAB>question`
  (UTF-8; blank lines and lines starting with # are skipped). Ids hy-001, hy-002, ... in order.
- questions_agent.jsonl: the writer agent's 50, one JSON object per line:
  {"id": "ha-001", "persona": "v2-SAL-01", "text": "...", "lang": "en" | "fr"}.
- gold_a.jsonl, gold_b.jsonl: one gold plan per question, written independently:
  {"id": "...", "expect": "answer" | "refused" | "clarify" | "not_found",
   "ambiguous": true | false, "steps": [...]}.

Commands (none prints a question or a plan, except `disagreements`, which only Yousef runs):
    python -m eval.ask_heldout check-questions   # counts by persona, language; format problems
    python -m eval.ask_heldout check-gold --which a   # counts; plans that fail or run otherwise
    python -m eval.ask_heldout fingerprint       # SHA-256 of each file and of the set
    python -m eval.ask_heldout disagreements     # Yousef only: plans that differ, in plain words,
                                                 # and 10 agreed ones chosen with SPOT_SEED
"""

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).resolve().parents[1] / "data" / "ask_heldout"
YOUSEF = "questions_yousef.txt"
AGENT = "questions_agent.jsonl"
GOLD = {"a": "gold_a.jsonl", "b": "gold_b.jsonl"}
EXPECT = ("answer", "refused", "clarify", "not_found")
SPOT_SEED = 20261120  # fixed before anyone saw a gold plan (spec, step 9)
SPOT_N = 10


@dataclass(frozen=True)
class HeldOutQuestion:
    id: str
    persona: str
    text: str
    lang: str = ""


def read_yousef(path: Path) -> list[HeldOutQuestion]:
    out: list[HeldOutQuestion] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        persona, _, text = line.partition("\t")
        out.append(HeldOutQuestion(f"hy-{len(out) + 1:03d}", persona.strip(), text.strip()))
    return out


def read_agent(path: Path) -> list[HeldOutQuestion]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out.append(HeldOutQuestion(d["id"], d["persona"], d["text"], d.get("lang", "")))
    return out


def questions(folder: Path = FOLDER) -> list[HeldOutQuestion]:
    found: list[HeldOutQuestion] = []
    if (folder / YOUSEF).exists():
        found += read_yousef(folder / YOUSEF)
    if (folder / AGENT).exists():
        found += read_agent(folder / AGENT)
    return found


def read_gold(path: Path) -> dict[str, dict[str, Any]]:
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
    return [
        f"questions: {len(qs)} (hy: {sum(q.id.startswith('hy') for q in qs)}, "
        f"ha: {sum(q.id.startswith('ha') for q in qs)})",
        "by persona: " + _counts(Counter(q.persona for q in qs)),
        "by language (agent file): " + _counts(Counter(q.lang for q in qs if q.id[:2] == "ha")),
        *(problems or ["problems: none"]),
    ]


def check_gold(which: str, folder: Path = FOLDER) -> list[str]:
    """Run each gold plan on its persona; counts and the ids of plans that fail, no content."""
    from eval.ask_score import Runner
    from lookahead.ask.plan import PlanError, check_phrases, parse
    from lookahead.ask.run import run

    qs = {q.id: q for q in questions(folder)}
    gold = read_gold(folder / GOLD[which])
    runner = Runner()
    kinds: Counter[str] = Counter()
    problems = []
    for qid, q in qs.items():
        g = gold.get(qid)
        if g is None:
            problems.append(f"{qid}: no gold plan")
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
        out = run(plan, runner.context(q.persona))
        if out.kind != g["expect"]:
            problems.append(f"{qid}: runs to {out.kind}, expect says {g['expect']}")
        kinds[f"{g['expect']}{' (ambiguous)' if g.get('ambiguous') else ''}"] += 1
    problems += [f"{i}: a gold plan for no question" for i in sorted(set(gold) - set(qs))]
    return [
        f"gold {which}: {len(gold)} plans for {len(qs)} questions",
        "by expected outcome: " + _counts(kinds),
        *(problems or ["problems: none"]),
    ]


def fingerprint(folder: Path = FOLDER) -> list[str]:
    """SHA-256 of each file present, and one over all of them in a fixed order."""
    overall = hashlib.sha256()
    lines = []
    for name in (YOUSEF, AGENT, *GOLD.values()):
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


def disagreements(folder: Path = FOLDER) -> list[str]:
    """For Yousef only: each question whose two gold plans compute different answers (or disagree
    on ambiguity), both in plain words; then SPOT_N agreed ones chosen with SPOT_SEED."""
    from eval.ask_score import Runner
    from lookahead.ask.plan import parse
    from lookahead.ask.run import run, signature
    from lookahead.ask.words import plan_words

    a, b = read_gold(folder / GOLD["a"]), read_gold(folder / GOLD["b"])
    runner = Runner()
    differ, agree = [], []
    for q in questions(folder):
        if q.id not in a or q.id not in b:
            continue
        pa, pb = parse({"steps": a[q.id]["steps"]}), parse({"steps": b[q.id]["steps"]})
        same = (signature(run(pa, runner.context(q.persona)))
                == signature(run(pb, runner.context(q.persona)))
                and a[q.id].get("ambiguous") == b[q.id].get("ambiguous"))  # fmt: skip
        (agree if same else differ).append((q, pa, pb))
    lines = [f"{len(differ)} of {len(differ) + len(agree)} differ", ""]
    for q, pa, pb in differ:
        lines += [f"== {q.id} ({q.persona}) {q.text}", "  A:"]
        lines += ["    " + w for w in plan_words(pa)] + ["  B:"]
        lines += ["    " + w for w in plan_words(pb)] + [""]
    spot = random.Random(SPOT_SEED).sample(agree, min(SPOT_N, len(agree)))
    lines += [f"{len(spot)} agreed plans chosen at random (seed {SPOT_SEED}) to check:", ""]
    for q, pa, _ in spot:
        lines += [f"== {q.id} ({q.persona}) {q.text}", *("    " + w for w in plan_words(pa)), ""]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask's held-out set: counts and fingerprints.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-questions")
    gold = sub.add_parser("check-gold")
    gold.add_argument("--which", choices=sorted(GOLD), required=True)
    sub.add_parser("fingerprint")
    sub.add_parser("disagreements")
    args = parser.parse_args(argv)
    commands = {"check-questions": check_questions, "fingerprint": fingerprint,
                "disagreements": disagreements}  # fmt: skip
    lines = check_gold(args.which) if args.command == "check-gold" else commands[args.command]()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
