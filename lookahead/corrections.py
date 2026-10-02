"""Category corrections made in the app (step 8b).

A correction is dated and stored in its own table, apart from the labels of the label
review page, so the labels used to evaluate the model never change because of something
corrected here.
- one row:         works on any row, also rows a rule decided (an e-Transfer that was a
                   gift). Refused if the category contradicts the row's flow type;
- whole merchant:  every row of the merchant that a rule did not decide. Rows you set one
                   by one keep their own category.
The category shown for the corrected rows changes at once. The model learns from a
correction at the next retrain, and only if the row is a development row or was imported
after the splits were frozen: holdout rows change on screen only.

Deliberately NOT stored as a category preference: a preference makes the rules decide the
merchant, which would take its rows out of the frozen evaluation splits. Future rows of a
corrected merchant are covered by the retrained model instead (it reuses the label).
"""

import sqlite3
from datetime import date

import pandas as pd

from lookahead.categories import conflicts
from lookahead.taxonomy import CATEGORIES

NO_MERCHANT = "(no merchant name)"
_MODEL_SOURCES = ("your label", "known merchant", "model", "LLM", "none")  # not rule, not netted


def _has_table(conn: sqlite3.Connection) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'category_corrections'"
        ).fetchone()
    )


def corrections(
    conn: sqlite3.Connection, account: str, as_of: date | None = None
) -> dict[str, tuple[str, str]]:
    """fingerprint -> (category, scope): the latest single-row correction of a row, else its
    latest whole-merchant correction. `as_of` keeps only corrections made by that date."""
    if not _has_table(conn):
        return {}
    by_scope: dict[str, dict[str, tuple[str, str]]] = {"merchant": {}, "row": {}}
    for fingerprint, category, scope in conn.execute(
        "SELECT fingerprint, category, scope FROM category_corrections WHERE account = ?"
        " AND decided_at <= ? ORDER BY decided_at, correction_id",
        (account, (as_of or date.max).isoformat()),
    ):
        by_scope[scope][fingerprint] = (category, scope)
    return {**by_scope["merchant"], **by_scope["row"]}


def _store(
    conn: sqlite3.Connection, account: str, fingerprints: list[str], category: str, scope: str,
    when: date | None,
) -> None:  # fmt: skip
    """Write the corrections and make the stored predictions show them, in one transaction."""
    day = (when or date.today()).isoformat()
    with conn:
        conn.executemany(
            "INSERT INTO category_corrections (fingerprint, account, category, scope,"
            " decided_at) VALUES (?, ?, ?, ?, ?)",
            [(f, account, category, scope, day) for f in fingerprints],
        )
        current = corrections(conn, account)
        conn.executemany(
            "UPDATE category_predictions SET category = ?, source = 'your label',"
            " detail = 'corrected by you', flagged = 0, confidence = 1.0 WHERE fingerprint = ?",
            [(current[f][0], f) for f in fingerprints],
        )


def correct_row(
    conn: sqlite3.Connection, account: str, fingerprint: str, category: str,
    when: date | None = None,
) -> None:  # fmt: skip
    """Set the category of one row. Raises ValueError if it contradicts the flow type."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    row = conn.execute(
        "SELECT flow_type FROM category_predictions WHERE account = ? AND fingerprint = ?",
        (account, fingerprint),
    ).fetchone()
    if row is None:
        raise ValueError("unknown row: run lookahead.categorize first")
    if conflicts(row[0], category):
        raise ValueError(
            f"{category} does not fit a row whose flow type is {row[0]} "
            "(for example, Income only fits money coming in as income)"
        )
    _store(conn, account, [fingerprint], category, "row", when)


def merchant_fingerprints(conn: sqlite3.Connection, account: str, merchant: str) -> list[str]:
    """Rows a whole-merchant correction covers: the merchant's rows not decided by a rule."""
    marks = ", ".join("?" for _ in _MODEL_SOURCES)
    return [
        r[0]
        for r in conn.execute(
            "SELECT fingerprint FROM category_predictions WHERE account = ? AND merchant = ?"
            f" AND source IN ({marks})",
            (account, merchant, *_MODEL_SOURCES),
        )
    ]


def correct_merchant(
    conn: sqlite3.Connection, account: str, merchant: str, category: str,
    when: date | None = None,
) -> int:  # fmt: skip
    """Set the category of every such row of a merchant. Returns the rows written."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    fingerprints = merchant_fingerprints(conn, account, merchant)
    _store(conn, account, fingerprints, category, "merchant", when)
    return len(fingerprints)


def to_check(conn: sqlite3.Connection, account: str, flagged_only: bool = True) -> pd.DataFrame:
    """One line per merchant: rows, rows to check, lowest confidence and current category.
    Least confident first. Rows without a merchant name share one line."""
    df = pd.read_sql_query(
        "SELECT merchant, category, flagged, confidence FROM category_predictions"
        " WHERE account = ?",
        conn,
        params=[account],
    )
    df["merchant"] = df["merchant"].astype("object").fillna(NO_MERCHANT)
    lines = []
    for merchant, part in df.groupby("merchant", sort=True):
        flagged = int(part["flagged"].sum())
        if flagged_only and not flagged:
            continue
        categories = part["category"].dropna()
        confidence = part["confidence"].min()
        lines.append(
            {
                "merchant": merchant,
                "rows": len(part),
                "to_check": flagged,
                "confidence": 1.0 if pd.isna(confidence) else float(confidence),
                "category": categories.mode().iloc[0] if len(categories) else None,
            }
        )
    out = pd.DataFrame(lines, columns=["merchant", "rows", "to_check", "confidence", "category"])
    return out.sort_values(["confidence", "merchant"], kind="stable").reset_index(drop=True)
