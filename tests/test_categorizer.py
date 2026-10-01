"""Tests for the categorizer's features, models and the synthetic splits. Made-up rows only."""

from datetime import date, timedelta

import pandas as pd
import pytest

from lookahead.categorizer import (
    FEATURE_COLUMNS,
    MerchantLookup,
    amount_bucket,
    examples,
    make_logreg,
    make_svm,
)
from lookahead.flows import Txn

D = date(2026, 9, 10)


def card(merchant: str, prefix: str = "Apos", location: str = "Montr") -> str:
    return f"{prefix} {merchant[:20]:<20}{location:<5} "


# --- Features ---


@pytest.mark.parametrize(
    ("cents", "bucket"),
    [
        (-999, "under 10"),
        (-1_000, "10-30"),
        (2_999, "10-30"),
        (-3_000, "30-100"),
        (-10_000, "100-500"),
        (-49_999, "100-500"),
        (-50_000, "500+"),
        (0, "under 10"),
    ],
)
def test_amount_bucket(cents: int, bucket: str) -> None:
    assert amount_bucket(cents) == bucket


def test_examples_are_only_the_rows_left_for_the_categorizer() -> None:
    rows = [
        Txn(1, D, -6_000, "abm withdrawal", " "),  # rule: Cash
        Txn(2, D, -1_000, "pos purchase", card("Couche-Tard #496")),  # default: Groceries
        Txn(3, D, -1_200, "pos purchase", card("Cafe Velo")),  # left
        Txn(4, D, -9_500, "bill payment", "Mb-Hydro-Quebec 55231204"),  # left
        Txn(5, D, -5_000, "pos purchase", card("Winners #9074")),  # left
        Txn(6, D + timedelta(days=3), 2_000, "pos return", card("Winners #9074")),  # waits
        Txn(7, D, -2_999, "pos purchase", card("Best Buy #12")),  # fully returned: netted
        Txn(8, D + timedelta(days=3), 2_999, "pos return", card("Best Buy #12")),  # netted
    ]
    df = examples(rows)
    assert list(df["row_id"]) == [3, 4, 5]
    assert set(FEATURE_COLUMNS) <= set(df.columns)


def test_example_features() -> None:
    rows = [Txn(1, D, -1_250, "pos purchase", card("Cafe Velo", "Opos"))]
    ex = examples(rows).iloc[0]
    assert ex["name"] == "Cafe Velo"
    assert ex["channel"] == "online" and ex["txn_type"] == "pos purchase"
    assert ex["direction"] == "out" and ex["amount_bucket"] == "10-30"
    assert ex["group"] == "Cafe Velo"


def test_named_rows_have_no_channel() -> None:
    ex = examples([Txn(1, D, -9_500, "bill payment", "Mb-Hydro-Quebec 55231204")]).iloc[0]
    assert ex["channel"] == "none" and ex["name"] == "Hydro-Quebec"


def test_no_rows_left_gives_an_empty_frame_with_columns() -> None:
    df = examples([Txn(1, D, -6_000, "abm withdrawal", " ")])
    assert df.empty and set(FEATURE_COLUMNS) <= set(df.columns)


# --- Models ---


def toy() -> tuple[pd.DataFrame, list[str]]:
    rows = []
    for i, store in enumerate(["#12", "#88", "#401", "#77"]):
        rows.append(Txn(i, D, -4_000, "pos purchase", card(f"Maxi {store}")))
        rows.append(Txn(10 + i, D, -450, "pos purchase", card(f"Tim Hortons {store}")))
        rows.append(Txn(20 + i, D, -320, "pos purchase", card(f"Stm-Opus {store}")))
    df = examples(rows)
    labels = {
        "Maxi": "Groceries",
        "Tim Hortons": "Restaurants & cafés",
        "STM": "Transport",
    }  # Stm-Opus resolves to STM
    return df, [labels[g] for g in df["group"]]


def test_merchant_lookup_reuses_a_known_merchants_label() -> None:
    df, y = toy()
    model = MerchantLookup().fit(df, y)
    new = examples([Txn(99, D, -900, "pos purchase", card("Maxi #999"))])
    assert list(model.predict(new)) == ["Groceries"]


def test_merchant_lookup_falls_back_to_the_most_common_label() -> None:
    df, y = toy()
    y[0] = "Groceries"  # all three labels appear 4 times; make Groceries the most common
    y[4] = "Groceries"
    model = MerchantLookup().fit(df, y)
    unseen = examples([Txn(99, D, -900, "pos purchase", card("Cafe Velo"))])
    assert list(model.predict(unseen)) == ["Groceries"]


@pytest.mark.parametrize("make", [make_svm, make_logreg])
def test_text_models_generalize_to_new_store_numbers(make) -> None:  # type: ignore[no-untyped-def]
    df, y = toy()
    model = make().fit(df, y)
    new = examples(
        [
            Txn(97, D, -3_000, "pos purchase", card("Maxi Express")),
            Txn(98, D, -500, "pos purchase", card("Tim Hortons Lasalle")),
        ]
    )
    assert list(model.predict(new)) == ["Groceries", "Restaurants & cafés"]


def test_logistic_regression_gives_probabilities() -> None:
    df, y = toy()
    model = make_logreg().fit(df, y)
    proba = model.predict_proba(df)
    assert proba.shape == (len(df), 3)
    assert ((proba.sum(axis=1) - 1).__abs__() < 1e-9).all()


# --- Synthetic splits (eval/categorizer.py) ---


@pytest.fixture(scope="module")
def synthetic() -> pd.DataFrame:
    from eval.categorizer import synthetic_examples

    return synthetic_examples(months=4)


def test_synthetic_examples_carry_labels_and_merchants(synthetic: pd.DataFrame) -> None:
    assert {"label", "merchant", "persona"} <= set(synthetic.columns)
    assert set(synthetic["persona"]) == {"A", "B", "C"}
    assert synthetic["row_id"].is_unique
    assert "Cash" not in set(synthetic["label"])  # rule-decided categories never get here


def test_time_split_is_per_persona_and_respects_dates(synthetic: pd.DataFrame) -> None:
    from eval.categorizer import time_split

    train = time_split(synthetic, share=0.7)
    for _, rows in synthetic.groupby("persona"):
        in_train = train[rows.index]
        assert rows.loc[in_train, "posted_date"].max() < rows.loc[~in_train, "posted_date"].min()
        assert 0.5 < in_train.mean() < 0.85


def test_grouped_folds_hold_out_each_merchant_once(synthetic: pd.DataFrame) -> None:
    from eval.categorizer import grouped_folds

    tested = []
    for train_idx, test_idx in grouped_folds(synthetic, k=4):
        train_m = set(synthetic.iloc[train_idx]["merchant"])
        test_m = set(synthetic.iloc[test_idx]["merchant"])
        assert not train_m & test_m
        tested += list(test_idx)
    assert sorted(tested) == list(range(len(synthetic)))


# --- Counts-only report ---


def test_report_prints_counts_only(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    import re
    from datetime import datetime

    from lookahead.categorizer import main
    from lookahead.db import connect
    from lookahead.generator.personas import PERSONAS
    from lookahead.generator.writer import write_persona
    from lookahead.ingest.scotiabank import parse_export
    from lookahead.ingest.store import store_exports

    export, _ = write_persona(
        PERSONAS["C"], tmp_path / "s", 42, date(2026, 6, 1), date(2026, 9, 30)
    )
    path = tmp_path / "t.db"
    conn = connect(path)
    store_exports(conn, "synthetic-C", [(export.name, parse_export(export))], datetime(2026, 10, 2))
    conn.commit()
    conn.close()
    assert main(["report", "--db", str(path), "--account", "synthetic-C"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"rows left for the categorizer \(with a merchant name\): \d+", out)
    assert re.search(r"merchant groups among them: \d+", out)
    assert not re.search(r"\d+\.\d{2}", out) and "$" not in out
    for row in parse_export(export):
        if row.sub_description.strip():
            assert row.sub_description.strip() not in out


def test_report_reads_the_stored_labels(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    from datetime import datetime

    from lookahead.categorizer import main
    from lookahead.db import connect
    from lookahead.generator.personas import PERSONAS
    from lookahead.generator.writer import write_persona
    from lookahead.ingest.scotiabank import parse_export
    from lookahead.ingest.store import store_exports
    from lookahead.labels import freeze_splits, label_merchant, load_rows

    export, _ = write_persona(
        PERSONAS["C"], tmp_path / "s", 42, date(2026, 6, 1), date(2026, 9, 30)
    )
    path = tmp_path / "t.db"
    conn = connect(path)
    store_exports(conn, "synthetic-C", [(export.name, parse_export(export))], datetime(2026, 10, 2))
    conn.commit()
    freeze_splits(conn, "synthetic-C", seed=7)
    group = load_rows(conn, "synthetic-C")["group"].iloc[0]
    n = label_merchant(conn, "synthetic-C", group, "Groceries")
    conn.close()
    main(["report", "--db", str(path), "--account", "synthetic-C"])
    out = capsys.readouterr().out
    assert f"labels: labelled {n} rows, skipped 0, cleared 0" in out
    assert "merchants done: 1 of" in out
    assert "labels stored: 0" not in out


def test_report_before_the_splits_are_frozen(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    from lookahead.categorizer import main
    from lookahead.db import connect

    path = tmp_path / "empty.db"
    connect(path).close()
    main(["report", "--db", str(path), "--account", "x"])
    assert "splits not frozen yet" in capsys.readouterr().out
