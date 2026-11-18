"""The categorizer v2 evaluation harness: its refusals, its ledger and its dry run. Synthetic
data only. These tests check the plumbing; no number they compute is a result."""

import csv
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from eval import categorizer_v2 as h

ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo, check=True, capture_output=True,
    )  # fmt: skip


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    return r


RULE = "# Adopt rule\nIn force since my approval.\n"


def _committed_rule(repo: Path, text: str = RULE) -> Path:
    rule = repo / "rule.md"
    rule.write_text(text, encoding="utf-8")
    _git(repo, "add", "rule.md")
    _git(repo, "commit", "-q", "-m", "rule")
    return rule


@pytest.fixture(autouse=True)
def _safe_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refused runs are logged too: the default ledger must never be my real one, and the
    approved adopt-rule path is the test repository's rule."""
    monkeypatch.setattr(h, "DEFAULT_LEDGER", tmp_path / "data" / "private" / "default.jsonl")
    monkeypatch.setattr(h, "APPROVED_ADOPT_RULE", tmp_path / "repo" / "rule.md")


# --- The adopt rule ---


def test_rule_missing_is_refused(repo: Path) -> None:
    status = h.adopt_rule_status(repo / "rule.md")
    assert not status.ok and "does not exist" in status.reason


def test_rule_not_committed_is_refused(repo: Path) -> None:
    (repo / "rule.md").write_text(RULE, encoding="utf-8")
    assert "not committed" in h.adopt_rule_status(repo / "rule.md").reason


def test_rule_committed_and_unchanged_is_accepted(repo: Path) -> None:
    status = h.adopt_rule_status(_committed_rule(repo))
    assert status.ok and status.commit and len(status.commit) == 40 and status.sha256


def test_rule_changed_after_its_commit_is_refused(repo: Path) -> None:
    rule = _committed_rule(repo)
    rule.write_text(RULE + "A threshold moved after the fact.\n", encoding="utf-8")
    assert not h.adopt_rule_status(rule).ok
    _git(repo, "add", "rule.md")  # staged but not committed: still refused
    status = h.adopt_rule_status(rule)
    assert not status.ok and "changed" in status.reason


def test_a_draft_rule_is_refused(repo: Path) -> None:
    status = h.adopt_rule_status(_committed_rule(repo, "# Adopt rule, DRAFT\n"))
    assert not status.ok and "draft" in status.reason


def test_the_repositorys_rule_is_not_in_force_yet() -> None:
    assert not h.adopt_rule_status(h.DEFAULT_ADOPT_RULE).ok
    draft = ROOT / "docs" / "specs" / "2026-11-18-categorizer-v2-adopt-rule-DRAFT.md"
    if draft.exists():
        assert not h.adopt_rule_status(draft).ok


# --- Labels, candidates, bootstrap ---


def test_labels_file_is_checked(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    path.write_text("fingerprint,category\nf1,Groceries\nf2,\n", encoding="utf-8")
    assert h.load_blind_labels(path) == {"f1": "Groceries"}  # a blank category is skipped
    path.write_text("fingerprint,category\nf1,Food\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown category"):
        h.load_blind_labels(path)
    path.write_text("fingerprint,category\nf1,Groceries\nf1,Shopping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="twice"):
        h.load_blind_labels(path)
    path.write_text("id,label\nf1,Groceries\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columns"):
        h.load_blind_labels(path)


def test_candidates_are_new_merchants_first_seen_since_the_date() -> None:
    features = pd.DataFrame(
        {
            "fingerprint": ["a", "b", "c", "d", "e", "f"],
            "group": ["Old Cafe", "Old Cafe", "New Bakery", "New Bakery", "Labelled Shop",
                      "Late Shop"],
            "posted_date": [date(2026, 8, 1), date(2026, 10, 5), date(2026, 10, 2),
                            date(2026, 10, 9), date(2026, 10, 3), date(2026, 11, 1)],
        }
    )  # fmt: skip
    got = h.candidate_blind_rows(features, {"Labelled Shop"}, date(2026, 10, 1))
    assert list(got["fingerprint"]) == ["c", "d", "f"]  # Old Cafe was seen before October


def test_candidates_on_synthetic_personas(tmp_path: Path) -> None:
    split, _ = h.synthetic_split(tmp_path)
    blind = split.blind
    trained = set(split.train_x["group"])
    since = date(2026, 6, 1)
    got = h.candidate_blind_rows(blind, trained, since)
    assert not got.empty
    assert not set(got["group"]) & trained
    first = blind.groupby("group")["posted_date"].min()
    assert all(first[g] >= since for g in set(got["group"]))


def test_paired_bootstrap() -> None:
    base = np.array([1, 0, 0, 1, 0, 1, 0, 0])
    other = np.array([1, 1, 0, 1, 1, 1, 0, 1])
    clusters = ["m1", "m1", "m2", "m3", "m3", "m4", "m5", "m5"]
    gain, low = h.paired_bootstrap(base, other, clusters)
    assert gain == pytest.approx(other.mean() - base.mean())
    assert low <= gain
    assert h.paired_bootstrap(base, other, clusters) == (gain, low)  # seeded
    same, low_same = h.paired_bootstrap(base, base, clusters)
    assert same == 0.0 and low_same == 0.0


# --- The dry run ---


def test_dry_run_prints_counts_only(capsys: pytest.CaptureFixture[str]) -> None:
    assert h.main(["score", "--synthetic"]) == 0
    out = capsys.readouterr().out
    assert h.DRY_RUN in out
    assert "%" not in out and "accuracy" not in out and "points" not in out
    for variant in h.VARIANTS:
        assert f"variant {variant}: rows" in out


def test_dry_run_writes_no_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "data" / "private" / "ledger.jsonl"
    monkeypatch.setattr(h, "DEFAULT_LEDGER", ledger)
    h.main(["score", "--synthetic"])
    assert not ledger.exists()


# --- Scoring refusals and the ledger ---


def test_db_labels_and_choice_must_be_given(capsys: pytest.CaptureFixture[str]) -> None:
    assert h.main(["score"]) == 2
    assert "--db is required" in capsys.readouterr().out


def test_no_adopt_rule_no_score(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    labels = tmp_path / "labels.csv"
    labels.write_text("fingerprint,category\n", encoding="utf-8")
    code = h.main(["score", "--db", str(tmp_path / "x.db"), "--labels", str(labels),
                   "--choice", str(tmp_path / "c.json")])  # fmt: skip
    assert code == 2
    assert "refusing to score" in capsys.readouterr().out


def _blind_labels(db: Path, tmp_path: Path, splits: tuple[str, ...]) -> Path:
    """A labels file for persona C's rows of the given frozen splits, from its answer key."""
    from lookahead.db import connect_readonly
    from lookahead.labels import current_labels, load_rows

    conn = connect_readonly(db)
    rows = load_rows(conn, "scotia-chequing")
    labels = current_labels(conn, "scotia-chequing")
    conn.close()
    path = tmp_path / "labels.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fingerprint", "category"])
        for fp in rows[rows["split"].isin(splits)]["fingerprint"]:
            w.writerow([fp, labels[fp].category])
    return path


def _private_copy(db: Path, tmp_path: Path) -> Path:
    private = tmp_path / "data" / "private"
    private.mkdir(parents=True)
    return Path(shutil.copy(db, private / "t.db"))


def test_old_holdout_rows_are_refused(
    db: Path, tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = _blind_labels(db, tmp_path, ("test_b",))
    ledger = tmp_path / "data" / "private" / "ledger.jsonl"
    rule = _committed_rule(repo)
    code = h.main(["score", "--db", str(_private_copy(db, tmp_path)), "--labels", str(labels),
                   "--choice", str(tmp_path / "none.json"),
                   "--adopt-rule", str(rule), "--ledger", str(ledger)])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 2 and "old holdout" in out
    assert [e["status"] for e in h.ledger_entries(ledger)] == ["refused"]  # logged


def _unlabelled_new_rows(db: Path, tmp_path: Path) -> tuple[Path, Path]:
    """A copy of the database where persona C's Test B rows lose their labels and their split
    (as if they were fresh rows), and a labels file for them."""
    labels = _blind_labels(db, tmp_path, ("test_b",))
    copy = _private_copy(db, tmp_path)
    import sqlite3

    conn = sqlite3.connect(copy)
    with conn:
        conn.execute(
            "DELETE FROM category_labels WHERE fingerprint IN"
            " (SELECT fingerprint FROM label_splits WHERE split = 'test_b')"
        )
        conn.execute("DELETE FROM label_splits WHERE split = 'test_b'")
    conn.close()
    return copy, labels


@pytest.mark.slow
def test_scoring_prints_n_never_row_text_and_counts_every_variant(
    db: Path, tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copy, labels = _unlabelled_new_rows(db, tmp_path)
    ledger = tmp_path / "data" / "private" / "ledger.jsonl"
    choice = tmp_path / "choice.json"
    choice.write_text(json.dumps({"params": {"model__C": 1.0}, "flag_threshold": 0.5,
                                  "synthetic_months": 0}), encoding="utf-8")  # fmt: skip
    args = ["score", "--db", str(copy), "--labels", str(labels), "--choice", str(choice),
            "--adopt-rule", str(_committed_rule(repo)), "--ledger", str(ledger)]  # fmt: skip
    assert h.main(args) == 0
    out = capsys.readouterr().out
    assert "(n = " in out
    from lookahead.db import connect_readonly

    conn = connect_readonly(copy)
    texts = [r[0] for r in conn.execute("SELECT sub_description FROM transactions")]
    conn.close()
    features = h.categorizer_features(connect_readonly(copy), "scotia-chequing")
    for text in [*texts, *features["group"]]:
        if text.strip():
            assert text.strip() not in out  # no row text, no merchant name
    entries = h.ledger_entries(ledger)
    assert [e["variant"] for e in entries] == list(h.VARIANTS[:2])
    assert all(e["adopt_rule_commit"] and not e["dry_run"] for e in entries)
    # the same labels again: refused, unless --again (which is recorded)
    assert h.main(args) == 2
    assert "already scored" in capsys.readouterr().out
    assert h.main([*args, "--again"]) == 0
    entries = h.ledger_entries(ledger)
    assert [e["status"] for e in entries] == ["scored", "scored", "refused", "scored", "scored"]
    assert [e["again"] for e in entries] == [False, False, False, True, True]


def test_blind_rows_labelled_in_the_app_are_refused(
    db: Path, tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Test B rows lose their split but keep their labels: blind rows I labelled in the app.
    # They would reach training and the RAG examples, so the harness refuses to score.
    labels = _blind_labels(db, tmp_path, ("test_b",))
    copy = _private_copy(db, tmp_path)
    import sqlite3

    conn = sqlite3.connect(copy)
    with conn:
        conn.execute("DELETE FROM label_splits WHERE split = 'test_b'")
    conn.close()
    ledger = tmp_path / "data" / "private" / "ledger.jsonl"
    rule = _committed_rule(repo)
    code = h.main(["score", "--db", str(copy), "--labels", str(labels),
                   "--choice", str(tmp_path / "none.json"), "--rag",
                   "--adopt-rule", str(rule), "--ledger", str(ledger)])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 2 and "blind rows carry a label" in out
    assert [e["status"] for e in h.ledger_entries(ledger)] == ["refused"]  # logged


def test_rag_examples_leave_the_blind_set_out(db: Path) -> None:
    from lookahead import rag_categorizer as rag
    from lookahead.categorize import training_rows
    from lookahead.db import connect_readonly

    conn = connect_readonly(db)
    rows = training_rows(conn, "scotia-chequing")
    key = b"k" * 32
    everything = rag.labelled_pairs(conn, "scotia-chequing", key)
    blind = frozenset(rows["fingerprint"])
    assert everything and rag.labelled_pairs(conn, "scotia-chequing", key, exclude=blind) == []
    conn.close()


def test_the_ledger_must_be_private(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="data/private"):
        h.add_to_ledger(tmp_path / "ledger.jsonl", {"variant": "current"})


def test_candidates_command_refuses_a_public_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = h.main(["candidates", "--db", str(tmp_path / "x.db"), "--since", "2026-10-01",
                   "--out", str(tmp_path / "out.csv")])  # fmt: skip
    assert code == 2 and "data/private" in capsys.readouterr().out


# --- One adopt-rule file; a selection is scored once; refusals are logged ---


def _score_args(tmp_path: Path, labels: Path, rule: Path) -> list[str]:
    return ["score", "--db", str(tmp_path / "none.db"), "--labels", str(labels),
            "--choice", str(tmp_path / "c.json"), "--adopt-rule", str(rule),
            "--ledger", str(tmp_path / "data" / "private" / "ledger.jsonl")]  # fmt: skip


def test_only_the_approved_adopt_rule_file_is_accepted(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _committed_rule(repo)
    other = repo / "other.md"
    other.write_text(RULE, encoding="utf-8")
    _git(repo, "add", "other.md")
    _git(repo, "commit", "-q", "-m", "other")
    labels = tmp_path / "labels.csv"
    labels.write_text("fingerprint,category\nf1,Shopping\n", encoding="utf-8")
    assert h.main(_score_args(tmp_path, labels, other)) == 2
    assert "only docs/specs/2026-11-18-categorizer-v2-adopt-rule.md" in capsys.readouterr().out
    [entry] = h.ledger_entries(tmp_path / "data" / "private" / "ledger.jsonl")
    assert entry["status"] == "refused" and "only docs/specs" in str(entry["reason"])


def test_the_repositorys_approved_path_is_the_named_file() -> None:
    assert h.DEFAULT_ADOPT_RULE == Path("docs/specs/2026-11-18-categorizer-v2-adopt-rule.md")


def _earlier_scored(ledger: Path, fingerprints: list[str]) -> None:
    h.add_to_ledger(ledger, {"status": "scored", "variant": "current", "selection": fingerprints,
                             "selection_key": h.selection_key(fingerprints)})  # fmt: skip


@pytest.mark.parametrize(
    "rows",
    [
        "f1,Shopping\nf2,Groceries\n",  # the same labels
        "f1,Transport\nf2,Groceries\n",  # a label edited
        "f1,Shopping\nf2,\n",  # a label removed
        "f2,Groceries\nf3,Shopping\n",  # a row swapped for a new one
    ],
)
def test_a_scored_selection_is_never_scored_again(
    tmp_path: Path, repo: Path, rows: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rule = _committed_rule(repo)
    ledger = tmp_path / "data" / "private" / "ledger.jsonl"
    _earlier_scored(ledger, ["f1", "f2"])
    labels = tmp_path / "labels.csv"
    labels.write_text("fingerprint,category\n" + rows, encoding="utf-8")
    assert h.main(_score_args(tmp_path, labels, rule)) == 2
    assert "already scored" in capsys.readouterr().out
    assert h.ledger_entries(ledger)[-1]["status"] == "refused"


def test_the_selection_key_ignores_the_labels() -> None:
    assert h.selection_key(["b", "a"]) == h.selection_key(["a", "b"])


def test_a_missing_flag_is_refused_and_logged(tmp_path: Path) -> None:
    assert h.main(["score"]) == 2
    [entry] = h.ledger_entries(h.DEFAULT_LEDGER)
    assert entry["status"] == "refused" and "--db is required" in str(entry["reason"])


# --- Blind labelling: merchant text only ---


def _selection(tmp_path: Path, fingerprints: list[str]) -> Path:
    path = tmp_path / "data" / "private" / "eval" / "candidates.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"fingerprint": fingerprints, "posted_date": "2026-10-02",
                  "group": "x", "category": ""}).to_csv(path, index=False)  # fmt: skip
    return path


def test_the_first_run_freezes_the_selection(tmp_path: Path) -> None:
    labels = tmp_path / "data" / "private" / "eval" / "labels.csv"
    assert h.freeze_selection(_selection(tmp_path, ["f2", "f1"]), labels) == ["f2", "f1"]
    assert h.selection_fingerprints(labels) == ["f1", "f2"]
    assert h.load_blind_labels(labels) == {}  # every category empty
    # the same selection again: fine; another selection: refused
    assert h.freeze_selection(_selection(tmp_path, ["f1", "f2"]), labels) == ["f2", "f1"]
    with pytest.raises(ValueError, match="another set of rows"):
        h.freeze_selection(_selection(tmp_path, ["f1", "f3"]), labels)


def test_labelling_shows_the_text_only_and_resumes(tmp_path: Path) -> None:
    labels = tmp_path / "data" / "private" / "eval" / "labels.csv"
    h.freeze_selection(_selection(tmp_path, ["f1", "f2", "f3"]), labels)
    texts = {"f1": "Opos Velorin Cafe   Montr ", "f2": "Apos Lunaire       Laval ",
             "f3": "Opos Zephyr Pharma  Montr "}  # fmt: skip
    shown: list[str] = []
    answers = iter(["2", "x", "s", "q"])
    first = h.label_session(texts, labels, lambda prompt: (shown.append(prompt), next(answers))[1],
                            shown.append)  # fmt: skip
    assert first == h.Session(labelled=1, skipped=1, left=2)
    assert h.load_blind_labels(labels) == {"f1": h.CATEGORIES[1]}  # saved at once
    screen = "\n".join(shown)
    for row_text in texts.values():
        assert row_text.strip() in screen or row_text == texts["f3"]
    for word in ("confidence", "predict", "source", "model", "suggest", "%"):
        assert word not in screen.casefold()
    # a second session asks only the rows still without a category
    asked: list[str] = []
    second = h.label_session(texts, labels, lambda prompt: (asked.append(prompt), "1")[1],
                             lambda _: None)  # fmt: skip
    assert second.left == 0 and len(asked) == 2
    assert all(texts["f1"].strip() not in a for a in asked)


def test_the_label_command_needs_private_paths(tmp_path: Path) -> None:
    public = tmp_path / "labels.csv"
    selection = _selection(tmp_path, ["f1"])
    code = h.main(["label", "--db", str(tmp_path / "x.db"),
                   "--selection", str(selection), "--labels", str(public)])  # fmt: skip
    assert code == 2 and not public.exists()
