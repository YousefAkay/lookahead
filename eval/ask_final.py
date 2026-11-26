"""The one scored run on Ask's test set (spec docs/specs/2026-11-20-ask.md, "Scoring and pass
rule", step 10). No arguments:

    python -m eval.ask_final

It scores the offline parser and the LLM planner on the same 100 questions against
gold_final.jsonl, with the development scorer's own logic (eval.ask_score), and applies the
committed pass rule.

- **Record before show:** the plans, then the result file, then the ledger entry are written, and
  only then is anything about the result printed.
- **Runs once:** it refuses when a result exists. An attempt that did not finish (API credit or
  key refused, spending cap, a crash, the process cut off) is void: the void is recorded and it
  stops; another attempt needs a "go" line for that attempt in the ledger, committed, which is
  added only on the owner's word.
- **A single question** whose API call still fails after its retries counts as not correct (and
  not silent wrong), as the rule says.
- **No content:** the log, the result file and the ledger hold ids, counts and error type names,
  never a question, a plan or an error's text. The LLM's plans are saved in the test set's folder.

Progress goes to logs/ask-final.log.
"""

import fcntl
import hashlib
import json
import subprocess
import sys
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import eval.ask_score as dev
import lookahead.ask.llm as llm
from eval.ask_dev_set import Question
from eval.ask_heldout import FINAL, FOLDER, fingerprint, questions, read_gold
from eval.ask_score import Row, Runner, Summary, gold_outcome, score_one, summarize

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_N = 100
CAP_USD = dev.SPEND_CAP_USD  # all LLM runs together, development ones included
MIN_ROOM_USD = 1.00  # not started with less than this left under the cap
SET = "test"  # the ledger's name for this run's lines
RESULT_NAME = "ask_final_result_"
ACCOUNT_REFUSED = (401, 402, 403)  # no later call can succeed
CODE_PATHS = ("eval", "lookahead", "tools", "docs/specs", "docs/results")


@dataclass(frozen=True)
class Paths:
    folder: Path  # the questions and the gold
    results: Path
    ledger: Path
    fingerprints: Path
    log: Path


def default_paths() -> Paths:
    results = ROOT / "docs" / "results"
    return Paths(FOLDER, results, dev.LEDGER, results / "ask_test_set_fingerprint.md",
                 ROOT / "logs" / "ask-final.log")  # fmt: skip


class Refused(RuntimeError):
    """Not started: the message says why. Nothing about an attempt is recorded."""


class Void(RuntimeError):
    """The attempt cannot finish: the message (no content) goes in the ledger."""


def say(paths: Paths, message: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}"
    paths.log.parent.mkdir(parents=True, exist_ok=True)
    with paths.log.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


# --- the ledger and the state of earlier attempts ---


def ledger_lines(paths: Paths) -> list[dict[str, Any]]:
    if not paths.ledger.exists():
        return []
    text = paths.ledger.read_text(encoding="utf-8")
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def record(paths: Paths, event: str, attempt: str, **fields: Any) -> None:
    entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "set": SET,
             "event": event, "attempt": attempt, **fields}  # fmt: skip
    with paths.ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def state(paths: Paths) -> tuple[str, str]:
    """("ready" | "done" | "unfinished" | "needs_go", the attempt it is about)."""
    mine = [e for e in ledger_lines(paths) if e.get("set") == SET]
    results = sorted(paths.results.glob(RESULT_NAME + "*.md"))
    if results or any(e["event"] == "result" for e in mine):
        return "done", results[0].name if results else "in the ledger"
    starts = [e["attempt"] for e in mine if e["event"] == "start"]
    if not starts:
        return "ready", ""
    last = starts[-1]
    events = {e["event"] for e in mine if e["attempt"] == last}
    if "void" not in events:
        return "unfinished", last
    return ("ready" if "go" in events else "needs_go"), last


# --- checks before an attempt ---


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()  # fmt: skip


def code_problems() -> list[str]:
    """The run uses the main folder, on main, with nothing uncommitted in what it relies on."""
    problems = []
    if Path.cwd().resolve() != ROOT or llm.main_checkout().resolve() != ROOT:
        problems.append("run it from the main folder")
    if _git("branch", "--show-current") != "main":
        problems.append("the main folder is not on branch main")
    if _git("status", "--porcelain", "--", *CODE_PATHS):
        problems.append("uncommitted changes under " + ", ".join(CODE_PATHS))
    return problems


def committed_fingerprint(paths: Paths) -> str:
    """The last set fingerprint written in the committed fingerprint file."""
    found = ""
    for line in paths.fingerprints.read_text(encoding="utf-8").splitlines():
        if line.startswith("Set fingerprint: `"):
            found = line.split("`")[1]
    return found


def load_set(paths: Paths, runner: Runner, n: int) -> list[Question]:
    """The questions with their final gold plans, after checking they are the committed files and
    every gold plan runs to what it says."""
    want = committed_fingerprint(paths)
    if not want or fingerprint(paths.folder)[-1].split()[-1] != want:
        raise Refused("the files are not the ones fingerprinted before scoring")
    asked = questions(paths.folder)
    gold = read_gold(paths.folder / FINAL)
    if len(asked) != n or len(gold) != n or {q.id for q in asked} != set(gold):
        raise Refused(f"expected {n} questions with one final gold plan each")
    out = []
    for q in asked:
        try:
            g = gold[q.id]
            item = Question(q.id, q.persona, q.text, tuple(g["steps"]), expect=g["expect"],
                            lang=q.lang, ambiguous=bool(g.get("ambiguous")))  # fmt: skip
            gold_outcome(runner, item)
        except Exception as e:  # the id and the error type only
            raise Refused(f"the gold plan for {q.id} fails ({type(e).__name__})") from None
        out.append(item)
    return out


# --- the run ---


def watched(send: llm.Send, refused: list[str]) -> llm.Send:
    """The sender, noting a failure that stops every later call (credit or key refused)."""

    def inner(request: dict[str, Any]) -> Any:
        try:
            return send(request)
        except Exception as e:
            status = getattr(e, "status_code", None)
            if status in ACCOUNT_REFUSED:
                refused.append(f"the API refused the key or account ({status})")
            elif "credit balance" in str(e).lower():
                refused.append("the API credit ran out")
            raise

    return inner


def score_all(paths: Paths, name: str, planner: dev.Planner, items: list[Question],
              runner: Runner, refused: list[str],
              plans: list[dict[str, Any]]) -> list[Row]:  # fmt: skip
    """One planner over the questions; `plans` collects each plan an LLM gave, by id."""
    rows = []
    for number, q in enumerate(items, 1):
        rows.append(score_one(runner, q, planner))
        if q.text in dev.PRODUCED:
            plans.append({"id": q.id, "plan": dev.PRODUCED.pop(q.text)})
        if refused:
            raise Void(refused[0])
        if number % 10 == 0 or number == len(items):
            say(paths, f"{name}: {number} of {len(items)} questions")
    return rows


def conditions(llm_s: Summary, off_s: Summary) -> list[tuple[str, bool]]:
    """The committed pass rule, in whole numbers where it is a share (no rounding at an edge).
    A share with nothing to count is not met."""
    a, r = llm_s.answerable, llm_s.should_refuse
    return [
        ("1. correct on answerable >= 85%", a > 0 and 100 * llm_s.correct_answerable >= 85 * a),
        (
            "2. bootstrap 2.5th percentile of that rate >= 75%",
            a > 0 and llm_s.bootstrap_low >= 0.75,
        ),  # fmt: skip
        ("3. refuses >= 90% of should-refuse questions", r > 0 and 100 * llm_s.refused >= 90 * r),
        (
            "4a. silent wrong <= 5% of all questions",
            llm_s.n > 0 and 100 * llm_s.silent_wrong <= 5 * llm_s.n,
        ),  # fmt: skip
        (
            "4b. beats the offline parser on correct-on-answerable by >= 20 points",
            a > 0 and 100 * (llm_s.correct_answerable - off_s.correct_answerable) >= 20 * a,
        ),  # fmt: skip
    ]


def _share(a: int, b: int) -> str:
    return f"{a / b:.1%} ({a} of {b})" if b else "n/a"


def report(llm_rows: list[Row], off_rows: list[Row], meta: dict[str, Any]) -> str:
    both = (summarize(llm_rows), summarize(off_rows))
    checks = conditions(*both)
    passed = all(ok for _, ok in checks)

    def line(label: str, value: Callable[[Summary], str]) -> str:
        return f"| {label} | {value(both[0])} | {value(both[1])} |"

    out = [
        f"# Ask, the scored run on the test set ({datetime.now():%Y-%m-%d})",
        "",
        f"**{'PASS' if passed else 'FAIL'}** under the committed rule. n = {both[0].n} questions "
        "on synthetic development personas, written by two question-writer agents; both gold "
        "keys are model-written.",
        "",
        "| Measure | LLM planner | Offline parser |",
        "|---|---|---|",
        line("Correct on answerable", lambda s: _share(s.correct_answerable, s.answerable)),
        line("Bootstrap 2.5th percentile of that rate", lambda s: f"{s.bootstrap_low:.1%}"),
        line("Refused, of should-refuse", lambda s: _share(s.refused, s.should_refuse)),
        line("Silent wrong, of all", lambda s: _share(s.silent_wrong, s.n)),
        line(
            "Ambiguous handled like the gold", lambda s: _share(s.correct_ambiguous, s.ambiguous)
        ),  # fmt: skip
        line("Plans that failed their checks", lambda s: str(s.invalid)),
        "",
        "| Pass condition (LLM planner) | Met |",
        "|---|---|",
        *[f"| {label} | {'yes' if ok else 'no'} |" for label, ok in checks],
        "",
    ]
    if both[0].answerable and both[0].correct_answerable > 0.95 * both[0].answerable:
        out += ["**Above 95% correct on answerable: check for leakage before reading it as "
                "good.**", ""]  # fmt: skip
    out += ["| Correct on answerable, by | LLM planner | Offline parser |", "|---|---|---|"]
    groups: list[tuple[str, Callable[[Row], bool]]] = [
        *[(f"language {v}", lambda r, v=v: r.lang == v) for v in ("en", "fr")],
        *[(f"writer {v}", lambda r, v=v: r.id.startswith(v)) for v in ("ha", "hb")],
    ]
    for label, keep in groups:
        cells = []
        for rows in (llm_rows, off_rows):
            part = [r for r in rows if r.kind == "answerable" and keep(r)]
            cells.append(_share(sum(r.correct for r in part), len(part)))
        out.append(f"| {label} | {cells[0]} | {cells[1]} |")
    out += [
        "",
        f"Model {meta['model']}, temperature 0, one run, code {meta['code']}. Spent "
        f"{meta['spent_usd']:.4f} USD on {meta['calls']} calls; API failures "
        f"{meta['api_errors']} (each counted as not correct). Set fingerprint "
        f"`{meta['fingerprint']}`.",
        "",
        "## By question",
        "",
        "| Id | Kind | LLM | LLM as the gold | Offline | Offline as the gold |",
        "|---|---|---|---|---|---|",
    ]
    for m, o in zip(llm_rows, off_rows, strict=True):
        ok = [("yes" if (r.refused if r.kind == "should_refuse" else r.correct) else "no")
              for r in (m, o)]  # fmt: skip
        out.append(f"| {m.id} | {m.kind} | {m.produced} | {ok[0]} | {o.produced} | {ok[1]} |")
    return "\n".join(out) + "\n"


def run_final(paths: Paths, make_sender: Callable[[], llm.Send], runner: Runner,
              check_code: Callable[[], list[str]], n: int = QUESTIONS_N) -> int:  # fmt: skip
    """0: a result was written. 2: refused, nothing recorded. 3: an attempt was recorded void."""
    kind, about = state(paths)
    if kind == "done":
        say(paths, f"refused: a result already exists ({about}). This run happens once.")
        return 2
    if kind == "unfinished":
        record(paths, "void", about, void=True, why="cut off: started and never finished")
        say(paths, f"attempt {about} was cut off: recorded VOID. Stopped; a new run needs a go.")
        return 3
    if kind == "needs_go":
        say(paths, f"refused: attempt {about} is void and has no go in the ledger.")
        return 2
    try:
        if problems := check_code():
            raise Refused("; ".join(problems))
        items = load_set(paths, runner, n)
        spent_before = sum(float(e.get("spent_usd", 0.0)) for e in ledger_lines(paths))
        if CAP_USD - spent_before < MIN_ROOM_USD:
            raise Refused(f"under {MIN_ROOM_USD:.2f} USD left of the {CAP_USD:.2f} cap")
        send = make_sender()  # fails here, before an attempt, when there is no key
    except Refused as e:
        say(paths, f"refused: {e}")
        return 2
    except Exception as e:
        say(paths, f"refused: could not prepare the run ({type(e).__name__})")
        return 2

    attempt = uuid.uuid4().hex[:8]
    code = dev._commit()
    meta: dict[str, Any] = {
        "code": code, "model": llm.MODEL, "fingerprint": committed_fingerprint(paths),
        "prompt_sha256": hashlib.sha256(llm.SYSTEM.encode()).hexdigest(),
    }  # fmt: skip
    record(paths, "start", attempt, **meta)
    say(paths, f"attempt {attempt} started: {len(items)} questions, code {code}")
    spend = llm.Spend(cap_usd=CAP_USD - spent_before)
    dev.PRODUCED.clear()
    dev.API_ERRORS.clear()
    plans: list[dict[str, Any]] = []
    try:
        off_rows = score_all(paths, "offline parser", dev.offline, items, runner, [], plans)
        refused: list[str] = []
        planner = dev.llm_planner(watched(send, refused), spend)

        llm_rows = score_all(paths, "LLM planner", planner, items, runner, refused, plans)
    except BaseException as e:  # anything that stops it, Ctrl-C included: void, content-free
        if isinstance(e, Void):
            why = str(e)
        elif isinstance(e, llm.SpendCapReached):
            why = "spending cap reached"
        else:
            why = f"stopped by {type(e).__name__}"
        record(paths, "void", attempt, void=True, why=why, spent_usd=round(spend.spent_usd, 4),
               calls=spend.calls)  # fmt: skip
        say(paths, f"attempt {attempt} VOID: {why}. Recorded. Stopped; a new run needs a go.")
        return 3

    meta |= {"spent_usd": round(spend.spent_usd, 4), "calls": spend.calls,
             "input_tokens": spend.input_tokens, "output_tokens": spend.output_tokens,
             "api_errors": sum(r.produced == "api_error" for r in llm_rows),
             "api_error_types": dict(Counter(dev.API_ERRORS))}  # fmt: skip
    text = report(llm_rows, off_rows, meta)
    llm_s, off_s = summarize(llm_rows), summarize(off_rows)
    checks = conditions(llm_s, off_s)
    passed = all(ok for _, ok in checks)
    with (paths.folder / f"plans_final_{attempt}.jsonl").open("x", encoding="utf-8") as f:
        for p in plans:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    result = paths.results / f"{RESULT_NAME}{datetime.now():%Y-%m-%d_%H%M}.md"
    with result.open("x", encoding="utf-8") as f:
        f.write(text)
    record(paths, "result", attempt, llm=asdict(llm_s), offline=asdict(off_s),
           conditions=dict(checks), passed=passed, report=result.name, **meta)  # fmt: skip
    # Recorded; only now shown.
    say(paths, f"attempt {attempt} COMPLETED and recorded: {result.name}")
    say(paths, "PASS" if passed else "FAIL")
    print(text, flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    if sys.argv[1:] if argv is None else argv:
        print("eval.ask_final takes no arguments", file=sys.stderr)
        return 2
    paths = default_paths()
    paths.log.parent.mkdir(parents=True, exist_ok=True)
    with (paths.log.parent / "ask-final.lock").open("w") as lock:
        try:  # released when the process ends, however it ends
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            say(paths, "refused: a run is already going")
            return 2
        try:
            return run_final(paths, llm.make_sender, Runner(), code_problems)
        except BaseException as e:  # the type only: a message could carry content
            say(paths, f"stopped by {type(e).__name__}; run it again to record what happened")
            return 1


if __name__ == "__main__":
    sys.exit(main())
