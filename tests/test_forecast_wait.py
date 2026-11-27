"""Forecast says what it is doing while it simulates its futures, so the page never looks blank or
broken; the message goes away once the forecast is there."""

import ast
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "app" / "pages" / "5_Forecast.py"


def _top_level_calls(source: str) -> list[str]:
    """The page's top-level statements, as text, in order."""
    return [ast.get_source_segment(source, node) or "" for node in ast.parse(source).body]


def test_the_waiting_message_is_shown_before_the_futures_and_cleared_after() -> None:
    lines = _top_level_calls(PAGE.read_text(encoding="utf-8"))
    shown = lines.index("wait.info(WAITING)")
    built = next(i for i, s in enumerate(lines) if "build_forecast(" in s and "=" in s)
    cleared = lines.index("wait.empty()")
    assert shown < built < cleared
    assert shown == built - 1 and cleared == built + 1  # nothing slow in between
    assert any(s.startswith('WAITING = f"Simulating {N_FUTURES} futures') for s in lines)
