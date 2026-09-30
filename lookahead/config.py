"""Local settings from `.env` (git-ignored). Standard library only: no dotenv package."""

from pathlib import Path


def load_env(path: Path = Path(".env")) -> dict[str, str]:
    """KEY=VALUE lines from `path`; blank lines and `#` comments are skipped.

    Values may be wrapped in single or double quotes. A missing file gives {}.
    """
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values
