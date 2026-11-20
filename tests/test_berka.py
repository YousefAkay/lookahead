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
from eval.berka_split import load_split
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


def test_the_opening_balance_ignores_the_order_of_the_first_days_rows() -> None:
    # two rows on the first day, the larger one second in the file: 1,100 opens the account and
    # 3,871 comes in after it (balance 4,971). Taking the larger row first gave an opening of 1,100.
    day = pd.Timestamp(START)
    base = {"account_id": 1, "day": day, "type": "PRIJEM", "operation": "VKLAD"}
    base |= {"k_symbol": None, "bank": None, "account": np.nan}
    frame = pd.DataFrame(
        [
            {**base, "amount": 1100.0, "signed": 1100.0, "balance": 1100.0},
            {**base, "amount": 3871.0, "signed": 3871.0, "balance": 4971.0},
        ]
    )
    assert berka.opening_koruna(frame) == 0.0
    rows = account_rows(frame)
    assert balance_chain_breaks(rows) == []
    assert rows[0].balance_cents == 4971 * berka.CENTS_PER_KORUNA  # the day closes as the file does


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
    held = int(load_split()["primary_held_out"][0])  # type: ignore[index]
    with pytest.raises(ValueError):
        run_account((held, made_up_account(100, held), tmp_path))


# --- Why an everyday estimate can be zero ---


def withdrawals_account(amounts_by_day: dict[int, float], days: int = 500) -> pd.DataFrame:
    """The made-up account without its random withdrawals, and these cash withdrawals instead."""
    f = made_up_account(days)
    f = f[~((f["operation"] == "VYBER") & (f["k_symbol"] != "SLUZBY"))].copy()
    extra = []
    for offset, amount in amounts_by_day.items():
        extra.append({
            "account_id": 99001, "day": pd.Timestamp(START + timedelta(days=offset)),
            "type": "VYDAJ", "operation": "VYBER", "k_symbol": None, "amount": amount,
            "signed": -amount, "bank": None, "account": np.nan,
        })  # fmt: skip
    f = pd.concat([f, pd.DataFrame(extra)]).sort_values("day", kind="stable")
    f = f.reset_index(drop=True)
    f["balance"] = f["signed"].cumsum() + 500000.0
    f["trans_id"] = range(1, len(f) + 1)
    return f


def reason_at(frame: pd.DataFrame, origin: date, tmp_path: Path) -> dict:
    conn = berka.build_db(frame, 99001, tmp_path / "z.db", 100000)
    try:
        return berka.zero_everyday_reason(conn, 99001, origin)
    finally:
        conn.close()


def test_zero_everyday_with_no_money_out_rows_in_eight_weeks(tmp_path: Path) -> None:
    # small withdrawals all through the account's first 300 days, then none for 200 days
    frame = withdrawals_account({d: 800.0 for d in range(5, 300, 6)}, days=500)
    origin = START + timedelta(days=490)
    info = reason_at(frame, origin, tmp_path)
    # the fee and the household payment are money out; what matters is that nothing is counted
    assert info["counted"] == 0 and info["rows_8w"] == info["fee"] + info["series"]
    assert info["reason"] in {
        "every money-out row belongs to a recurring series", "only bank fees",
        "no money-out rows in the last 8 weeks",
    }  # fmt: skip


def test_the_reasons_for_a_zero_estimate_are_told_apart() -> None:
    from collections import Counter

    row = ("1994-05-02", -150000, "abm withdrawal", "fp1")
    fee = ("1994-05-03", -73, "service charge", "fp2")
    paid = ("1994-05-04", -18310, "bill payment", "fp3")
    aside = Counter({(date(1994, 5, 2), 150000): 1})
    assert berka.classify_rows([], set(), Counter())["reason"].startswith("no money-out rows")
    big = berka.classify_rows([row, fee], set(), aside)
    assert big["set_aside"] == 1 and big["fee"] == 1 and big["reason"].startswith("every money-out")
    assert "big one-time money" in big["reason"]
    inside = berka.classify_rows([paid, fee], {"fp3"}, Counter())
    assert inside["reason"] == "every money-out row belongs to a recurring series"
    assert berka.classify_rows([fee], set(), Counter())["reason"] == "only bank fees"
    counted = berka.classify_rows([row, paid], {"fp3"}, Counter())
    assert counted["counted"] == 1 and "counted" in counted["reason"]
    assert aside[(date(1994, 5, 2), 150000)] == 1  # the caller's counter is not used up


def test_a_sanity_report_goes_to_a_new_dated_file_and_never_changes_an_old_one(
    tmp_path: Path,
) -> None:
    old = tmp_path / "berka_sanity_2026-10-17.md"
    old.write_text("## primary development accounts\n\nold numbers\n", encoding="utf-8")
    first = berka.write_report(["## primary development accounts", "", "new"], "primary",
                               tmp_path, date(2026, 11, 20))  # fmt: skip
    assert first == tmp_path / "berka_sanity_2026-11-20.md"
    assert old.read_text(encoding="utf-8") == "## primary development accounts\n\nold numbers\n"
    # a second group the same day joins that day's file; a rerun of a group replaces its block
    berka.write_report(["## sensitivity development accounts", "", "other"], "sensitivity",
                       tmp_path, date(2026, 11, 20))  # fmt: skip
    berka.write_report(["## primary development accounts", "", "newer"], "primary",
                       tmp_path, date(2026, 11, 20))  # fmt: skip
    text = first.read_text(encoding="utf-8")
    assert "newer" in text and "other" in text and "new\n" not in text.replace("newer", "")
    assert text.index("## primary") < text.index("## sensitivity")
    assert "old numbers" not in text
