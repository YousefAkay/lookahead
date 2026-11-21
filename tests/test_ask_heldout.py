"""The held-out module (spec docs/specs/2026-11-20-ask.md, step 9), on made-up files in a temporary
folder, never the real one: the fingerprint (its hash is committed before the scored run), the
review files Yousef answers with one letter per case, and how his answers become the final gold.
"""

import hashlib
import json
from pathlib import Path

import eval.ask_heldout as held

REFUSE = [{"op": "cannot_answer", "reason": "advice"}]
CLARIFY = [{"op": "clarify", "about": "subject"}]


def q(qid: str) -> str:
    return json.dumps({"id": qid, "persona": "v2-SAL-01", "text": "q", "lang": "en"})


def plan(qid: str, steps: list, expect: str = "refused") -> dict:  # type: ignore[type-arg]
    return {"id": qid, "expect": expect, "ambiguous": False, "steps": steps}


def write(path: Path, rows: list) -> None:  # type: ignore[type-arg]
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows),
                    encoding="utf-8")  # fmt: skip


def made_up(folder: Path) -> None:
    """ha-001: both agree (a spot check); ha-002: A refuses, B asks back (a disagreement);
    hb-001: plan A cannot run (a disagreement)."""
    write(folder / held.QUESTION_FILES[0], [q("ha-001"), q("ha-002")])
    write(folder / held.QUESTION_FILES[1], [q("hb-001")])
    write(folder / held.GOLD["a"], [plan("ha-001", REFUSE), plan("ha-002", REFUSE),
                                    plan("hb-001", [{"op": "nonsense"}])])  # fmt: skip
    write(folder / held.GOLD["b"], [plan("ha-001", REFUSE), plan("ha-002", CLARIFY, "clarify"),
                                    plan("hb-001", REFUSE)])  # fmt: skip


def test_the_fingerprint_is_fixed_and_follows_every_byte(tmp_path: Path) -> None:
    (tmp_path / held.AGENT).write_bytes(b'{"id": "ha-001"}\n')
    lines = held.fingerprint(tmp_path)
    digest = hashlib.sha256(b'{"id": "ha-001"}\n').hexdigest()
    assert lines[0] == f"{held.AGENT}: 1 lines, sha256 {digest}"
    assert lines[1] == f"{held.QUESTION_FILES[1]}: absent"
    overall = hashlib.sha256(held.AGENT.encode() + b"\0" + digest.encode() + b"\n").hexdigest()
    assert lines[-1] == f"set: sha256 {overall}"
    (tmp_path / held.AGENT).write_bytes(b'{"id": "ha-002"}\n')
    assert held.fingerprint(tmp_path)[-1] != lines[-1]


def test_the_review_shows_cases_side_by_side_and_prints_only_counts(
    tmp_path: Path,
    monkeypatch,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    made_up(tmp_path)
    monkeypatch.setattr(held, "FOLDER", tmp_path)
    assert held.main(["disagreements"]) == 0
    out = capsys.readouterr().out
    assert "2 disagreements, 1 spot checks" in out and "gives" not in out and "q" not in out[:5]
    cases = json.loads((tmp_path / held.CASES).read_text("utf-8"))
    assert [(c["case"], c["question"]) for c in cases] == [
        ("d01", "ha-002"), ("d02", "hb-001"), ("s01", "ha-001")]  # fmt: skip
    text = (tmp_path / held.REVIEW).read_text("utf-8")
    assert "=== d01" in text and " | " in text and "could not run (PlanError)" in text
    assert "gives: I can't give advice" in text  # what plan A computes, in plain words
    assert (tmp_path / held.ANSWERS).read_text("utf-8") == "d01: \nd02: \ns01: \n"


def test_answers_become_the_final_gold(tmp_path: Path) -> None:
    made_up(tmp_path)
    held.write_review(tmp_path)
    (tmp_path / held.ANSWERS).write_text("d01: OK\nd02: x\ns01: ok\n", "utf-8")
    lines = held.apply_review(tmp_path)
    assert "d01: a disagreement takes A, B or X" in lines
    assert "d02: X needs one line saying what the right plan does" in lines
    assert not (tmp_path / held.FINAL).exists()
    (tmp_path / held.ANSWERS).write_text("d01: b\nd02: X it should refuse: advice\ns01: OK\n",
                                         "utf-8")  # fmt: skip
    lines = held.apply_review(tmp_path)
    assert lines[0] == "answers: 3 of 3 cases (B 1, OK 1, X 1)"
    assert lines[1] == "X cases still waiting for the fixer: 1 (d02)"
    assert not (tmp_path / held.FINAL).exists()
    assert held.write_review(tmp_path)[0].startswith("answers.txt already has answers")
    write(tmp_path / held.FIXES, [plan("hb-001", REFUSE)])
    assert held.check_fixes(tmp_path)[-1] == "problems: none"
    assert held.apply_review(tmp_path)[-1] == "wrote gold_final.jsonl: 3 plans"
    final = held.read_gold(tmp_path / held.FINAL)
    assert final["ha-002"]["steps"] == CLARIFY  # B chosen
    assert final["hb-001"]["steps"] == REFUSE  # the fix
    assert final["ha-001"]["steps"] == REFUSE  # agreed
    assert held.check_final(tmp_path)[-1] == "problems: none"


def test_problem_lines_never_carry_the_review_text(tmp_path: Path) -> None:
    """Review B1 (2026-11-21): a line without its colon, a wrapped line or a duplicate is named by
    case id or line number only; no word of an explanation reaches the output."""
    made_up(tmp_path)
    held.write_review(tmp_path)
    (tmp_path / held.ANSWERS).write_text(
        "d01 X zebrawords only the dining rows\n"
        "   and leave out quokkatext: both months\n"
        "d02: A\nd02: B\n"
        "s01 - OK walrusnote\n",
        "utf-8",
    )
    out = "\n".join(held.apply_review(tmp_path))
    for word in ("zebrawords", "dining", "quokkatext", "months", "walrusnote"):
        assert word not in out
    assert "d01: not understood" in out
    assert "answers.txt line 2: not understood" in out
    assert "d02: answered twice" in out
    assert "s01: not understood" in out


def test_a_stale_final_gold_is_removed_and_missing_plans_are_named(tmp_path: Path) -> None:
    made_up(tmp_path)
    held.write_review(tmp_path)
    (tmp_path / held.ANSWERS).write_text("d01: A\nd02: B\ns01: OK\n", "utf-8")
    assert held.apply_review(tmp_path)[-1] == "wrote gold_final.jsonl: 3 plans"
    (tmp_path / held.ANSWERS).write_text("d01: A\nd02: B\ns01: X it should ask back\n", "utf-8")
    held.apply_review(tmp_path)
    assert not (tmp_path / held.FINAL).exists()  # never an older final gold left in place
    write(tmp_path / held.GOLD["b"], [plan("ha-001", REFUSE), plan("ha-002", CLARIFY, "clarify")])
    assert "hb-001: no plan in gold B" in held.apply_review(tmp_path)


def test_each_question_writer_sees_only_its_own_file(tmp_path: Path) -> None:
    write(tmp_path / held.QUESTION_FILES[0], [q("ha-001"), q("ha-002")])
    write(tmp_path / held.QUESTION_FILES[1], [q("hb-001"), q("ha-009")])  # a wrong prefix
    a = held.check_questions(tmp_path, "a")
    b = held.check_questions(tmp_path, "b")
    assert a[0] == "questions: 2 (ha: 2, hb: 0)" and a[-1] == "problems: none"
    assert b[0] == "questions: 2 (ha: 1, hb: 1)"
    assert "ha-009: id should start with hb- in questions_agent_b.jsonl" in b
    assert not any("ha-001" in line or "ha-002" in line for line in b)


def test_text_in_an_id_is_never_printed(tmp_path: Path) -> None:
    """Review B2 (2026-11-21): an id not in the form ha-001 is named by file and line only, in
    every command the session may run."""
    bad = json.dumps({"id": "ha-007 platypusword grocery", "persona": "v2-SAL-01", "text": "q"})
    write(tmp_path / held.QUESTION_FILES[0], [q("ha-001"), bad])
    write(tmp_path / held.GOLD["a"], [plan("ha-001", REFUSE),
                                      {**plan("x", REFUSE), "id": "ha-009 otterword"}])  # fmt: skip
    write(tmp_path / held.GOLD["b"], [plan("ha-001", REFUSE)])
    outputs = [held.check_questions(tmp_path, "a"), held.check_gold("a", tmp_path),
               held.apply_review_or_none(tmp_path), held.check_final(tmp_path)]  # fmt: skip
    text = "\n".join(line for out in outputs for line in out)
    assert "platypusword" not in text and "otterword" not in text and "grocery" not in text
    assert "questions_agent.jsonl line 2: id not in the form ha-001 / hb-001" in text
    assert "gold_a.jsonl line 2: a plan for no question" in text


def test_apply_review_with_a_bad_id_prints_no_text_and_a_restart_clears_the_final_gold(
    tmp_path: Path,
) -> None:
    """Review N1/N2: apply-review itself (with review files) on a question whose id carries text,
    and a restarted review leaves no older final gold behind."""
    made_up(tmp_path)
    held.write_review(tmp_path)
    (tmp_path / held.ANSWERS).write_text("d01: A\nd02: B\ns01: OK\n", "utf-8")
    assert held.apply_review(tmp_path)[-1] == "wrote gold_final.jsonl: 3 plans"
    (tmp_path / held.ANSWERS).unlink()  # he starts the review again
    held.apply_review_or_none(tmp_path)
    assert not (tmp_path / held.FINAL).exists()
    bad = json.dumps({"id": "hb-001 narwhalword\n", "persona": "v2-SAL-01", "text": "q"})
    write(tmp_path / held.QUESTION_FILES[1], [bad])
    held.write_review(tmp_path)
    out = "\n".join(held.apply_review(tmp_path))
    assert "narwhalword" not in out and "questions_agent_b.jsonl line 1" in out
    assert held.check_questions(tmp_path, "b")[-1].endswith("id not in the form ha-001 / hb-001")


def test_no_persona_or_language_text_is_printed(tmp_path: Path) -> None:
    """Review round 4, B1: persona and language values not on the sheet count as "other"."""
    odd = json.dumps({"id": "hb-001", "persona": "v2-SAL-01 dugongword", "text": "q",
                      "lang": "en manateeword"})  # fmt: skip
    write(tmp_path / held.QUESTION_FILES[1], [odd, q("hb-002")])
    out = "\n".join(held.check_questions(tmp_path, "b"))
    assert "dugongword" not in out and "manateeword" not in out
    assert "by persona: other 1, v2-SAL-01 1" in out and "by language: en 1, other 1" in out


def test_a_new_review_removes_an_older_final_gold_and_says_so(tmp_path: Path) -> None:
    """Review round 4, N1/N3: write_review itself removes it (answers not yet given)."""
    made_up(tmp_path)
    (tmp_path / held.FINAL).write_text("{}\n", "utf-8")
    out = held.write_review(tmp_path)[0]
    assert not (tmp_path / held.FINAL).exists() and out.endswith(
        "(removed an older gold_final.jsonl)"
    )
    (tmp_path / held.FINAL).write_text("{}\n", "utf-8")
    (tmp_path / held.ANSWERS).unlink()
    assert held.apply_review_or_none(tmp_path)[0].endswith("(removed an older gold_final.jsonl)")


def test_ids_must_match_in_full(tmp_path: Path) -> None:
    """Review round 4, N2: a trailing newline or non-ASCII digits (accepted by the old pattern)
    are named by file and line."""
    rows = [json.dumps({"id": i, "persona": "v2-SAL-01", "text": "q"})
            for i in ("ha-001\n", "ha-\u0660\u0660\u0661", "ha-002")]  # fmt: skip
    write(tmp_path / held.QUESTION_FILES[0], rows)
    ids = [x.id for x in held.read_questions(tmp_path / held.QUESTION_FILES[0])]
    assert ids == ["questions_agent.jsonl line 1", "questions_agent.jsonl line 2", "ha-002"]
