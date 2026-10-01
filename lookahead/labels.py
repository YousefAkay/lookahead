"""Labels for the categorizer (step 6): frozen splits, stored labels, the review queue.

Order of work (DECISIONS.md):
1. Freeze the splits ONCE per account, before any labelling:
       python -m lookahead.labels freeze-splits --db data/private/db/lookahead.db
   Test B = about 25% of merchant groups (whole groups, seeded); Test A = rows after a date
   cutoff, outside Test B; dev = the rest. The seed is random and saved. Refuses twice.
2. Label in the review screen (app page "Label review"): one decision labels all of a
   merchant's rows; a single row can be overridden; "skip / don't know" excludes rows from
   training and testing. Labels are dated and only ever written by the owner's clicks.
3. Merchants with any holdout row are shown blind (no model suggestion). Suggestions come
   from a model trained on dev labels only.
"""

import argparse
import json
import math
import secrets
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from lookahead.categories import load_category_preferences
from lookahead.categorizer import HybridCategorizer, examples
from lookahead.db import connect
from lookahead.flows import Txn
from lookahead.resolve import load_user_decisions
from lookahead.taxonomy import CATEGORIES

TEST_B_SHARE = 0.25  # of merchant groups
TEST_A_SHARE = 0.20  # of the remaining rows, the latest ones
SUGGESTION_PARAMS = {"model__C": 1.0, "features__name__ngram_range": (2, 5)}  # provisional


class AlreadyFrozen(RuntimeError):
    """The splits of this account were frozen before; they are never changed."""


def _categorizer_rows(conn: sqlite3.Connection, account: str) -> pd.DataFrame:
    """Rows the rules leave for the categorizer, with fingerprint, text and amount."""
    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ?",
        (account,),
    ).fetchall()
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    df = examples(txns, load_user_decisions(conn), load_category_preferences(conn))
    extra = pd.DataFrame(
        [(r[0], r[2], r[4], r[5]) for r in raw],
        columns=["row_id", "amount_cents", "text", "fingerprint"],
    )
    return df.merge(extra, on="row_id", how="left")


def freeze_splits(
    conn: sqlite3.Connection, account: str, seed: int | None = None, today: date | None = None
) -> dict[str, tuple[int, int]]:
    """Assign every categorizer row to dev / test_a / test_b, once. Returns
    {split: (merchant groups, rows)}."""
    if conn.execute("SELECT 1 FROM label_split_info WHERE account = ?", (account,)).fetchone():
        raise AlreadyFrozen(f"splits for account {account} are already frozen")
    seed = secrets.randbelow(2**31) if seed is None else seed
    rows = _categorizer_rows(conn, account)
    groups = sorted(set(rows["group"]))
    order = np.random.default_rng(seed).permutation(len(groups))
    test_b = {groups[i] for i in order[: round(TEST_B_SHARE * len(groups))]}
    rest = rows[~rows["group"].isin(test_b)]
    dates = rest["posted_date"].sort_values()
    cutoff = dates.iloc[min(len(dates) - 1, int(len(dates) * (1 - TEST_A_SHARE)))]
    split = np.where(
        rows["group"].isin(test_b),
        "test_b",
        np.where(rows["posted_date"] > cutoff, "test_a", "dev"),
    )
    with conn:
        conn.execute(
            "INSERT INTO label_split_info (account, frozen_at, seed, cutoff_date, test_b_groups)"
            " VALUES (?, ?, ?, ?, ?)",
            (account, (today or date.today()).isoformat(), seed, cutoff.isoformat(),
             json.dumps(sorted(test_b))),
        )  # fmt: skip
        conn.executemany(
            "INSERT INTO label_splits (fingerprint, account, split) VALUES (?, ?, ?)",
            zip(rows["fingerprint"], [account] * len(rows), split, strict=True),
        )
    counts = {}
    for name in ("dev", "test_a", "test_b"):
        mask = split == name
        counts[name] = (rows.loc[mask, "group"].nunique(), int(mask.sum()))
    return counts


def load_rows(conn: sqlite3.Connection, account: str) -> pd.DataFrame:
    """Categorizer rows that have a frozen split (empty before freezing)."""
    splits = pd.DataFrame(
        conn.execute(
            "SELECT fingerprint, split FROM label_splits WHERE account = ?", (account,)
        ).fetchall(),
        columns=["fingerprint", "split"],
    )
    if splits.empty:
        return splits
    return _categorizer_rows(conn, account).merge(splits, on="fingerprint", how="inner")


# --- Labels ---


@dataclass(frozen=True)
class Label:
    category: str | None  # None = skipped
    status: str  # "labelled" or "skipped"


def _write(
    conn: sqlite3.Connection, account: str, fingerprints: list[str], category: str | None,
    scope: str, when: date | None, status: str | None = None,
) -> int:  # fmt: skip
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    status = status or ("skipped" if category is None else "labelled")
    day = (when or date.today()).isoformat()
    with conn:
        conn.executemany(
            "INSERT INTO category_labels (fingerprint, account, category, status, scope,"
            " decided_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(f, account, category, status, scope, day) for f in fingerprints],
        )
    return len(fingerprints)


def label_merchant(
    conn: sqlite3.Connection, account: str, group: str, category: str | None,
    when: date | None = None,
) -> int:  # fmt: skip
    """Label every frozen row of a merchant group (None = skip). Returns rows written."""
    rows = load_rows(conn, account)
    fingerprints = rows.loc[rows["group"] == group, "fingerprint"].tolist()
    return _write(conn, account, fingerprints, category, "merchant", when)


def skip_merchant(
    conn: sqlite3.Connection, account: str, group: str, when: date | None = None
) -> int:
    return label_merchant(conn, account, group, None, when)


def label_row(
    conn: sqlite3.Connection, account: str, fingerprint: str, category: str | None,
    when: date | None = None,
) -> None:  # fmt: skip
    """Override one row (None = skip it). Beats any merchant decision, earlier or later."""
    _write(conn, account, [fingerprint], category, "row", when)


def clear_merchant(
    conn: sqlite3.Connection, account: str, group: str, when: date | None = None
) -> int:
    """Reset every frozen row of a merchant group to "not reviewed": its earlier merchant
    decisions and single-row overrides no longer count. Returns rows cleared."""
    rows = load_rows(conn, account)
    fingerprints = rows.loc[rows["group"] == group, "fingerprint"].tolist()
    return _write(conn, account, fingerprints, None, "merchant", when, status="cleared")


def current_labels(conn: sqlite3.Connection, account: str) -> dict[str, Label]:
    """fingerprint -> its current label: latest row decision, else latest merchant decision,
    counting only decisions made after the row's latest "cleared"."""
    by_scope: dict[str, dict[str, Label]] = {"merchant": {}, "row": {}}
    for fingerprint, category, status, scope in conn.execute(
        "SELECT fingerprint, category, status, scope FROM category_labels WHERE account = ?"
        " ORDER BY decided_at, label_id",
        (account,),
    ):
        if status == "cleared":
            for decisions in by_scope.values():
                decisions.pop(fingerprint, None)
        else:
            by_scope[scope][fingerprint] = Label(category, status)
    return {**by_scope["merchant"], **by_scope["row"]}


def labelled_examples(
    conn: sqlite3.Connection, account: str, splits: tuple[str, ...] = ("dev",)
) -> pd.DataFrame:
    """Frozen rows of the given splits that have a label (skipped rows excluded)."""
    rows = load_rows(conn, account)
    if rows.empty:
        return rows.assign(label=pd.Series(dtype="str"))
    labels = current_labels(conn, account)
    rows = rows[rows["split"].isin(splits)].copy()
    rows["label"] = [labels[f].category if f in labels else None for f in rows["fingerprint"]]
    return rows[rows["label"].notna()].reset_index(drop=True)


# --- Review queue ---


@dataclass(frozen=True)
class Task:
    group: str
    rows: int
    blind: bool  # some rows are holdout: no model suggestion is shown
    done: bool  # every row labelled or skipped
    suggestion: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class Progress:
    merchants_done: int
    merchants_total: int
    rows_covered: int
    rows_total: int
    labelled: int
    skipped: int


def suggestion_model(conn: sqlite3.Connection, account: str) -> HybridCategorizer | None:
    """A model trained on dev labels only, for suggestions; None until at least two
    categories are labelled. Settings are provisional (re-chosen in step 7)."""
    ex = labelled_examples(conn, account, splits=("dev",))
    if ex["label"].nunique() < 2:
        return None
    return HybridCategorizer(params=SUGGESTION_PARAMS, threshold=0.0).fit(ex, ex["label"])


def review_queue(
    conn: sqlite3.Connection, account: str, model: HybridCategorizer | None = None
) -> list[Task]:
    """Merchants to review: open ones first (blind or unscored ones by size, then the
    least confident suggestions), finished ones last."""
    rows = load_rows(conn, account)
    if rows.empty:
        return []
    labels = current_labels(conn, account)
    tasks = []
    for group, part in rows.groupby("group", sort=True):
        blind = bool((part["split"] != "dev").any())
        done = all(f in labels for f in part["fingerprint"])
        suggestion = confidence = None
        if model is not None and not blind and not done:
            preds = model.predict_rows(part)
            suggestion = pd.Series([p.category for p in preds]).mode().iloc[0]
            scores = [p.confidence for p in preds if p.confidence is not None]
            confidence = float(np.mean(scores)) if scores else 1.0  # lookup: known merchant
        tasks.append(Task(str(group), len(part), blind, done, suggestion, confidence))
    return sorted(
        tasks,
        key=lambda t: (t.done, t.confidence if t.confidence is not None else 0.0, -t.rows, t.group),
    )


def progress(conn: sqlite3.Connection, account: str) -> Progress:
    rows = load_rows(conn, account)
    labels = current_labels(conn, account)
    if rows.empty:
        return Progress(0, 0, 0, 0, 0, 0)
    covered = rows["fingerprint"].map(lambda f: f in labels)
    status = rows["fingerprint"].map(lambda f: labels[f].status if f in labels else "")
    per_group = covered.groupby(rows["group"]).all()
    return Progress(
        merchants_done=int(per_group.sum()),
        merchants_total=len(per_group),
        rows_covered=int(covered.sum()),
        rows_total=len(rows),
        labelled=int((status == "labelled").sum()),
        skipped=int((status == "skipped").sum()),
    )


def label_status_counts(conn: sqlite3.Connection, account: str) -> dict[str, int]:
    """Counts over the frozen rows still left for the categorizer: labelled, skipped,
    cleared (reset and not labelled again), not reviewed (includes cleared), merchants."""
    rows = load_rows(conn, account)
    labels = current_labels(conn, account)
    touched = {
        f for (f,) in conn.execute(
            "SELECT DISTINCT fingerprint FROM category_labels WHERE account = ?", (account,)
        )
    }  # fmt: skip
    fps = list(rows["fingerprint"]) if not rows.empty else []
    status = [labels[f].status if f in labels else "" for f in fps]
    p = progress(conn, account)
    return {
        "labelled": status.count("labelled"),
        "skipped": status.count("skipped"),
        "cleared": sum(1 for f in fps if f in touched and f not in labels),
        "not reviewed": status.count(""),
        "merchants done": p.merchants_done,
        "merchants": p.merchants_total,
    }


# --- Checking the frozen splits after a code change ---


@dataclass(frozen=True)
class SplitCheck:
    frozen: int  # rows with a frozen split
    still_left: int  # ... still left for the categorizer
    now_rules: int  # ... now decided by a rule (labels kept, no longer used)
    unsplit: int  # rows left for the categorizer without a frozen split
    mixed_test_b_groups: int  # merchant groups with Test B rows and other rows

    @property
    def ok(self) -> bool:
        return self.unsplit == 0 and self.mixed_test_b_groups == 0


def check_splits(conn: sqlite3.Connection, account: str) -> SplitCheck:
    """Counts showing whether a code change disturbed the frozen splits. A row never changes
    split (the table is keyed by fingerprint); what a change can alter is which rows reach
    the categorizer and which merchant group they fall in."""
    frozen = dict(
        conn.execute("SELECT fingerprint, split FROM label_splits WHERE account = ?", (account,))
    )
    rows = _categorizer_rows(conn, account)
    present = rows["fingerprint"].isin(list(frozen))
    split = rows.loc[present, "fingerprint"].map(frozen)
    per_group = split.groupby(rows.loc[present, "group"]).agg(set)
    mixed = sum(1 for s in per_group if "test_b" in s and len(s) > 1)
    still = int(present.sum())
    return SplitCheck(len(frozen), still, len(frozen) - still, int((~present).sum()), mixed)


# --- Commands ---


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.labels")
    commands = parser.add_subparsers(dest="command", required=True)
    fr = commands.add_parser("freeze-splits", help="assign dev / test_a / test_b once")
    ck = commands.add_parser("check-splits", help="counts: did a code change disturb them?")
    cl = commands.add_parser("clear", help="reset one merchant group's labels")
    cl.add_argument("--group", required=True, help="the merchant's name as shown on the page")
    for sub in (fr, ck, cl):
        sub.add_argument("--db", type=Path, required=True)
        sub.add_argument("--account", default="scotia-chequing")
    args = parser.parse_args(argv)
    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 2
    conn = connect(args.db)
    if args.command == "check-splits":
        c = check_splits(conn, args.account)
        conn.close()
        print(f"Frozen splits, account {args.account}: {c.frozen} rows")
        print(f"  still left for the categorizer: {c.still_left}")
        print(f"  now decided by a rule (labels kept, no longer used): {c.now_rules}")
        print(f"rows left for the categorizer without a split: {c.unsplit}")
        print(f"merchant groups mixing Test B with other rows: {c.mixed_test_b_groups}")
        print("RESULT: PASS" if c.ok else "RESULT: CHECK")
        return 0 if c.ok else 1
    if args.command == "clear":
        n = clear_merchant(conn, args.account, args.group)
        conn.close()
        print(f"cleared the labels of {n} rows (0: no frozen rows in a group of that name)")
        return 0
    try:
        counts = freeze_splits(conn, args.account)
    except AlreadyFrozen as e:
        print(f"{e}; nothing changed (splits are frozen once, before labelling)")
        return 1
    finally:
        conn.close()
    total = sum(rows for _, rows in counts.values())
    print(f"Splits frozen for account {args.account}: {total} rows left for the categorizer")
    for name, (groups, rows) in counts.items():
        share = rows / total if total else math.nan
        print(f"  {name}: {groups} merchant groups, {rows} rows ({share:.0%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
