"""Budget alerts (spec docs/specs/2026-11-20-budgets-that-forecast.md): above 50 percent from day
8, one episode per category per month, ends at month end, a dismissed one comes back only on a
higher band, a $25 move only refreshes the evidence. Made-up paces, temporary databases; the
replay on a synthetic persona is in test_budgets.py."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead.budget_alerts import (
    alert_message,
    budget_episodes,
    dismiss,
    open_budget_alerts,
    record_look,
    restore,
    should_alert,
)
from lookahead.budgets import BudgetMonth, Pace, month_start
from lookahead.db import connect
from lookahead.needs_a_look import needs_a_look

ACCOUNT = "acct"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "a.db")


def pace(chance: float, likely_end: int = 15_000, category: str = "Shopping",
         limit: int = 10_000, spent: int = 6_000) -> Pace:  # fmt: skip
    return Pace(category, limit, spent, likely_end, chance, 100, False, ())


def look(conn: sqlite3.Connection, data_date: date, *paces: Pace):  # type: ignore[no-untyped-def]
    first = data_date + timedelta(days=1)
    month = BudgetMonth(month_start(first), data_date, 1, tuple(paces))
    return record_look(conn, ACCOUNT, month, first)


def events(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM budget_alert_events").fetchone()[0]


def test_the_rule_is_above_half_from_the_8th() -> None:
    assert not should_alert(pace(0.9), date(2026, 9, 7))
    assert should_alert(pace(0.9), date(2026, 9, 8))
    assert not should_alert(pace(0.50), date(2026, 9, 20))
    assert should_alert(pace(0.51), date(2026, 9, 20))


def test_no_alert_in_the_first_week(conn: sqlite3.Connection) -> None:
    [r] = look(conn, date(2026, 9, 6), pace(0.95))  # first forecast day: the 7th
    assert r.episode_id is None and budget_episodes(conn, ACCOUNT) == []
    [r] = look(conn, date(2026, 9, 7), pace(0.95))  # the 8th
    assert r.notified and r.reason == "new alert"
    [e] = open_budget_alerts(conn, ACCOUNT)
    assert e.category == "Shopping" and e.month == "2026-09" and e.likely_over_cents == 5_000
    assert "Shopping" in alert_message(e) and "95%" in alert_message(e)


def test_the_same_numbers_again_add_nothing(conn: sqlite3.Connection) -> None:
    look(conn, date(2026, 9, 10), pace(0.6))
    look(conn, date(2026, 9, 10), pace(0.6))
    look(conn, date(2026, 9, 11), pace(0.62, likely_end=15_500))  # small move: not recorded
    assert events(conn) == 1
    [e] = budget_episodes(conn, ACCOUNT)
    assert e.last_seen_on == date(2026, 9, 11)


def test_a_band_change_is_notified_and_a_25_dollar_move_refreshes_quietly(
    conn: sqlite3.Connection,
) -> None:
    look(conn, date(2026, 9, 10), pace(0.6))
    [r] = look(conn, date(2026, 9, 11), pace(0.6, likely_end=17_500))  # overrun 50 -> 75
    assert r.recorded and not r.notified and "moved by $25" in r.reason
    [r] = look(conn, date(2026, 9, 12), pace(0.85, likely_end=17_500))
    assert r.recorded and r.notified and "changed band" in r.reason
    [e] = budget_episodes(conn, ACCOUNT)
    assert e.probability == 0.85 and e.evidence["likely_end_cents"] == 17_500


def test_a_dismissed_alert_comes_back_only_on_a_higher_band(conn: sqlite3.Connection) -> None:
    [r] = look(conn, date(2026, 9, 10), pace(0.6))
    dismiss(conn, ACCOUNT, r.episode_id, date(2026, 9, 11))
    assert open_budget_alerts(conn, ACCOUNT) == []
    # Much worse overrun, same band: evidence refreshed, still dismissed.
    [r2] = look(conn, date(2026, 9, 12), pace(0.7, likely_end=40_000))
    assert r2.recorded and not r2.notified
    [e] = budget_episodes(conn, ACCOUNT)
    assert e.status == "dismissed" and e.likely_end_cents == 40_000
    # Getting better stays quiet too.
    look(conn, date(2026, 9, 13), pace(0.55, likely_end=40_000))
    assert budget_episodes(conn, ACCOUNT)[0].status == "dismissed"
    # A higher band than when it last spoke (60%, band 50-80): back.
    [r3] = look(conn, date(2026, 9, 14), pace(0.81, likely_end=40_000))
    assert r3.notified and "higher band" in r3.reason
    [e] = open_budget_alerts(conn, ACCOUNT)
    assert e.episode_id == r.episode_id


def test_one_episode_per_category_per_month_even_when_the_chance_comes_back(
    conn: sqlite3.Connection,
) -> None:
    [r] = look(conn, date(2026, 9, 10), pace(0.6))
    dismiss(conn, ACCOUNT, r.episode_id, date(2026, 9, 11))
    look(conn, date(2026, 9, 12), pace(0.3))  # below: it ends
    assert budget_episodes(conn, ACCOUNT)[0].status == "closed"
    [r2] = look(conn, date(2026, 9, 15), pace(0.65))  # back the same month: the same episode
    assert r2.episode_id == r.episode_id
    [e] = budget_episodes(conn, ACCOUNT)
    assert e.status == "dismissed"  # still dismissed: 65% is not a higher band than 60%
    other = look(conn, date(2026, 9, 16), pace(0.65), pace(0.9, category="Food delivery"))
    assert len({x.episode_id for x in other}) == 2  # each category its own episode


def test_a_new_month_ends_the_old_episode_at_its_last_day(conn: sqlite3.Connection) -> None:
    [r] = look(conn, date(2026, 9, 20), pace(0.9))
    look(conn, date(2026, 10, 3), pace(0.9))  # 4 October: before the 8th, nothing new
    [e] = budget_episodes(conn, ACCOUNT)
    assert e.episode_id == r.episode_id and e.closed_on == date(2026, 9, 30)
    [r2] = look(conn, date(2026, 10, 9), pace(0.9))
    assert r2.episode_id != r.episode_id and r2.reason == "new alert"
    assert [x.month for x in budget_episodes(conn, ACCOUNT)] == ["2026-10", "2026-09"]


def test_turning_a_budget_off_ends_its_alert(conn: sqlite3.Connection) -> None:
    look(conn, date(2026, 9, 10), pace(0.9), pace(0.9, category="Food delivery"))
    look(conn, date(2026, 9, 11), pace(0.9))  # Food delivery no longer budgeted
    assert [e.category for e in open_budget_alerts(conn, ACCOUNT)] == ["Shopping"]


def test_restore_and_unknown_episodes(conn: sqlite3.Connection) -> None:
    [r] = look(conn, date(2026, 9, 10), pace(0.9))
    dismiss(conn, ACCOUNT, r.episode_id)
    restore(conn, ACCOUNT, r.episode_id)
    assert len(open_budget_alerts(conn, ACCOUNT)) == 1
    with pytest.raises(ValueError):
        dismiss(conn, "someone else", r.episode_id)
    with pytest.raises(ValueError):
        restore(conn, ACCOUNT, 999)


def posted(conn: sqlite3.Connection, day: str) -> None:
    """A stored row, so the account's data ends on `day`."""
    with conn:
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, -100, 'pos purchase', 'x', 0, ?, 'x.csv', '2026-10-02')",
            (ACCOUNT, day, f"fp-{day}"),
        )


def test_home_counts_open_budget_alerts(conn: sqlite3.Connection) -> None:
    posted(conn, "2026-09-10")
    assert not needs_a_look(conn, ACCOUNT).anything
    [r] = look(conn, date(2026, 9, 10), pace(0.9))
    look_now = needs_a_look(conn, ACCOUNT)
    assert look_now.anything and [e.category for e in look_now.budgets] == ["Shopping"]
    dismiss(conn, ACCOUNT, r.episode_id)
    assert not needs_a_look(conn, ACCOUNT).anything


def test_an_old_read_only_database_has_no_budget_alerts(tmp_path: Path) -> None:
    raw = sqlite3.connect(tmp_path / "old.db")
    raw.execute("CREATE TABLE transactions (id INTEGER)")
    assert budget_episodes(raw, ACCOUNT) == []
    raw.close()


def test_home_leaves_out_an_alert_from_an_earlier_month(conn: sqlite3.Connection) -> None:
    """N4: after an import into the next month, Home no longer lists last month's budget alert,
    even before the Budgets tab looks again and ends it."""
    posted(conn, "2026-09-20")
    look(conn, date(2026, 9, 20), pace(0.9))
    assert [e.month for e in needs_a_look(conn, ACCOUNT).budgets] == ["2026-09"]
    posted(conn, "2026-09-30")  # data to the 30th: the budget month is now October
    assert needs_a_look(conn, ACCOUNT).budgets == ()
    assert open_budget_alerts(conn, ACCOUNT)  # still open in the store until the tab looks
    look(conn, date(2026, 10, 9), pace(0.9))
    posted(conn, "2026-10-09")
    assert [e.month for e in needs_a_look(conn, ACCOUNT).budgets] == ["2026-10"]


def test_home_with_no_rows_lists_no_budget_alert(conn: sqlite3.Connection) -> None:
    look(conn, date(2026, 9, 20), pace(0.9))
    assert needs_a_look(conn, ACCOUNT).budgets == ()


def test_already_over_says_so_instead_of_quoting_a_chance(conn: sqlite3.Connection) -> None:
    look(conn, date(2026, 9, 10), pace(1.0, likely_end=11_000, spent=12_000))
    [e] = budget_episodes(conn, ACCOUNT)
    message = alert_message(e)
    assert message.startswith("You're already over this budget: Shopping")
    assert "%" not in message and "simulated futures" not in message
    look(conn, date(2026, 9, 11), pace(0.9, spent=6_000))  # not over: the chance again
    assert "90% of simulated futures" in alert_message(budget_episodes(conn, ACCOUNT)[0])


def test_dollars_put_the_minus_sign_before_the_dollar_sign() -> None:
    from lookahead.budget_alerts import _dollars

    assert _dollars(-2_500) == "\u2212$25" and _dollars(2_500) == "$25"


def test_the_chart_shows_a_likely_end_below_spent_so_far() -> None:
    from app.theme import budget_chart

    figure = budget_chart(-50_000, -70_000, 10_000)  # refunds drawn in the futures go lower
    low = figure.layout.xaxis.range[0]
    labels = dict(zip(figure.data[0].y, figure.data[0].text, strict=True))
    assert low <= -700
    assert labels["Spent so far"] == "−$500.00"
    assert labels["Likely end of month"] == "−$700.00"
    assert labels["Limit"] == "$100.00"


def test_the_tab_shows_the_live_pace_not_the_last_recorded_event(conn: sqlite3.Connection) -> None:
    # Reviewer NB5: a 95% episode, then a look at 92% (same band, small move: no new event).
    from lookahead.budget_alerts import with_live_pace

    look(conn, date(2026, 9, 7), pace(0.95))
    look(conn, date(2026, 9, 8), pace(0.92))
    [e] = open_budget_alerts(conn, ACCOUNT)
    assert "95%" in alert_message(e)  # the recorded event
    shown = alert_message(with_live_pace(e, pace(0.92)))
    assert "92%" in shown and "95%" not in shown
    other = pace(0.92)
    assert with_live_pace(e, None) == e and with_live_pace(e, other) != e


def test_the_chart_turns_amber_when_over_and_the_picker_marks_series_rows() -> None:
    # Reviewer NB6. Bars are drawn bottom first: likely end, limit, spent so far.
    from app.theme import COLORS, budget_chart
    from lookahead.budgets import SERIES_SUFFIX, picker_label

    def colours(spent: int, likely: int, limit: int) -> list[str]:
        return list(budget_chart(spent, likely, limit).data[0].marker.color)

    assert colours(5_000, 9_000, 10_000)[0] == COLORS.blue  # on track
    assert colours(5_000, 12_000, 10_000)[0] == COLORS.amber_line  # likely to end over
    assert colours(11_000, 11_000, 10_000)[0] == COLORS.amber_line  # already over
    assert picker_label("2026-09-03", 12.5, "Quorvel", True).endswith(SERIES_SUFFIX)
    assert picker_label("2026-09-03", 12.5, "Quorvel", False) == "2026-09-03 · 12.50 · Quorvel"
