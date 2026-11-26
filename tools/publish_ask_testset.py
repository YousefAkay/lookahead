"""Publish Ask's test set after its one scored run (spec docs/specs/2026-11-20-ask.md, line 78).

Copies the 100 questions and the three answer keys from the blocked folder into
docs/results/ask_testset/, and checks every file against the SHA-256 fingerprints committed
before the scored run (docs/results/ask_test_set_fingerprint.md, 2026-11-25). The review files
(review.txt, answers.txt, review_cases.json) are not copied.

    python -m tools.publish_ask_testset

Prints only file names, line counts and whether each fingerprint matches. Refuses (exit 1,
nothing copied) if any source file is missing or differs; a copy that does not match afterwards
is removed and the run exits 1.
"""

import hashlib
import shutil
import sys
from pathlib import Path

from eval.ask_heldout import FINAL, FOLDER, GOLD, QUESTION_FILES

DESTINATION = Path(__file__).resolve().parents[1] / "docs" / "results" / "ask_testset"

# Committed 2026-11-25, before the scored run (set fingerprint 9fb38ba7...).
EXPECTED = {
    QUESTION_FILES[0]: "0854b44fb19390bf3b26eb13967bbbeb58b0fce29a5c92ce5727ac5186a459a2",
    QUESTION_FILES[1]: "18af90ad8fad5cda0dbbae3995403e7d75f97d9e87f399281fbabe86f3d55f0c",
    GOLD["a"]: "ca3c9ade3c4ec24d058df984b99ce3615c63475648e8efe87f954cf805ce61e2",
    GOLD["b"]: "7c8e465799467ccfca570934b2e59589062914b38ea890271e0b48c88fb090cb",
    FINAL: "95c65c2f69916b1397f2c77dbd93895c3f86f88d15f51d73e753440210a7955a",
}


def _digest(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lines(path: Path) -> int:
    """Number of lines in a file."""
    return len(path.read_bytes().splitlines())


def publish(source: Path = FOLDER, destination: Path = DESTINATION) -> tuple[bool, list[str]]:
    """Check, copy, check again. Returns (all matched, report lines without any file content)."""
    report: list[str] = []
    ok = True
    for name, expected in EXPECTED.items():
        path = source / name
        if not path.is_file():
            report.append(f"{name}: missing in the source folder")
            ok = False
            continue
        match = _digest(path) == expected
        report.append(
            f"{name}: {_lines(path)} lines, source matches fingerprint: {'yes' if match else 'no'}"
        )
        ok = ok and match
    if not ok:
        report.append("Refused: nothing copied.")
        return False, report

    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    try:
        for name, expected in EXPECTED.items():
            target = destination / name
            shutil.copyfile(source / name, target)
            copied.append(target)
            match = _digest(target) == expected
            report.append(
                f"{name}: {_lines(target)} lines, copy matches fingerprint: "
                f"{'yes' if match else 'no'}"
            )
            ok = ok and match
    except OSError as e:
        report.append(f"Copy failed: {type(e).__name__}.")
        ok = False
    if not ok:
        for target in copied:
            target.unlink(missing_ok=True)
        report.append("Refused: a copy failed or did not match; every copy was removed.")
        return False, report
    report.append(f"Done: {len(copied)} files copied.")
    return True, report


def main() -> int:
    """Run the publish and print the report."""
    ok, report = publish()
    print("\n".join(report))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
