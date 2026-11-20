"""The forecast and safe-to-spend, byte for byte, as they were before the budgets (reviewer N7).

The budgets moved the futures' week draws into one shared function and gave the forecast a few
read-only extras; the output must not move. EXPECTED is the SHA-256 of `dump()` computed on main at
67475ca, the commit before that change (the same function, run against that commit's code), on the
synthetic demo personas A, B and C (never held-out) at five data dates each, with the app's 500
futures. A later change to the forecaster that is meant to move the output updates EXPECTED in the
same commit and says why.
"""

import hashlib
import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest

SYNTHETIC = Path(__file__).resolve().parents[1] / "data" / "synthetic"
AS_OF = (date(2026, 6, 30), date(2026, 7, 31), date(2026, 8, 14), date(2026, 9, 1), None)
EXPECTED = "1a2f6cf0e917abd99852d094779b3e1a9656ea960dcc9a499832fdf10c03c821"


def dump() -> str:
    """Every forecast day, the whole simulation and safe-to-spend, as text."""
    from lookahead.db import connect
    from lookahead.forecast.path import build_forecast
    from lookahead.ingest.scotiabank import parse_export
    from lookahead.ingest.store import store_exports
    from lookahead.safe_to_spend import safe_to_spend_from

    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(Path(tmp) / "golden.db")
        for name in "ABC":
            export = SYNTHETIC / f"persona_{name}.csv"
            store_exports(conn, f"synthetic-{name}", [(export.name, parse_export(export))],
                          datetime(2026, 10, 1))  # fmt: skip
        out = []
        for name in "ABC":
            for as_of in AS_OF:
                f = build_forecast(conn, f"synthetic-{name}", "weekday", as_of, None)
                assert f is not None
                days = [(d.day, d.balance_cents, d.spending_cents) for d in f.days]
                out.append(repr((name, as_of, days, f.simulation)))
                out.append(repr(safe_to_spend_from(f)))
        conn.close()
    return "\n".join(out)


def digest() -> str:
    return hashlib.sha256(dump().encode()).hexdigest()


@pytest.mark.slow
def test_the_forecast_and_safe_to_spend_are_unchanged_byte_for_byte() -> None:
    assert digest() == EXPECTED
