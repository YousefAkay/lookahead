"""Tests for pseudonym tokens, the secret key, and the init-key command."""

import re
from pathlib import Path

import pytest

from lookahead.config import load_env
from lookahead.pseudonymize import PseudonymKeyError, main, pseudonym_key, token

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))
HEX_KEY = KEY.hex()


# --- Tokens ---


def test_token_is_stable_and_readable() -> None:
    a = token("PERSON", "Nadia Haddad", KEY)
    assert a == token("PERSON", "Nadia Haddad", KEY)
    assert re.fullmatch(r"PERSON_[0-9a-f]{12}", a)


def test_token_ignores_case_and_spacing() -> None:
    assert token("PERSON", "Nadia Haddad", KEY) == token("PERSON", "  NADIA   haddad ", KEY)


def test_different_key_or_kind_gives_a_different_token() -> None:
    base = token("PERSON", "Nadia Haddad", KEY)
    assert token("PERSON", "Nadia Haddad", OTHER_KEY) != base
    assert token("REF", "Nadia Haddad", KEY).removeprefix("REF_") != base.removeprefix("PERSON_")


def test_different_values_give_different_tokens() -> None:
    assert token("REF", "12345678", KEY) != token("REF", "12345679", KEY)


def test_token_never_contains_the_value() -> None:
    for value in ("Nadia Haddad", "12345678", "a"):
        assert value.lower() not in token("PERSON", value, KEY).lower().removeprefix("person_")


@pytest.mark.parametrize("kind", ["person", "REF1", "", "A B"])
def test_kind_must_be_capital_letters(kind: str) -> None:
    with pytest.raises(ValueError):
        token(kind, "x", KEY)


# --- .env reading ---


def test_load_env_parses_simple_lines(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n\nPSEUDONYM_KEY=abc\n"
        "QUOTED=\"hello world\"\nSINGLE='x'\n  SPACED = y \nEQ=a=b\n",
        encoding="utf-8",
    )
    assert load_env(env) == {
        "PSEUDONYM_KEY": "abc",
        "QUOTED": "hello world",
        "SINGLE": "x",
        "SPACED": "y",
        "EQ": "a=b",
    }


def test_missing_env_file_is_empty(tmp_path: Path) -> None:
    assert load_env(tmp_path / "nope.env") == {}


# --- The key ---


def write_env(tmp_path: Path, text: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(text, encoding="utf-8")
    return env


def test_key_is_read_from_env_file(tmp_path: Path) -> None:
    env = write_env(tmp_path, f"PSEUDONYM_KEY={HEX_KEY}\n")
    assert pseudonym_key(env, environ={}) == KEY


def test_environment_variable_wins_over_the_file(tmp_path: Path) -> None:
    env = write_env(tmp_path, f"PSEUDONYM_KEY={OTHER_KEY.hex()}\n")
    assert pseudonym_key(env, environ={"PSEUDONYM_KEY": HEX_KEY}) == KEY


def test_missing_key_explains_how_to_create_one(tmp_path: Path) -> None:
    with pytest.raises(PseudonymKeyError, match="init-key"):
        pseudonym_key(write_env(tmp_path, "PSEUDONYM_KEY=\n"), environ={})


@pytest.mark.parametrize("bad", ["abc", "zz" * 32, "ab" * 16])
def test_bad_key_is_rejected_without_revealing_it(tmp_path: Path, bad: str) -> None:
    with pytest.raises(PseudonymKeyError) as err:
        pseudonym_key(write_env(tmp_path, f"PSEUDONYM_KEY={bad}\n"), environ={})
    assert bad not in str(err.value)


# --- init-key command ---


def test_init_key_creates_env_and_never_prints_the_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = tmp_path / ".env"
    assert main(["init-key", "--env", str(env)]) == 0
    key = pseudonym_key(env, environ={})
    assert len(key) == 32
    assert key.hex() not in capsys.readouterr().out


def test_init_key_keeps_other_lines(tmp_path: Path) -> None:
    env = write_env(tmp_path, "OTHER=1\nPSEUDONYM_KEY=\n")
    assert main(["init-key", "--env", str(env)]) == 0
    values = load_env(env)
    assert values["OTHER"] == "1" and len(bytes.fromhex(values["PSEUDONYM_KEY"])) == 32


def test_init_key_refuses_to_replace_an_existing_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = write_env(tmp_path, f"PSEUDONYM_KEY={HEX_KEY}\n")
    assert main(["init-key", "--env", str(env)]) == 1
    assert pseudonym_key(env, environ={}) == KEY  # unchanged
    assert "already" in capsys.readouterr().out
