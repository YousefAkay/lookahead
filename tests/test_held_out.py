"""The held-out harness: the route lockdown, readiness and the ledger, the per-unit rules, the dry
run's comparison and the void checks. Hand-made records only: no test opens held-out data."""

import json
import subprocess
from pathlib import Path

import pytest

from eval import held_out as h
from eval import metrics
from eval.berka_scores import CORE_ROWS, summary_core
from eval.berka_split import held_out_accounts

# --- The only route ---


def test_held_out_berka_lists_are_refused_outside_the_harness() -> None:
    with pytest.raises(ValueError, match="eval/held_out.py"):
        held_out_accounts("primary")
    with pytest.raises(ValueError, match="eval/held_out.py"):
        held_out_accounts("sensitivity")


def test_the_metrics_final_test_flag_is_gone() -> None:
    with pytest.raises(SystemExit):
        metrics.main(["--final-test"])


def test_held_out_ids_refuse_without_an_open_attempt_and_sizes_are_the_protocols() -> None:
    assert h.EXPECTED_UNITS == {"12-month": 10, "24-month": 10, "berka-primary": 200,
                                "berka-sensitivity": 200}  # fmt: skip
    dev = {p: len(h.pool_ids(p, False)) for p in h.POOLS}
    assert dev == {"12-month": 20, "24-month": 20, "berka-primary": 200,
                   "berka-sensitivity": 200}  # fmt: skip
    for p in h.POOLS:
        with pytest.raises(ValueError, match="dry run passed"):
            h.pool_ids(p, True)
    with pytest.raises(ValueError, match="dry run passed"):
        h.compute(True, Path("nowhere"), 1)
    with pytest.raises(ValueError, match="dry run passed"):
        h._synthetic_job("v2-SAL-04")  # refused before anything is generated
    with pytest.raises(ValueError, match="dry run passed"):
        h._synthetic_job("v2-SAL-04", ("/tmp", "made-up"))


def test_an_attempt_is_open_only_between_its_dry_run_and_its_final_entry(tmp_path: Path) -> None:
    attempt = (str(tmp_path), "a1")
    with pytest.raises(ValueError):
        h.check_open(attempt)  # no ledger
    h.append(tmp_path, {"attempt": "a1", "status": h.STARTED})
    with pytest.raises(ValueError):
        h.check_open(attempt)  # dry run not passed yet
    h.append(tmp_path, {"attempt": "a1", "status": h.DRY_RUN_PASSED})
    h.check_open(attempt)
    with pytest.raises(ValueError):
        h.check_open((str(tmp_path), "another"))
    h.append(tmp_path, {"attempt": "a1", "status": "void"})
    with pytest.raises(ValueError):
        h.check_open(attempt)  # closed


# --- Readiness and the ledger (in a throw-away git repository) ---


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    *args], check=True, capture_output=True)  # fmt: skip


def _repo(tmp_path: Path, title: str = "# Protocol") -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / h.PROTOCOL).parent.mkdir(parents=True)
    (tmp_path / h.PROTOCOL).write_text(f"{title}\n\nbody\n", encoding="utf-8")
    (tmp_path / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "start")
    return tmp_path


def test_a_clean_repository_is_ready(tmp_path: Path) -> None:
    assert h.refusals(_repo(tmp_path)) == []


def test_a_draft_protocol_is_refused(tmp_path: Path) -> None:
    assert any("DRAFT" in r for r in h.refusals(_repo(tmp_path, "# Protocol — DRAFT")))


def test_uncommitted_code_or_protocol_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "code.py").write_text("x = 2\n", encoding="utf-8")
    assert any("uncommitted" in r for r in h.refusals(root))
    (root / h.PROTOCOL).write_text("# Protocol\n\nchanged\n", encoding="utf-8")
    assert any("protocol has changed" in r for r in h.refusals(root))


def test_an_uncommitted_ledger_is_refused_and_a_refusal_is_written(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    h.append(root, {"status": "refused"})
    assert any("ledger is not committed" in r for r in h.refusals(root))
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "ledger")
    assert h.refusals(root) == []


def test_a_completed_run_in_the_ledger_history_refuses_forever(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    h.append(root, {"status": "completed"})
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "done")
    (root / h.LEDGER).unlink()  # deleting the ledger does not reset it
    _git(root, "commit", "-q", "-am", "gone")
    assert any("completed run" in r for r in h.refusals(root))


def test_another_threshold_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(h, "PROBABILITY_THRESHOLD", 0.2)
    assert any("threshold" in r for r in h.refusals(_repo(tmp_path)))


def test_the_ledger_entry_holds_commits_protocol_hash_and_threshold(tmp_path: Path) -> None:
    ctx = h.context(_repo(tmp_path))
    assert len(ctx["code_commit"]) == 40 and ctx["protocol_commit"] == ctx["code_commit"]
    assert len(ctx["protocol_sha256"]) == 64 and ctx["threshold"] == 0.35


# --- Hand-made records ---


def _record(persona: str, origin: str, *, dipped: bool, chance: float = 0.5,
            archetype: str = "A") -> dict:  # fmt: skip
    return {
        "persona": persona, "archetype": archetype, "origin": origin, "futures": h.FUTURES,
        "logic": h.FORECASTER_LOGIC, "in_shortfall_at_origin": False,
        "realized": [10, 20], "p10": [0, 0], "p50": [10, 10], "p90": [30, 30],
        "start_balance_cents": 1000, "crps_forecast": [100.0, 100.0],
        "crps_stays": [200.0, 200.0], "crps_walk": [400.0, 400.0],
        "shortfall_probability": chance, "median_lowest_cents": 500,
        "realized_lowest_cents": 300,
        "realized_first_below_buffer": "2020-01-10" if dipped else None,
        "walk": {"shortfall_probability": 0.5},
    }  # fmt: skip


def test_per_unit_follows_the_inclusion_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    a = [_record("1", f"2020-01-{d:02d}", dipped=True, archetype="A") for d in range(1, 7)]
    b = [_record("2", f"2020-01-{d:02d}", dipped=d == 1, archetype="B") for d in range(1, 7)]
    metrics_by_unit = {  # unit B: no episode and no alert
        "A": {"caught_on": 3, "caught": 1, "shortfall_episodes": 4, "alert_episodes": 2,
              "precision": 0.5, "false_per_30_days": 0.2},
        "B": {"caught_on": 0, "caught": 0, "shortfall_episodes": 0, "alert_episodes": 0,
              "precision": None, "false_per_30_days": 0.0},
    }  # fmt: skip
    monkeypatch.setattr(h, "dips", lambda g: {t: metrics_by_unit[g[0]["archetype"]]
                                              for t in ("f", "r")})  # fmt: skip
    out = h.per_unit(a + b, h.unit_of("12-month"))
    assert out["units"] == 2 and out["units_scored"] == 2
    assert out["skill_n"] == {"value": pytest.approx(0.5), "units": 2}
    assert out["brier_skill_n"]["units"] == 1  # only A has 5 or more dips
    assert out["recall_on_f"] == {"value": 0.75, "units": 1}  # units with an episode
    assert out["recall_f"] == {"value": 0.25, "units": 1}
    assert out["precision_f"] == {"value": 0.5, "units": 1}  # units with an alert
    assert out["false30_f"] == {"value": pytest.approx(0.1), "units": 2}  # all units


def test_berka_units_are_accounts_and_synthetic_units_are_archetypes() -> None:
    r = _record("123", "2020-01-01", dipped=False, archetype="berka")
    assert h.unit_of("berka-primary")(r) == "123" and h.unit_of("24-month")(r) == "berka"


# --- The dry run's comparison ---


def _report(summaries: list[dict]) -> str:
    lines = ["| | a | b | c | zero |", "|---|---|---|---|---|"]
    for label, fn in CORE_ROWS:
        lines.append(f"| {label} | " + " | ".join(fn(s) for s in summaries) + " | x |")
    return "\n".join(lines)


def test_the_dry_run_matches_its_own_formatting_and_catches_one_changed_cell() -> None:
    s = summary_core([_record("1", "2020-01-01", dipped=True),
                      _record("2", "2020-01-01", dipped=False)])  # fmt: skip
    pools = dict.fromkeys(h.DRY_RUN_COLUMNS, s)
    text = _report([s, s, s])
    assert h.dry_run_differences(pools, text) == []
    changed = text.replace("| dips among them | 1 |", "| dips among them | 2 |")
    diffs = h.dry_run_differences(pools, changed)
    assert len(diffs) == 1 and "12-month | dips among them" in diffs[0]
    assert len(h.dry_run_differences(pools, "")) == len(CORE_ROWS) * 3  # missing rows differ


# --- The void checks ---


def _pool(units: int) -> list[dict]:
    return [_record(str(u), f"2020-{1 + o // 28:02d}-{1 + o % 28:02d}", dipped=False)
            for u in range(units) for o in range(h.ORIGINS)]  # fmt: skip


def test_a_complete_pool_is_not_void() -> None:
    assert h.void_problems({"p": _pool(2)}, {"p": 2}) == []


def test_missing_units_origins_futures_or_nan_make_it_void() -> None:
    assert h.void_problems({"p": _pool(1)}, {"p": 2})  # a unit missing
    short = _pool(2)[:-1]
    assert any("origins" in x for x in h.void_problems({"p": short}, {"p": 2}))
    few = _pool(2)
    few[0] = {**few[0], "futures": 499}
    assert any("futures" in x for x in h.void_problems({"p": few}, {"p": 2}))
    nan = _pool(2)
    nan[3] = {**nan[3], "crps_forecast": [float("nan"), 1.0]}
    problems = h.void_problems({"p": nan}, {"p": 2})
    assert problems == ["p: 1 origins with a NaN or infinite score"]
    old = _pool(2)
    old[0] = {**old[0], "logic": 2}
    assert any("logic" in x for x in h.void_problems({"p": old}, {"p": 2}))


def test_the_ledger_is_one_json_line_per_attempt(tmp_path: Path) -> None:
    h.append(tmp_path, {"status": "refused", "reasons": ["x"]})
    h.append(tmp_path, {"status": "void"})
    lines = (tmp_path / h.LEDGER).read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["status"] for x in lines] == ["refused", "void"]


# --- The result file comes from the ledger alone ---


def test_the_result_file_is_rebuilt_from_the_ledger_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    caught = {"caught_on": 3, "caught": 1, "shortfall_episodes": 4, "alert_episodes": 3,
              "false_alerts": 1, "precision": 2 / 3, "false_per_30_days": 0.0123,
              "ordinary_origins": 9}  # fmt: skip
    none = {**caught, "alert_episodes": 0, "false_alerts": 0, "precision": None}
    monkeypatch.setattr(h, "dips", lambda g: {"f": caught, "r": none})

    def pool(prefix: str) -> list[dict]:
        return [_record(f"{prefix}{u}", f"2020-01-{d:02d}", dipped=d % 3 == 0,
                        chance=0.1 * (d % 10), archetype=f"A{u % 2}")
                for u in range(3) for d in range(1, 10)]  # fmt: skip

    ctx = {"time": "2026-11-20T03:00:00+00:00", "code_commit": "c" * 40,
           "protocol_commit": "p" * 40, "protocol_sha256": "s" * 64, "threshold": 0.35}  # fmt: skip
    dev = {p: h.results(p, pool("d")) for p in h.POOLS}
    held = {p: h.results(p, pool("h")) for p in h.POOLS}
    entry = {**ctx, "status": "completed", "development": dev, "held_out": held}
    h.append(tmp_path, {"status": "refused"})
    h.append(tmp_path, entry)
    (tmp_path / "docs/results").mkdir(parents=True, exist_ok=True)
    direct = h.write_result(tmp_path, json.loads(json.dumps(entry, default=float)))
    first = direct.read_bytes()
    direct.unlink()
    assert h.rebuild(tmp_path) == 0
    assert direct.read_bytes() == first and direct.name == "held_out_2026-11-20.md"
    assert "| dips, forecast at 35%: alert on" in first.decode("utf-8")


def test_rebuild_refuses_without_exactly_one_completed_entry(tmp_path: Path) -> None:
    h.append(tmp_path, {"status": "void"})
    with pytest.raises(SystemExit, match="0 completed"):
        h.rebuild(tmp_path)


# --- The command: order of ledger entries, prints and held-out access ---


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """main() in a throw-away repository, with compute and the scores replaced by stand-ins.
    Returns (events, call): events lists ledger statuses, prints and compute calls in order."""
    root = _repo(tmp_path)
    (root / h.DEV_REPORT).parent.mkdir(parents=True, exist_ok=True)
    (root / h.DEV_REPORT).write_text("report\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "report")
    monkeypatch.chdir(root)
    events: list[str] = []
    real_append = h.append

    def append(r: Path, entry: dict) -> None:  # type: ignore[type-arg]
        events.append(f"ledger {entry['status']}")
        real_append(r, entry)

    def fake_print(*args: object, **kwargs: object) -> None:
        events.append("print " + " ".join(str(a) for a in args)[:40])

    held_records = {p: [{"persona": "x"}] for p in h.POOLS}

    def compute(held: bool, berka: Path, jobs: int, attempt=None):  # type: ignore[no-untyped-def]
        events.append(f"compute held={held}")
        if held:
            h.check_open(attempt)  # the real check passes inside main's attempt
        return held_records

    monkeypatch.setattr(h, "append", append)
    monkeypatch.setattr(h, "print", fake_print, raising=False)
    monkeypatch.setattr(h, "compute", compute)
    monkeypatch.setattr(h, "save", lambda folder, pools: events.append(f"save {folder.name}"))
    monkeypatch.setattr(h, "summary_core", lambda r: {})
    monkeypatch.setattr(h, "dry_run_differences", lambda s, t: [])
    monkeypatch.setattr(h, "void_problems", lambda pools, expected: [])
    monkeypatch.setattr(h, "results", lambda p, r: {"n": 1})
    monkeypatch.setattr(h, "result_text", lambda e: "RESULT")

    def call() -> int:
        return h.main(["--berka", "berka"])

    return events, call, monkeypatch, root


def _ledger(root: Path) -> list[dict]:  # type: ignore[type-arg]
    return [json.loads(x) for x in (root / h.LEDGER).read_text(encoding="utf-8").splitlines()]


def test_a_completed_run_writes_the_ledger_before_any_number_and_saves_held_out_last(
    run,  # type: ignore[no-untyped-def]
) -> None:
    events, call, _, root = run
    assert call() == 0
    order = [e for e in events if not e.startswith("print  ") and "pools" not in e]
    i = events.index("ledger completed")
    assert events.index("ledger started") < events.index("compute held=False")
    assert events.index("ledger dry run passed") < events.index("compute held=True")
    assert events.index("save held-out") > i
    assert not [e for e in events[:i] if e.startswith("print RESULT")]
    assert any(e.startswith("print RESULT") for e in events[i:]), order
    entries = _ledger(root)
    assert [e["status"] for e in entries] == ["started", "dry run passed", "completed"]
    assert len({e["attempt"] for e in entries}) == 1


def test_a_dry_run_difference_stops_before_held_out_data(run) -> None:  # type: ignore[no-untyped-def]
    events, call, monkeypatch, root = run
    monkeypatch.setattr(h, "dry_run_differences", lambda s, t: ["12-month | dips: a, b"])
    assert call() == 3
    assert "compute held=True" not in events
    assert [e["status"] for e in _ledger(root)] == ["started", "stopped: the dry run differs"]


def test_a_void_run_records_void_and_prints_no_number(run) -> None:  # type: ignore[no-untyped-def]
    events, call, monkeypatch, root = run
    monkeypatch.setattr(h, "void_problems", lambda pools, expected: ["12-month: 9 units"])
    results_called = []
    monkeypatch.setattr(h, "results", lambda p, r: results_called.append(p) or {"n": 1})
    assert call() == 4
    assert results_called == list(h.POOLS)  # development only, never the held-out pools
    assert [e["status"] for e in _ledger(root)] == ["started", "dry run passed", "void"]
    assert "save held-out" not in events and not any("RESULT" in e for e in events)


def test_a_crash_is_recorded_and_held_out_records_are_not_saved(run) -> None:  # type: ignore[no-untyped-def]
    events, call, monkeypatch, root = run

    def boom(pools, expected):  # type: ignore[no-untyped-def]
        raise RuntimeError("worker died")

    monkeypatch.setattr(h, "void_problems", boom)
    with pytest.raises(RuntimeError):
        call()
    statuses = [e["status"] for e in _ledger(root)]
    assert statuses == ["started", "dry run passed", "crashed"]
    assert "save held-out" not in events


def test_a_refusal_is_recorded_and_nothing_runs(run) -> None:  # type: ignore[no-untyped-def]
    events, call, monkeypatch, root = run
    monkeypatch.setattr(h, "PROBABILITY_THRESHOLD", 0.2)
    assert call() == 2
    assert not [e for e in events if e.startswith("compute")]
    assert [e["status"] for e in _ledger(root)] == ["refused"]


def test_a_started_entry_left_by_a_hard_kill_blocks_the_next_attempt(run) -> None:  # type: ignore[no-untyped-def]
    _, call, _, root = run
    h.append(root, {"attempt": "killed", "status": h.STARTED})  # as a kill would leave it
    assert call() == 2
    assert _ledger(root)[-1]["status"] == "refused"
