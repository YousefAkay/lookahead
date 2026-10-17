"""The Berka gate (eval/berka_gate.py) on a tiny file in Berka's own format."""

from pathlib import Path

import pandas as pd

from eval.berka_gate import build, closing_balances, load, per_account

HEADER = (
    '"trans_id";"account_id";"date";"type";"operation";"amount";"balance";"k_symbol";'
    '"bank";"account"'
)
ROWS = [
    # account 1: a deposit, a pension, interest, then a day where the file lists the withdrawal
    # before the fee, though the fee came first (the last row listed is not the day's close)
    '1;1;930101;"PRIJEM";"VKLAD";1000.00;1000.00;"";;',
    '2;1;930111;"PRIJEM";"PREVOD Z UCTU";500.00;1500.00;"DUCHOD";"AB";12',
    '3;1;930131;"PRIJEM";;10.10;1510.10;"UROK";;',
    '4;1;930131;"VYDAJ";"VYBER";300.00;1195.50;"";;',
    '5;1;930131;"VYDAJ";"VYBER";14.60;1495.50;"SLUZBY";;',
    '6;1;930215;"VYDAJ";"PREVOD NA UCET";200.00;995.50;"SIPO";"CD";34',
    '7;1;930220;"VYBER";"VYBER";10.00;985.50;"";;',
    # account 2: only a deposit
    '8;2;930105;"PRIJEM";"VKLAD";700.00;700.00;"";;',
]


def write(tmp_path: Path) -> Path:
    (tmp_path / "trans.asc").write_text(HEADER + "\n" + "\n".join(ROWS) + "\n", encoding="utf-8")
    return tmp_path


def test_signs_dates_and_flow_classes(tmp_path: Path) -> None:
    t = load(write(tmp_path)).set_index("trans_id")
    assert t.loc[1, "signed"] == 1000 and t.loc[4, "signed"] == -300
    assert t.loc[7, "signed"] == -10  # type VYBER is a withdrawal
    assert t.loc[1, "day"] == pd.Timestamp("1993-01-01")
    assert t.loc[4, "flow"] == "everyday" and t.loc[6, "flow"] == "monthly"
    assert t.loc[5, "flow"] == "fee" and t.loc[2, "flow"] == "in"
    assert bool(t.loc[3, "mechanical"]) and bool(t.loc[5, "mechanical"])
    assert not bool(t.loc[4, "mechanical"])


def test_the_close_is_the_balance_nearest_to_the_running_total(tmp_path: Path) -> None:
    closes = closing_balances(load(write(tmp_path)))
    day = closes[(closes["account_id"] == 1) & (closes["day"] == pd.Timestamp("1993-01-31"))]
    # 1500 + 10.10 - 300 - 14.60 = 1195.50: the last row listed says 1495.50 (the fee alone)
    assert day["close"].iloc[0] == 1195.50 and bool(day["matched"].iloc[0])
    assert closes["matched"].all()
    last = closes[(closes["account_id"] == 1)]["close"].tolist()
    assert last == [1000.0, 1500.0, 1195.5, 995.5, 985.5]


def test_account_summaries(tmp_path: Path) -> None:
    t = load(write(tmp_path))
    acc = per_account(t)
    assert acc.loc[1, "n"] == 7 and acc.loc[2, "n"] == 1
    assert acc.loc[1, "n_real"] == 5  # interest and the fee are mechanical
    assert acc.loc[1, "everyday_value_share"] > 0.5


def test_the_report_builds_and_says_it_scores_nothing(tmp_path: Path) -> None:
    text = build(write(tmp_path))
    assert "nothing is scored" in text and "Dips after weekly origins" in text
