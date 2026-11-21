"""The held-out module (spec docs/specs/2026-11-20-ask.md, step 9), on made-up files in a temporary
folder, never the real one: id numbering of Yousef's file, the fingerprint (its hash is
committed before the scored run), and that the printout shows no content."""

import hashlib
from pathlib import Path

import eval.ask_heldout as held


def test_yousef_ids_skip_blank_and_comment_lines(tmp_path: Path) -> None:
    path = tmp_path / held.YOUSEF
    path.write_text("# notes\nv2-SAL-01\tfirst?\n\n  # more\nv2-TIGHT-03\tsecond?\n", "utf-8")
    qs = held.read_yousef(path)
    assert [(q.id, q.persona, q.text) for q in qs] == [
        ("hy-001", "v2-SAL-01", "first?"),
        ("hy-002", "v2-TIGHT-03", "second?"),
    ]


def test_the_fingerprint_is_fixed_and_follows_every_byte(tmp_path: Path) -> None:
    (tmp_path / held.AGENT).write_bytes(b'{"id": "ha-001"}\n')
    lines = held.fingerprint(tmp_path)
    digest = hashlib.sha256(b'{"id": "ha-001"}\n').hexdigest()
    assert lines[0] == f"{held.YOUSEF}: absent"
    assert lines[1] == f"{held.AGENT}: 1 lines, sha256 {digest}"
    overall = hashlib.sha256(held.AGENT.encode() + b"\0" + digest.encode() + b"\n").hexdigest()
    assert lines[-1] == f"set: sha256 {overall}"
    (tmp_path / held.AGENT).write_bytes(b'{"id": "ha-002"}\n')
    assert held.fingerprint(tmp_path)[-1] != lines[-1]


def test_the_printout_writes_into_the_folder_and_prints_no_content(
    tmp_path: Path,
    monkeypatch,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    """Review B1b (2026-11-21): whatever route runs it, the command shows only a line count; the
    text goes into the (blocked) folder. Made-up empty folder: nothing to compare."""
    monkeypatch.setattr(held, "FOLDER", tmp_path)
    for name in held.GOLD.values():
        (tmp_path / name).write_text("", encoding="utf-8")
    assert held.main(["disagreements"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("wrote ") and "lines to disagreements.txt" in out
    assert "differ" not in out and "agreed" not in out
    assert (tmp_path / held.REVIEW).read_text(encoding="utf-8").startswith("0 of 0 differ")


def test_a_broken_gold_plan_is_listed_and_the_rest_still_compared(tmp_path: Path) -> None:
    """Review N5 and NB3/NB4: a question whose plan A cannot run is named (A), counted in the
    first line, and the other question is still compared (its two plans agree)."""
    import json

    def line(qid: str) -> str:
        return json.dumps({"id": qid, "persona": "v2-SAL-01", "text": "q", "lang": "en"})

    (tmp_path / held.AGENT).write_text(line("ha-001") + "\n" + line("ha-002") + "\n", "utf-8")
    refuse = [{"op": "cannot_answer", "reason": "advice"}]
    good = {"expect": "refused", "ambiguous": False, "steps": refuse}
    a = [{"id": "ha-001", **good, "steps": [{"op": "nonsense"}]}, {"id": "ha-002", **good}]
    b = [{"id": "ha-001", **good}, {"id": "ha-002", **good}]
    for name, plans in ((held.GOLD["a"], a), (held.GOLD["b"], b)):
        (tmp_path / name).write_text("".join(json.dumps(x) + "\n" for x in plans), "utf-8")
    lines = held.disagreements(tmp_path)
    assert lines[0] == "0 of 1 differ, 1 could not run"
    assert "== ha-001: could not run: A (PlanError)" in lines
