"""The fresh held-out set for forecaster v2: chosen and written without anyone looking, refused by
every development-only check and by the guard. Temporary folders and made-up lists only."""

import json
from pathlib import Path

import pytest

from eval import berka_split, fresh_set
from lookahead.generator import archetypes as a
from tools.heldout_guard import decide


def test_the_fresh_personas_are_the_held_out_mix_under_new_ids() -> None:
    assert len(a.FRESH_IDS) == len(a.FRESH_LONG_IDS) == 10
    slots = sorted(pid.replace(f"{a.FRESH_MIX}-", f"{a.MIX}-", 1) for pid in a.FRESH_IDS)
    assert slots == sorted(a.HELD_OUT)  # one or two of each of the 8 archetypes
    assert {pid.split("-")[1] for pid in a.FRESH_IDS} == set(a.ARCHETYPES)
    assert a.FRESH_PARAM_SEED != a.PARAM_SEED


def test_every_development_only_check_refuses_them() -> None:
    for pid in (a.FRESH_IDS[0], a.FRESH_LONG_IDS[-1]):
        with pytest.raises(ValueError, match="held-out"):
            a.assert_development_only([pid])
        with pytest.raises(ValueError):
            a.build_persona(pid)  # only build_fresh makes them
        with pytest.raises(ValueError):
            a.main(["--only", pid, "--out", "/nonexistent"])  # the generator's command refuses
    assert not set(a.FRESH_IDS) & set(a.development_ids())


def _ids_in(persona: a.Persona) -> list[str]:
    """Every id a persona's sources carry: series ids and link ids."""
    out = [str(getattr(s, "series_id", "")) for s in persona.sources]
    for s in persona.sources:
        for item in getattr(s, "items", ()):
            out.append(str(getattr(item, "link_id", "") or ""))
    return [i for i in out if i]


@pytest.mark.parametrize("pid", [a.FRESH_IDS[0], a.FRESH_LONG_IDS[0], a.FRESH_IDS[4],
                                 a.FRESH_LONG_IDS[4]])  # fmt: skip
def test_a_fresh_persona_always_the_same_and_carries_only_its_own_id(pid: str) -> None:
    one, two = a.build_fresh(pid), a.build_fresh(pid)
    assert one == two and one.held_out and one.persona.persona_id == pid
    assert one.archetype == pid.split("-")[1]
    assert one.buffer_cents == a.ARCHETYPES[one.archetype].buffer_cents
    assert one.start == (a.LONG_START if a.is_long(pid) else a.START)
    ids = _ids_in(one.persona)
    assert ids and all(i.startswith(pid + "-") or i.startswith(pid) for i in ids), ids[:3]
    assert not any(i.startswith(("v2-", "v2L-")) for i in ids)  # nothing of the old slot's id
    with pytest.raises(ValueError):
        a.build_fresh(a.PERSONA_IDS[0])


def test_the_fresh_24_month_personas_count_as_24_month() -> None:
    assert all(a.is_long(pid) for pid in a.FRESH_LONG_IDS)
    assert not any(a.is_long(pid) for pid in a.FRESH_IDS)
    assert [a.base_id(pid) for pid in a.FRESH_LONG_IDS] == list(a.FRESH_IDS)


DEVELOPMENT_DIGEST = "9ef910667f0ed6f656fd6ddd2ab85758b511b1f6c9e6e258e39e1cd2741ecc97"


@pytest.mark.slow
def test_the_40_development_personas_are_unchanged(tmp_path: Path) -> None:
    """The generator change for the fresh set leaves every development persona byte-identical
    (the digest was taken before the change)."""
    import hashlib

    from lookahead.generator.writer import write_persona

    parts = []
    for pid in a.development_ids() + a.development_ids(long=True):
        p = a.build_persona(pid)
        export, key = write_persona(p.persona, tmp_path, 42, p.start, a.END)
        parts.append(hashlib.sha256(export.read_bytes() + key.read_bytes()).hexdigest())
    assert len(parts) == 40
    assert hashlib.sha256("".join(parts).encode()).hexdigest() == DEVELOPMENT_DIGEST


def test_the_berka_choice_skips_every_used_account_and_follows_the_new_salt() -> None:
    passing = list(range(1, 1001))
    used = set(range(1, 401))
    chosen = fresh_set.choose(passing, used, n=50)
    assert len(chosen) == 50 and not set(chosen) & used
    expected = sorted(sorted(range(401, 1001), key=fresh_set.digest)[:50])
    assert chosen == expected
    assert fresh_set.choose(passing, used, n=50) == chosen  # the same every time


def test_the_berka_development_check_refuses_the_fresh_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lists = tmp_path / "fresh.json"
    lists.write_text(json.dumps({"berka": {"chosen": [123456, 654321]}}))
    monkeypatch.setattr(fresh_set, "LIST_FILE", lists)
    with pytest.raises(ValueError, match="fresh held-out"):
        berka_split.assert_development_only([berka_split.development_accounts()[0], 654321])
    berka_split.assert_development_only(berka_split.development_accounts()[:3])  # still fine


@pytest.mark.parametrize("kind, text", [
    ("read", "data/forecast_lockbox/12/persona_v3-SAL-04.csv"),
    ("command", "cat data/forecast_lockbox/long/x.csv"),
    ("command", "ls data/Forecast-Lock_Box"),
    ("search", "data/forecast_lockbox"),
])  # fmt: skip
def test_the_guard_refuses_the_fresh_folder_in_any_spelling(kind: str, text: str) -> None:
    assert not decide(kind, text, None).allowed


def test_the_guard_lets_the_module_print_counts_and_fingerprints() -> None:
    assert decide("command", ".venv/bin/python -m eval.fresh_set fingerprint", None).allowed
    assert decide("read", "eval/fresh_set.py", None).allowed
