"""The suite-wide guard in conftest.py: no test reads the developer's settings file."""

import ast
import os
from pathlib import Path

import pytest
from conftest import ENV_KEYS, ENV_READERS

from lookahead import config, llm_categorizer, llm_fallback, pseudonymize
from lookahead.ask import llm as ask_llm

ROOT = Path(__file__).resolve().parents[1]
CODE = ("lookahead", "app", "eval", "tools")


def test_every_module_that_imports_load_env_is_guarded_and_uses_its_name() -> None:
    importers, aliased = set(), set()
    for folder in CODE:
        for path in (ROOT / folder).rglob("*.py"):
            module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == "load_env":
                            importers.add(module)
                            if alias.asname not in (None, "load_env"):
                                aliased.add(module)
    assert importers  # the scan found the known readers
    assert importers <= set(ENV_READERS)
    assert not aliased  # the guard replaces the name load_env only


def test_a_settings_file_outside_the_temp_folders_is_never_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(self: Path, *args: object, **kwargs: object) -> str:
        raise AssertionError(f"opened {self.name}")

    # As if the file existed (a worktree has none), so only the guard can keep it closed.
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "read_text", refuse)
    for reader in (config, llm_fallback, llm_categorizer, pseudonymize, ask_llm):
        assert reader.load_env(Path(".env")) == {}
        assert reader.load_env(ROOT / ".env") == {}


def test_a_settings_file_in_the_temp_folder_is_read(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("NAME=value\n", encoding="utf-8")
    assert pseudonymize.load_env(env) == {"NAME": "value"}


def test_writing_a_key_outside_the_temp_folders_is_refused(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    # A folder that does not exist: even a broken guard could not write here.
    outside = tmp_path_factory.getbasetemp().parent / "never-created-by-tests" / ".env"
    with pytest.raises(AssertionError, match="outside its temp folder"):
        pseudonymize.init_key(outside)
    assert not outside.parent.exists()


def test_writing_a_key_in_the_temp_folder_works(tmp_path: Path) -> None:
    assert pseudonymize.init_key(tmp_path / ".env")
    assert pseudonymize.load_env(tmp_path / ".env")[pseudonymize.KEY_NAME]


def test_no_key_from_the_shell_reaches_a_test() -> None:
    assert not any(key in os.environ for key in ENV_KEYS)
    assert not llm_fallback.has_api_key()
