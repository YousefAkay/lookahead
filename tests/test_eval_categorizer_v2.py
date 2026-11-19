"""The categorizer v2 evaluation harness: its refusals, its ledger and its dry run. Synthetic
data only. These tests check the plumbing; no number they compute is a result."""

import csv
import json
import re
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


RULE = f"# Adopt rule\nIn force since my approval.\n\n{h.RULE_MARKER}\n"


def _log_draw(labels: Path, rule: Path) -> None:
    """The ledger's draw entry for the rows of `labels`, under the committed `rule` (what the
    candidates command writes). Scoring needs it."""
    key = h.selection_key(h.selection_fingerprints(labels))
    sha = h.adopt_rule_status(rule).sha256
    h.add_to_ledger(h.DEFAULT_LEDGER, {"status": "drawn", "selection_key": key,
                                       "adopt_rule_sha256": sha})  # fmt: skip


def _public_run_scored() -> None:
    """The ledger entry of a scored public deciding run: my rows are drawn only after it."""
    sha = h.adopt_rule_status(h.APPROVED_ADOPT_RULE).sha256  # the committed test rule
    h.add_to_ledger(h.DEFAULT_LEDGER, {"run": "external", "status": "scored",
                                       "comparison": "cleaner+directory vs current",
                                       "passes": True, "adopt_rule_sha256": sha})  # fmt: skip


def _runs(ledger: Path) -> list[dict[str, object]]:
    """The ledger without its draw entry: the scoring runs and refusals."""
    return [e for e in h.ledger_entries(ledger)
            if e.get("status") != "drawn" and e.get("run") != "external"]  # fmt: skip


def _rule_and_draw(repo: Path, labels: Path) -> Path:
    rule = _committed_rule(repo)
    _log_draw(labels, rule)
    return rule


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
    monkeypatch.setattr(h, "DEFAULT_LEDGER", tmp_path / "data" / "private" / "ledger.jsonl")
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


def test_the_repositorys_rule_is_final_and_states_the_codes_numbers() -> None:
    text = (ROOT / h.DEFAULT_ADOPT_RULE).read_text(encoding="utf-8")
    assert "DRAFT" not in text and "not yet in force" not in text.casefold()
    assert not (ROOT / "docs" / "specs" / "2026-11-18-categorizer-v2-adopt-rule-DRAFT.md").exists()
    # the numbers in code are the rule's numbers (the veto on my rows, and the comparisons)
    assert (h.A_MAX_ROWS, h.A_PER_MERCHANT, h.A_MIN_ROWS) == (150, 3, 15)
    assert (h.K_ROWS, h.K_MIN_ROWS, h.GUARD_EXTRA_WRONG, h.A_VETO_EXTRA_WRONG) == (60, 40, 1, 2)
    assert (h.MIN_GAIN, h.BOOTSTRAP_RESAMPLES, h.SELECTION_SEED) == (0.03, 2000, 0)
    assert h.BLIND_SINCE == date(2026, 10, 1) and h.LEAKAGE_AT == 0.95
    for phrase in ("version 2", "up to 150 rows", "at most 3 per merchant", "at least 15 group A",
                   "60 unlabelled rows", "at least 40 labelled group K", "at least 3 points",
                   "2,000 draws", "seed 0", "at most 2 rows net worse", "at most 1 row net worse",
                   "2026-10-01", "95%", "(c) passes only if (b) passes too",
                   "passes Part 1 AND is not vetoed", "600 rows",
                   "40% of the merchants"):  # fmt: skip
        assert phrase in text, phrase


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


def _blind_labels(
    db: Path, tmp_path: Path, splits: tuple[str, ...], sets: dict[str, str] | None = None
) -> Path:
    """A labels file for persona C's rows of the given frozen splits, from its answer key; each
    row's group is `sets[fingerprint]`, K when not given."""
    from lookahead.db import connect_readonly
    from lookahead.labels import current_labels, load_rows

    conn = connect_readonly(db)
    rows = load_rows(conn, "scotia-chequing")
    labels = current_labels(conn, "scotia-chequing")
    conn.close()
    path = tmp_path / "labels.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fingerprint", "set", "category"])
        for fp in rows[rows["split"].isin(splits)]["fingerprint"]:
            w.writerow([fp, (sets or {}).get(fp, "K"), labels[fp].category])
    return path


def _private_copy(db: Path, tmp_path: Path) -> Path:
    private = tmp_path / "data" / "private"
    private.mkdir(parents=True, exist_ok=True)
    return Path(shutil.copy(db, private / "t.db"))


def test_old_holdout_rows_are_refused(
    db: Path, tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = _blind_labels(db, tmp_path, ("test_b",))
    ledger = tmp_path / "data" / "private" / "ledger.jsonl"
    rule = _rule_and_draw(repo, labels)
    code = h.main(["score", "--db", str(_private_copy(db, tmp_path)), "--labels", str(labels),
                   "--choice", str(tmp_path / "none.json"),
                   "--adopt-rule", str(rule)])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 2 and "old holdout" in out
    assert [e["status"] for e in _runs(ledger)] == ["refused"]  # logged


def _unlabelled_new_rows(
    db: Path, tmp_path: Path, splits: tuple[str, ...] = ("test_b",)
) -> tuple[Path, Path]:
    """A copy of the database where persona C's rows of `splits` lose their labels and their
    split (as if they were fresh rows), and a labels file for them."""
    labels = _blind_labels(db, tmp_path, splits)
    marks = ", ".join("?" * len(splits))
    copy = _private_copy(db, tmp_path)
    import sqlite3

    conn = sqlite3.connect(copy)
    with conn:
        conn.execute(
            "DELETE FROM category_labels WHERE fingerprint IN"
            f" (SELECT fingerprint FROM label_splits WHERE split IN ({marks}))",
            splits,
        )
        conn.execute(f"DELETE FROM label_splits WHERE split IN ({marks})", splits)
    conn.close()
    return copy, labels


def _true_sets(copy: Path, labels: Path) -> dict[str, int]:
    """Rewrite the labels file's group column as the selection would draw it: K when the
    row's merchant has training labels, A otherwise. Returns the count of each."""
    from lookahead.categorize import training_rows
    from lookahead.db import connect_readonly

    conn = connect_readonly(copy)
    groups = dict(zip(*h.categorizer_features(conn, "scotia-chequing")[["fingerprint", "group"]]
                      .to_numpy().T, strict=True))  # fmt: skip
    trained = set(training_rows(conn, "scotia-chequing")["group"])
    conn.close()
    rows = pd.read_csv(labels, dtype=str, keep_default_na=False)
    rows["set"] = ["K" if groups.get(f) in trained else "A" for f in rows["fingerprint"]]
    rows.to_csv(labels, index=False)
    return rows["set"].value_counts().to_dict()


@pytest.mark.slow
def test_scoring_prints_n_never_row_text_and_counts_every_variant(
    db: Path, tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    copy, labels = _unlabelled_new_rows(db, tmp_path, ("test_a", "test_b"))
    counts = _true_sets(copy, labels)
    assert counts.get("A") and counts.get("K")  # both groups present
    monkeypatch.setattr(h, "A_MIN_ROWS", 1)  # a small synthetic persona has fewer than 100
    ledger = tmp_path / "data" / "private" / "ledger.jsonl"
    choice = tmp_path / "choice.json"
    choice.write_text(json.dumps({"params": {"model__C": 1.0}, "flag_threshold": 0.5,
                                  "synthetic_months": 0}), encoding="utf-8")  # fmt: skip
    args = ["score", "--db", str(copy), "--labels", str(labels), "--choice", str(choice),
            "--adopt-rule", str(_rule_and_draw(repo, labels))]  # fmt: skip
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
    assert "veto, cleaner+directory vs current on my rows" in out and "adopted: " in out
    entries = _runs(ledger)
    assert [e.get("variant") or e.get("veto") for e in entries] == [
        *h.VARIANTS[:2], "cleaner+directory"]  # fmt: skip
    assert all(e["adopt_rule_commit"] and not e["dry_run"] for e in entries)
    # the same labels again: refused, unless --again (which is recorded)
    assert h.main(args) == 2
    assert "already scored" in capsys.readouterr().out
    assert h.main([*args, "--again"]) == 0
    entries = _runs(ledger)
    assert [e["status"] for e in entries] == ["scored"] * 3 + ["refused"] + ["scored"] * 3
    assert [e["again"] for e in entries] == [False] * 4 + [True] * 3


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
    rule = _rule_and_draw(repo, labels)
    code = h.main(["score", "--db", str(copy), "--labels", str(labels),
                   "--choice", str(tmp_path / "none.json"), "--rag",
                   "--adopt-rule", str(rule)])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 2 and "blind rows carry a label" in out
    assert [e["status"] for e in _runs(ledger)] == ["refused"]  # logged


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
    code = h.main(["candidates", "--db", str(tmp_path / "x.db"),
                   "--out", str(tmp_path / "out.csv")])  # fmt: skip
    assert code == 2 and "data/private" in capsys.readouterr().out


# --- One adopt-rule file; a selection is scored once; refusals are logged ---


def _score_args(tmp_path: Path, labels: Path, rule: Path) -> list[str]:
    return ["score", "--db", str(tmp_path / "none.db"), "--labels", str(labels),
            "--choice", str(tmp_path / "c.json"), "--adopt-rule", str(rule)]  # fmt: skip


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
        "f1,A,Shopping\nf2,A,Groceries\n",  # the same labels
        "f1,A,Transport\nf2,A,Groceries\n",  # a label edited
        "f1,A,Shopping\nf2,A,\n",  # a label removed
        "f2,A,Groceries\nf3,A,Shopping\n",  # a row swapped for a new one
    ],
)
def test_a_scored_selection_is_never_scored_again(
    tmp_path: Path, repo: Path, rows: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rule = _committed_rule(repo)
    ledger = tmp_path / "data" / "private" / "ledger.jsonl"
    _earlier_scored(ledger, ["f1", "f2"])
    labels = tmp_path / "labels.csv"
    labels.write_text("fingerprint,set,category\n" + rows, encoding="utf-8")
    _log_draw(labels, rule)
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


def _selection(tmp_path: Path, fingerprints: list[str], sets: list[str] | None = None) -> Path:
    path = tmp_path / "data" / "private" / "eval" / "candidates.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"fingerprint": fingerprints, "posted_date": "2026-10-02", "group": "x",
                  "set": sets or ["A"] * len(fingerprints),
                  "category": ""}).to_csv(path, index=False)  # fmt: skip
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


def test_there_is_one_ledger() -> None:
    with pytest.raises(SystemExit):  # no --ledger option: a fresh ledger cannot reset the rule
        h.main(["score", "--ledger", "data/private/other.jsonl"])


def test_the_label_command_needs_an_existing_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = tmp_path / "data" / "private" / "eval" / "labels.csv"
    selection = _selection(tmp_path, ["f1"])
    code = h.main(["label", "--db", str(tmp_path / "missing.db"),
                   "--selection", str(selection), "--labels", str(labels)])  # fmt: skip
    assert code == 2 and "no database" in capsys.readouterr().out
    assert not labels.exists()  # nothing frozen either


def test_the_ledger_is_one_absolute_path_in_the_main_folder() -> None:
    ledger = h.default_ledger()
    assert ledger.is_absolute()
    assert ledger == h.main_folder() / "data" / "private" / "eval" / "categorizer_v2_ledger.jsonl"
    assert h.ledger_is_private(ledger)


def test_a_worktree_finds_the_main_folder(repo: Path, tmp_path: Path) -> None:
    (repo / "f.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "f")
    tree = tmp_path / "tree"
    _git(repo, "worktree", "add", "-q", str(tree))
    assert h.main_folder(tree).resolve() == repo.resolve()
    assert h.main_folder(repo).resolve() == repo.resolve()


# --- The adopt rule of 2026-11-18: groups A and K, the cap, the minimum, (c) against (b) ---


def _features(
    new_merchants: int, rows_each: int, known: int = 4, known_rows: int = 40
) -> pd.DataFrame:
    """Made-up categorizer rows: `new_merchants` merchants first seen in October with
    `rows_each` rows each, `known` labelled merchants with rows before and after October, and
    one merchant first seen in August (not new)."""
    rows = []
    for m in range(new_merchants):
        rows += [(f"n{m}-{r}", f"New {m}", date(2026, 10, 1 + r % 28)) for r in range(rows_each)]
    for m in range(known):
        rows += [(f"k{m}-{r}", f"Known {m}", date(2026, 9 + r % 2, 1 + r % 28))
                 for r in range(known_rows)]  # fmt: skip
    rows += [("o-0", "Old", date(2026, 8, 3)), ("o-1", "Old", date(2026, 10, 3))]
    return pd.DataFrame(rows, columns=["fingerprint", "group", "posted_date"])


KNOWN = {"Known 0", "Known 1", "Known 2", "Known 3"}
SINCE = date(2026, 10, 1)


def test_the_selection_caps_each_merchant_and_group_a_and_draws_60_known_rows() -> None:
    features = _features(new_merchants=80, rows_each=5)
    excluded = {"n0-0", "k0-1"}  # old holdout or labelled rows: never picked
    got = h.select_blind_rows(features, KNOWN, excluded, SINCE)
    a, k = got[got["set"] == "A"], got[got["set"] == "K"]
    assert len(a) == h.A_MAX_ROWS == 150
    assert a.groupby("group").size().max() <= h.A_PER_MERCHANT == 3
    assert not set(a["group"]) & (KNOWN | {"Old"})  # new merchants only
    assert len(k) == h.K_ROWS == 60 and set(k["group"]) <= KNOWN
    assert (got["posted_date"] >= SINCE).all()
    assert not set(got["fingerprint"]) & excluded
    assert got["fingerprint"].is_unique


def test_the_selection_is_seeded_and_mixes_the_groups() -> None:
    features = _features(new_merchants=80, rows_each=5)
    first = h.select_blind_rows(features, KNOWN, set(), SINCE)
    again = h.select_blind_rows(features.sample(frac=1, random_state=3), KNOWN, set(), SINCE)
    assert list(first["fingerprint"]) == list(again["fingerprint"])  # input order is irrelevant
    other = h.select_blind_rows(features, KNOWN, set(), SINCE, seed=1)
    assert set(other["fingerprint"]) != set(first["fingerprint"])
    # shuffled together: the labelling order does not tell the group
    assert list(first["set"]) != sorted(first["set"])


def test_fewer_than_15_rows_for_group_a_selects_nothing() -> None:
    # 5 new merchants with 5 rows each: 25 rows, 15 after the cap of 3: enough
    assert len(h.select_blind_rows(_features(5, 5), KNOWN, set(), SINCE)) > 0
    # 4 merchants with 5 rows each: 20 rows, 12 after the cap: refused
    with pytest.raises(ValueError, match="Wait for more data"):
        h.select_blind_rows(_features(4, 5), KNOWN, set(), SINCE)


def test_the_candidates_command_never_redraws(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "data" / "private" / "eval" / "candidates.csv"
    out.parent.mkdir(parents=True)
    out.write_text("frozen\n", encoding="utf-8")
    code = h.main(["candidates", "--db", str(tmp_path / "x.db"),
                   "--out", str(out)])  # fmt: skip
    assert code == 2 and "never redrawn" in capsys.readouterr().out
    assert out.read_text(encoding="utf-8") == "frozen\n"


def test_the_candidates_command_writes_both_groups_once(
    db: Path, tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    from lookahead.categorize import training_rows
    from lookahead.db import connect_readonly

    copy, _ = _unlabelled_new_rows(db, tmp_path, ("test_a", "test_b"))
    monkeypatch.setattr(h, "BLIND_SINCE", date(2025, 10, 1))  # the persona's data ends in 2026-09
    out = tmp_path / "data" / "private" / "eval" / "candidates.csv"
    args = ["candidates", "--db", str(copy), "--out", str(out)]
    # no committed adopt rule: no draw
    assert h.main(args) == 2 and "rule comes before the draw" in capsys.readouterr().out
    _committed_rule(repo)
    _public_run_scored()
    monkeypatch.setattr(h, "A_MIN_ROWS", 10_000)  # too few rows: refused, nothing written
    assert h.main(args) == 2 and "Wait for more data" in capsys.readouterr().out
    assert not out.exists()
    assert not [e for e in h.ledger_entries(h.DEFAULT_LEDGER) if e.get("status") == "drawn"]
    monkeypatch.setattr(h, "A_MIN_ROWS", 1)
    assert h.main(args) == 0
    printed = capsys.readouterr().out
    written = pd.read_csv(out, dtype=str, keep_default_na=False)
    assert list(written.columns) == ["fingerprint", "posted_date", "group", "set", "category"]
    assert set(written["set"]) == {"A", "K"} and (written["category"] == "").all()
    assert (written["set"] == "K").sum() <= h.K_ROWS
    for name in written["group"]:
        assert name not in printed  # counts only, no merchant names
    conn = connect_readonly(copy)
    labelled = set(training_rows(conn, "scotia-chequing")["fingerprint"])
    holdout = h.holdout_fingerprints(conn)
    conn.close()
    assert labelled and not set(written["fingerprint"]) & (labelled | holdout)
    [drawn] = [e for e in h.ledger_entries(h.DEFAULT_LEDGER) if e.get("status") == "drawn"]
    assert drawn["status"] == "drawn" and drawn["adopt_rule_commit"]
    assert drawn["a_rows"] == (written["set"] == "A").sum()
    # a second draw is refused, even to another file
    other = out.with_name("other.csv")
    assert h.main(["candidates", "--db", str(copy), "--out", str(other)]) == 2
    assert "never redrawn" in capsys.readouterr().out and not other.exists()


def test_variant_b_uses_exact_and_alias_matches_only() -> None:
    b = h.variant_options("cleaner+directory")
    assert b.cleaner and b.directory and not b.directory_similar
    assert not h.variant_options("current").any_on
    assert not h.variant_options("+RAG").directory_similar


def test_freezing_keeps_each_rows_group_but_labelling_never_shows_it(tmp_path: Path) -> None:
    labels = tmp_path / "data" / "private" / "eval" / "labels.csv"
    h.freeze_selection(_selection(tmp_path, ["f1", "f2"], ["K", "A"]), labels)
    assert h.load_blind_sets(labels) == {"f1": "K", "f2": "A"}
    prompts: list[str] = []
    h.label_session({"f1": "Opos Velorin Cafe", "f2": "Apos Lunaire"}, labels,
                    lambda prompt: (prompts.append(prompt), "1")[1], lambda _: None)  # fmt: skip
    ask = "category number, s to skip, q to stop: "
    # the prompt is the position and the bank text, nothing else: no group, no set
    assert prompts == [f"[1 of 2] Opos Velorin Cafe\n{ask}", f"[2 of 2] Apos Lunaire\n{ask}"]
    assert h.load_blind_sets(labels) == {"f1": "K", "f2": "A"}  # kept after labelling


def test_a_selection_without_groups_is_refused(tmp_path: Path) -> None:
    labels = tmp_path / "data" / "private" / "eval" / "labels.csv"
    old = tmp_path / "data" / "private" / "eval" / "old.csv"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("fingerprint,posted_date,group,category\nf1,2026-10-02,x,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="set column"):
        h.freeze_selection(old, labels)
    labels.write_text("fingerprint,category\nf1,Shopping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="set column"):
        h.load_blind_sets(labels)


def _blind(a_merchants: int, k_rows: int) -> pd.DataFrame:
    """Group A: two rows per merchant; group K: `k_rows` rows. Every truth is Shopping."""
    groups = [f"m{i}" for i in range(a_merchants) for _ in range(2)] + ["known"] * k_rows
    return pd.DataFrame({"group": groups, "truth": "Shopping",
                         "set": ["A"] * (2 * a_merchants) + ["K"] * k_rows})  # fmt: skip


def _results(blind: pd.DataFrame, right: dict[str, tuple[int, int]]) -> dict[str, h.VariantResult]:
    """Per variant, (rows right in group A, rows right in group K), the first ones of each."""
    out = {}
    for variant, (a_right, k_right) in right.items():
        a, k = (blind["set"] == "A").to_numpy(), (blind["set"] == "K").to_numpy()
        cats = ["Groceries"] * len(blind)
        for i in [*np.flatnonzero(a)[:a_right], *np.flatnonzero(k)[:k_right]]:
            cats[i] = "Shopping"
        out[variant] = h.VariantResult(variant, cats, ["model"] * len(blind))
    return out


def test_c_is_compared_with_b_not_with_a() -> None:
    blind = _blind(a_merchants=75, k_rows=60)  # 150 rows in A
    # (a) 60 of 150 right, (b) 105 (+30 points), (c) 108: +32 points on (a), only +2 on (b)
    got = h.compare(_results(blind, {"current": (60, 50), "cleaner+directory": (105, 50),
                                     "+RAG": (108, 50)}), blind)  # fmt: skip
    b, c = got
    assert (b.variant, b.base, c.variant, c.base) == (
        "cleaner+directory", "current", "+RAG", "cleaner+directory")  # fmt: skip
    assert b.a_n == 150 and b.k_n == 60
    assert b.gain == pytest.approx(0.30) and b.low > 0 and b.passes
    assert c.gain == pytest.approx(0.02) and not c.gain_ok and not c.passes


def test_the_gain_needs_3_points_and_a_bootstrap_bound_above_0() -> None:
    blind = _blind(a_merchants=75, k_rows=60)
    # exactly +3 points on 150 rows: 4.5 rows is not possible, so +3.33 (5 rows) passes the
    # size, +2.67 (4 rows) does not
    five = h.compare(_results(blind, {"current": (60, 50), "cleaner+directory": (65, 50)}), blind)
    four = h.compare(_results(blind, {"current": (60, 50), "cleaner+directory": (64, 50)}), blind)
    assert five[0].gain >= h.MIN_GAIN and not four[0].gain_ok
    # five more rows right, all in 3 merchants: the bound reaches 0, so it does not pass
    assert five[0].low <= 0 and not five[0].passes


def test_the_guard_allows_one_more_known_row_wrong_than_a() -> None:
    blind = _blind(a_merchants=75, k_rows=60)
    one = h.compare(_results(blind, {"current": (60, 50), "cleaner+directory": (105, 49)}), blind)
    two = h.compare(_results(blind, {"current": (60, 50), "cleaner+directory": (105, 48)}), blind)
    assert one[0].extra_wrong == 1 and one[0].passes
    assert two[0].extra_wrong == 2 and not two[0].guard_ok and not two[0].passes
    # (c)'s guard is against (a) too, not against (b)
    c = h.compare(_results(blind, {"current": (60, 50), "cleaner+directory": (105, 50),
                                   "+RAG": (150, 48)}), blind)[1]  # fmt: skip
    assert c.extra_wrong == 2 and not c.passes


def test_scoring_refuses_fewer_than_100_group_a_rows(
    db: Path, tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copy, labels = _unlabelled_new_rows(db, tmp_path)
    _true_sets(copy, labels)
    rows = pd.read_csv(labels, dtype=str, keep_default_na=False)
    rows.head(h.A_MIN_ROWS - 1).to_csv(labels, index=False)  # 99 rows of group A
    assert (rows.head(h.A_MIN_ROWS - 1)["set"] == "A").all()
    code = h.main(["score", "--db", str(copy), "--labels", str(labels),
                   "--choice", str(tmp_path / "none.json"),
                   "--adopt-rule", str(_rule_and_draw(repo, labels))])  # fmt: skip
    assert code == 2 and "Wait for more data" in capsys.readouterr().out
    assert (
        h.ledger_entries(tmp_path / "data" / "private" / "ledger.jsonl")[-1]["status"] == "refused"
    )


def test_scoring_refuses_a_group_a_merchant_that_has_training_labels(
    db: Path, tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copy, labels = _unlabelled_new_rows(db, tmp_path, ("test_a",))
    _true_sets(copy, labels)
    rows = pd.read_csv(labels, dtype=str, keep_default_na=False)
    assert (rows["set"] == "K").any()
    rows["set"] = "A"  # known merchants passed off as new ones
    rows.to_csv(labels, index=False)
    code = h.main(["score", "--db", str(copy), "--labels", str(labels),
                   "--choice", str(tmp_path / "none.json"),
                   "--adopt-rule", str(_rule_and_draw(repo, labels))])  # fmt: skip
    assert code == 2 and "not new merchants" in capsys.readouterr().out


def test_labelling_waits_for_the_committed_rule(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = tmp_path / "data" / "private" / "eval" / "labels.csv"
    code = h.main(["label", "--db", str(db), "--selection", str(_selection(tmp_path, ["f1"])),
                   "--labels", str(labels)])  # fmt: skip
    assert code == 2 and "The rule comes first" in capsys.readouterr().out
    assert not labels.exists()  # nothing frozen


# --- My answers of 2026-11-18: (c) needs (b), K minimum, skipped rows left out ---


def test_c_is_not_adopted_when_b_fails_even_if_c_passes_on_b() -> None:
    blind = _blind(a_merchants=75, k_rows=60)
    # (b) +1 point on (a): fails; (c) +30 points on (b), guard fine: passes on its own only
    b, c = h.compare(_results(blind, {"current": (60, 50), "cleaner+directory": (62, 50),
                                      "+RAG": (107, 50)}), blind)  # fmt: skip
    assert not b.passes
    assert c.gain_ok and c.guard_ok and not c.base_passes and not c.passes


def test_scoring_refuses_fewer_than_40_labelled_group_k_rows(
    db: Path, tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    copy, labels = _unlabelled_new_rows(db, tmp_path, ("test_a", "test_b"))
    _true_sets(copy, labels)
    rows = pd.read_csv(labels, dtype=str, keep_default_na=False)
    k = rows.index[rows["set"] == "K"]
    assert len(k) >= h.K_MIN_ROWS
    rows.loc[k[h.K_MIN_ROWS - 1 :], "category"] = ""  # skipped while labelling: 39 left
    rows.to_csv(labels, index=False)
    monkeypatch.setattr(h, "A_MIN_ROWS", 1)
    code = h.main(["score", "--db", str(copy), "--labels", str(labels),
                   "--choice", str(tmp_path / "none.json"),
                   "--adopt-rule", str(_rule_and_draw(repo, labels))])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 2 and "only 39 labelled group K rows" in out
    assert h.ledger_entries(h.DEFAULT_LEDGER)[-1]["status"] == "refused"


def test_the_draw_leaves_out_rows_i_skipped_or_labelled_in_the_app(
    db: Path, tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    from lookahead.labels import label_row

    copy, _ = _unlabelled_new_rows(db, tmp_path, ("test_a", "test_b"))
    from lookahead.db import connect_readonly

    ro = connect_readonly(copy)
    candidates = set(h.categorizer_features(ro, "scotia-chequing")["fingerprint"])
    ro.close()
    conn = sqlite3.connect(copy)
    decided = {r[0] for r in conn.execute("SELECT fingerprint FROM category_labels")}
    fresh = sorted(candidates - decided)  # rows the draw could pick
    assert len(fresh) >= 20
    with conn:
        for fp in fresh[:20]:
            label_row(conn, "scotia-chequing", fp, None)  # "Skip / don't know"
    conn.close()
    _committed_rule(repo)
    _public_run_scored()
    monkeypatch.setattr(h, "BLIND_SINCE", date(2025, 10, 1))
    monkeypatch.setattr(h, "A_MIN_ROWS", 1)
    monkeypatch.setattr(h, "A_MAX_ROWS", 10_000)  # draw every A row, so a miss would show
    monkeypatch.setattr(h, "K_ROWS", 10_000)
    out = tmp_path / "data" / "private" / "eval" / "candidates.csv"
    assert h.main(["candidates", "--db", str(copy), "--out", str(out)]) == 0
    written = set(pd.read_csv(out, dtype=str)["fingerprint"])
    assert written and not written & set(fresh[:20])


# --- After the second review: counts before the draw, the rule pinned to the draw ---


def test_count_draws_nothing_and_matches_the_draw(
    db: Path, tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    copy, _ = _unlabelled_new_rows(db, tmp_path, ("test_a", "test_b"))
    monkeypatch.setattr(h, "BLIND_SINCE", date(2025, 10, 1))
    out = tmp_path / "data" / "private" / "eval" / "candidates.csv"
    # no adopt rule needed to count, and nothing is written or logged
    assert h.main(["candidates", "--db", str(copy), "--count"]) == 0
    printed = capsys.readouterr().out
    assert "nothing drawn" in printed and not out.exists()
    assert not [e for e in h.ledger_entries(h.DEFAULT_LEDGER) if e.get("status") == "drawn"]
    a_rows, k_rows = (int(x) for x in re.findall(r"group [AK] (\d+) rows", printed))
    # the draw, with no row limit, takes exactly what the count announced
    _committed_rule(repo)
    _public_run_scored()
    monkeypatch.setattr(h, "A_MIN_ROWS", 1)
    monkeypatch.setattr(h, "A_MAX_ROWS", 10_000)
    monkeypatch.setattr(h, "K_ROWS", 10_000)
    assert h.main(["candidates", "--db", str(copy), "--out", str(out)]) == 0
    written = pd.read_csv(out, dtype=str)
    assert (written["set"] == "A").sum() == a_rows and (written["set"] == "K").sum() == k_rows


def test_scoring_refuses_a_rule_changed_after_the_draw(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rule = _committed_rule(repo)
    h.add_to_ledger(h.DEFAULT_LEDGER, {"status": "drawn", "adopt_rule_sha256": "an older rule"})
    labels = tmp_path / "labels.csv"
    labels.write_text("fingerprint,set,category\nf1,A,Shopping\n", encoding="utf-8")
    assert h.main(_score_args(tmp_path, labels, rule)) == 2
    assert "changed after the draw" in capsys.readouterr().out
    assert h.ledger_entries(h.DEFAULT_LEDGER)[-1]["status"] == "refused"


def test_a_database_error_is_not_taken_for_no_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    import lookahead.labels

    def broken(*_: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(lookahead.labels, "current_labels", broken)
    with pytest.raises(sqlite3.OperationalError):
        h.decided_fingerprints(None, "scotia-chequing")
    conn = sqlite3.connect(":memory:")  # no labels table at all: nothing was decided
    monkeypatch.undo()
    assert h.decided_fingerprints(conn, "scotia-chequing") == set()


# --- After the third review: scoring needs the logged draw and exactly its rows ---


@pytest.mark.slow
def test_draw_then_label_then_score_goes_through(
    db: Path, tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    from lookahead.db import connect_readonly
    from lookahead.labels import current_labels

    conn = connect_readonly(db)
    truth = {f: lab.category for f, lab in current_labels(conn, "scotia-chequing").items()}
    conn.close()
    copy, _ = _unlabelled_new_rows(db, tmp_path, ("test_a", "test_b"))
    monkeypatch.setattr(h, "BLIND_SINCE", date(2025, 10, 1))
    monkeypatch.setattr(h, "A_MIN_ROWS", 1)
    rule = _committed_rule(repo)
    _public_run_scored()
    selection = tmp_path / "data" / "private" / "eval" / "candidates.csv"
    assert h.main(["candidates", "--db", str(copy), "--out", str(selection)]) == 0
    labels = tmp_path / "data" / "private" / "eval" / "labels.csv"
    h.freeze_selection(selection, labels)
    rows = pd.read_csv(labels, dtype=str, keep_default_na=False)
    rows["category"] = [truth.get(f) or "" for f in rows["fingerprint"]]  # as if I labelled
    rows.to_csv(labels, index=False)
    choice = tmp_path / "choice.json"
    choice.write_text(json.dumps({"params": {"model__C": 1.0}, "flag_threshold": 0.5,
                                  "synthetic_months": 0}), encoding="utf-8")  # fmt: skip
    code = h.main(["score", "--db", str(copy), "--labels", str(labels), "--choice", str(choice),
                   "--adopt-rule", str(rule)])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0, out
    assert "changed after the draw" not in out and "adopted: " in out


def test_scoring_refuses_without_a_logged_draw(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rule = _committed_rule(repo)
    labels = tmp_path / "labels.csv"
    labels.write_text("fingerprint,set,category\nf1,A,Shopping\n", encoding="utf-8")
    assert h.main(_score_args(tmp_path, labels, rule)) == 2
    assert "no draw" in capsys.readouterr().out


def test_scoring_refuses_a_labels_file_that_is_not_the_draw(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rule = _committed_rule(repo)
    labels = tmp_path / "labels.csv"
    labels.write_text("fingerprint,set,category\nf1,A,Shopping\n", encoding="utf-8")
    _log_draw(labels, rule)
    labels.write_text("fingerprint,set,category\nf1,A,Shopping\nf9,A,Shopping\n",
                      encoding="utf-8")  # fmt: skip
    assert h.main(_score_args(tmp_path, labels, rule)) == 2
    assert "does not hold the drawn rows" in capsys.readouterr().out


def test_a_database_error_is_not_taken_for_no_holdout() -> None:
    import sqlite3

    class Broken:
        def execute(self, *_: object) -> None:
            raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError):
        h.holdout_fingerprints(Broken())
    assert h.holdout_fingerprints(sqlite3.connect(":memory:")) == set()  # no splits table


# --- The deciding run moved to the public set (2026-11-18): my rows are a veto, drawn last ---


def test_my_rows_are_drawn_only_after_the_public_run(
    db: Path, tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _committed_rule(repo)
    out = tmp_path / "data" / "private" / "eval" / "candidates.csv"
    assert h.main(["candidates", "--db", str(db), "--out", str(out)]) == 2
    assert "public deciding run comes first" in capsys.readouterr().out and not out.exists()


def test_the_veto_counts_rows_wrong_beyond_a_per_group() -> None:
    blind = pd.DataFrame({"truth": "Shopping", "set": ["A"] * 18 + ["K"] * 76,
                          "group": [f"m{i}" for i in range(94)]})  # fmt: skip

    def result(variant: str, a_wrong: int, k_wrong: int) -> h.VariantResult:
        cats = ["Shopping"] * 94
        for i in [*range(a_wrong), *range(18, 18 + k_wrong)]:
            cats[i] = "Groceries"
        return h.VariantResult(variant, cats, ["model"] * 94)

    a = result("current", 3, 5)
    ok = h.veto({"current": a, "cleaner+directory": result("cleaner+directory", 5, 6)}, blind)
    assert ok[0].a_extra_wrong == 2 and ok[0].k_extra_wrong == 1 and ok[0].ok
    a_bad = h.veto({"current": a, "cleaner+directory": result("cleaner+directory", 6, 5)}, blind)
    assert a_bad[0].a_extra_wrong == 3 and not a_bad[0].ok  # 3 net worse on A: veto
    k_bad = h.veto({"current": a, "+RAG": result("+RAG", 0, 7)}, blind)
    assert k_bad[0].k_extra_wrong == 2 and not k_bad[0].ok  # (c) is checked against (a) too


def test_the_first_public_run_decides_and_only_under_this_rule_text() -> None:
    def run(comparison: str, passes: bool, sha: str = "v2", status: str = "scored") -> dict:
        return {"run": "external", "status": status, "comparison": comparison,
                "passes": passes, "adopt_rule_sha256": sha}  # fmt: skip

    entries = [
        run("cleaner+directory vs current", False),
        run("+RAG vs cleaner+directory", False),
        run("cleaner+directory vs current", True),  # a later --again run: recorded, not deciding
        run("+RAG vs cleaner+directory", True, status="refused"),  # a refusal decides nothing
        run("+RAG vs cleaner+directory", True, sha="v3"),  # scored under another rule text
    ]
    assert h.external_verdicts(entries, "v2") == {"cleaner+directory": False, "+RAG": False}
    assert h.external_verdicts(entries, "v3") == {"+RAG": True}
    assert h.external_verdicts(entries, None) == {}


def test_a_rule_without_the_public_run_is_refused(repo: Path) -> None:
    old = _committed_rule(repo, "# Adopt rule, version 1\nIn force.\n")
    status = h.adopt_rule_status(old)
    assert not status.ok and "not version 2" in status.reason
