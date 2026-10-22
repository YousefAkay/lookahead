"""The scoring script for the proposals (eval.proposals_v2): code and tests only, no scoring run."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from eval import proposals_v2 as pv
from lookahead import recurring_store as rs
from lookahead.generator.archetypes import HELD_OUT, LONG_HELD_OUT, development_ids
from lookahead.generator.plant import PlantedEvent, read_answer_key, write_planted
from lookahead.generator.schedules import GeneratedTxn
from lookahead.rent import RentRule
from lookahead.setting_proposals import SettingProposal

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def planted_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("planted")
    for pid in development_ids(long=False):
        write_planted(pid, root / "12")
    return root


def persona_with(planted_root: Path, what: str) -> str:
    for pid in development_ids(long=False):
        if any(e.what == what for e in read_answer_key(planted_root / "12", pid)):
            return pid
    raise AssertionError(what)


def gen(day: date, cents: int, series: str) -> GeneratedTxn:
    return GeneratedTxn(
        posted_date=day, amount_cents=cents, txn_type="withdrawal", merchant="x",
        category="Rent & housing", flow_type="recurring_outflow", series_id=series,
    )  # fmt: skip


# --- the matching rules ---


def test_a_window_may_wrap_a_month_end() -> None:
    rule = RentRule((90000,), 30, 3)
    inside = [pv.in_window(d, rule) for d in (28, 30, 31, 1, 3, 4)]
    assert inside == [False, True, True, True, True, False]
    assert pv.in_window(2, RentRule((1,), 1, 4)) and not pv.in_window(5, RentRule((1,), 1, 4))


def test_share_in_is_the_share_of_a_proposals_payments_that_are_the_events() -> None:
    a, b, c = (date(2026, 1, 2), 1), (date(2026, 2, 2), 1), (date(2026, 3, 2), 1)
    assert pv.share_in({a, b}, {a, b, c}) == 1.0 and pv.share_in({a, b}, {a}) == 0.5
    assert pv.share_in(set(), {a}) == 0.0


def rent_rows() -> list[GeneratedTxn]:
    days = [date(2026, m, d) for m, d in [(1, 2), (2, 3), (3, 2), (4, 1), (5, 4), (6, 2), (7, 3)]]
    return [gen(d, -(90000 if d.month < 5 else 95000), "p-rent") for d in days]


def rent_event() -> PlantedEvent:
    return PlantedEvent(
        "p:rent-increase", "p", "rent", "increase", date(2026, 7, 3),
        {"sizes_cents": [90000, 95000], "first_new_payment": "2026-05-04"},
    )  # fmt: skip


def rent_proposal(rule: RentRule) -> SettingProposal:
    return SettingProposal("rent", "sid|x", "rent|sid|x", "t", (), "likely", rent=rule)


def test_a_rent_proposal_needs_both_sizes_and_a_window_over_the_send_days() -> None:
    rows = rent_rows()
    pairs = {(t.posted_date, t.amount_cents) for t in rows}
    ok = rent_proposal(RentRule((90000, 95000), 1, 4))
    assert pv.matches(ok, rent_event(), rows, pairs)
    assert not pv.matches(rent_proposal(RentRule((95000,), 1, 4)), rent_event(), rows, pairs)
    wrong_days = rent_proposal(RentRule((90000, 95000), 10, 12))
    assert not pv.matches(wrong_days, rent_event(), rows, pairs)


def test_a_proposal_about_other_payments_is_not_a_match() -> None:
    rows = rent_rows()
    other = {(date(2026, m, 15), -1500) for m in range(1, 5)}
    assert not pv.matches(rent_proposal(RentRule((90000, 95000), 1, 4)), rent_event(), rows, other)


def test_a_link_needs_payments_on_both_sides_of_the_rename() -> None:
    rows = [gen(date(2026, m, 8), -2500, "p-sub-x") for m in range(1, 9)]
    event = PlantedEvent(
        "p:subscription-rename", "p", "link", "rename", date(2026, 7, 8),
        {"series_id": "p-sub-x", "first_new_payment": "2026-06-08"},
    )  # fmt: skip
    link = SettingProposal("link", "a", "link|a|b", "t", (), "likely")
    both = {(t.posted_date, t.amount_cents) for t in rows}
    assert pv.matches(link, event, rows, both)
    assert not pv.matches(link, event, rows, {p for p in both if p[0].month < 6})  # only old side
    assert not pv.matches(link, event, rows, {p for p in both if p[0].month >= 6})  # only new side


def test_a_possible_subscription_matches_the_planted_charges_only() -> None:
    rows = [gen(date(2026, m, 5), -1299, "p-sub-planted") for m in (7, 8, 9)]
    event = PlantedEvent("p:new", "p", "possible", "new", date(2026, 8, 5), {})
    proposal = SettingProposal("possible", "k", "possible|k", "t", (), "possible")
    assert pv.matches(proposal, event, rows, {(date(2026, 7, 5), -1299), (date(2026, 8, 5), -1299)})
    other = {(date(2026, 7, 6), -999), (date(2026, 8, 6), -999)}
    assert not pv.matches(proposal, event, rows, other)


# --- the adopt rule (b) and the leakage flag (e) ---


def test_a_kind_with_fewer_than_twenty_planted_events_cannot_pass() -> None:
    ks = pv.KindScore(shown=20, matched=18, events=19, observable=19, recovered=15)
    ok, reasons = pv.kind_status(ks)
    assert not ok and any("cannot pass" in r for r in reasons)


def test_precision_and_recall_both_have_to_clear_their_bars() -> None:
    def status(**kw: int) -> bool:
        base = {"shown": 10, "matched": 8, "events": 25, "observable": 20, "recovered": 12}
        return pv.kind_status(pv.KindScore(**{**base, **kw}))[0]

    assert status()
    assert not status(matched=7)  # precision 70 percent
    assert not status(recovered=11)  # recall 55 percent of the 20 counted
    assert not status(shown=0, matched=0)


def test_recall_is_over_the_events_complete_by_the_last_origin_only() -> None:
    ks = pv.KindScore(shown=10, matched=8, events=25, observable=10, recovered=6)
    ok, reasons = pv.kind_status(ks)
    assert ok, reasons  # 6 of the 10 counted, although 6 of 25 would be 24 percent
    zero = pv.KindScore(shown=10, matched=8, events=25, observable=0, recovered=0)
    assert not pv.kind_status(zero)[0]  # nothing counted is not a pass


def test_precision_above_ninety_five_percent_raises_the_leakage_flag() -> None:
    loud = pv.KindScore(shown=40, matched=39, events=30, observable=30, recovered=25)
    ok, reasons = pv.kind_status(loud)
    assert not ok and any("LEAKAGE CHECK FIRST" in r for r in reasons)
    quiet = pv.KindScore(shown=40, matched=34, events=30, observable=30, recovered=25)
    assert not any("LEAKAGE" in r for r in pv.kind_status(quiet)[1])


# --- (c) one click against zero ---


def test_one_click_more_than_one_percent_worse_for_any_archetype_fails_the_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pv, "stats", lambda rows: {"n": len(rows), "err30": rows[0]["err"]})
    zero = [{"archetype": "A", "err": 100.0}, {"archetype": "B", "err": 200.0}]
    one = [{"archetype": "A", "err": 100.9}, {"archetype": "B", "err": 202.1}]
    table = pv.compare_arms(zero, one)
    assert table["A"]["ok"] and not table["B"]["ok"]
    assert table["B"]["change"] == pytest.approx(0.0105)
    text = pv.report_text(pv.Scores(), 1, table)
    assert "FAILS" in text and "1 of 2 archetypes" in text


# --- the ledger, the report, the refusals ---


def test_every_run_is_counted_in_the_variant_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "variants.json"
    assert pv.record_variant(ledger, "spec as written", ["12"], False) == 1
    assert pv.record_variant(ledger, "a changed proposer", ["12", "long"], True) == 2
    entries = json.loads(ledger.read_text("utf-8"))
    assert [e["label"] for e in entries] == ["spec as written", "a changed proposer"]
    assert all({"code", "planter_version", "seed", "when"} <= set(e) for e in entries)


def test_the_report_shows_n_the_excluded_events_and_the_variant_count() -> None:
    scores = pv.Scores(personas=3)
    scores.kinds["rent"] = pv.KindScore(shown=4, matched=3, events=18, observable=12, recovered=9)
    text = pv.report_text(scores, 2)
    assert "Variants tried so far: 2" in text
    assert "| rent | 18 | 12 | 6 | 4 | 75% (3/4) | 75% (9/12) |" in text  # 6 events excluded
    assert "| link | 0 | 0 | 0 | 0 | - (0/0) | - (0/0) |" in text and "cannot pass" in text


def test_every_report_repeats_the_card_pair_caution() -> None:
    for arms in (None, {}):
        text = pv.report_text(pv.Scores(), 1, arms)
        assert "likely flattering" in text and "308757c" in text and "after the planter" in text


@pytest.mark.parametrize("held_out", [sorted(HELD_OUT)[0], sorted(LONG_HELD_OUT)[0]])
def test_held_out_personas_are_refused(held_out: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError), pv.planted_db(held_out, Path("12"), tmp_path, tmp_path):
        pass


def test_an_answer_key_that_is_not_the_planters_output_is_refused(
    planted_root: Path, tmp_path: Path
) -> None:
    pid = development_ids(long=False)[0]
    copy = tmp_path / "root"
    (copy / "12" / pid).mkdir(parents=True)
    for name in ("export.csv", "answer_key.json"):
        (copy / "12" / pid / name).write_bytes((planted_root / "12" / pid / name).read_bytes())
    key = json.loads((copy / "12" / pid / "answer_key.json").read_text("utf-8"))
    fake = {"event_id": f"{pid}:fake", "persona_id": pid, "kind": "rent", "what": "late",
            "observable_from": "2026-08-01", "details": {}}  # fmt: skip
    key["events"].append(fake)
    (copy / "12" / pid / "answer_key.json").write_text(json.dumps(key), encoding="utf-8")
    with (
        pytest.raises(ValueError, match="answer key on disk"),
        pv.planted_db(pid, Path("12"), tmp_path / "w", copy),
    ):
        pass


# --- precision and recall on a planted persona, with a controlled proposer ---


def rent_evidence(db: pv.PlantedDb) -> tuple[str, ...]:
    pid = db.persona.persona.persona_id
    rows = db.conn.execute(
        "SELECT fingerprint FROM transactions WHERE account = ? AND description = 'withdrawal'"
        " AND sub_description LIKE 'Free Interac%'", (pid,),
    ).fetchall()  # fmt: skip
    return tuple(r[0] for r in rows)


def test_a_good_rent_proposal_is_matched_and_recovers_the_event(
    planted_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = persona_with(planted_root, "increase")
    with pv.planted_db(pid, Path("12"), tmp_path, planted_root) as db:
        event = next(e for e in db.events if e.what == "increase")
        sizes = tuple(sorted(event.details["sizes_cents"]))
        good = SettingProposal("rent", "sid|x", "rent|sid|x", "t", rent_evidence(db), "likely",
                               rent=RentRule(sizes, 1, 31))  # fmt: skip
        monkeypatch.setattr(pv, "current_proposals", lambda *a, **k: [good])
        days = pv.backtest_origins()[-3:]
        scores = pv.score_planted(db, days)
    rent = scores.kinds["rent"]
    assert (rent.shown, rent.matched) == (1, 1) and rent.recovered >= 1 and rent.events >= 1
    assert scores.kinds["link"].shown == 0 and scores.kinds["possible"].shown == 0


def test_a_proposal_that_misses_the_new_size_is_shown_but_not_matched(
    planted_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = persona_with(planted_root, "increase")
    with pv.planted_db(pid, Path("12"), tmp_path, planted_root) as db:
        event = next(e for e in db.events if e.what == "increase")
        bad = SettingProposal(
            "rent",
            "sid|x",
            "rent|sid|x",
            "t",
            rent_evidence(db),
            "likely",
            rent=RentRule((min(event.details["sizes_cents"]),), 1, 31),
        )
        monkeypatch.setattr(pv, "current_proposals", lambda *a, **k: [bad])
        scores = pv.score_planted(db, pv.backtest_origins()[-3:])
    rent = scores.kinds["rent"]
    assert rent.shown == 1 and rent.matched == 0 and rent.recovered == 0


def test_a_proposal_shown_only_long_after_the_event_does_not_count_as_recall(
    planted_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = persona_with(planted_root, "increase")
    with pv.planted_db(pid, Path("12"), tmp_path, planted_root) as db:
        event = next(e for e in db.events if e.what == "increase")
        sizes = tuple(sorted(event.details["sizes_cents"]))
        good = SettingProposal("rent", "sid|x", "rent|sid|x", "t", rent_evidence(db), "likely",
                               rent=RentRule(sizes, 1, 31))  # fmt: skip
        late = event.observable_from + timedelta(days=61)
        monkeypatch.setattr(
            pv, "current_proposals", lambda c, p, as_of=None, **k: [good] if as_of >= late else []
        )
        origins = [event.observable_from, late]
        scores = pv.score_planted(db, origins)
    increase_recovered = scores.kinds["rent"].recovered
    assert scores.kinds["rent"].matched == 1 and increase_recovered == 0


# --- the plumbing end to end, on two planted personas and two origins (not a result) ---


def test_the_scorer_runs_end_to_end_and_counts_the_run(planted_root: Path, tmp_path: Path) -> None:
    ledger = tmp_path / "variants.json"
    text = pv.run_score(
        ["12"], "plumbing", tmp_path / "w", planted_root, origin_days=pv.backtest_origins()[-2:],
        personas=2, ledger=ledger,
    )  # fmt: skip
    assert "Personas scored: 2" in text and "Variants tried so far: 1" in text
    assert json.loads(ledger.read_text("utf-8"))[0]["label"] == "plumbing"


def test_the_arms_run_end_to_end_with_a_handful_of_futures(
    planted_root: Path, tmp_path: Path
) -> None:
    pid = development_ids(long=False)[0]
    with pv.planted_db(pid, Path("12"), tmp_path, planted_root) as db:
        zero, one, done = pv.forecast_arms(db, pv.backtest_origins()[-2:], futures=20)
        decisions = rs.load_decisions(db.conn, pid)
    assert len(zero) == len(one) == 2 and set(done) == set(pv.KINDS)
    assert all(d.key.startswith(("sid|", "user|")) for d in decisions)
    assert all(d.decided_at in pv.backtest_origins()[-2:] for d in decisions)  # dated at an origin


# --- (d) zero-confirmation forecasts do not read the proposers ---


def test_nothing_in_the_forecast_reads_the_proposers() -> None:
    readers = []
    for folder in ("lookahead/forecast", "eval", "lookahead"):
        for path in (ROOT / folder).glob("*.py"):
            if path.name in ("setting_proposals.py", "proposals_v2.py", "proposals_recover.py"):
                continue
            if "setting_proposals" in path.read_text("utf-8"):
                readers.append(path.name)
    assert readers == []  # only the app page and the scorers import them
    app = (ROOT / "app" / "pages" / "4_Recurring.py").read_text("utf-8")
    assert "setting_proposals" in app


def test_events_not_complete_by_the_last_origin_are_excluded_from_recall_by_the_answer_key(
    planted_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a proposal that matches such an event cannot make it count: the answer key decides."""
    pid = persona_with(planted_root, "increase")
    with pv.planted_db(pid, Path("12"), tmp_path, planted_root) as db:
        early = pv.backtest_origins()[:2]  # December 2025: before any event is complete
        event = next(e for e in db.events if e.what == "increase")
        sizes = tuple(sorted(event.details["sizes_cents"]))
        good = SettingProposal("rent", "sid|x", "rent|sid|x", "t", rent_evidence(db), "likely",
                               rent=RentRule(sizes, 1, 31))  # fmt: skip
        monkeypatch.setattr(pv, "current_proposals", lambda *a, **k: [good])
        scores = pv.score_planted(db, early)
    rent = scores.kinds["rent"]
    assert rent.events >= 1 and rent.observable == 0 and rent.recovered == 0
    assert rent.matched == 1  # precision still sees it; recall does not


def test_every_report_says_the_veto_rerun_after_the_real_account_changes_is_not_independent() -> (
    None
):
    for arms in (None, {}):
        text = pv.report_text(pv.Scores(), 4, arms)
        assert "Not an independent check" in text and "rent cluster rule" in text
        assert "5 percent tolerance" in text and "planter additions of version 3" in text


def test_a_change_goes_on_the_variant_ledger_without_a_scored_run(tmp_path: Path) -> None:
    ledger = tmp_path / "variants.json"
    assert pv.record_variant(ledger, "rent cluster", [], False, "change") == 1
    assert pv.record_variant(ledger, "5 percent tolerance", [], False, "change") == 2
    assert pv.record_variant(ledger, "planter version 3", [], False, "change") == 3
    assert pv.record_variant(ledger, "first scored run", ["12"], False) == 4
    kinds = [e["kind"] for e in json.loads(ledger.read_text("utf-8"))]
    assert kinds == ["change", "change", "change", "scored run"]


def test_the_variant_command_adds_a_change_to_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pv, "LEDGER", tmp_path / "ledger.json")
    assert pv.main(["variant", "--label", "rent cluster"]) == 0
    assert "variants on the ledger: 1" in capsys.readouterr().out
    assert json.loads((tmp_path / "ledger.json").read_text("utf-8"))[0]["kind"] == "change"


def test_the_variant_command_takes_the_kind_of_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pv, "LEDGER", tmp_path / "ledger.json")
    assert pv.main(["variant", "--label", "a fix", "--kind", "bug fix"]) == 0
    assert json.loads((tmp_path / "ledger.json").read_text("utf-8"))[0]["kind"] == "bug fix"


def test_every_report_lists_the_changes_of_both_days() -> None:
    text = pv.report_text(pv.Scores(), 5)
    for word in (
        "2026-10-21",
        "2026-10-22",
        "newest rent amount",
        "rent window over every send day",
    ):
        assert word in text


# --- the ledger lives in the repo ---


def test_the_ledger_is_in_the_repo_is_not_ignored_and_holds_names_only() -> None:
    import subprocess

    path = ROOT / "docs" / "results" / "proposals_v2_ledger.json"
    assert pv.LEDGER == Path("docs/results/proposals_v2_ledger.json") and path.exists()
    ignored = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=ROOT).returncode
    assert ignored == 1  # 1 = not ignored
    entries = json.loads(path.read_text("utf-8"))
    assert len(entries) >= 5
    allowed = {"label", "kind", "when", "code", "planter_version", "seed", "sets", "arms", "run_id"}
    assert all(set(e) <= allowed for e in entries)
    assert [e["kind"] for e in entries[:5]] == ["change", "change", "change", "bug fix", "change"]


def test_the_report_lists_the_ledger_and_a_scored_run_gets_a_run_id(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    pv.record_variant(ledger, "a change", [], False, "change")
    pv.record_variant(ledger, "first scored run", ["12"], False)
    entries = json.loads(ledger.read_text("utf-8"))
    assert "run_id" not in entries[0] and entries[1]["run_id"].startswith("part-b-")
    text = pv.report_text(pv.Scores(), 2, None, entries)
    assert "## Variant ledger" in text and "1. change: a change" in text
    assert "2. scored run: first scored run" in text and entries[1]["run_id"] in text


# --- part (b): groups, memory stop, resume, the saved report ---


@pytest.fixture(autouse=True)
def calm_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests do not depend on how full this laptop's memory is (a test may set its own)."""
    monkeypatch.setattr(pv, "memory_load", lambda: 40)


def make_event(what: str, **details: object) -> PlantedEvent:
    kind = {"increase": "rent", "late": "rent", "rename": "link"}.get(what, "possible")
    return PlantedEvent(f"p:{what}", "p", kind, what, date(2026, 6, 1), dict(details))


def test_rent_increases_are_split_at_15_percent_and_pairs_into_plain_and_currency() -> None:
    low = make_event("increase", old_cents=100000, new_cents=112000)
    high = make_event("increase", old_cents=100000, new_cents=116000)
    assert (
        pv.event_group(low) == "increase below 15%" and pv.event_group(high) == "increase above 15%"
    )
    assert pv.event_group(make_event("late")) == "late payments"
    assert pv.event_group(make_event("rename")) == "rename"
    assert pv.event_group(make_event("new")) == "plain pairs"
    assert pv.event_group(make_event("new-fx")) == "currency pairs"


def test_scores_survive_a_round_trip_through_json_with_their_groups() -> None:
    scores = pv.Scores(personas=2)
    scores.kinds["rent"] = pv.KindScore(shown=3, matched=2, events=5, observable=4, recovered=3)
    scores.groups["rent: late payments"] = pv.KindScore(events=2, observable=2, recovered=1)
    again = pv.Scores.from_json(json.loads(json.dumps(scores.to_json())))
    assert (
        again.to_json() == scores.to_json() and again.groups["rent: late payments"].recovered == 1
    )


def part_b(planted_root: Path, tmp_path: Path, **kwargs):  # type: ignore[no-untyped-def]
    return pv.run_part_b(
        ["12"], "plumbing", tmp_path / "w", planted_root, origin_days=pv.backtest_origins()[-2:],
        personas=2, ledger=tmp_path / "ledger.json", results_root=tmp_path / "res", **kwargs,
    )  # fmt: skip


def test_part_b_reports_per_kind_per_set_by_sub_kind_and_marks_rent_vetoed(
    planted_root: Path, tmp_path: Path
) -> None:
    text = part_b(planted_root, tmp_path)
    needles = [
        "## Per kind, both sets together",
        "## 12-month set alone",
        "## By sub-kind",
        "rent: increase below 15%",
        "rent: increase above 15%",
        "possible: currency pairs",
        "possible: plain pairs",
        "Leakage flag",
        "likely flattering",
        "Not an independent check",
        "## Variant ledger",
        "VETOED BY MY ACCOUNT",
    ]
    for needle in needles:
        assert needle in text, needle
    assert text.count("| rent |") >= 2 and "(b) fails" in text  # on two personas it cannot pass


def test_rent_is_marked_vetoed_whatever_part_b_shows() -> None:
    scores = pv.Scores(personas=40)
    scores.kinds["rent"] = pv.KindScore(
        shown=40, matched=36, events=30, observable=30, recovered=25
    )
    info = {"run_id": "r", "planter_version": 3, "seed": 2026, "code": "x", "origins": 40}
    text = pv.part_b_text({"12": scores}, [], {**info, "seconds": 1.0})
    row = next(x for x in text.splitlines() if x.startswith("| rent |"))
    assert "(b) passes" in row and "VETOED BY MY ACCOUNT" in row


def test_a_new_run_goes_on_the_ledger_once_and_a_resume_does_not_add_to_it(
    planted_root: Path, tmp_path: Path
) -> None:
    part_b(planted_root, tmp_path)
    entries = json.loads((tmp_path / "ledger.json").read_text("utf-8"))
    assert len(entries) == 1 and entries[0]["run_id"].startswith("part-b-")
    part_b(planted_root, tmp_path, run_id=entries[0]["run_id"])
    assert len(json.loads((tmp_path / "ledger.json").read_text("utf-8"))) == 1


def test_above_the_memory_line_the_run_stops_after_saving_and_a_resume_finishes_it(
    planted_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads = iter([50, 95])  # fine for the first persona, too high for the second
    monkeypatch.setattr(pv, "memory_load", lambda: next(loads))
    with pytest.raises(pv.MemoryStop) as stop:
        part_b(planted_root, tmp_path)
    run_id = stop.value.run_id
    done = sorted((tmp_path / "res" / run_id / "12").glob("*.json"))
    assert len(done) == 1  # the first persona was saved before stopping
    monkeypatch.setattr(pv, "memory_load", lambda: 40)
    seen: list[str] = []
    real = pv.planted_db

    def spy(pid: str, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        seen.append(pid)
        return real(pid, *args, **kwargs)

    monkeypatch.setattr(pv, "planted_db", spy)
    text = part_b(planted_root, tmp_path, run_id=run_id)
    assert len(seen) == 1 and done[0].stem not in seen  # only the missing persona was run
    assert "Personas scored: " not in text and "2 personas" in text


def test_the_command_exits_3_with_the_run_id_when_memory_is_too_high(
    planted_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    monkeypatch.setattr(pv, "memory_load", lambda: 97)
    monkeypatch.setattr(pv, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(pv, "PLANTED", planted_root)
    code = pv.main(["part-b", "--set", "12", "--work", str(tmp_path / "w"), "--label", "x"])
    out = capsys.readouterr().out
    assert code == 3 and "STOPPED for memory" in out and "--resume part-b-" in out


def test_the_run_is_saved_as_a_report_and_a_run_record_without_data(
    planted_root: Path, tmp_path: Path
) -> None:
    part_b(planted_root, tmp_path, save_dir=tmp_path / "docs")
    md = next((tmp_path / "docs").glob("proposals_v2_part_b_*.md"))
    record = json.loads(md.with_suffix(".json").read_text("utf-8"))
    assert {"run_id", "planter_version", "seed", "code", "origins", "seconds", "kinds"} <= set(
        record
    )
    assert record["planter_version"] == 3 and "personas" in record and "groups" in record
    assert "Variant ledger" in md.read_text("utf-8")
