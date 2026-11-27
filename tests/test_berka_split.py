"""The Berka split (eval/berka_split.py): decided by the account id alone."""

import json
from pathlib import Path

import pandas as pd
import pytest

from eval import berka_split as split
from eval.berka_split import compute, digest, pool_of


def frame(n: int = 1500) -> pd.DataFrame:
    ids = range(1, n + 1)
    return pd.DataFrame(
        {
            "months": [40.0] * n,
            "real_per_month": [5.0 if i % 3 else 2.5 for i in ids],  # 2/3 primary, 1/3 sensitivity
            "longest_real_gap": [30.0] * n,
        },
        index=pd.Index(list(ids), name="account_id"),
    )


def test_the_pool_depends_on_the_id_only() -> None:
    assert pool_of(576) == pool_of(576) and digest(576) != digest(577)
    pools = {pool_of(i) for i in range(1, 50)}
    assert pools == {"development", "held_out"}


def test_the_split_is_disjoint_capped_and_ordered_by_hash() -> None:
    out = compute(frame())
    lists = {k: v for k, v in out.items() if k.endswith(("development", "held_out"))}
    assert set(lists) == {
        "primary_development", "primary_held_out", "sensitivity_development",
        "sensitivity_held_out",
    }  # fmt: skip
    assert all(len(v) <= split.PER_POOL for v in lists.values())  # type: ignore[arg-type]
    everyone = [a for v in lists.values() for a in v]  # type: ignore[union-attr]
    assert len(everyone) == len(set(everyone))  # no account twice, in no two lists
    assert all(pool_of(a) == "development" for a in lists["primary_development"])  # type: ignore[union-attr]
    assert all(pool_of(a) == "held_out" for a in lists["primary_held_out"])  # type: ignore[union-attr]
    assert out["primary_filter_accounts"] == 1000 and out["sensitivity_filter_accounts"] == 500


def test_accounts_below_the_filter_are_never_taken() -> None:
    acc = frame(300)
    acc.loc[acc.index[:100], "months"] = 10.0  # too short
    out = compute(acc)
    taken = {a for k, v in out.items() if isinstance(v, list) for a in v}
    assert not taken & set(acc.index[:100])


def test_the_split_does_not_move_when_the_rows_come_in_another_order() -> None:
    acc = frame(400)
    assert compute(acc) == compute(acc.sample(frac=1.0, random_state=3))


def test_a_held_out_account_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from eval import fresh_set

    monkeypatch.setattr(fresh_set, "LIST_FILE", tmp_path / "none.json")  # made-up ids only
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"primary_held_out": [7], "sensitivity_held_out": [9]}))
    with pytest.raises(ValueError):
        split.assert_development_only([1, 7], path)
    split.assert_development_only([1, 2], path)  # development ids pass
