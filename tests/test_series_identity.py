"""The identity rules on plain values (lookahead.series_identity). Made-up keys and dates."""

from datetime import date, timedelta

from lookahead.series_identity import (
    Habit,
    assign_ids,
    family_of,
    head_of,
    is_id,
    successors,
)

START = date(2026, 1, 5)


def habit(key: str, first: int, last: int, fp: str = "f1", cadence: str = "weekly") -> Habit:
    return Habit(key, fp, START + timedelta(days=first), START + timedelta(days=last), cadence)


def gym(first: int, last: int, fp: str, amount: int) -> Habit:
    return habit(f"merchant|out|GYM|{amount}", first, last, fp)


def test_the_family_drops_a_trailing_amount_only() -> None:
    assert family_of("merchant|out|CAFE|1204") == "merchant|out|CAFE"
    assert family_of("merchant|out|CAFE") == "merchant|out|CAFE"
    assert family_of("own transfer|out|5000") == "own transfer|out"
    assert family_of("e-transfer|out|tok|-2500") == "e-transfer|out|tok"
    assert family_of("merchant|out|7 ELEVEN") == "merchant|out|7 ELEVEN"


def test_an_id_does_not_move_when_the_amount_in_the_key_moves() -> None:
    before = assign_ids([habit("merchant|out|CAFE|1204", 0, 60, "oldest")])
    after = assign_ids([habit("merchant|out|CAFE|1200", 0, 90, "oldest")])
    assert before["merchant|out|CAFE|1204"] == after["merchant|out|CAFE|1200"]
    assert is_id(after["merchant|out|CAFE|1200"])


def test_an_id_follows_the_oldest_payment_and_the_family() -> None:
    a = assign_ids([habit("merchant|out|CAFE|1200", 0, 60, "x")])["merchant|out|CAFE|1200"]
    b = assign_ids([habit("merchant|out|CAFE|1200", 0, 60, "y")])["merchant|out|CAFE|1200"]
    c = assign_ids([habit("merchant|out|BAKERY|1200", 0, 60, "x")])["merchant|out|BAKERY|1200"]
    assert len({a, b, c}) == 3


def test_two_habits_that_would_share_an_id_do_not() -> None:
    ids = assign_ids(
        [habit("merchant|out|CAFE|1200", 0, 60, "x"), habit("merchant|out|CAFE|900", 0, 60, "x")]
    )
    assert len(set(ids.values())) == 2


def test_a_new_cluster_carries_on_an_older_one_that_stopped() -> None:
    old, new = gym(0, 100, "a", 4000), gym(107, 200, "b", 4500)
    chain = successors([old, new])
    assert chain == {old.key: new.key}
    assert head_of(old.key, chain) == new.key and head_of(new.key, chain) == new.key


def test_not_when_the_gap_is_longer_than_two_periods() -> None:
    old, new = gym(0, 100, "a", 4000), gym(100 + 15, 200, "b", 4500)
    assert successors([old, new]) == {}


def test_not_when_they_overlap_in_time() -> None:
    """Two habits at one merchant paid in the same weeks are two habits, not one."""
    one, two = gym(0, 100, "a", 4000), gym(50, 150, "b", 9000)
    assert successors([one, two]) == {}


def test_not_from_a_series_i_ended_or_rejected() -> None:
    old, new = gym(0, 100, "a", 4000), gym(107, 200, "b", 4500)
    assert successors([old, new], blocked={old.key}) == {}


def test_not_across_families_or_cadences() -> None:
    old = habit("merchant|out|GYM|4000", 0, 100, "a")
    assert successors([old, habit("merchant|out|POOL|4000", 107, 200, "b")]) == {}
    assert successors([old, habit("merchant|out|GYM|4500", 107, 200, "b", "monthly")]) == {}


def test_one_to_one_nearest_first() -> None:
    old1, old2 = habit("merchant|out|A|1", 0, 50, "a"), habit("merchant|out|A|2", 0, 60, "b")
    new = habit("merchant|out|A|3", 64, 200, "c")
    chain = successors([old1, old2, new])
    assert chain == {old2.key: new.key}  # the nearer one; the other finds nobody


def test_a_chain_of_two_price_changes_has_one_head() -> None:
    a, b, c = gym(0, 50, "a", 1), gym(56, 110, "b", 2), gym(116, 200, "c", 3)
    chain = successors([a, b, c])
    assert head_of(a.key, chain) == head_of(b.key, chain) == c.key
