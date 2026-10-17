"""The Berka adapter and its checks (eval/berka.py), on a made-up account in Berka's format."""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from eval import berka
from eval.berka import (
    account_rows,
    describe,
    dip_line_cents,
    leakage,
    origin_dates,
    run_account,
    to_cents,
)
from eval.berka_split import held_out_accounts
from lookahead.ingest.scotiabank import balance_chain_breaks

START = date(1994, 1, 3)


def made_up_account(days: int = 800, account_id: int = 99001, seed: int = 4) -> pd.DataFrame:
    """A pension each month, a household payment, a monthly fee and interest, cash withdrawals."""
    rng = np.random.default_rng(seed)
    rows: list[dict] = []

    def add(day: date, signed: float, operation: str | None, code: str | None, kind: str) -> None:
        rows.append({
            "account_id": account_id, "day": pd.Timestamp(day), "type": kind,
            "operation": operation, "k_symbol": code, "amount": abs(signed), "signed": signed,
            "bank": "AB" if operation in ("PREVOD Z UCTU", "PREVOD NA UCET") else None,
            "account": 1234.0 if operation in ("PREVOD Z UCTU", "PREVOD NA UCET") else np.nan,
        })  # fmt: skip

    add(START, 30000.0, "VKLAD", None, "PRIJEM")
    for k in range(days):
        day = START + timedelta(days=k + 1)
        if day.day == 11:
            add(day, 6207.0, "PREVOD Z UCTU", "DUCHOD", "PRIJEM")
        if day.day == 13:
            add(day, -3662.0, "PREVOD NA UCET", "SIPO", "VYDAJ")
        if day.day == 28:
            add(day, 22.5, None, "UROK", "PRIJEM")
            add(day, -14.6, "VYBER", "SLUZBY", "VYDAJ")
        if k % 6 == 0:
            add(day, -float(rng.integers(5, 40) * 100), "VYBER", None, "VYDAJ")
    frame = pd.DataFrame(rows).sort_values("day", kind="stable").reset_index(drop=True)
    frame["balance"] = frame["signed"].cumsum()
    frame["trans_id"] = range(1, len(frame) + 1)
    return frame


def test_descriptions_follow_the_codes() -> None:
    f = made_up_account(60)

    def key(r: pd.Series) -> tuple[str | None, str | None]:
        return (r["operation"] if isinstance(r["operation"], str) else None,
                r["k_symbol"] if isinstance(r["k_symbol"], str) else None)  # fmt: skip

    by_code = {
        key(r): describe(r) for _, r in f.drop_duplicates(["operation", "k_symbol"]).iterrows()
    }
    assert by_code[("VKLAD", None)] == ("deposit", "BERKA CASH DEPOSIT")
    assert by_code[("PREVOD Z UCTU", "DUCHOD")] == ("credit memo", "BERKA IN DUCHOD AB 1234")
    assert by_code[("PREVOD NA UCET", "SIPO")] == ("bill payment", "BERKA OUT SIPO AB 1234")
    assert by_code[(None, "UROK")] == ("credit memo", "BERKA INTEREST")
    assert by_code[("VYBER", "SLUZBY")] == ("service charge", "BERKA FEE")
    assert by_code[("VYBER", None)] == ("abm withdrawal", "BERKA CASH")


def test_koruna_become_cents_and_small_rows_stay() -> None:
    assert to_cents(14.6) == 73 and to_cents(1000.0) == 5000
    assert to_cents(0.1) == 1  # never rounded away to a zero row
    assert to_cents(0.0) == 0


def test_account_rows_are_a_consistent_export_newest_first() -> None:
    rows = account_rows(made_up_account(200))
    assert balance_chain_breaks(rows) == []  # each balance = the older one + the amount
    assert rows[0].posted_date >= rows[-1].posted_date and rows[0].position == 1
    assert rows[-1].balance_cents == rows[-1].amount_cents == 30000 * berka.CENTS_PER_KORUNA


def test_origins_are_forty_weekly_dates_ending_thirty_days_before_the_last_posting() -> None:
    f = made_up_account(500)
    dates = origin_dates(f)
    assert len(dates) == 40 and dates == sorted(dates)
    assert {(b - a).days for a, b in zip(dates, dates[1:], strict=False)} == {7}
    assert dates[-1] == f["day"].max().date() - timedelta(days=30)


def test_the_dip_line_reads_only_the_year_before_the_first_origin() -> None:
    f = made_up_account(800)
    first = origin_dates(f)[0]
    line = dip_line_cents(f, first)
    assert line > 0
    changed = f.copy()
    after = changed["day"] > pd.Timestamp(first)
    changed.loc[after & (changed["signed"] < 0), "amount"] *= 100  # a much bigger future
    assert dip_line_cents(changed, first) == line  # rows after the first origin are not read
    assert dip_line_cents(f, first, 0.5) == pytest.approx(line / 2, abs=1)


def test_a_forecast_does_not_depend_on_rows_after_its_origin(tmp_path: Path) -> None:
    f = made_up_account(800)
    dates = origin_dates(f)
    for origin in (dates[0], dates[20], dates[-1]):
        assert leakage(f, 99001, origin, tmp_path)  # full database == database cut at the origin


def test_a_held_out_account_cannot_be_run(tmp_path: Path) -> None:
    held = held_out_accounts("primary")[0]
    with pytest.raises(ValueError):
        run_account((held, made_up_account(100, held), tmp_path))
