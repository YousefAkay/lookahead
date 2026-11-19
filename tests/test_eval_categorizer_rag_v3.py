"""Adopt rule version 3 (RAG alone, on a new public split): the split's pinned counts, which
rows RAG is asked about, the void rule, scored once, first valid run decides. Fake models and
clients only; no network. The pinned-count test runs only where the public files are."""

import json
from pathlib import Path

import pandas as pd
import pytest

from eval import categorizer_rag_v3 as r
from eval import categorizer_v2 as h
from eval import categorizer_v2_external as x
from lookahead.categorizer import Prediction


@pytest.fixture(autouse=True)
def _safe_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(h, "DEFAULT_LEDGER", tmp_path / "data" / "private" / "ledger.jsonl")
    monkeypatch.setattr(r, "code_state", lambda: ("abc123", False))  # a clean tree


def _ledger() -> list[dict[str, object]]:
    return h.ledger_entries(h.DEFAULT_LEDGER)


# --- The split ---


@pytest.mark.slow
@pytest.mark.skipif(x.check_files(x.EXTERNAL_DIR) != [], reason="public files not downloaded")
def test_the_split_is_the_one_the_rule_pins() -> None:
    counts = r.split_counts(r.load_split())
    assert counts == {
        "train": [36993, 786], "test": [11007, 286], "sample": [600, 275],
        "v2_sample_merchants_in_test": 0, "merchants_in_train_and_test": 0,
        "sample_names_in_the_v2_sample": 0,
    }  # fmt: skip


def test_the_rule_numbers_are_the_code_numbers() -> None:
    text = r.RULE.read_text(encoding="utf-8")
    assert text.startswith(r.RULE_MARKER)
    assert "default_rng(1).choice(..., size=286, replace=False)" in text
    assert "more than 5% of its answers" in text and r.MAX_REFUSED_SHARE == 0.05
    assert "at least 3 points" in text and r.MIN_GAIN == 0.03
    assert "600 of those" in text and r.SAMPLE_ROWS == 600 and r.SAMPLE_PER_MERCHANT == 3


# --- Which rows RAG is asked about ---


class _Model:
    """Row 0: a flagged new merchant; row 1: a sure new merchant; row 2: a known merchant
    whose labels disagree (flagged, but not a new merchant)."""

    def predict_rows(self, rows: pd.DataFrame) -> list[Prediction]:
        return [Prediction("Shopping", "new merchant", True, 0.2),
                Prediction("Groceries", "new merchant", False, 0.99),
                Prediction("Transport", "known merchant, labels disagree", True, 0.5)]  # fmt: skip


SAMPLE = pd.DataFrame({
    "description": ["[debit] TST* Golden Dragon #12", "[debit] SAFEWAY 1", "[debit] SHELL 9"],
    "merchant": ["GOLDEN DRAGON", "SAFEWAY", "SHELL"],
    "mine": ["Restaurants & cafés", "Groceries", "Transport"],
})  # fmt: skip


def test_rag_is_asked_only_about_flagged_new_merchants_with_the_cleaned_name() -> None:
    asked: list[str] = []
    p = r.predict(_Model(), SAMPLE, lambda name: asked.append(name) or "Restaurants & cafés")
    assert asked == ["Golden Dragon"]  # the cleaned description, never the key
    assert p.a == ["Shopping", "Groceries", "Transport"]
    assert p.with_rag == ["Restaurants & cafés", "Groceries", "Transport"]
    assert (p.rows_asked, p.rows_answered) == (1, 1)


# --- The void rule ---


def _log(**reasons: int) -> r.AnswerLog:
    keys = (f"{reason}-{i}" for reason, n in reasons.items() for i in range(n))
    return r.AnswerLog({k: k.rsplit("-", 1)[0].replace("_", " ") for k in keys})


def test_void_when_rag_answers_no_row() -> None:
    assert "none" in (r.void_reason(5, 0, _log(unsure=5)) or "")
    assert r.void_reason(0, 0, _log()) is None  # nothing asked: not void


def test_void_above_five_percent_refused_answers_unsure_is_usable() -> None:
    assert r.void_reason(100, 90, _log(ok=90, unsure=5, not_JSON=5)) is None  # exactly 5%
    assert r.void_reason(100, 89, _log(ok=89, unsure=5, not_JSON=6)) is not None
    assert r.void_reason(100, 50, _log(ok=50, unsure=50)) is None


def _run(answer: str | None, log: r.AnswerLog) -> tuple[bool | None, list[dict[str, object]], bool]:
    split = r.Split(SAMPLE, SAMPLE, SAMPLE, SAMPLE)
    entries: list[dict[str, object]] = []
    voided: list[bool] = []
    result = r.score(split, _Model(), lambda name: answer, log, entries.append,
                     lambda: voided.append(True))  # fmt: skip
    return result, entries, bool(voided)


def test_a_void_run_records_counts_only_and_no_accuracy(capsys: pytest.CaptureFixture[str]) -> None:
    result, entries, voided = _run(None, _log(not_JSON=1))
    out = capsys.readouterr().out
    assert result is None and voided and "VOID RUN" in out and "variant" not in out
    [entry] = entries
    assert entry["status"] == "void" and entry["answer_reasons"] == {"not JSON": 1}
    assert "a_correct" not in entry


def test_a_valid_run_records_both_variants_and_the_comparison() -> None:
    result, entries, voided = _run("Restaurants & cafés", _log(ok=1))
    assert not voided and isinstance(result, bool)
    assert [e.get("variant") or e.get("comparison") for e in entries] == [
        "(a)", "(a)+RAG", r.COMPARISON]  # fmt: skip
    assert entries[0]["a_correct"] == 2 and entries[1]["a_correct"] == 3


def test_move_cache_aside_keeps_the_answers_and_empties_the_path(tmp_path: Path) -> None:
    cache = tmp_path / "data" / "private" / "cache" / "rag_v3.json"
    assert r.move_cache_aside(cache, "2026-11-19T10:00:00") is None
    cache.parent.mkdir(parents=True)
    cache.write_text("{}", encoding="utf-8")
    moved = r.move_cache_aside(cache, "2026-11-19T10:00:00")
    assert moved is not None and moved.exists() and not cache.exists()
    assert moved.name == "rag_v3.void-2026-11-19T100000.json"


# --- RAG answers and their reasons ---


def test_answer_reasons_are_logged_once_per_request(tmp_path: Path) -> None:
    from tests.test_rag_categorizer import FakeClient

    class Citing(FakeClient):
        def create(self, **request: object):  # type: ignore[no-untyped-def]
            content = request["messages"][0]["content"]  # type: ignore[index]
            first = next(line.split(" | ", 1)[0] for line in content.splitlines() if " | " in line)
            self.answer = (
                "```json\n" + json.dumps({"category": "Groceries", "cited": [first]}) + "\n```"
            )
            return super().create(**request)

    train = pd.DataFrame({"description": ["[debit] SAFEWAY 1", "[debit] KROGER 2"],
                          "mine": ["Groceries", "Groceries"]})  # fmt: skip
    cache = tmp_path / "data" / "private" / "cache" / "rag_v3.json"
    answer, log = r.rag_answer_function(train, cache, send=True, client=Citing(""))
    assert answer("SAFEWAYY") == "Groceries" and answer("SAFEWAYY") == "Groceries"
    assert log.counts() == {"ok": 1}  # a fenced answer is usable; asked twice, logged once
    plain, plain_log = r.rag_answer_function(train, tmp_path / "data" / "private" / "b.json",
                                             send=True, client=FakeClient("Groceries"))  # fmt: skip
    assert plain("KROGERR") is None and plain_log.counts() == {"not JSON": 1}


# --- The command: refusals, scored once, first valid run decides ---


def _ok_rule(monkeypatch: pytest.MonkeyPatch, sha: str = "rule-sha") -> None:
    monkeypatch.setattr(h, "adopt_rule_status",
                        lambda path, marker: h.RuleStatus(True, "ok", "c0ffee", sha))  # fmt: skip


def test_scoring_needs_rag_and_send_and_logs_the_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    _ok_rule(monkeypatch)
    assert r.main(["score"]) == 2 and r.main(["score", "--rag"]) == 2
    assert [e["status"] for e in _ledger()] == ["refused", "refused"]
    assert all(e["run"] == r.RUN for e in _ledger())


def test_an_uncommitted_rule_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(r, "RULE", tmp_path / "rule.md")
    monkeypatch.setattr(r, "ROOT", tmp_path)
    assert r.main(["score", "--rag", "--send"]) == 2
    [entry] = _ledger()
    assert entry["status"] == "refused" and "does not exist" in str(entry["reason"])


def test_a_cache_outside_data_private_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ok_rule(monkeypatch)
    assert r.main(["score", "--rag", "--send", "--rag-cache", str(tmp_path / "c.json")]) == 2
    assert "data/private" in str(_ledger()[0]["reason"])


def test_the_sample_is_scored_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _ok_rule(monkeypatch)
    split = r.Split(SAMPLE, SAMPLE, SAMPLE, SAMPLE)
    monkeypatch.setattr(x, "check_files", lambda folder: [])
    monkeypatch.setattr(r, "load_split", lambda folder: split)
    h.add_to_ledger(h.DEFAULT_LEDGER, {"run": r.RUN, "status": "scored",
                                       "selection_key": r.sample_key(SAMPLE)})  # fmt: skip
    cache = tmp_path / "data" / "private" / "cache" / "rag_v3.json"
    assert r.main(["score", "--rag", "--send", "--rag-cache", str(cache)]) == 2
    assert "scored once" in str(_ledger()[-1]["reason"])


def test_a_void_run_does_not_block_the_next_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ok_rule(monkeypatch)
    split = r.Split(SAMPLE, SAMPLE, SAMPLE, SAMPLE)
    monkeypatch.setattr(x, "check_files", lambda folder: [])
    monkeypatch.setattr(r, "load_split", lambda folder: split)
    monkeypatch.setattr(r, "split_counts", lambda s: {"train": 0, "test": 0, "sample": 0})
    monkeypatch.setattr(x, "train_variant", lambda variant, rows: _Model())
    cache = tmp_path / "data" / "private" / "cache" / "rag_v3.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("{}", encoding="utf-8")

    def silent(train, path, send, client=None):  # type: ignore[no-untyped-def]
        return (lambda name: None), _log(not_JSON=1)

    monkeypatch.setattr(r, "rag_answer_function", silent)
    assert r.main(["score", "--rag", "--send", "--rag-cache", str(cache)]) == 3
    assert _ledger()[-1]["status"] == "void" and not cache.exists()  # moved aside

    def answering(train, path, send, client=None):  # type: ignore[no-untyped-def]
        return (lambda name: "Restaurants & cafés"), _log(ok=1)

    monkeypatch.setattr(r, "rag_answer_function", answering)
    assert r.main(["score", "--rag", "--send", "--rag-cache", str(cache)]) == 0
    assert r.verdict(_ledger(), "rule-sha") is not None


def test_the_first_valid_run_decides() -> None:
    def comparison(passes: bool, status: str = "scored", sha: str = "s3") -> dict[str, object]:
        return {"run": r.RUN, "status": status, "comparison": r.COMPARISON, "passes": passes,
                "adopt_rule_sha256": sha}  # fmt: skip

    entries = [
        {"run": r.RUN, "status": "void", "adopt_rule_sha256": "s3"},
        comparison(False, sha="other"),  # another rule text
        comparison(True),  # the first valid run
        comparison(False),  # a later --again: recorded, decides nothing
    ]
    assert r.verdict(entries, "s3") is True
    assert r.verdict(entries, None) is None and r.verdict([], "s3") is None


def test_uncommitted_code_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _ok_rule(monkeypatch)
    monkeypatch.setattr(r, "code_state", lambda: ("abc123", True))
    assert r.main(["score", "--rag", "--send"]) == 2
    assert "uncommitted" in str(_ledger()[-1]["reason"])


def test_a_crashed_run_is_recorded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _ok_rule(monkeypatch)
    monkeypatch.setattr(x, "check_files", lambda folder: [])
    monkeypatch.setattr(r, "load_split", lambda folder: r.Split(SAMPLE, SAMPLE, SAMPLE, SAMPLE))
    monkeypatch.setattr(r, "split_counts", lambda s: {"train": 0, "test": 0, "sample": 0})

    def boom(variant, rows):  # type: ignore[no-untyped-def]
        raise TimeoutError("network")

    monkeypatch.setattr(x, "train_variant", boom)
    cache = tmp_path / "data" / "private" / "cache" / "rag_v3.json"
    with pytest.raises(TimeoutError):
        r.main(["score", "--rag", "--send", "--rag-cache", str(cache)])
    last = _ledger()[-1]
    assert last["status"] == "crashed" and last["reason"] == "TimeoutError"
    assert last["code_commit"] == "abc123"


def test_an_again_run_never_decides() -> None:
    entries = [{"run": r.RUN, "status": "scored", "comparison": r.COMPARISON, "passes": True,
                "adopt_rule_sha256": "s3", "again": True}]  # fmt: skip
    assert r.verdict(entries, "s3") is None


def test_the_v2_harness_does_not_take_the_v3_rule_file() -> None:
    assert r.RULE.resolve() != h.APPROVED_ADOPT_RULE.resolve()
    assert h.adopt_rule_status(h.APPROVED_ADOPT_RULE, r.RULE_MARKER).ok is False
