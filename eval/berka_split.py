"""The Berka account split: fixed by a hash of the account id, committed before any forecast.

Method in DECISIONS.md (2026-10-17, experiment 2). Every account falls in a development or a
held-out pool by one bit of sha256("berka-split-2026-10-17:<account id>"), so the split depends on
nothing but the id. Inside a pool, the accounts that pass a filter are ordered by that hash and the
first 200 are taken. The primary filter is at least 24 months of history, at least 4
non-mechanical transactions a month and no quiet stretch over 90 days (1,387 accounts); the
sensitivity group is the accounts that pass the same filter with 2 or more a month and not 4
(so it is disjoint from the primary group).

    python -m eval.berka_split              # recomputes the split and writes docs/berka_split.json
    python -m eval.berka_split --check      # recomputes it and says whether the file agrees

The held-out lists are for the one final test. Code that tunes or checks anything reads the
development lists only, through `development_accounts()`.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

from eval.berka_gate import load, per_account
from lookahead.generator.archetypes import called_by_harness

SPLIT_FILE = Path("docs/berka_split.json")
SALT = "berka-split-2026-10-17"
PER_POOL = 200
MIN_MONTHS = 24
PRIMARY_PER_MONTH = 4
SENSITIVITY_PER_MONTH = 2
MAX_QUIET_DAYS = 90


def digest(account_id: int) -> str:
    return hashlib.sha256(f"{SALT}:{int(account_id)}".encode()).hexdigest()


def pool_of(account_id: int) -> str:
    """ "development" or "held_out": the lowest bit of the hash."""
    return "held_out" if int(digest(account_id), 16) % 2 else "development"


def passes(acc: pd.DataFrame, per_month: float) -> pd.Series:
    return (
        (acc["months"] >= MIN_MONTHS)
        & (acc["real_per_month"] >= per_month)
        & (acc["longest_real_gap"] <= MAX_QUIET_DAYS)
    )


def compute(acc: pd.DataFrame) -> dict[str, object]:
    """The split from the per-account counts of the gate."""
    primary = passes(acc, PRIMARY_PER_MONTH)
    sensitivity = passes(acc, SENSITIVITY_PER_MONTH) & ~primary
    out: dict[str, object] = {
        "rule": (
            f"pool = lowest bit of sha256('{SALT}:<account_id>'); inside a pool, accounts passing "
            f"the filter are ordered by that hash and the first {PER_POOL} are taken. Primary: "
            f"{MIN_MONTHS}+ months, {PRIMARY_PER_MONTH}+ non-mechanical transactions a month, no "
            f"quiet stretch over {MAX_QUIET_DAYS} days. Sensitivity: the same with "
            f"{SENSITIVITY_PER_MONTH}+ a month, minus the primary accounts."
        ),
        "primary_filter_accounts": int(primary.sum()),
        "sensitivity_filter_accounts": int(sensitivity.sum()),
    }
    for name, mask in (("primary", primary), ("sensitivity", sensitivity)):
        ids = sorted(acc.index[mask], key=digest)
        for pool in ("development", "held_out"):
            chosen = [int(a) for a in ids if pool_of(a) == pool][:PER_POOL]
            out[f"{name}_{pool}"] = sorted(chosen)
    return out


def load_split(path: Path = SPLIT_FILE) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def development_accounts(group: str = "primary", path: Path = SPLIT_FILE) -> list[int]:
    """The development accounts of a group ("primary" or "sensitivity"). Never the held-out."""
    ids = load_split(path)[f"{group}_development"]
    assert isinstance(ids, list)
    return [int(a) for a in ids]


def held_out_accounts(group: str = "primary", path: Path = SPLIT_FILE) -> list[int]:
    """Only for the one final test: refused unless the held-out harness asks."""
    if not called_by_harness():
        raise ValueError("held-out Berka accounts are read only by eval/held_out.py")
    ids = load_split(path)[f"{group}_held_out"]
    assert isinstance(ids, list)
    return [int(a) for a in ids]


def assert_development_only(ids: list[int], path: Path = SPLIT_FILE) -> None:
    """Tuning and checking code calls this first: a held-out account is refused (only the
    held-out harness passes)."""
    split = load_split(path)
    held = {int(a) for k in ("primary_held_out", "sensitivity_held_out") for a in split[k]}  # type: ignore[attr-defined]
    bad = sorted(set(ids) & held)
    if bad and not called_by_harness():
        raise ValueError(f"{len(bad)} held-out Berka accounts are not for tuning or checking")
    from eval.fresh_set import fresh_berka_accounts  # here: fresh_set imports this module

    fresh = sorted(set(ids) & fresh_berka_accounts())
    if fresh:  # the fresh set for forecaster v2: refused for every caller until its own run
        raise ValueError(f"{len(fresh)} fresh held-out Berka accounts are not for tuning or "
                         "checking")  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.berka_split",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dir", type=Path, default=Path("data/berka"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    split = compute(per_account(load(args.dir)))
    if args.check:
        same = SPLIT_FILE.exists() and load_split() == split
        print("the committed split matches the recomputed one" if same else "the split differs")
        return 0 if same else 1
    SPLIT_FILE.write_text(json.dumps(split, indent=1) + "\n", encoding="utf-8")
    for key in ("primary_filter_accounts", "sensitivity_filter_accounts"):
        print(f"{key}: {split[key]}")
    for key in sorted(k for k in split if k.endswith(("development", "held_out"))):
        print(f"{key}: {len(split[key])}")  # type: ignore[arg-type]
    print(f"written: {SPLIT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
