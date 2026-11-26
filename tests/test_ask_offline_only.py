"""Ask ships offline-only (the scored run failed its pass rule, spec docs/specs/2026-11-20-ask.md):
no page imports the LLM planner and no page offers a "Smarter questions" setting."""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def _app_files() -> list[Path]:
    """Every Python file of the app (pages and helpers)."""
    files = sorted(APP.rglob("*.py"))
    assert files, "no app files found"
    return files


def _imports(path: Path) -> set[str]:
    """Modules a file imports, as dotted names (`from a.b import c` gives a.b and a.b.c)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_no_app_file_imports_the_llm_planner() -> None:
    offenders = [p.name for p in _app_files() if "lookahead.ask.llm" in _imports(p)]
    assert offenders == []


def test_no_module_the_app_imports_loads_the_llm_planner() -> None:
    """Catches an indirect import too (a helper the page uses importing the planner)."""
    modules = sorted(
        {name for p in _app_files() for name in _imports(p) if name.startswith("lookahead.")}
    )
    script = (
        "import importlib, sys\n"
        f"for name in {modules!r}:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except ModuleNotFoundError:\n"
        "        pass  # `from module import function` also lists module.function\n"
        "print('lookahead.ask.llm' in sys.modules)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.strip().splitlines()[-1] == "False"


def test_no_app_or_package_text_offers_smarter_questions() -> None:
    """The label is absent from the pages and from the package code they show text from."""
    files = _app_files() + [p for p in (ROOT / "lookahead").rglob("*.py")]
    offenders = [
        p.name for p in files if "smarter questions" in p.read_text(encoding="utf-8").lower()
    ]
    assert offenders == []


def test_the_checks_would_catch_an_import(tmp_path: Path) -> None:
    page = tmp_path / "page.py"
    page.write_text("from lookahead.ask import llm\nimport lookahead.ask.llm\n", encoding="utf-8")
    assert "lookahead.ask.llm" in _imports(page)
