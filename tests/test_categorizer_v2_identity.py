"""Categorizer v2 is opt-in: with every v2 setting off (the default), the categorizer's stored
outputs on the synthetic personas A, B and C are identical to a snapshot taken on origin/main
before any v2 code existed (tests/fixtures/categorizer_v2_baseline.csv).

Two runs are compared:
- "demo": the synthetic demo build (every persona merchant is in the synthetic training rows);
- "new": a model trained only on persona A's dev labels (no synthetic rows), predicting B and
  C, so many of their merchants go down the new-merchant route.

Rebuild the snapshot only on purpose (it is the "before" side of the proof):
    python tests/test_categorizer_v2_identity.py --write
"""

import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "data" / "synthetic"
SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "categorizer_v2_baseline.csv"
COLUMNS = ["run", "account", "fingerprint", "merchant", "flow_type", "category", "source",
           "detail", "flagged", "confidence"]  # fmt: skip
ACCOUNTS = ("synthetic-A", "synthetic-B", "synthetic-C")


def _rows(conn: sqlite3.Connection, run: str, accounts: tuple[str, ...]) -> list[list[str]]:
    out = []
    for account in accounts:
        for r in conn.execute(
            "SELECT fingerprint, merchant, flow_type, category, source, detail, flagged,"
            " confidence FROM category_predictions WHERE account = ? ORDER BY fingerprint",
            (account,),
        ):
            confidence = "" if r[7] is None else f"{r[7]:.6f}"
            out.append([run, account, *("" if v is None else str(v) for v in r[:7]), confidence])
    return out


def _label_from_answer_key(conn: sqlite3.Connection, account: str, persona: str) -> None:
    from lookahead.ingest.scotiabank import parse_export
    from lookahead.labels import freeze_splits, label_row, load_rows

    freeze_splits(conn, account, seed=11)
    parsed = parse_export(SYNTHETIC / f"persona_{persona}.csv")
    key = SYNTHETIC / f"persona_{persona}_answer_key.csv"
    with key.open(encoding="utf-8", newline="") as f:
        truth = list(csv.DictReader(line for line in f if not line.startswith("#")))
    category = {(r.posted_date, r.amount_cents, r.sub_description): t["category"]
                for r, t in zip(parsed, truth, strict=True)}  # fmt: skip
    for r in load_rows(conn, account).itertuples():
        label_row(conn, account, r.fingerprint,
                  category[(r.posted_date, r.amount_cents, r.text)])  # fmt: skip


def build_outputs(tmp: Path, turn_on: tuple[str, ...] = ()) -> list[list[str]]:
    """Every stored category output of both runs, sorted, as text. `turn_on`: categorizer v2
    settings switched on in the "new" run's database (none for the identity check)."""
    from lookahead.categorize import Settings, categorize, train
    from lookahead.db import connect
    from lookahead.demo import build_demo_db
    from lookahead.ingest.scotiabank import parse_export
    from lookahead.ingest.store import store_exports

    build_demo_db(tmp / "demo.db", SYNTHETIC)
    conn = connect(tmp / "demo.db")
    rows = _rows(conn, "demo", ACCOUNTS)
    conn.close()

    conn = connect(tmp / "new.db")
    for account in ACCOUNTS:
        export = SYNTHETIC / f"persona_{account[-1]}.csv"
        store_exports(conn, account, [(export.name, parse_export(export))],
                      datetime(2026, 10, 2))  # fmt: skip
    conn.commit()
    from lookahead import categorizer_v2

    for setting in turn_on:
        categorizer_v2.set_option(conn, setting, True)
    _label_from_answer_key(conn, "synthetic-A", "A")
    settings = Settings(threshold=0.5, synthetic_months=0)
    model, _ = train(conn, "synthetic-A", settings)
    for account in ACCOUNTS[1:]:
        categorize(conn, account, settings, model=model)
    rows += _rows(conn, "new", ACCOUNTS[1:])
    conn.close()
    return rows


def read_snapshot() -> list[list[str]]:
    with SNAPSHOT.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        assert next(reader) == COLUMNS
        return list(reader)


@pytest.mark.slow
def test_everything_off_matches_the_snapshot_taken_before_v2(tmp_path: Path) -> None:
    got = build_outputs(tmp_path)
    want = read_snapshot()
    assert len(got) == len(want)
    assert {r[0] for r in want} == {"demo", "new"}
    assert any(r[0] == "new" and r[6] == "model" for r in want)  # the new-merchant route ran
    assert got == want


@pytest.mark.slow
def test_directory_on_changes_only_flagged_new_merchant_rows(tmp_path: Path) -> None:
    from lookahead import categorizer_v2

    got = build_outputs(tmp_path, turn_on=(categorizer_v2.DIRECTORY,))
    want = read_snapshot()
    assert len(got) == len(want)
    changed = [(g, w) for g, w in zip(got, want, strict=True) if g != w]
    assert changed  # some of B's and C's new merchants are directory chains
    for g, w in changed:
        assert g[0] == "new" and w[6] == "model" and w[8] == "1"
        assert g[6] == "directory" and g[8] == "1"
        assert g[:3] == w[:3] and g[9] == w[9]  # same row, same confidence


if __name__ == "__main__":
    import tempfile

    sys.path.insert(0, str(ROOT))
    if "--write" not in sys.argv:
        print("usage: python tests/test_categorizer_v2_identity.py --write")
        sys.exit(2)
    with tempfile.TemporaryDirectory() as d:
        outputs = build_outputs(Path(d))
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    with SNAPSHOT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(COLUMNS)
        writer.writerows(outputs)
    print(f"wrote {len(outputs)} rows to {SNAPSHOT.relative_to(ROOT)}")
