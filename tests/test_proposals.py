"""Tests for the proposals of step 2 (DECISIONS.md, 2026-10-11): a) expected lump income by
sender, b) repeated large outflows placed by default, and the dated decision log. Made-up rows
and names only."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from lookahead.db import connect
from lookahead.flows import Txn, flow_types
from lookahead.forecast import path as _path
from lookahead.forecast.path import ForecastOptions
from lookahead.proposals import (
    LOOKS_RIGHT,
    NOT_THIS_TIME,
    decide,
    decisions_as_of,
    lump_proposals,
    repeated_outflows,
    sender_of,
)

KEEPS_OFF = {"keeps": None}  # these tests pin the option on the forecaster without keeps-coming


def build_forecast(*args, **kwargs):  # type: ignore[no-untyped-def]
    return _path.build_forecast(*args, **{**KEEPS_OFF, **kwargs})


KEY = b"made-up key for tests"
CARD = "Opos {:<20}toron "


def background(as_of: date, n: int = 120) -> list[Txn]:
    """Small card purchases every third day, so that the typical row is small."""
    return [Txn(1000 + i, as_of - timedelta(days=3 * i), -2500, "pos purchase",
                CARD.format("Shop")) for i in range(n)]  # fmt: skip


def fps(rows: list[Txn]) -> dict[int, str]:
    return {t.row_id: f"fp{t.row_id}" for t in rows}


def memo(row_id: int, day: date, cents: int, who: str = "Family Member") -> Txn:
    return Txn(row_id, day, cents, "credit memo", f"1/{who} ")


def bill(row_id: int, day: date, cents: int, payee: str = "City University") -> Txn:
    return Txn(row_id, day, -cents, "bill payment", f"{payee} ")


TERM_ROWS = [bill(1, date(2025, 1, 15), 300000), bill(2, date(2025, 5, 20), 300000),
             bill(3, date(2025, 9, 12), 310000)]  # fmt: skip

# --- a) Lump income as proposals ---

AS_OF = date(2026, 9, 30)
FIRST, LAST = date(2026, 10, 1), date(2026, 10, 30)


def lumps(rows: list[Txn], key: bytes | None = KEY, as_of: date = AS_OF):  # type: ignore[no-untyped-def]
    return lump_proposals(rows, flow_types(rows), set(), as_of, as_of + timedelta(days=1),
                          as_of + timedelta(days=30), key, fps(rows))[0]  # fmt: skip


def test_three_lumps_from_one_sender_give_a_likely_proposal_with_median_size_and_gap() -> None:
    rows = background(AS_OF) + [memo(1, date(2026, 1, 12), 500000),
                                memo(2, date(2026, 4, 13), 520000),
                                memo(3, date(2026, 7, 14), 480000)]  # fmt: skip
    [p] = lumps(rows)
    assert p.typical_cents == 500000 and p.gap_days == 92 and p.confidence == "likely"
    assert p.expected == date(2026, 10, 14)  # a Wednesday: a business day already
    assert (p.first, p.last) == (date(2026, 9, 21), date(2026, 11, 6))  # a quarter of 92 is 23
    assert p.evidence == ("fp1", "fp2", "fp3") and p.status == "detected, not confirmed"


def test_two_lumps_are_possible_and_the_key_holds_a_token_never_the_name() -> None:
    rows = background(AS_OF) + [memo(1, date(2026, 4, 13), 500000),
                                memo(2, date(2026, 7, 14), 500000)]  # fmt: skip
    [p] = lumps(rows)
    assert p.confidence == "possible"
    assert "family" not in p.key.casefold() and p.sender.startswith("PERSON_")
    assert p.key == f"lump|{p.sender}|2026-07-14"


def test_no_key_no_name_or_a_single_lump_gives_no_proposal() -> None:
    two = [memo(1, date(2026, 4, 13), 500000), memo(2, date(2026, 7, 14), 500000)]
    assert lumps(background(AS_OF) + two, key=None) == []
    unnamed = [
        Txn(1, date(2026, 4, 13), 500000, "deposit", "Free Interac E-Transfer "),
        Txn(2, date(2026, 7, 14), 500000, "deposit", "Free Interac E-Transfer "),
    ]
    assert sender_of(unnamed[0].text) == ""
    assert lumps(background(AS_OF) + unnamed) == []
    assert lumps(background(AS_OF) + two[:1]) == []


def test_two_senders_are_kept_apart_and_an_expected_date_outside_the_horizon_is_not_proposed() -> (
    None
):
    rows = background(AS_OF) + [
        memo(1, date(2026, 4, 13), 500000, "Person A"),
        memo(2, date(2026, 7, 14), 500000, "Person A"),
        memo(3, date(2026, 6, 1), 300000, "Person B"),
        memo(4, date(2026, 9, 1), 300000, "Person B"),
    ]
    [p] = lumps(rows)  # B is expected on Dec 1: after the horizon
    assert p.expected == date(2026, 10, 14)


def test_later_rows_never_change_a_lump_proposal() -> None:
    base = background(AS_OF) + [memo(1, date(2026, 4, 13), 500000),
                                memo(2, date(2026, 7, 14), 500000)]  # fmt: skip
    later = base + [memo(3, date(2026, 10, 10), 900000), memo(4, date(2026, 10, 20), 100000)]
    assert lumps(later) == lumps(base)


# --- b) Repeated large outflows ---


def placements(rows: list[Txn], as_of: date, rule: str = "earliest"):  # type: ignore[no-untyped-def]
    return repeated_outflows(rows, flow_types(rows), set(), as_of, as_of + timedelta(days=1),
                             as_of + timedelta(days=30), KEY, fps(rows), rule)  # type: ignore[arg-type]  # fmt: skip


def test_three_terms_a_year_are_placed_by_the_typical_gap() -> None:
    as_of = date(2025, 12, 29)
    rows = background(as_of) + TERM_ROWS
    [p], offers = placements(rows, as_of)
    assert offers == []
    assert p.payee == "City University" and p.cents == -310000  # the latest amount
    assert p.day == date(2026, 1, 12)  # Sep 12 + 120 days is Saturday Jan 10: moved later
    assert p.rule == "the typical gap after the latest" and p.status == "detected, not confirmed"
    assert p.key == "outflow|City University|2025-09-12"


def test_a_yearly_payment_is_placed_a_year_after_the_latest() -> None:
    as_of = date(2025, 8, 25)
    rows = background(as_of) + [
        bill(1, date(2024, 1, 15), 300000),
        bill(2, date(2024, 9, 10), 300000),
    ]
    [p], _ = placements(rows, as_of)
    assert p.day == date(2025, 9, 10) and p.rule == "a year after the latest"
    assert placements(rows, as_of, "gap")[0] == []  # the gap alone (240 days) is not ahead
    assert placements(rows, as_of, "year")[0] == [p]


def test_installments_under_60_days_apart_are_not_a_repeat() -> None:
    as_of = date(2025, 3, 30)
    rows = background(as_of) + [
        bill(1, date(2025, 1, 15), 300000),
        bill(2, date(2025, 2, 25), 300000),
    ]
    found, offers = placements(rows, as_of)
    assert found == [] and offers == []


def test_one_payment_is_a_first_time_offer_not_a_placement() -> None:
    as_of = date(2025, 10, 1)
    rows = background(as_of) + [bill(1, date(2025, 9, 12), 450000)]
    found, [offer] = placements(rows, as_of)
    assert found == [] and offer.payee == "City University" and offer.cents == -450000
    assert offer.day == date(2026, 9, 12) and offer.evidence == ("fp1",)


def test_a_person_paid_by_e_transfer_is_keyed_by_token() -> None:
    as_of = date(2025, 12, 29)
    rows = background(as_of) + [
        Txn(k, d, -300000, "withdrawal", "Interac E-Transfer Jane Doe ")
        for k, d in ((1, date(2025, 1, 15)), (2, date(2025, 5, 20)), (3, date(2025, 9, 12)))
    ]  # fmt: skip
    [p], _ = placements(rows, as_of)
    assert p.payee.startswith("PERSON_") and "jane" not in p.key.casefold()


def test_later_rows_never_change_a_placement() -> None:
    as_of = date(2025, 12, 29)
    base = background(as_of) + TERM_ROWS
    later = base + [bill(4, date(2026, 1, 12), 900000), bill(5, date(2026, 1, 3), 50000)]
    assert placements(later, as_of) == placements(base, as_of)


# --- In the forecaster, with the dated decision log ---

ACCOUNT = "test"


def insert(conn: sqlite3.Connection, rows: list[Txn]) -> None:
    balance = 2_000_000
    with conn:
        for t in sorted(rows, key=lambda r: (r.posted_date, r.row_id)):
            balance += t.amount_cents
            conn.execute(
                "INSERT INTO transactions (account, posted_date, amount_cents, description,"
                " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'f', '2026-10-01')",
                (ACCOUNT, t.posted_date.isoformat(), t.amount_cents, t.txn_type, t.text, balance,
                 f"fp{t.row_id}"),
            )  # fmt: skip


DATA_END = date(2025, 12, 29)
LUMP_ROWS = [memo(11, date(2025, 6, 9), 400000), memo(12, date(2025, 9, 8), 410000),
             memo(13, date(2025, 12, 8), 420000)]  # fmt: skip
B = ForecastOptions(outflow_placements=True)
A = ForecastOptions(lump_arrivals=True, lump_proposals=True, proposal_key=KEY)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "p.db")
    insert(c, background(DATA_END, 200) + TERM_ROWS + LUMP_ROWS)
    return c


def test_off_by_default_nothing_is_proposed_or_placed(conn: sqlite3.Connection) -> None:
    f = build_forecast(conn, ACCOUNT, futures=50)
    assert f is not None and f.lump_proposals == () and f.outflow_placements == ()
    assert not [i for i in f.items if i.series_key.startswith(("lump|", "outflow|"))]


def test_a_repeated_payment_is_placed_by_default_and_jittered_within_5_days(
    conn: sqlite3.Connection,
) -> None:
    f = build_forecast(conn, ACCOUNT, futures=200, keep_paths=True, options=B)
    assert f is not None and f.inputs is not None and f.simulation is not None
    [item] = [i for i in f.items if i.series_key.startswith("outflow|")]
    assert (item.day, item.cents, item.basis) == (date(2026, 1, 12), -310000, "detected")
    assert f.inputs.item_windows[item.series_key] == (5, 5)
    k0 = (item.day - f.start.first_day).days
    for path in f.simulation.paths:
        drops = [k for k in range(1, len(path)) if path[k - 1] - path[k] >= 300000]
        assert len(drops) == 1 and abs(drops[0] - k0) <= 5


def test_not_this_time_skips_the_occurrence_from_its_date_and_looks_right_fixes_the_date(
    conn: sqlite3.Connection,
) -> None:
    key = "outflow|City University|2025-09-12"
    decide(conn, ACCOUNT, key, NOT_THIS_TIME, DATA_END + timedelta(days=1))  # after the date
    f = build_forecast(conn, ACCOUNT, as_of=DATA_END, futures=0, options=B)
    assert f is not None and [p.key for p in f.outflow_placements] == [key]  # ignored: too late
    decide(conn, ACCOUNT, key, NOT_THIS_TIME, DATA_END)
    f = build_forecast(conn, ACCOUNT, as_of=DATA_END, futures=0, options=B)
    assert f is not None and f.outflow_placements == ()
    decide(conn, ACCOUNT, key, LOOKS_RIGHT, DATA_END)  # the latest decision wins
    f = build_forecast(conn, ACCOUNT, as_of=DATA_END, futures=20, options=B)
    assert f is not None and f.inputs is not None
    [p] = f.outflow_placements
    assert p.status == "confirmed" and key not in f.inputs.item_windows
    assert decisions_as_of(conn, ACCOUNT, DATA_END) == {key: LOOKS_RIGHT}
    with pytest.raises(ValueError):
        decide(conn, ACCOUNT, key, "maybe", DATA_END)


def test_an_unconfirmed_lump_proposal_is_only_shown_and_its_lumps_stay_in_the_pool(
    tmp_path: Path,
) -> None:
    c = connect(tmp_path / "u.db")
    as_of = date(2026, 2, 27)
    insert(c, background(as_of, 200) + LUMP_ROWS)
    f = build_forecast(c, ACCOUNT, as_of=as_of, futures=50, options=A)
    assert f is not None and f.inputs is not None
    [p] = f.lump_proposals
    assert p.expected == date(2026, 3, 9) and p.status == "detected, not confirmed"
    assert not [i for i in f.items if i.series_key.startswith("lump|")]
    assert sorted(f.inputs.lump_sizes) == [400000, 410000, 420000]


def test_a_confirmed_lump_is_placed_in_its_window_and_leaves_the_pool(
    tmp_path: Path,
) -> None:
    c = connect(tmp_path / "a.db")
    as_of = date(2026, 2, 27)
    insert(c, background(as_of, 200) + LUMP_ROWS)
    f = build_forecast(c, ACCOUNT, as_of=as_of, futures=0, options=A)
    assert f is not None
    [p] = f.lump_proposals
    assert p.expected == date(2026, 3, 9) and p.typical_cents == 410000
    decide(c, ACCOUNT, p.key, LOOKS_RIGHT, as_of)
    f = build_forecast(c, ACCOUNT, as_of=as_of, futures=100, keep_paths=True, options=A)
    assert f is not None and f.inputs is not None and f.simulation is not None
    [item] = [i for i in f.items if i.series_key == p.key]
    assert (item.day, item.cents, item.basis) == (p.expected, 410000, "confirmed")
    assert f.lump_proposals[0].status == "confirmed"
    assert f.inputs.lump_sizes == () and f.inputs.lump_rate == 0  # never counted twice
    assert f.inputs.item_windows[p.key] == (23, 23)
    k_first, k_last = 0, (p.last - f.start.first_day).days
    for path in f.simulation.paths:
        jumps = [k for k in range(1, len(path)) if path[k] - path[k - 1] >= 400000]
        jumps += [0] if path[0] - f.start.balance_cents >= 400000 else []
        assert len(jumps) <= 1 and all(k_first <= k <= k_last for k in jumps)
    # Not this time: nothing placed, and the sender is not drawn at random either.
    decide(c, ACCOUNT, p.key, NOT_THIS_TIME, as_of)
    f = build_forecast(c, ACCOUNT, as_of=as_of, futures=10, options=A)
    assert f is not None and f.inputs is not None
    assert not [i for i in f.items if i.series_key == p.key] and f.inputs.lump_sizes == ()


def test_confirm_all_and_confirmed_keys_place_without_a_decision(tmp_path: Path) -> None:
    c = connect(tmp_path / "b.db")
    as_of = date(2026, 2, 27)
    insert(c, background(as_of, 200) + LUMP_ROWS[1:])
    every = ForecastOptions(lump_proposals=True, confirm_all_lumps=True, proposal_key=KEY)
    f = build_forecast(c, ACCOUNT, as_of=as_of, futures=0, options=every)
    assert f is not None and [i.basis for i in f.items if i.series_key.startswith("lump|")] == [
        "confirmed"
    ]
    key = f.lump_proposals[0].key
    chosen = ForecastOptions(lump_proposals=True, confirmed_keys=frozenset({key}),
                             proposal_key=KEY)  # fmt: skip
    g = build_forecast(c, ACCOUNT, as_of=as_of, futures=0, options=chosen)
    assert g is not None and g.items == f.items


def test_proposals_ignore_later_rows_end_to_end(tmp_path: Path) -> None:
    """Rule zero: a database with rows after the date, rewritten or deleted, gives the same."""
    as_of = DATA_END
    options = ForecastOptions(lump_arrivals=True, lump_proposals=True, outflow_placements=True,
                              confirm_all_lumps=True, proposal_key=KEY)  # fmt: skip
    a = connect(tmp_path / "a.db")
    insert(a, background(as_of, 200) + TERM_ROWS + LUMP_ROWS)
    b = connect(tmp_path / "b.db")
    insert(b, background(as_of, 200) + TERM_ROWS + LUMP_ROWS
           + [bill(90, date(2026, 1, 5), 990000), memo(91, date(2026, 1, 9), 777700)])  # fmt: skip
    with b:
        b.execute("UPDATE transactions SET amount_cents = amount_cents * 2 WHERE posted_date > ?",
                  (as_of.isoformat(),))  # fmt: skip
    fa = build_forecast(a, ACCOUNT, as_of=as_of, futures=60, options=options)
    fb = build_forecast(b, ACCOUNT, as_of=as_of, futures=60, options=options)
    assert fa is not None and fb is not None
    assert fa.items == fb.items and fa.simulation == fb.simulation
    assert fa.lump_proposals == fb.lump_proposals and fa.outflow_placements == fb.outflow_placements


def test_the_same_seed_gives_the_same_futures_with_proposals(conn: sqlite3.Connection) -> None:
    options = ForecastOptions(outflow_placements=True, lump_arrivals=True)
    f = build_forecast(conn, ACCOUNT, futures=80, options=options)
    g = build_forecast(conn, ACCOUNT, futures=80, options=options)
    assert f is not None and g is not None and f.simulation == g.simulation


@pytest.mark.slow
def test_the_scorer_runs_on_a_development_persona_and_refuses_a_held_out_one() -> None:
    from eval.proposals_score import score_persona, summarize_a, summarize_b

    result = score_persona("v2L-DRENT-01", n_origins=4)
    assert result["archetype"] == "DRENT" and result["persona"] == "v2L-DRENT-01"
    a, b = summarize_a([result]), summarize_b([result])
    assert a["proposals"] == len(result["a"]) and b["placements"] == len(
        [p for p in result["b"] if p["rule"] == "earliest"]
    )
    assert all(not p["key"].casefold().count("bmo") for p in result["a"])  # tokens, not names
    with pytest.raises(ValueError, match="held-out"):
        score_persona("v2L-DRENT-04", n_origins=1)


# --- b) by calendar position (DECISIONS.md, 2026-10-12, step 1) ---

TWO_TERM_ROWS = [bill(21, date(2025, 1, 20), 300000), bill(22, date(2025, 9, 12), 320000),
                 bill(23, date(2026, 1, 19), 300000)]  # fmt: skip


def test_calendar_places_a_year_after_the_payment_of_the_same_term() -> None:
    as_of = date(2026, 8, 24)  # two terms a year: the next is September, a year after Sep 12
    rows = background(as_of) + TWO_TERM_ROWS
    [p], offers = placements(rows, as_of, "calendar")
    assert offers == []
    assert p.day == date(2026, 9, 14)  # Sep 12, 2025 + 365 days is Saturday Sep 12: moved later
    assert p.cents == -320000  # the amount of that same-term payment, not the latest
    assert p.key == "outflow|City University|2025-09-12"
    assert p.rule == "a year after the same-term payment" and p.status == "detected, not confirmed"
    found, _ = placements(rows, as_of, "earliest")  # the earlier rule looked a gap past January
    assert [q.day for q in found] != [p.day]


def test_calendar_places_nothing_when_no_payment_a_year_ago_falls_in_the_horizon() -> None:
    as_of = date(2026, 4, 1)
    rows = background(as_of) + TWO_TERM_ROWS
    assert placements(rows, as_of, "calendar") == ([], [])


def test_calendar_uses_the_latest_payment_when_several_propose_the_same_day() -> None:
    as_of = date(2026, 1, 5)
    rows = background(as_of) + [
        bill(31, date(2025, 1, 12), 300000),  # + 365 = Mon Jan 12, 2026
        bill(32, date(2025, 1, 10), 280000),  # + 365 = Sat Jan 10, 2026, moved to Mon Jan 12
    ]
    [p], _ = placements(rows, as_of, "calendar")
    assert p.day == date(2026, 1, 12) and p.cents == -300000 and p.key.endswith("2025-01-12")


def test_calendar_installments_a_month_apart_are_each_placed_a_year_on() -> None:
    as_of = date(2026, 1, 5)
    rows = background(as_of) + [bill(41, date(2025, 1, 15), 150000),
                                bill(42, date(2025, 2, 25), 150000)]  # fmt: skip
    [p], _ = placements(rows, as_of, "calendar")
    assert p.day == date(2026, 1, 15)  # the first installment; the second is after the horizon


def test_calendar_one_payment_is_still_a_first_time_offer() -> None:
    as_of = date(2026, 9, 1)
    rows = background(as_of) + [bill(1, date(2025, 9, 12), 450000)]
    found, [offer] = placements(rows, as_of, "calendar")
    assert found == [] and offer.cents == -450000


def test_calendar_later_rows_never_change_a_placement() -> None:
    as_of = date(2026, 8, 24)
    base = background(as_of) + TWO_TERM_ROWS
    later = base + [bill(51, date(2026, 9, 14), 900000), bill(52, date(2026, 8, 30), 400000)]
    assert placements(later, as_of, "calendar") == placements(base, as_of, "calendar")


CAL = ForecastOptions(outflow_placements=True, outflow_rule="calendar")
CAL_END = date(2026, 8, 24)


@pytest.fixture
def cal_conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "cal.db")
    insert(c, background(CAL_END, 260) + TWO_TERM_ROWS)
    return c


def _with_keeps(*args, **kwargs):  # type: ignore[no-untyped-def]
    return _path.build_forecast(*args, **kwargs)  # keeps-coming on, as by default


def test_calendar_in_the_forecaster_rewriting_later_rows_changes_nothing(
    cal_conn: sqlite3.Connection,
) -> None:
    f = _with_keeps(cal_conn, ACCOUNT, as_of=CAL_END, futures=60, options=CAL)
    assert f is not None and [p.day for p in f.outflow_placements] == [date(2026, 9, 14)]
    insert(cal_conn, [bill(61, date(2026, 9, 14), 320000), bill(62, date(2026, 9, 1), 700000)])
    cal_conn.execute("UPDATE transactions SET amount_cents = amount_cents * 3"
                     " WHERE posted_date > ?", (CAL_END.isoformat(),))  # fmt: skip
    g = _with_keeps(cal_conn, ACCOUNT, as_of=CAL_END, futures=60, options=CAL)
    assert g is not None
    assert g.outflow_placements == f.outflow_placements and g.items == f.items
    assert g.simulation == f.simulation


def test_calendar_a_decision_after_the_date_is_ignored(cal_conn: sqlite3.Connection) -> None:
    key = "outflow|City University|2025-09-12"
    decide(cal_conn, ACCOUNT, key, NOT_THIS_TIME, CAL_END + timedelta(days=1))
    f = _with_keeps(cal_conn, ACCOUNT, as_of=CAL_END, futures=0, options=CAL)
    assert f is not None and [p.key for p in f.outflow_placements] == [key]
    decide(cal_conn, ACCOUNT, key, NOT_THIS_TIME, CAL_END)
    f = _with_keeps(cal_conn, ACCOUNT, as_of=CAL_END, futures=0, options=CAL)
    assert f is not None and f.outflow_placements == ()


def test_calendar_is_deterministic(cal_conn: sqlite3.Connection) -> None:
    f = _with_keeps(cal_conn, ACCOUNT, as_of=CAL_END, futures=80, options=CAL)
    g = _with_keeps(cal_conn, ACCOUNT, as_of=CAL_END, futures=80, options=CAL)
    assert f is not None and g is not None and f.simulation == g.simulation


def test_a_placed_payment_is_never_also_a_keeps_coming_arrival(tmp_path: Path) -> None:
    """Ten big payments a year to one payee: keeps-coming would draw them as arrivals (6 or more
    a year), but once one of them is placed by date, none of that payee's rows is drawn."""
    c = connect(tmp_path / "k.db")
    as_of = date(2026, 1, 5)
    monthly = [bill(70 + k, date(2025, 1, 12) + timedelta(days=40 * k), 300000) for k in range(10)]
    insert(c, background(as_of, 260) + monthly)
    off = _with_keeps(c, ACCOUNT, as_of=as_of, futures=20)
    assert off is not None and off.arrivals.out_sizes  # drawn as arrivals without placements
    on = _with_keeps(c, ACCOUNT, as_of=as_of, futures=20, options=CAL)
    assert on is not None and on.outflow_placements
    placed = {fp for p in on.outflow_placements for fp in p.evidence}
    rows = {fp: rid for rid, fp in c.execute("SELECT id, fingerprint FROM transactions")}
    assert not {rows[fp] for fp in placed} & on.arrivals.members
    assert on.arrivals.out_sizes == ()


def test_a_confirmed_senders_lumps_are_never_also_keeps_coming_arrivals(tmp_path: Path) -> None:
    """Step 2 of 2026-10-12: keeps-coming draws "the rest". A sender whose lump proposal is
    confirmed is placed by its proposal, so its lumps leave the keeps-coming pool too."""
    c = connect(tmp_path / "l.db")
    as_of = date(2026, 2, 20)  # the next lump (Dec 8 + about 91 days) falls in the horizon
    insert(c, background(as_of, 200) + LUMP_ROWS)
    lumps = {rid for rid, fp in c.execute("SELECT id, fingerprint FROM transactions")
             if fp in {"fp11", "fp12", "fp13"}}  # fmt: skip
    plain = _with_keeps(c, ACCOUNT, as_of=as_of, futures=20)
    assert plain is not None and plain.arrivals.members & lumps  # drawn by keeps-coming
    naive = ForecastOptions(lump_proposals=True, confirm_all_lumps=True, proposal_key=KEY)
    confirmed = _with_keeps(c, ACCOUNT, as_of=as_of, futures=20, options=naive)
    assert confirmed is not None and [p.status for p in confirmed.lump_proposals] == ["confirmed"]
    assert not confirmed.arrivals.members & lumps
    shown = ForecastOptions(lump_proposals=True, proposal_key=KEY)  # proposed, not confirmed
    unconfirmed = _with_keeps(c, ACCOUNT, as_of=as_of, futures=20, options=shown)
    assert unconfirmed is not None and unconfirmed.arrivals == plain.arrivals
    assert unconfirmed.simulation == plain.simulation  # an unconfirmed proposal changes nothing
