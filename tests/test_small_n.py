"""The small-sample rule for what the real-account scripts print: a median or a gap statistic
needs 10 or more values, a group needs 5 or more rows ("too few to show"). Made-up values."""

from datetime import date, timedelta

import pytest

from eval import big_money, diagnose_real, income_timing, proposals_real
from eval.small_n import MIN_GROUP, MIN_STATISTIC, TOO_FEW

SEVEN = 700_000  # cents, a made-up amount that must never be printed when it is one row's


def test_the_two_limits_are_5_and_10() -> None:
    assert (MIN_GROUP, MIN_STATISTIC) == (5, 10)


# --- eval.diagnose_real ---


def record(i: int) -> dict[str, object]:
    parts = {p: float(100 * (i + 1)) for p in diagnose_real.PARTS}
    split = {name: 1.0 * i for name in diagnose_real.RECURRING_SPLIT}
    return {
        "total_30": 1000.0 * i, "median_30": 500.0 * i, "lowest_error": 300.0 * i,
        "error_30": parts, "error_low": {**parts, "low_point": 5.0},
        "recurring_30": split, "recurring_low": split,
        "n_placed": 3, "n_actual": 3, "n_matched": 2, "n_missed": 1, "n_extra": 1,
        "date_off": [1, 2],
    }  # fmt: skip


@pytest.mark.parametrize(
    "n, group_shown, median_shown", [(4, False, False), (5, True, False), (11, True, True)]
)
def test_the_error_report_hides_a_small_group_and_a_median_of_few_origins(
    n: int, group_shown: bool, median_shown: bool
) -> None:  # fmt: skip
    text = "\n".join(diagnose_real.report([record(i) for i in range(n)], "All origins"))
    if not group_shown:
        assert text.count(TOO_FEW) == 1 and "mean" not in text
        return
    assert "median" in text and ("median $" in text) is median_shown
    assert (TOO_FEW in text) is not median_shown


def test_the_days_off_of_few_matched_payments_are_not_averaged() -> None:
    rows = [{**record(i), "date_off": [3]} for i in range(11)]  # 11 origins, 11 matched days
    assert "days off" in "\n".join(diagnose_real.report(rows, "x"))
    assert "mean 3.0 days off" in "\n".join(diagnose_real.report(rows, "x"))
    few = [{**record(i), "date_off": []} for i in range(11)]
    few[0]["date_off"] = [3]
    assert "mean 3.0 days off" not in "\n".join(diagnose_real.report(few, "x"))


def band(count: int, gap: float | None = 12.0) -> dict[str, object]:
    return {"count": count, "share_money": 0.5, "share_weeks": 0.2, "share_windows": 0.3,
            "median_gap": gap}  # fmt: skip


@pytest.mark.parametrize(
    "n, shown, gap_shown", [(4, False, False), (5, True, False), (11, True, True)]
)
def test_the_deposit_bands_hide_a_group_under_5_and_a_median_gap_under_10(
    n: int, shown: bool, gap_shown: bool
) -> None:  # fmt: skip
    ev = {"cutoff_is_floor": False, "windows": {"the window": {
        "rows": 30, "bands": [band(n)] * len(diagnose_real.BANDS), "by_type": {"credit memo": n},
    }}}  # fmt: skip
    text = "\n".join(diagnose_real.evidence_lines(ev))
    assert ("deposits, " in text) is shown
    assert ("median gap 12" in text) is gap_shown
    assert (TOO_FEW in text) is (n < 10)


# --- eval.income_timing ---


def deps(gaps: list[int]) -> list[income_timing.Dep]:
    day, out = date(2026, 1, 5), [income_timing.Dep(date(2026, 1, 5), 0, "credit memo")]
    for i, g in enumerate(gaps, start=1):
        day += timedelta(days=g)
        out.append(income_timing.Dep(day, i, "credit memo"))
    return out


@pytest.mark.parametrize("n, shown", [(4, False), (5, False), (11, True)])
def test_gap_statistics_need_10_gaps(n: int, shown: bool) -> None:
    text = "\n".join(income_timing.gap_lines(deps([28 + i for i in range(n)])))
    assert ("median" in text) is shown
    assert (TOO_FEW in text) is (not shown)
    assert "quartiles" not in text or shown


@pytest.mark.parametrize("n, shown", [(4, False), (5, True), (11, True)])
def test_a_timing_group_under_5_origins_is_not_shown(n: int, shown: bool) -> None:
    obs = [income_timing.Obs(3, False, 0.1, 0.2, False) for _ in range(n)]  # all in 0-7 days
    text = "\n".join(income_timing.chance_lines(obs, 0))
    line = next(x for x in text.splitlines() if "0-7 days" in x and "since" not in x)
    assert (TOO_FEW in line) is (not shown)
    assert ("n " in line) is shown


# --- eval.proposals_real ---


def test_a_median_of_fewer_than_10_values_is_not_printed() -> None:
    assert proposals_real._median([1.0] * 9) == TOO_FEW
    assert proposals_real._median([1.0] * 10) == "1.0"
    assert proposals_real._median([]) == "-"


# --- eval.big_money ---


@pytest.mark.parametrize("rows, hidden", [(3, True), (4, False), (9, True), (10, False)])
def test_a_cutoff_from_an_odd_median_of_fewer_than_10_rows_is_a_range_not_the_value(
    rows: int, hidden: bool
) -> None:  # fmt: skip
    facts = [
        {"cut_in": 5 * SEVEN, "cut_out": 5 * SEVEN, "n_in": rows, "n_out": rows,
         "own_yardstick": True, "deposits_large": 0, "deposits_pass": 0, "payments_large": 0,
         "payments_pass": 0}
        for _ in range(12)
    ]  # fmt: skip
    text = "\n".join(big_money.origin_lines(facts, (5 * SEVEN, 5 * SEVEN), (rows, rows)))
    assert ("$35,000" in text) is not hidden  # the exact value (5 times a made-up row)
    assert ("$25,000 to $50,000" in text) is hidden


def test_a_cutoff_at_the_floor_is_always_printed() -> None:
    facts = [
        {
            "cut_in": 50000,
            "cut_out": 50000,
            "n_in": 3,
            "n_out": 3,
            "own_yardstick": False,
            "deposits_large": 0,
            "deposits_pass": 0,
            "payments_large": 0,
            "payments_pass": 0,
        }
    ]
    text = "\n".join(big_money.origin_lines(facts, (50000, 50000), (3, 3)))
    assert "$500" in text and "to $" not in text.split("cutoffs at the last")[0]
