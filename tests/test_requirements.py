"""The suite runs on the versions requirements.txt pins, and the demo server installs exactly those:
every line is an exact pin, and each installed package has its pinned version."""

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"
PIN = re.compile(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)")


def _pins() -> dict[str, str]:
    lines = [line.split("#", 1)[0].strip() for line in REQUIREMENTS.read_text().splitlines()]
    lines = [line for line in lines if line]
    assert all(PIN.fullmatch(line) for line in lines), "every requirement is an exact pin"
    return dict(line.split("==", 1) for line in lines)


def test_every_pinned_package_is_installed_at_its_pinned_version() -> None:
    wrong = {}
    for name, pinned in _pins().items():
        try:
            installed = version(name)
        except PackageNotFoundError:
            installed = "missing"
        if installed != pinned:
            wrong[name] = f"{installed} installed, {pinned} pinned"
    assert wrong == {}


def test_the_app_s_main_libraries_are_pinned() -> None:
    pins = {name.lower() for name in _pins()}
    assert {"pandas", "numpy", "streamlit", "plotly", "pyarrow"} <= pins
