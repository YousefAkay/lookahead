"""The real-account commands of the 2026-10-11 plan, tested only on synthetic databases: they
must leave the database untouched and print counts, never rows, names, amounts or tokens."""

import hashlib
import re
from datetime import datetime
from pathlib import Path

import pytest

from eval.backtest_real import main as backtest_real_main
from eval.backtest_real import real_arm_options
from eval.proposals_real import main
from lookahead.db import connect
from lookahead.generator.archetypes import END, START, build_persona
from lookahead.generator.writer import write_persona
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports

ACCOUNT = "my-private-chequing"  # a name that must never be printed
KEY_HEX = "11" * 32  # a made-up pseudonym key


@pytest.fixture(scope="module")
def made_up_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("real")
    export, _ = write_persona(build_persona("v2-ITIGHT-01").persona, folder, 42, START, END)
    conn = connect(folder / "made_up.db")
    store_exports(conn, ACCOUNT, [(export.name, parse_export(export))], datetime(2026, 10, 1))
    conn.close()
    return folder / "made_up.db"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.slow
def test_the_proposal_check_prints_counts_only_and_leaves_the_database_alone(
    made_up_db: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PSEUDONYM_KEY", KEY_HEX)
    before = digest(made_up_db)
    assert main(["--db", str(made_up_db)]) == 0
    out = capsys.readouterr().out
    assert digest(made_up_db) == before
    assert "account-1" in out and ACCOUNT not in out
    assert "PERSON_" not in out and "$" not in out and not re.search(r"\d{4}-\d{2}-\d{2}", out)
    assert "followed by a deposit from the same sender" in out
    proposals = int(re.search(r"proposals \(origin, proposal\): (\d+)", out).group(1))  # type: ignore[union-attr]
    assert proposals > 0  # the stretched family's transfers are proposed at some origins


def test_without_a_pseudonym_key_it_says_so_and_stops(
    made_up_db: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:  # fmt: skip
    monkeypatch.delenv("PSEUDONYM_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    assert main(["--db", str(made_up_db)]) == 1
    assert "No pseudonym key" in capsys.readouterr().out


def test_real_arms_are_0_1_and_2_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSEUDONYM_KEY", KEY_HEX)
    assert real_arm_options(1).lump_arrivals and not real_arm_options(1).lump_proposals
    two = real_arm_options(2)
    assert two.confirm_all_lumps and two.proposal_key == bytes.fromhex(KEY_HEX)
    with pytest.raises(ValueError):
        real_arm_options(3)


@pytest.mark.slow
def test_a_real_arm_saves_to_its_own_folder(
    made_up_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    monkeypatch.chdir(tmp_path)
    copy = tmp_path / "data" / "private" / "db.db"  # private files only
    copy.parent.mkdir(parents=True)
    copy.write_bytes(made_up_db.read_bytes())
    assert (
        backtest_real_main(["--db", str(copy), "--quick", "--arm", "1", "--keeps-coming", "off"])
        == 0
    )
    saved = list((tmp_path / "data/private/backtest/arms/arm1/quick").glob("*.jsonl"))
    assert len(saved) == 1 and re.fullmatch(r"account-[0-9a-f]{10}\.jsonl", saved[0].name)
    assert ACCOUNT not in capsys.readouterr().out
