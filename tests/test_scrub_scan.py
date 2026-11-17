"""The history scrub scan (tools/scrub_scan.py) on a small made-up git repository.

Everything here is invented: the people, amounts and wordings exist only in this file.
"""

import subprocess
from pathlib import Path

import pytest

from tools import scrub_scan
from tools.scrub_scan import load_patterns, mask_email, scan

SECRETS = (
    "SENTINEL-PRIVATE",
    "SENTINEL-ENV",
    "SENTINEL-LOCAL",
    "SENTINEL-PROD",
    "SENTINEL-RC",
    "SENTINEL-DECISIONS",
)
PRIVATE = (
    "data/private/secret.csv",
    ".env",
    ".env.local",
    "config/prod.env",
    ".envrc",
    "DECISIONS-private.md",
)
MADE_UP = ("Zorblax", "Quintworth", "income arrives late", "Glimfood")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()  # fmt: skip


def write(repo: Path, path: str, text: str) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text, encoding="utf-8")


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> dict[str, str]:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.name", "Test Person")
    git(r, "config", "user.email", "test.person@example.org")
    write(r, "DECISIONS.md", "My rent was $1,234.56 this month.\nNothing here.\n")
    write(r, "notes.md", "A plain line about Netflix.\n")
    write(r, "data/synthetic/persona_A.csv", "2026-01-01,Netflix,-1899\n")
    write(r, "docs/prepublic-scrub-report.md", "my old amount $99.99\n")
    write(r, "data/private/secret.csv", "SENTINEL-PRIVATE,$5,555.55 my\n")
    for path, secret in zip(PRIVATE[1:], SECRETS[1:], strict=True):
        write(r, path, f"{secret}=1\n")
    first = commit(r, "First")
    write(r, "DECISIONS.md", "Nothing here.\n")  # the amount line is now in history only
    write(r, "story.md", "Zorblax Quintworth sent 77.10 by e-Transfer.\nMy income arrives late.\n")
    (r / "docs/prepublic-scrub-report.md").unlink()
    tip = commit(r, "Second")
    git(r, "checkout", "-q", "-b", "side")
    write(r, "side.md", "amount_cents = -7710\n")  # 77.10, written as whole cents
    side = commit(
        r, "Side work for Zorblax Quintworth\n\nCo-Authored-By: Claude <noreply@example.com>"
    )
    git(r, "checkout", "-q", "main")
    git(r, "checkout", "-q", "-b", "proto")
    write(r, "proto.md", "Glimfood\n")
    proto = commit(r, "Prototype, Generated with a tool")
    git(r, "checkout", "-q", "main")
    git(r, "branch", "-q", "-D", "proto")  # only the HEAD reflog still holds it
    tree = git(r, "rev-parse", "HEAD^{tree}")
    lost = subprocess.run(["git", "-C", str(r), "commit-tree", tree, "-p", tip, "-m",
                           "lost: anthropic"], capture_output=True, text=True, check=True,
                          ).stdout.strip()  # no ref, no reflog: unreachable  # fmt: skip
    patterns = tmp_path / "patterns.txt"
    patterns.write_text(
        "# made up\nname: Zorblax Quintworth\namount: 77.10\netransfer: Quintworth\n"
        "merchant: Glimfood\nphrase: income arrives late\n",
        encoding="utf-8",
    )
    return {"repo": str(r), "first": first, "tip": tip, "side": side, "proto": proto,
            "lost": lost, "patterns": str(patterns)}  # fmt: skip


def run(repo: dict[str, str], with_patterns: bool = True) -> str:
    pats = load_patterns(Path(repo["patterns"])) if with_patterns else None
    return scan(Path(repo["repo"]), "main", pats)


def test_the_report_never_quotes_an_amount_a_pattern_a_name_or_an_email(
    repo: dict[str, str],
) -> None:
    text = run(repo)
    for leaked in ("1,234", "1234", "77.10", "99.99", "5,555", *MADE_UP, *SECRETS,
                   "test.person", "Test Person"):  # fmt: skip
        assert leaked not in text, leaked
    assert "t***@example.org" in text


def test_private_files_are_never_read(
    repo: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    read: list[str] = []
    original = scrub_scan.BlobReader.read

    def spy(self: scrub_scan.BlobReader, oid: str) -> bytes:
        read.append(oid)
        return original(self, oid)

    monkeypatch.setattr(scrub_scan.BlobReader, "read", spy)
    text = run(repo)
    r = Path(repo["repo"])
    oids = {path: git(r, "rev-parse", f"{repo['first']}:{path}") for path in PRIVATE}
    assert read
    for path, oid in oids.items():
        assert oid not in read, path
    assert "- a path under data/private: 1 paths" in text
    assert "- a .env file: 4 paths" in text and "- DECISIONS-private.md: 1 paths" in text


def test_ai_mentions_are_found_on_side_branches_reflogs_and_unreachable_commits(
    repo: dict[str, str],
) -> None:
    section = run(repo).split("## 1.")[1].split("## 2.")[0]
    lines = {c: next((x for x in section.splitlines() if repo[c][:10] in x), "")
             for c in ("side", "proto", "lost")}  # fmt: skip
    assert "co-authored-by" in lines["side"] and "claude" in lines["side"]
    assert "side" in lines["side"] and "on a ref" in lines["side"]
    assert "generated with" in lines["proto"] and "reflog only" in lines["proto"]
    assert "anthropic" in lines["lost"] and "unreachable" in lines["lost"]


def test_a_path_is_told_apart_as_at_the_tip_or_in_history_only(repo: dict[str, str]) -> None:
    text = run(repo)
    amounts = text.split("## 3.")[1].split("## 4.")[0]
    assert "`DECISIONS.md`" in amounts and "history only" in amounts
    own = text.split("## 4.")[1].split("## 5.")[0]
    assert "`story.md`" in own and "at the tip" in own


def test_brands_in_synthetic_persona_files_are_counted_apart(repo: dict[str, str]) -> None:
    text = run(repo)
    elsewhere = text.split("## 5.")[1].split("## 6.")[0]
    synthetic = text.split("## 6.")[1].split("## 7.")[0]
    assert "`notes.md`" in elsewhere and "persona_A" not in elsewhere
    assert "`data/synthetic/persona_A.csv`" in synthetic


def test_files_that_must_leave_the_history_whole_are_listed(repo: dict[str, str]) -> None:
    whole = run(repo).split("## 8.")[1]
    assert "`docs/prepublic-scrub-report.md`" in whole and repo["first"][:10] in whole
    assert "history only" in whole


def test_my_patterns_give_counts_per_category_only(repo: dict[str, str]) -> None:
    personal = run(repo).split("## 7.")[1].split("## 8.")[0]
    for category in ("name", "amount", "etransfer", "merchant", "phrase"):
        assert f"| {category} |" in personal, category
    assert "`story.md`" in personal and "`proto.md`" in personal
    without = run(repo, with_patterns=False).split("## 7.")[1].split("## 8.")[0]
    assert "Not run" in without


def test_a_bad_patterns_line_names_its_line_number_not_its_text(tmp_path: Path) -> None:
    bad = tmp_path / "p.txt"
    bad.write_text("name: Zorblax\nnickname: Quintworth\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2") as error:
        load_patterns(bad)
    assert "Quintworth" not in str(error.value) and "Zorblax" not in str(error.value)


def test_emails_are_masked_to_their_first_letter_and_domain() -> None:
    assert mask_email("someone@example.org") == "s***@example.org"
    assert mask_email("123+someone@users.noreply.github.com") == "1***@users.noreply.github.com"


def test_my_patterns_are_also_matched_in_commit_messages_and_amounts_in_whole_cents(
    repo: dict[str, str],
) -> None:
    personal = run(repo).split("## 7.")[1].split("## 8.")[0]
    messages = next(x for x in personal.splitlines() if x.startswith("| commit messages |"))
    assert repo["side"][:10] in messages and "name" in messages
    amounts = personal.split("### amount")[1]
    assert "`side.md`" in amounts and "`story.md`" in amounts


@pytest.mark.parametrize("path", PRIVATE)
def test_never_read_covers_every_private_kind_of_path(path: str) -> None:
    assert scrub_scan.never_read(path) is not None
    assert scrub_scan.never_read(".env.example") is None
