"""The pre-public history scan: detectors, masking, and a run on a throw-away git repository with
planted, made-up problems. Never prints a match in full."""

import subprocess
from pathlib import Path

from eval.history_scan import mask, mask_email, render, scan, scan_text


def test_masking_keeps_the_first_character_and_the_shape() -> None:
    assert mask("12345678") == "1#######"
    assert mask("Ab-12") == "Ax-##"
    assert mask("ab") == "**"
    assert mask_email("jane.doe@mail.example.net") == "j***@m***.net"


def test_the_detectors_find_made_up_problems() -> None:
    text = "\n".join([
        "write to jane.doe@mailbox.net", "call 514-555-0199", "card 4111 1111 1111 1111",
        "account 123456789012", "PSEUDONYM_KEY=0123456789abcdef0123456789abcdef",
        "sent to Sophie Tremblay", "made with " + "tolipoC"[::-1],
    ])  # fmt: skip
    kinds = sorted({f.kind for f in scan_text(text, "c1", "notes.txt", set())})
    assert kinds == sorted([
        "email address", "phone number", "card-number-like", "account-number-like digits",
        "secret-like string", "possible person's name", "mention of an AI assistant or vendor",
    ])  # fmt: skip
    joined = " ".join(f.masked for f in scan_text(text, "c1", "notes.txt", set()))
    for secret in ("jane.doe", "555-0199", "123456789012", "0123456789abcdef", "Tremblay"):
        assert secret not in joined


def test_numbers_that_are_not_identifiers_are_left_alone() -> None:
    text = "ratio 1.8850040420371867, date 20261010, cents 1500000, hash 3e576d3a9f"
    assert scan_text(text, "c1", "x.json", set()) == []


def test_synthetic_names_are_not_reported() -> None:
    assert scan_text("from Sophie Tremblay", "c", "p", {"Sophie", "Tremblay"}) == []
    assert scan_text("noreply@example.com", "c", "p", set()) == []


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                    "user.email=test@example.com", *args], check=True,
                   capture_output=True)  # fmt: skip


def test_a_whole_history_is_scanned_and_nothing_is_changed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "DECISIONS.md").write_text("# Log\n- a plan\n", encoding="utf-8")
    (repo / "data" / "private").mkdir(parents=True)
    (repo / "data" / "private" / "export.csv").write_text("x\n", encoding="utf-8")
    (repo / ".env").write_text("PSEUDONYM_KEY=\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")
    (repo / "data" / "private" / "export.csv").unlink()  # removed later: still in history
    (repo / "DECISIONS.md").write_text(
        "# Log\n- a plan\n- on my data the balance was $1,234.56\n- a synthetic $50 bill\n",
        encoding="utf-8",
    )
    (repo / "big.bin").write_bytes(b"\0" * 1_100_000)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second, mail me at someone@mailbox.net")
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout  # fmt: skip
    report = scan(repo)
    kinds = [f.kind for f in report.findings]
    assert kinds.count("file that should not be committed") == 2  # data/private and .env
    assert "large file" in kinds
    amounts = [f for f in report.findings if f.kind == "dollar amounts added to DECISIONS.md"]
    assert len(amounts) == 2 and sum(bool(f.note) for f in amounts) == 1
    assert any(f.path == "(commit message)" and f.kind == "email address"
               for f in report.findings)  # fmt: skip
    text = render(report)
    assert "1,234" not in text and "someone@" not in text  # masked, amounts not printed
    assert "t***@e***.com" in text  # the commit author's email, masked
    after = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                           text=True, check=True).stdout  # fmt: skip
    assert after == head
