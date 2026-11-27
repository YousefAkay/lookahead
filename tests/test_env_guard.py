"""The suite-wide guard in conftest.py: no test reads the developer's settings file."""

import ast
import os
from pathlib import Path

import pytest
from conftest import ENV_KEYS, ENV_READERS

from lookahead import config, llm_fallback, pseudonymize
from lookahead.ask import llm as ask_llm

ROOT = Path(__file__).resolve().parents[1]


def test_every_module_that_imports_load_env_is_guarded() -> None:
    importers = set()
    for path in (ROOT / "lookahead").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(a.name == "load_env" for a in node.names):
                importers.add(".".join(path.relative_to(ROOT).with_suffix("").parts))
    assert importers  # the scan found the known readers
    assert importers <= set(ENV_READERS)


def test_a_settings_file_outside_the_temp_folders_is_never_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(self: Path, *args: object, **kwargs: object) -> str:
        raise AssertionError(f"opened {self.name}")

    monkeypatch.setattr(Path, "read_text", refuse)
    for reader in (config, llm_fallback, pseudonymize, ask_llm):
        assert reader.load_env(Path(".env")) == {}
        assert reader.load_env(ROOT / ".env") == {}


def test_a_settings_file_in_the_temp_folder_is_read(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("NAME=value\n", encoding="utf-8")
    assert pseudonymize.load_env(env) == {"NAME": "value"}


def test_no_key_from_the_shell_reaches_a_test() -> None:
    assert not any(key in os.environ for key in ENV_KEYS)
    assert not llm_fallback.has_api_key()
