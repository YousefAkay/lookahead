"""tools.publish_ask_testset on made-up files in a temporary folder (never the real set)."""

import hashlib
from pathlib import Path

import pytest

from tools import publish_ask_testset as pub

SECRET = "question text that must never be printed"


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake source folder: the 5 published files plus the review files, fingerprints faked."""
    folder = tmp_path / "source"
    folder.mkdir()
    expected = {}
    for i, name in enumerate(pub.EXPECTED):
        data = f'{{"id": "x-{i}", "text": "{SECRET}"}}\n{{"id": "y-{i}"}}\n'.encode()
        (folder / name).write_bytes(data)
        expected[name] = hashlib.sha256(data).hexdigest()
    for name in ("review.txt", "answers.txt", "review_cases.json"):
        (folder / name).write_text("private review\n", encoding="utf-8")
    monkeypatch.setattr(pub, "EXPECTED", expected)
    return folder


def test_matching_files_are_copied_byte_for_byte(source: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    ok, report = pub.publish(source, out)
    assert ok
    assert sorted(p.name for p in out.iterdir()) == sorted(pub.EXPECTED)
    for name in pub.EXPECTED:
        assert (out / name).read_bytes() == (source / name).read_bytes()
    assert report[-1] == "Done: 5 files copied."
    assert sum("copy matches fingerprint: yes" in line for line in report) == 5
    assert all("2 lines" in line for line in report[:-1])


def test_the_report_holds_no_file_content(source: Path, tmp_path: Path) -> None:
    _, report = pub.publish(source, tmp_path / "out")
    assert not any(SECRET in line or "x-0" in line for line in report)


def test_review_files_are_not_copied(source: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    pub.publish(source, out)
    names = {p.name for p in out.iterdir()}
    assert names.isdisjoint({"review.txt", "answers.txt", "review_cases.json"})


def test_a_changed_file_refuses_and_copies_nothing(source: Path, tmp_path: Path) -> None:
    changed = next(iter(pub.EXPECTED))
    (source / changed).write_bytes(b"edited\n")
    out = tmp_path / "out"
    ok, report = pub.publish(source, out)
    assert not ok
    assert not out.exists()
    assert f"{changed}: 1 lines, source matches fingerprint: no" in report
    assert report[-1] == "Refused: nothing copied."


def test_a_missing_file_refuses_and_copies_nothing(source: Path, tmp_path: Path) -> None:
    missing = list(pub.EXPECTED)[-1]
    (source / missing).unlink()
    out = tmp_path / "out"
    ok, report = pub.publish(source, out)
    assert not ok
    assert not out.exists()
    assert f"{missing}: missing in the source folder" in report


def test_the_fingerprints_are_the_committed_ones() -> None:
    """The five expected digests equal the 2026-11-25 table, and the review files are absent."""
    table = Path(pub.__file__).resolve().parents[1] / "docs/results/ask_test_set_fingerprint.md"
    section = table.read_text(encoding="utf-8").split("## 2026-11-25")[1]
    for name, digest in pub.EXPECTED.items():
        assert f"| {name} | " in section and digest in section
    assert len(pub.EXPECTED) == 5
    assert not {"review.txt", "answers.txt", "review_cases.json"} & set(pub.EXPECTED)
