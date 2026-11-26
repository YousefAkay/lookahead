"""The scored run's harness (spec docs/specs/2026-11-20-ask.md, step 10), on development
questions written into a temporary folder and a stand-in for the API: it records before it shows,
runs once, voids what did not finish, and applies the committed pass rule."""

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import eval.ask_final as final
from eval.ask_dev_set import QUESTIONS
from eval.ask_heldout import fingerprint
from eval.ask_score import Runner, Summary

PERSONA = QUESTIONS[0].persona
PICK = [q for q in QUESTIONS if q.persona == PERSONA and not q.ambiguous]
ITEMS = ([q for q in PICK if q.expect == "answer"][:4]
         + [q for q in PICK if q.expect == "refused"][:2])  # fmt: skip
N = len(ITEMS)


@pytest.fixture(scope="module")
def personas(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("personas")


@pytest.fixture
def runner(personas: Path) -> Runner:
    return Runner(personas)  # a new one per test: each test closes its connections


def make(tmp_path: Path) -> final.Paths:
    folder, results = tmp_path / "set", tmp_path / "results"
    folder.mkdir()
    results.mkdir()
    ids = [f"{'ha' if i % 2 == 0 else 'hb'}-{i:03d}" for i in range(N)]
    for name, prefix in (("questions_agent.jsonl", "ha"), ("questions_agent_b.jsonl", "hb")):
        (folder / name).write_text("".join(
            json.dumps({"id": i, "persona": q.persona, "text": q.text, "lang": q.lang}) + "\n"
            for i, q in zip(ids, ITEMS, strict=True) if i.startswith(prefix)))  # fmt: skip
    (folder / "gold_final.jsonl").write_text("".join(
        json.dumps({"id": i, "expect": q.expect, "ambiguous": False, "steps": list(q.steps)})
        + "\n" for i, q in zip(ids, ITEMS, strict=True)))  # fmt: skip
    prints = results / "fingerprints.md"
    prints.write_text(f"Set fingerprint: `{'0' * 64}`\n"
                      f"Set fingerprint: `{fingerprint(folder)[-1].split()[-1]}`\n")  # fmt: skip
    return final.Paths(folder, results, results / "ledger.jsonl", prints,
                       tmp_path / "logs" / "run.log")  # fmt: skip


class ApiError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class Fake:
    """Answers each question with its gold plan; `fail` maps a call number to what it raises."""

    def __init__(self, fail: dict[int, BaseException] | None = None) -> None:
        self.fail = fail or {}
        self.calls = 0

    def __call__(self, request: dict[str, Any]) -> Any:
        self.calls += 1
        if self.calls in self.fail:
            raise self.fail[self.calls]
        text = request["messages"][0]["content"]
        plan = next(q.gold for q in ITEMS if q.text == text)
        return SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", input=plan)],
            usage=SimpleNamespace(input_tokens=3000, output_tokens=200))  # fmt: skip


def go(paths: final.Paths, runner: Runner, send: Fake | None = None,
       code: Callable[[], list[str]] = list, n: int = N) -> int:  # fmt: skip
    fake = send or Fake()
    return final.run_final(paths, lambda: fake, runner, code, n)


def events(paths: final.Paths) -> list[str]:
    return [e["event"] for e in final.ledger_lines(paths) if e["set"] == final.SET]


def everything_written(paths: final.Paths) -> str:
    files = [paths.log, paths.ledger, *paths.results.glob("ask_final_result_*.md")]
    return "".join(f.read_text(encoding="utf-8") for f in files if f.exists())


def test_a_run_is_recorded_and_holds_no_question_or_plan(tmp_path: Path, runner: Runner) -> None:
    paths = make(tmp_path)
    assert go(paths, runner) == 0
    assert events(paths) == ["start", "result"]
    entry = final.ledger_lines(paths)[-1]
    assert entry["llm"]["correct_answerable"] == entry["llm"]["answerable"] == 4
    assert entry["llm"]["refused"] == entry["llm"]["should_refuse"] == 2
    assert entry["offline"]["n"] == N and entry["calls"] == N
    assert set(entry["conditions"]) and isinstance(entry["passed"], bool)
    written = everything_written(paths)
    assert "ha-000" in written and ("PASS" in written or "FAIL" in written)
    assert "check for leakage" in written  # 4 of 4 is above 95%
    for q in ITEMS:
        assert q.text not in written
    assert "cannot_answer" not in written
    saved = list(paths.folder.glob("plans_final_*.jsonl"))
    assert len(saved) == 1 and len(saved[0].read_text().splitlines()) == N


def test_the_result_is_on_disk_before_anything_about_it_is_shown(
    tmp_path: Path, runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = make(tmp_path)
    seen: list[tuple[str, bool]] = []

    def watch(*args: Any, **_: Any) -> None:
        recorded = "result" in events(paths) and bool(
            list(paths.results.glob("ask_final_result_*.md"))
        )
        seen.append((" ".join(map(str, args)), recorded))

    monkeypatch.setattr("builtins.print", watch)
    assert go(paths, runner) == 0
    shown = [(t, ok) for t, ok in seen if "PASS" in t or "FAIL" in t or "Correct on" in t]
    assert shown and all(ok for _, ok in shown)


def test_it_runs_once(tmp_path: Path, runner: Runner) -> None:
    paths = make(tmp_path)
    assert go(paths, runner) == 0
    before = paths.ledger.read_text()
    fake = Fake()
    assert go(paths, runner, fake) == 2
    assert fake.calls == 0 and paths.ledger.read_text() == before
    paths.ledger.unlink()  # a result file alone still refuses
    assert go(paths, runner, fake) == 2 and fake.calls == 0


@pytest.mark.parametrize("error", [
    ApiError("Your credit balance is too low to access the API", 400),
    ApiError("invalid x-api-key", 401),
    ApiError("forbidden", 403),
    KeyboardInterrupt(),
])  # fmt: skip
def test_a_stopped_attempt_is_void_and_waits_for_a_go(
    tmp_path: Path, runner: Runner, error: BaseException
) -> None:
    paths = make(tmp_path)
    assert go(paths, runner, Fake({3: error})) == 3
    assert events(paths) == ["start", "void"]
    assert not list(paths.results.glob("ask_final_result_*.md"))
    assert "credit balance is too low" not in everything_written(paths)
    assert "PASS" not in everything_written(paths)
    fake = Fake()
    assert go(paths, runner, fake) == 2 and fake.calls == 0
    attempt = final.ledger_lines(paths)[-1]["attempt"]
    final.record(paths, "go", "another")  # a go for some other attempt does not count
    assert go(paths, runner, fake) == 2 and fake.calls == 0
    final.record(paths, "go", attempt)
    assert go(paths, runner, fake) == 0
    assert events(paths)[-2:] == ["start", "result"]


def test_an_attempt_that_never_finished_is_recorded_void_and_stops(
    tmp_path: Path, runner: Runner
) -> None:
    paths = make(tmp_path)
    final.record(paths, "start", "dead0001")
    fake = Fake()
    assert go(paths, runner, fake) == 3 and fake.calls == 0
    assert events(paths) == ["start", "void"]
    assert go(paths, runner, fake) == 2 and fake.calls == 0


def test_one_failed_question_counts_wrong_and_the_run_goes_on(
    tmp_path: Path, runner: Runner
) -> None:
    paths = make(tmp_path)
    assert go(paths, runner, Fake({1: ApiError("overloaded", 529)})) == 0
    entry = final.ledger_lines(paths)[-1]
    assert entry["api_errors"] == 1 and entry["llm"]["correct_answerable"] == 3
    assert entry["llm"]["silent_wrong"] == 0 and entry["passed"] is False  # 3 of 4 is under 85%


def test_the_spending_cap_voids_the_attempt(
    tmp_path: Path, runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = make(tmp_path)
    monkeypatch.setattr(final, "CAP_USD", 0.02)
    monkeypatch.setattr(final, "MIN_ROOM_USD", 0.0)
    assert go(paths, runner) == 3
    assert final.ledger_lines(paths)[-1]["why"].startswith("spending cap reached (at LLM planner, ")


def test_refusals_before_an_attempt_record_nothing(tmp_path: Path, runner: Runner) -> None:
    paths = make(tmp_path)
    fake = Fake()
    assert go(paths, runner, fake, code=lambda: ["uncommitted changes"]) == 2
    assert go(paths, runner, fake, n=N + 1) == 2

    def no_key() -> Any:
        raise RuntimeError("not set")

    assert final.run_final(paths, no_key, runner, list, N) == 2
    final.record(paths, "note", "x", spent_usd=final.CAP_USD)
    assert go(paths, runner, fake) == 2
    paths.ledger.unlink()
    with (paths.folder / "gold_final.jsonl").open("a") as f:  # no longer the committed file
        f.write("\n")
    assert go(paths, runner, fake) == 2
    assert fake.calls == 0 and not paths.ledger.exists()


BASE = Summary(n=100, answerable=75, correct_answerable=64, bootstrap_low=0.75,
               should_refuse=10, refused=9, silent_wrong=5, ambiguous=15, correct_ambiguous=0,
               invalid=0, refused_no_plan=0)  # fmt: skip
OFFLINE = replace(BASE, correct_answerable=49)


def met(llm: Summary, offline: Summary = OFFLINE) -> list[bool]:
    return [ok for _, ok in final.conditions(llm, offline)]


def test_the_pass_rule_at_its_edges() -> None:
    assert met(BASE) == [True] * 5  # 64 of 75 is 85.3%; 15 more of 75 is 20 points
    assert met(replace(BASE, correct_answerable=63))[0] is False  # 84.0%
    assert met(replace(BASE, bootstrap_low=0.7499))[1] is False
    assert met(replace(BASE, refused=8))[2] is False
    assert met(replace(BASE, silent_wrong=6))[3] is False
    assert met(BASE, replace(OFFLINE, correct_answerable=50))[4] is False  # 14 of 75: 18.7
    exact = replace(BASE, answerable=20, correct_answerable=17)  # exactly 85%
    assert met(exact, replace(exact, correct_answerable=13))[0::4] == [True, True]  # 20 points
    assert met(replace(BASE, should_refuse=0, refused=0))[2] is False


def test_no_arguments_are_taken() -> None:
    assert final.main(["--again"]) == 2


def test_a_gold_that_asks_back_must_be_marked_ambiguous(tmp_path: Path, runner: Runner) -> None:
    paths = make(tmp_path)
    gold = paths.folder / "gold_final.jsonl"
    lines = [json.loads(x) for x in gold.read_text().splitlines()]
    lines[0] |= {"expect": "clarify", "ambiguous": False,
                 "steps": [{"op": "clarify", "about": "subject"}]}  # fmt: skip
    gold.write_text("".join(json.dumps(x) + "\n" for x in lines))
    paths.fingerprints.write_text(f"Set fingerprint: `{fingerprint(paths.folder)[-1].split()[-1]}`")
    fake = Fake()
    assert go(paths, runner, fake) == 2 and fake.calls == 0 and not paths.ledger.exists()
    assert "ha-000 asks back but is not marked ambiguous" in paths.log.read_text()


def test_a_gold_that_does_not_run_to_what_it_says_refuses(tmp_path: Path, runner: Runner) -> None:
    paths = make(tmp_path)
    gold = paths.folder / "gold_final.jsonl"
    gold.write_text(gold.read_text().replace('"expect": "answer"', '"expect": "not_found"', 1))
    paths.fingerprints.write_text(f"Set fingerprint: `{fingerprint(paths.folder)[-1].split()[-1]}`")
    assert go(paths, runner) == 2 and not paths.ledger.exists()


def test_a_crash_is_void_and_says_where_without_content(
    tmp_path: Path, runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = make(tmp_path)
    real = final.score_one
    count = [0]

    def crash(*args: Any) -> Any:
        count[0] += 1
        if count[0] == N + 2:  # the LLM planner's second question
            raise ValueError(ITEMS[2].text)
        return real(*args)

    monkeypatch.setattr(final, "score_one", crash)
    assert go(paths, runner) == 3
    why = final.ledger_lines(paths)[-1]["why"]
    assert why == "stopped by ValueError (at LLM planner, question ha-002)"
    assert ITEMS[2].text not in everything_written(paths)
    final.record(paths, "go", final.ledger_lines(paths)[-1]["attempt"])
    monkeypatch.setattr(final, "score_one", real)
    assert go(paths, runner) == 0
    result = next(paths.results.glob("ask_final_result_*.md")).read_text()
    assert "stopped by ValueError (at LLM planner, question ha-002)" in result


def test_a_result_file_left_without_its_ledger_line_is_a_cut_off_attempt(
    tmp_path: Path, runner: Runner
) -> None:
    paths = make(tmp_path)
    final.record(paths, "start", "dead0002")
    (paths.results / "ask_final_result_dead0002.md").write_text("")
    fake = Fake()
    assert go(paths, runner, fake) == 3 and fake.calls == 0
    assert events(paths) == ["start", "void"]
    assert go(paths, runner, fake) == 2 and fake.calls == 0
    final.record(paths, "go", "dead0002")
    assert go(paths, runner, fake) == 0
    assert go(paths, runner, fake) == 2
