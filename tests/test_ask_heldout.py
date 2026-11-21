"""The held-out module (spec docs/specs/2026-11-20-ask.md, step 9), on made-up files in a temporary
folder, never the real one: id numbering of Yousef's file, the fingerprint (its hash is
committed before the scored run), and the printout's lock."""

import hashlib
import io
from pathlib import Path

import eval.ask_heldout as held


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


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


def test_the_printout_needs_a_terminal_and_a_typed_confirmation() -> None:
    assert not held.confirm_printout(io.StringIO(), ask=lambda prompt: "SHOW")  # no terminal
    assert not held.confirm_printout(Terminal(), ask=lambda prompt: "yes")
    assert held.confirm_printout(Terminal(), ask=lambda prompt: "SHOW")


def test_the_printout_command_refuses_without_a_terminal(capsys) -> None:  # type: ignore[no-untyped-def]
    assert held.main(["disagreements"]) == 2  # pytest gives no terminal: nothing is read
    assert "Refused" in capsys.readouterr().out
