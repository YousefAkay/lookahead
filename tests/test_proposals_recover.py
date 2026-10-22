"""The real-account veto script (eval.proposals_recover), on a made-up database: counts only."""

import hashlib
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from eval import proposals_recover as pr
from lookahead import recurring_store as rs
from lookahead.calendar_rules import monthly_date, shift_to_business_day
from lookahead.db import connect
from lookahead.generator.schedules import GeneratedTxn
from lookahead.rent import RentRule
from lookahead.setting_proposals import (
    SettingProposal,
    accept,
    current_proposals,
)

ACCOUNT = "a"
RENT_DAYS = [
    date(2025, 1, 2), date(2025, 2, 3), date(2025, 3, 3), date(2025, 3, 31), date(2025, 5, 2),
    date(2025, 6, 4), date(2025, 7, 1), date(2025, 8, 2), date(2025, 9, 3), date(2025, 9, 30),
    date(2025, 11, 3), date(2025, 12, 1),
]  # fmt: skip
RENT_SIZES = [90000] * 8 + [95000] * 4


def month_day(month: int) -> date:
    year, m = 2025 + (month - 1) // 12, (month - 1) % 12 + 1
    return shift_to_business_day(monthly_date(year, m, 8), "later")


def build(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    n = 0

    def insert(day: date, cents: int, kind: str, text: str) -> None:
        nonlocal n
        n += 1
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?, 'f', '2026-10-01')",
            (ACCOUNT, day.isoformat(), cents, kind, text, f"fp{n:04d}"),
        )

    for day, cents in zip(RENT_DAYS, RENT_SIZES, strict=True):
        insert(day, -cents, "withdrawal", "Free Interac E-Transfer ")
    for month in range(1, 9):
        insert(month_day(month), -2500, "bill payment", "Fitplace ")
    for month in range(9, 13):
        insert(month_day(month), -2500, "bill payment", "Fitplace Online ")
    for month in (11, 12):
        insert(month_day(month), -1299, "bill payment", "Zenstudio ")
    conn.commit()
    return conn


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    build(path).close()
    return path


def accept_everything(path: Path) -> None:
    conn = connect(path)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    for proposal in current_proposals(conn, ACCOUNT):
        accept(conn, ACCOUNT, proposal, series, date(2026, 1, 5))
    conn.close()


def copy_of(path: Path) -> sqlite3.Connection:
    return pr._in_memory_copy(path)


def test_hand_entries_that_equal_the_proposals_are_all_recovered(db: Path) -> None:
    accept_everything(db)  # what a click would have written: the entries to recover
    assert pr.recover(copy_of(db)) == {"rent": (1, 0), "link": (1, 0), "hand-added": (1, 0)}


def test_a_rent_rule_with_other_amounts_is_not_recovered(db: Path) -> None:
    conn = connect(db)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    rent = next(s for s in series if s.kind == "e-transfer")
    rs.set_rent_rule(conn, ACCOUNT, rent, RentRule((120000,), 1, 4), date(2026, 1, 5))
    conn.close()
    assert pr.recover(copy_of(db))["rent"] == (0, 1)


def test_a_rent_rule_whose_window_holds_no_past_payment_has_nothing_to_cover(db: Path) -> None:
    """DECISIONS.md, 2026-10-22: the window must cover the days of my past payments that fall inside
    my window; if none do, there is nothing to cover (and the line says 0 of 0)."""
    conn = connect(db)
    (proposal,) = [p for p in current_proposals(conn, ACCOUNT) if p.kind == "rent"]
    far = RentRule(proposal.rent.amounts_cents, 15, 19)  # type: ignore[union-attr]
    comparison = pr.compare_rent(far, proposal, [d.day for d in RENT_DAYS])
    assert (comparison.covered, comparison.inside) == (0, 0) and comparison.recovered
    assert pr.rent_recovered(far, proposal, [d.day for d in RENT_DAYS])
    conn.close()


def test_with_no_hand_entries_there_is_nothing_to_check(db: Path) -> None:
    assert pr.recover(copy_of(db)) == {"rent": (0, 0), "link": (0, 0), "hand-added": (0, 0)}


def test_the_script_prints_counts_only_and_leaves_the_database_untouched(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    accept_everything(db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    assert pr.main(["--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before  # read-only
    assert "rent: yes 1, no 0 -> ok" in out and "link: yes 1, no 0 -> ok" in out
    assert "hand-added: yes 1, no 0 -> ok" in out
    body = without_note(out)
    for leak in ("Zenstudio", "Fitplace", "sid|", "$", "2025", "2026", "900", "950"):
        assert leak not in body
    assert not re.search(r"\d{4}-\d{2}-\d{2}", body)


def test_a_no_prints_a_veto(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    conn = connect(db)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    rent = next(s for s in series if s.kind == "e-transfer")
    rs.set_rent_rule(conn, ACCOUNT, rent, RentRule((120000,), 1, 4), date(2026, 1, 5))
    conn.close()
    pr.main(["--db", str(db)])
    assert "rent: yes 0, no 1 -> VETO" in capsys.readouterr().out


def test_a_missing_database_is_reported_not_created(tmp_path: Path) -> None:
    assert pr.main(["--db", str(tmp_path / "nope.db")]) == 2
    assert not (tmp_path / "nope.db").exists()


def test_the_hand_entries_are_hidden_from_the_copy_before_the_proposers_run(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accept_everything(db)  # the entries exist in the database
    real = connect(db)
    present = {r[0] for r in real.execute("SELECT DISTINCT action FROM recurring_decisions")}
    assert {"rent", "continue", "add"} <= present
    assert current_proposals(real, ACCOUNT) == []  # with them there, nothing is proposed
    real.close()
    seen: list[set[str]] = []
    original = pr.current_proposals

    def spy(conn: sqlite3.Connection, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        seen.append({r[0] for r in conn.execute("SELECT DISTINCT action FROM recurring_decisions")})
        return original(conn, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pr, "current_proposals", spy)
    result = pr.recover(copy_of(db))
    assert seen and all(not ({"rent", "continue", "add"} & actions) for actions in seen)
    assert result == {"rent": (1, 0), "link": (1, 0), "hand-added": (1, 0)}  # so it can say yes
    after = connect(db)  # the real database still has them
    assert {"rent", "continue", "add"} <= {
        r[0] for r in after.execute("SELECT DISTINCT action FROM recurring_decisions")
    }
    after.close()


# --- found by the payments, not by the id; and a reason for each "no" ---

INSERT_SQL = (
    "INSERT INTO transactions (account, posted_date, amount_cents, description,"
    " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
    " VALUES (?, ?, ?, ?, ?, 0, ?, 'f', '2026-10-01')"
)


def insert_row(
    conn: sqlite3.Connection, day: date, cents: int, kind: str, text: str, fp: str
) -> None:
    conn.execute(INSERT_SQL, (ACCOUNT, day.isoformat(), cents, kind, text, fp))


def subscription_db(path: Path, new_cents: int) -> sqlite3.Connection:
    """Eight months of one subscription, then four months of another name."""
    conn = connect(path)
    for month in range(1, 13):
        name, cents = ("Fitplace ", 2500) if month <= 8 else ("Fitplace Online ", new_cents)
        insert_row(conn, month_day(month), -cents, "bill payment", name, f"s{month:03d}")
    conn.commit()
    return conn


def without_note(out: str) -> str:
    """The output minus the fixed 'not an independent check' line (which carries a change date)."""
    lines = [x for x in out.splitlines() if "not an independent check" not in x]
    return chr(10).join(lines)


def recover_text(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    assert pr.main(["--db", str(path), "--explain"]) == 0
    return capsys.readouterr().out


def test_a_rent_rule_on_the_old_amount_series_is_recovered_by_a_proposal_on_the_live_one(
    tmp_path: Path,
) -> None:
    """The proposal goes to the live series; my rule is on the stale one. Same payments: a yes."""
    payments = [(d, 90000) for d in RENT_DAYS[:8]] + [(d, 95000) for d in RENT_DAYS[8:]]
    conn = connect(tmp_path / "t.db")
    for n, (day, cents) in enumerate(payments):
        insert_row(conn, day, -cents, "withdrawal", "Free Interac E-Transfer ", f"r{n:03d}")
    conn.commit()
    series, _ = rs.series_as_of(conn, ACCOUNT)
    (proposal,) = current_proposals(conn, ACCOUNT)
    stale = next(s for s in series if not s.active)
    assert stale.id != proposal.series_id  # the two differ
    rs.set_rent_rule(conn, ACCOUNT, stale, proposal.rent, date(2026, 1, 5))  # type: ignore[arg-type]
    conn.close()
    assert pr.recover(copy_of(tmp_path / "t.db"))["rent"] == (1, 0)


def test_a_link_that_a_migration_left_naming_one_id_on_both_sides_is_still_found(db: Path) -> None:
    conn = connect(db)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    new = next(s for s in series if s.name == "Fitplace Online")
    old = next(s for s in series if s.name == "Fitplace")
    assert [p for p in current_proposals(conn, ACCOUNT) if p.kind == "link"]
    rs.link_continuation(conn, ACCOUNT, new, old, date(2026, 1, 5))
    with conn:  # what the migration did to a joined habit: the same id on both sides
        conn.execute(
            "UPDATE recurring_decisions SET details = json_object('previous', series_key)"
            " WHERE action = 'continue'"
        )
    conn.close()
    assert pr.recover(copy_of(db))["link"] == (1, 0)


def test_explain_gives_a_reason_for_a_rent_rule_with_other_amounts(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = connect(db)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    rent = next(s for s in series if s.kind == "e-transfer")
    rs.set_rent_rule(conn, ACCOUNT, rent, RentRule((120000,), 1, 4), date(2026, 1, 5))
    conn.close()
    out = recover_text(db, capsys)
    assert "rent: yes 0, no 1 -> VETO" in out
    assert "why not (1): forward_amount_differs - a proposal exists but its larger amount" in out


def test_explain_says_when_the_rent_stopped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = connect(tmp_path / "t.db")
    for n, day in enumerate(RENT_DAYS[:6]):  # the rent stops in June; the account goes on
        insert_row(conn, day, -90000, "withdrawal", "Free Interac E-Transfer ", f"r{n:03d}")
    for month in range(7, 13):
        shop = "Opos Cornershop         toron "
        insert_row(conn, month_day(month), -1500, "pos purchase", shop, f"c{month:03d}")
    conn.commit()
    series, _ = rs.series_as_of(conn, ACCOUNT)
    rent = next(s for s in series if s.kind == "e-transfer")
    rs.set_rent_rule(conn, ACCOUNT, rent, RentRule((90000,), 1, 4), date(2026, 1, 5))
    conn.close()
    out = recover_text(tmp_path / "t.db", capsys)
    assert "why not (1): outside_freshness_limit" in out


def test_explain_says_when_the_amounts_of_a_link_differ(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = subscription_db(tmp_path / "t.db", new_cents=3500)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    old = next(s for s in series if s.name == "Fitplace")
    new = next(s for s in series if s.name == "Fitplace Online")
    rs.link_continuation(conn, ACCOUNT, new, old, date(2026, 1, 5))
    conn.close()
    out = recover_text(tmp_path / "t.db", capsys)
    assert "link: yes 0, no 1 -> VETO" in out and "why not (1): amounts_differ" in out


def test_explain_says_when_i_ended_the_old_name_so_the_guard_blocks_the_proposal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = subscription_db(tmp_path / "t.db", new_cents=2500)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    old = next(s for s in series if s.name == "Fitplace")
    new = next(s for s in series if s.name == "Fitplace Online")
    rs.end(conn, ACCOUNT, old, date(2026, 1, 4))  # I ended the old name, then linked
    rs.link_continuation(conn, ACCOUNT, new, old, date(2026, 1, 5))
    conn.close()
    out = recover_text(tmp_path / "t.db", capsys)
    assert "why not (1): guard_blocked_by_end_or_reject" in out


def test_explain_says_when_a_hand_added_series_has_more_than_two_payments_but_is_no_series(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = connect(tmp_path / "t.db")
    for n, month in enumerate((1, 3, 4, 7)):  # irregular: detection finds no series
        insert_row(conn, month_day(month), -800, "bill payment", "Gadgetclub ", f"g{n:03d}")
    conn.commit()
    start, today = date(2025, 7, 8), date(2026, 1, 5)
    rs.add_series(conn, ACCOUNT, "Gadgetclub", "out", "monthly", start, 800, today)
    conn.close()
    out = recover_text(tmp_path / "t.db", capsys)
    assert "hand-added: yes 0, no 1 -> VETO" in out and "why not (1): not_two_payments" in out


def test_the_explanation_prints_codes_and_counts_only(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = connect(db)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    rent = next(s for s in series if s.kind == "e-transfer")
    rs.set_rent_rule(conn, ACCOUNT, rent, RentRule((120000,), 1, 4), date(2026, 1, 5))
    conn.close()
    out = recover_text(db, capsys)
    body = without_note(out)
    for leak in ("Zenstudio", "Fitplace", "Gadgetclub", "sid|", "$", "2025", "2026", "120000"):
        assert leak not in body.replace("$100 floor", "")
    shape = (
        r"(would the proposers.*|  (rent|link|hand-added): yes \d+, no \d+ -> .*"
        r"|    why not \(\d+\): [a-z_]+ - [a-z ,().:;-]+"
        r"|    detail: rent: forward amount same (yes|no); allowed amounts missing \d+, extra \d+;"
        r" payment days covered \d+ of \d+"
        r"|    detail: rent window: rule (all days|80 percent fallback); cluster send days \d+;"
        r" outside the best 14-day span \d+; window covers \d+ of \d+"
        r"|    detail: rent payments of my rule: \d+; not in the proposal's rent cluster: \d+)"
    )
    for line in without_note(out).splitlines():
        assert re.fullmatch(shape, line), line


# --- the migration re-merges a link's two names through the members table ---


def legacy_link_db(path: Path) -> sqlite3.Connection:
    """Two names of one subscription, and a decision log as it was before ids existed: a confirm
    on each, and 'the new one continues the old one'."""
    conn = subscription_db(path, new_cents=2500)
    rows = [
        ("merchant|out|Fitplace", "confirm", "2026-03-01"),
        ("merchant|out|Fitplace Online", "confirm", "2026-03-02"),
        ("merchant|out|Fitplace Online", "continue", "2026-03-03"),
    ]
    with conn:
        for key, action, day in rows:
            details = '{"previous": "merchant|out|Fitplace"}' if action == "continue" else "{}"
            conn.execute(
                "INSERT INTO recurring_decisions (account, series_key, action, details, decided_at)"
                " VALUES (?, ?, ?, ?, ?)", (ACCOUNT, key, action, details, day),
            )  # fmt: skip
    return conn


def test_a_migrated_link_is_found_although_the_members_table_still_joins_its_two_names(
    tmp_path: Path,
) -> None:
    """The migration remembers the payments of both names under one id; hiding the link decision
    must also undo that merge in the copy, or there are no two series left to link."""
    from lookahead import identity

    legacy_link_db(tmp_path / "t.db").close()
    identity.apply(tmp_path / "t.db", None)  # the real migration, as I ran it
    conn = connect(tmp_path / "t.db")
    assert len([s for s in rs.series_as_of(conn, ACCOUNT)[0] if "Fitplace" in (s.name or "")]) == 1
    conn.close()
    result, reasons, _ = pr.recover_detailed(copy_of(tmp_path / "t.db"))
    assert result["link"] == (1, 0), reasons["link"]


def test_the_explanation_says_how_many_series_detection_alone_finds_for_a_link(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Counts only: what the entry rests on, and what detection alone finds there."""
    from lookahead import identity

    legacy_link_db(tmp_path / "t.db").close()
    identity.apply(tmp_path / "t.db", None)
    out = recover_text(tmp_path / "t.db", capsys)
    assert "link: yes 1, no 0 -> ok" in out
    assert "detail: link entry rests on 12 payments; detection alone finds 2 series" in out
    assert "covering 12 of them" in out


# --- rent: amounts stored exactly as the real importer stores them ---


def import_bank_export(path: Path, rows: list[GeneratedTxn]) -> sqlite3.Connection:
    """A bank file written and read back by the real importer (signed integer cents)."""
    from datetime import datetime

    from lookahead.generator.writer import write_export
    from lookahead.ingest.scotiabank import parse_export
    from lookahead.ingest.store import store_exports

    export = path.parent / "export.csv"
    write_export(rows, 5_000_000, export)
    conn = connect(path)
    store_exports(conn, ACCOUNT, [(export.name, parse_export(export))], datetime(2026, 10, 1))
    return conn


def etransfer(day: date, cents: int) -> GeneratedTxn:
    return GeneratedTxn(
        posted_date=day, amount_cents=cents, txn_type="withdrawal", merchant="Interac e-Transfer",
        category="Rent & housing", flow_type="recurring_outflow", series_id="x-rent",
        description="withdrawal", sub_description="Free Interac E-Transfer ",
    )  # fmt: skip


def test_ordinary_rent_imported_the_way_the_real_importer_stores_it_is_proposed(
    tmp_path: Path,
) -> None:
    """Debits are negative integer cents; the proposer takes the size and compares it in cents."""
    rows = [etransfer(d, -(120000 if i < 8 else 125000)) for i, d in enumerate(RENT_DAYS)]
    conn = import_bank_export(tmp_path / "t.db", rows)
    stored = {r[0] for r in conn.execute("SELECT amount_cents FROM transactions")}
    assert stored == {-120000, -125000}  # signed cents, exactly
    (proposal,) = current_proposals(conn, ACCOUNT)
    assert proposal.rent is not None and proposal.rent.amounts_cents == (120000, 125000)
    conn.close()


def test_rent_among_many_small_unnamed_e_transfers_is_proposed(tmp_path: Path) -> None:
    """The floor is applied to the rent cluster, not to the median of the whole recipient group,
    so small e-Transfers to the same (or no) recipient no longer pull rent under $100."""
    rows = [etransfer(d, -120000) for d in RENT_DAYS]
    days = RENT_DAYS * 2
    small = [etransfer(d + timedelta(days=9), -(2500 + 100 * i)) for i, d in enumerate(days)]
    conn = import_bank_export(tmp_path / "t.db", sorted(rows + small, key=lambda t: t.posted_date))
    (proposal,) = [p for p in current_proposals(conn, ACCOUNT) if p.kind == "rent"]
    assert proposal.rent is not None and proposal.rent.amounts_cents == (120000,)
    assert len(proposal.evidence) == len(RENT_DAYS)  # the cluster, not the 36 payments
    conn.close()


def test_explain_gives_the_rent_recipient_profile_as_counts_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rent stopped in June but small e-Transfers go on to December: no proposal, because the
    rent cluster is stale. The profile counts payments only."""
    rows = [etransfer(d, -120000) for d in RENT_DAYS[:6]]
    rows += [etransfer(d + timedelta(days=9), -(2500 + 100 * i)) for i, d in enumerate(RENT_DAYS)]
    conn = import_bank_export(tmp_path / "t.db", sorted(rows, key=lambda t: t.posted_date))
    series, _ = rs.series_as_of(conn, ACCOUNT)
    big = max(series, key=lambda s: abs(s.expected_cents))
    rs.set_rent_rule(conn, ACCOUNT, big, RentRule((120000,), 1, 4), date(2026, 1, 5))
    conn.close()
    out = recover_text(tmp_path / "t.db", capsys)
    assert "why not (1): outside_freshness_limit" in out
    months = len({(d.year, d.month) for d in RENT_DAYS[:6]})
    profile = "detail: rent recipient has 18 payments, 6 of them at or above the $100 floor"
    assert f"{profile}, in {months} different months" in out
    for leak in ("120000", "1200", "2500", "2025", "2026"):
        assert leak not in without_note(out)


def test_the_veto_output_says_the_rerun_is_not_an_independent_check(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert pr.main(["--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "not an independent check" in out and "rent cluster" in out and "5 percent" in out


# --- when a rent proposal recovers my rent rule (DECISIONS.md, 2026-10-22) ---


def rent_rule_with(db: Path, amounts: tuple[int, ...], first: int, last: int) -> None:
    conn = connect(db)
    series, _ = rs.series_as_of(conn, ACCOUNT)
    rent = next(s for s in series if s.kind == "e-transfer")
    rs.set_rent_rule(conn, ACCOUNT, rent, RentRule(amounts, first, last), date(2026, 1, 5))
    conn.close()


def rent_line(db: Path, capsys: pytest.CaptureFixture[str]) -> tuple[str, str]:
    out = recover_text(db, capsys)
    line = next(x for x in out.splitlines() if "detail: rent:" in x)
    return out, line.strip().removeprefix("detail: ")


def test_the_same_forward_amount_and_a_covering_window_recover_the_rule(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rent_rule_with(db, (95000,), 1, 4)  # the proposal also allows the older 90000: one extra
    out, line = rent_line(db, capsys)
    assert "rent: yes 1, no 0 -> ok" in out
    assert line == (
        "rent: forward amount same yes; allowed amounts missing 0, extra 1; "
        "payment days covered 10 of 10"
    )


def test_an_extra_old_amount_in_my_rule_is_reported_and_does_not_veto(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rent_rule_with(db, (80000, 95000), 1, 4)  # an old amount the proposal does not have
    out, line = rent_line(db, capsys)
    assert "rent: yes 1, no 0 -> ok" in out
    assert line.startswith("rent: forward amount same yes; allowed amounts missing 1, extra 1;")


def test_a_different_forward_amount_vetoes(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rent_rule_with(db, (90000, 99000), 1, 4)  # the larger amount is not 95000
    out, line = rent_line(db, capsys)
    assert "rent: yes 0, no 1 -> VETO" in out and "why not (1): forward_amount_differs" in out
    assert line.startswith("rent: forward amount same no;")


def test_the_proposal_window_now_covers_every_send_day_so_a_wide_rule_of_mine_is_recovered(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Before the window change (DECISIONS.md, 2026-10-22) the proposal's window was 1 to 4 and
    missed the 30th and the 31st, which a wide rule of mine (1 to 31) contains: a veto. The window
    now covers all twelve send days, so it is recovered."""
    rent_rule_with(db, (95000,), 1, 31)
    out, line = rent_line(db, capsys)
    assert "rent: yes 1, no 0 -> ok" in out and line.endswith("payment days covered 12 of 12")


def test_a_window_that_misses_payment_days_inside_my_window_vetoes() -> None:
    """The veto itself is unchanged (3590bcc): a proposal window of 1 to 4 misses a payment on the
    30th that lies inside my window 28 to 4."""
    proposal = SettingProposal(
        "rent", "sid|x", "rent|sid|x", "t", (), "likely", rent=RentRule((95000,), 1, 4)
    )
    mine = RentRule((95000,), 28, 4)
    comparison = pr.compare_rent(mine, proposal, [1, 2, 3, 4, 30])
    assert (comparison.covered, comparison.inside) == (4, 5) and not comparison.recovered
    assert comparison.reason == "window_misses_payment_days"


def test_a_narrower_window_of_mine_that_the_proposal_covers_does_not_veto(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rent_rule_with(db, (95000,), 1, 3)  # the proposal's window is wider: reported, not a veto
    out, line = rent_line(db, capsys)
    assert "rent: yes 1, no 0 -> ok" in out and "payment days covered 9 of 9" in line


def test_the_rent_detail_line_is_counts_only(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rent_rule_with(db, (80000, 95000), 1, 4)
    out, line = rent_line(db, capsys)
    assert re.fullmatch(
        r"rent: forward amount same (yes|no); allowed amounts missing \d+, extra \d+; "
        r"payment days covered \d+ of \d+",
        line,
    )
    for leak in ("80000", "95000", "900", "950", "2025", "2026"):
        assert leak not in without_note(out)


# --- which window rule the proposal used (counts only) ---

WINDOW_LINE = re.compile(
    r"rent window: rule (all days|80 percent fallback); cluster send days (\d+); "
    r"outside the best 14-day span (\d+); window covers (\d+) of (\d+)"
)


def detail_lines(db: Path, capsys: pytest.CaptureFixture[str]) -> list[str]:
    out = recover_text(db, capsys)
    return [x.strip().removeprefix("detail: ") for x in out.splitlines() if "detail: rent" in x]


def test_the_window_line_says_all_days_when_a_14_day_span_covers_every_send_day(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rent_rule_with(db, (95000,), 1, 4)
    lines = detail_lines(db, capsys)
    match = WINDOW_LINE.fullmatch(next(x for x in lines if x.startswith("rent window")))
    assert match is not None
    rule, days, outside, covered, of = match.groups()
    assert (rule, days, outside, covered, of) == ("all days", "12", "0", "12", "12")
    assert "rent payments of my rule: 12; not in the proposal's rent cluster: 0" in lines


def stray_day_rent(path: Path, distinct_small: bool = False) -> sqlite3.Connection:
    """Twelve rent payments, eleven between the 30th and the 4th and one on the 18th, among small
    e-Transfers (all of different amounts if `distinct_small`)."""
    days = [d if i != 5 else date(2025, 6, 18) for i, d in enumerate(RENT_DAYS)]
    rows = [etransfer(d, -(120000 if i < 8 else 125000)) for i, d in enumerate(days)]

    def amount(i: int) -> int:
        return 2500 + 100 * i if distinct_small else 2500 + 100 * (i % 5)

    start = date(2025, 1, 12)
    small = [etransfer(start + timedelta(days=5 * i), -amount(i)) for i in range(60)]
    return import_bank_export(path, sorted(rows + small, key=lambda t: t.posted_date))


def test_the_window_line_says_80_percent_fallback_when_no_14_day_span_fits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = stray_day_rent(tmp_path / "t.db")
    series, _ = rs.series_as_of(conn, ACCOUNT)
    rent = max(series, key=lambda s: abs(s.expected_cents))
    rs.set_rent_rule(conn, ACCOUNT, rent, RentRule((125000,), 30, 4), date(2026, 1, 5))
    conn.close()
    lines = detail_lines(tmp_path / "t.db", capsys)
    match = WINDOW_LINE.fullmatch(next(x for x in lines if x.startswith("rent window")))
    assert match is not None
    rule, days, outside, covered, of = match.groups()
    assert rule == "80 percent fallback" and days == of == "12" and outside == "1"
    assert covered == "10"  # the 80 percent window (31 to 3) leaves out the 30th and the stray 18th


def test_the_line_counts_my_rule_payments_that_are_not_in_the_rent_cluster(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A rule that also allows an amount at or above the floor, paid now and then outside the rent
    (an amount under the floor is no longer used to find e-Transfers), counts those payments."""
    conn = stray_day_rent(tmp_path / "t.db", distinct_small=True)
    friend = [etransfer(date(2025, m, 20), -15000) for m in (2, 5, 8)]
    for n, t in enumerate(friend):
        conn.execute(
            "INSERT INTO transactions (account, posted_date, amount_cents, description,"
            " sub_description, balance_after_cents, fingerprint, source_file, imported_at)"
            " VALUES (?, ?, ?, 'withdrawal', 'Free Interac E-Transfer ', 0, ?, 'f', 'x')",
            (ACCOUNT, t.posted_date.isoformat(), t.amount_cents, f"friend{n}"),
        )
    conn.commit()
    series, _ = rs.series_as_of(conn, ACCOUNT)
    rent = max(series, key=lambda s: abs(s.expected_cents))
    rs.set_rent_rule(
        conn, ACCOUNT, rent, RentRule((120000, 125000, 15000), 30, 4), date(2026, 1, 5)
    )
    conn.close()
    lines = detail_lines(tmp_path / "t.db", capsys)
    mine = next(x for x in lines if x.startswith("rent payments of my rule"))
    pattern = r"rent payments of my rule: (\d+); not in the proposal's rent cluster: (\d+)"
    found = re.fullmatch(pattern, mine)
    assert found is not None and int(found.group(2)) > 0


def test_the_window_lines_are_counts_only(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rent_rule_with(db, (95000,), 1, 4)
    out = recover_text(db, capsys)
    for line in without_note(out).splitlines():
        if "rent window" in line or "rent payments of my rule" in line:
            assert not re.search(r"\$|\d{4}|sid\|", line.replace("14-day", ""))


def test_the_veto_note_lists_the_changes_of_both_days(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert pr.main(["--db", str(db)]) == 0
    note = next(x for x in capsys.readouterr().out.splitlines() if "not an independent check" in x)
    for word in ("2026-10-21", "2026-10-22", "rent cluster", "5 percent", "planter version 3",
                 "newest rent amount", "rent window"):  # fmt: skip
        assert word in note
