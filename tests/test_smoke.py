"""Smoke test: checks that the package imports and pytest is wired up."""

import lookahead


def test_package_imports() -> None:
    """The lookahead package can be imported and has a version string."""
    assert isinstance(lookahead.__version__, str)
