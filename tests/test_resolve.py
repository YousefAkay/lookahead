"""Tests for merchant resolution rules. Made-up and generator-style names only."""

import pytest

from lookahead.resolve import (
    NameInfo,
    UserDecisions,
    name_infos,
    name_key,
    resolve,
    similarity,
)


def names(*items: str | NameInfo) -> list[NameInfo]:
    return [i if isinstance(i, NameInfo) else NameInfo(i) for i in items]


def group_of(result, name: str) -> str:  # type: ignore[no-untyped-def]
    return result.group_of(name)


# --- Keys and similarity ---


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Uber Canada/Ubereats", "uber canada ubereats"),
        ("Couche-Tard", "couche tard"),
        ("Amazon.Ca", "amazon ca"),
        ("Caf? Velisaro", "caf? velisaro"),  # the bank's "?" is kept: it stands for a letter
        ("Café  Velisaro", "cafe velisaro"),
        ("COUCHE TARD", "couche tard"),
    ],
)
def test_name_key(name: str, key: str) -> None:
    assert name_key(name) == key


def test_similarity_bounds_and_symmetry() -> None:
    assert similarity("Tim Hortons", "TIM HORTONS") == 1.0
    assert similarity("Maxi", "Netflix.Com") == 0.0
    a, b = "Uber Canada/Ubereats", "Uber Canada/Ubertrip"
    assert 0.0 < similarity(a, b) == similarity(b, a) < 1.0


# --- Automatic rules ---


def test_same_text_ignoring_case_and_punctuation_merges() -> None:
    r = resolve(names("Couche-Tard", "COUCHE TARD"))
    assert len(r.groups) == 1 and r.merged_by["same text"] == 1


def test_bank_question_mark_matches_an_accent() -> None:
    r = resolve(names("Caf? Velisaro", "Cafe Velisaro", "Café Velisaro"))
    assert len(r.groups) == 1 and r.merged_by["accents"] >= 1


def test_question_mark_does_not_hide_other_differences() -> None:
    r = resolve(names("Caf? Velisaro", "Cafe Velysaro"))  # one letter apart besides the "?"
    assert len(r.groups) == 2


def test_alias_list_joins_known_bank_spellings() -> None:
    r = resolve(names("Uber Canada/Ubereats", "Uber Eats", "Uber Canada/Ubertrip"))
    assert r.group_of("Uber Canada/Ubereats") == r.group_of("Uber Eats") == "Uber Eats"
    assert r.group_of("Uber Canada/Ubertrip") == "Uber"  # rides are a different merchant
    assert r.merged_by["alias list"] == 1


def test_cut_name_that_starts_exactly_one_longer_name_merges() -> None:
    cut = NameInfo("Caf? Quorvel Hochela", cut=True)
    assert len(cut.name) == 20
    r = resolve(names(cut, "Caf? Quorvel Hochelaga"))
    assert len(r.groups) == 1 and r.merged_by["cut prefix"] == 1


def test_cut_prefix_ignores_case_punctuation_and_accents() -> None:
    cut = NameInfo("Pharmaprix Saint-Lau", cut=True)
    r = resolve(names(cut, "PHARMAPRIX SAINT LAURENT"))
    assert len(r.groups) == 1


def test_cut_name_that_starts_two_different_names_is_only_proposed() -> None:
    cut = NameInfo("Pharmaprix Saint-Lau", cut=True)
    r = resolve(names(cut, "Pharmaprix Saint-Laurent", "Pharmaprix Saint-Lauzon"))
    assert len(r.groups) == 3
    reasons = {(p.reason, frozenset((p.a, p.b))) for p in r.proposals}
    assert ("cut prefix, ambiguous", frozenset((cut.name, "Pharmaprix Saint-Laurent"))) in reasons
    assert ("cut prefix, ambiguous", frozenset((cut.name, "Pharmaprix Saint-Lauzon"))) in reasons


def test_a_prefix_that_is_not_at_the_cut_length_is_only_proposed() -> None:
    # 19 characters: the bank may have cut it at a space, or it may be the whole name.
    r = resolve(names("Restaurant Le Petit", "Restaurant Le Petit Orme"))
    assert len(r.groups) == 2
    assert [p.reason for p in r.proposals] == ["prefix"]


def test_one_word_prefix_is_not_a_prefix_proposal() -> None:
    r = resolve(names("Maxi", "Maxi Dollar"), similar_at=1.0)
    assert len(r.groups) == 2 and r.proposals == []


def test_prefix_must_end_at_a_word_boundary_unless_cut() -> None:
    r = resolve(names("Super Cafe Velo", "Super Cafe Velours"), similar_at=1.0)
    assert r.proposals == []


# --- Similarity only proposes ---


def test_similar_names_are_proposed_not_merged() -> None:
    r = resolve(names("Jean Coutu", "Jean Coutou"), similar_at=0.5)
    assert len(r.groups) == 2
    assert [(p.reason, p.score > 0.5) for p in r.proposals] == [("similar names", True)]


def test_below_the_threshold_nothing_is_proposed() -> None:
    r = resolve(names("Jean Coutu", "Netflix.Com"), similar_at=0.5)
    assert r.proposals == []


def test_one_proposal_per_pair_of_groups() -> None:
    r = resolve(names("Jean Coutu", "JEAN-COUTU", "Jean Coutou"), similar_at=0.5)
    assert len(r.groups) == 2 and len(r.proposals) == 1


# --- Your decisions ---


def test_your_merge_joins_names_the_rules_would_not() -> None:
    mine = UserDecisions(merges={"Uber Eats": {name_key("Uber Canada/Ubereats"), "ue delivery"}})
    r = resolve(names("Uber Canada/Ubereats", "UE Delivery"), user=mine)
    assert r.group_of("UE Delivery") == r.group_of("Uber Canada/Ubereats") == "Uber Eats"
    assert r.merged_by["your merges"] == 1


def test_your_split_overrides_every_rule() -> None:
    split = frozenset((name_key("Caf? Velisaro"), name_key("Cafe Velisaro")))
    r = resolve(names("Caf? Velisaro", "Cafe Velisaro"), user=UserDecisions(splits={split}))
    assert len(r.groups) == 2 and r.proposals == []


def test_your_split_also_stops_the_pair_being_proposed() -> None:
    split = frozenset((name_key("Jean Coutu"), name_key("Jean Coutou")))
    r = resolve(
        names("Jean Coutu", "Jean Coutou"), user=UserDecisions(splits={split}), similar_at=0.5
    )
    assert r.proposals == []


# --- Group names and counts ---


def test_group_is_named_after_its_most_used_name() -> None:
    r = resolve(names(NameInfo("COUCHE TARD", rows=2), NameInfo("Couche-Tard", rows=9)))
    assert r.group_of("COUCHE TARD") == "Couche-Tard"


def test_group_name_prefers_a_real_accent_over_a_question_mark() -> None:
    r = resolve(names(NameInfo("Caf? Velisaro", rows=5), NameInfo("Cafe Velisaro", rows=5)))
    assert r.group_of("Caf? Velisaro") == "Cafe Velisaro"


def test_auto_merges_are_listed_for_review() -> None:
    r = resolve(names("Couche-Tard", "COUCHE TARD", "Maxi"))
    assert [(p.reason, {p.a, p.b}) for p in r.auto_pairs] == [
        ("same text", {"Couche-Tard", "COUCHE TARD"})
    ]


def test_names_before_ignores_case() -> None:
    r = resolve(names("Maxi", "MAXI", "Metro"))
    assert r.names_before == 2 and len(r.groups) == 2


# --- From bank text ---


def test_name_infos_counts_rows_and_flags_cut_card_names() -> None:
    texts = [
        "Apos Caf? Quorvel Hochela Montr ",  # 20-character card name: cut
        "Apos Caf? Quorvel Hochela Montr ",
        "Concordia University ",  # 20 characters, but a payee: not cut
        "Free Interac E-Transfer ",  # no merchant
    ]
    infos = {i.name: i for i in name_infos(texts)}
    assert infos["Caf? Quorvel Hochela"] == NameInfo("Caf? Quorvel Hochela", rows=2, cut=True)
    assert infos["Concordia University"].cut is False
    assert len(infos) == 2


# --- TF-IDF similarity: common chunks count less ---


def test_a_common_word_counts_less_when_many_names_share_it() -> None:
    corpus = ["Bell Canada", "Ticketmaster Canada", "Rogers Canada", "Maple Canada", "Pizza Canada"]
    alone = similarity("Bell Canada", "Ticketmaster Canada")
    in_corpus = similarity("Bell Canada", "Ticketmaster Canada", corpus=corpus)
    assert in_corpus < alone


def test_names_sharing_only_a_common_word_are_not_proposed() -> None:
    corpus = ["Bell Canada", "Ticketmaster Canada", "Rogers Canada", "Maple Canada", "Pizza Canada"]
    r = resolve(names(*corpus))
    assert r.proposals == []


def test_real_variants_still_score_high_in_a_corpus() -> None:
    corpus = ["Jean Coutu", "Jean Coutou", "Bell Canada", "Rogers Canada", "Maxi", "Metro"]
    assert similarity("Jean Coutu", "Jean Coutou", corpus=corpus) > 0.5


# --- Delivery platform orders resolve to the platform ---


def test_delivery_orders_from_many_restaurants_are_one_merchant() -> None:
    texts = [
        f"Opos {'Dd/Doordash' + r:<20}+1855 " for r in ("thaiexpres", "pizzaroma", "phobac")
    ] + [f"Opos {'Dd/Doordashdashpass':<20}+1855 "]
    r = resolve(name_infos(texts))
    assert sorted(g.name for g in r.groups) == ["DoorDash", "DoorDash DashPass"]
    assert r.names_before == 2


# --- The rides alias only catches ride names ---


def test_uber_rides_alias_only_catches_rides() -> None:
    r = resolve(
        names(
            "Uber Canada/Ubertrip", "Uber Trip", "Ubertrip",
            "Uber pending, unknown service", "Uber, unknown service",
            "Uber Eats", "Uber Canada/Ubereats", "Uber One",
        )
    )  # fmt: skip
    rides = {n for g in r.groups if g.name == "Uber" for n in g.members}
    assert rides == {"Uber Canada/Ubertrip", "Uber Trip", "Ubertrip"}
    assert r.group_of("Uber pending, unknown service") != r.group_of("Uber Eats")
    assert r.group_of("Uber Canada/Ubereats") == "Uber Eats"


def test_two_groups_never_share_a_display_name() -> None:
    # Your merge named "Maxi" and a separate merchant called "Maxi" must stay two groups
    # downstream (labels and the lookup use the group's name).
    mine = UserDecisions(merges={"Maxi": {"iga", "iga extra"}})
    r = resolve(names("Maxi", "Iga", "Iga Extra"), user=mine)
    assert len(r.groups) == 2
    assert len({g.name for g in r.groups}) == 2
    assert r.group_of("Maxi") != r.group_of("Iga")
