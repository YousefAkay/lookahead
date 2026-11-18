"""Categories for every stored transaction (step 8): rules first, then the trained model.

    python -m lookahead.categorize --db data/private/db/lookahead.db

Each row gets a merchant, a flow type, a category, where the category came from and a flag:
- "rule":            decided by the category rules (transaction type, delivery platform, ...);
- "your label":      a category you confirmed or corrected;
- "known merchant":  a merchant with labels in training reuses its label;
- "model":           a new merchant, decided by the trained model (flagged when unsure);
- "LLM":             only if you turned the LLM fallback on: a flagged new merchant answered
                     by Claude from the merchant name alone (saved answers; nothing is sent
                     by this command);
- "netted":          a fully reversed pair, which needs no category;
- "none":            nothing to go on (no merchant name).
Results are stored in `category_predictions`, so the app pages only read them. The model
is trained here from your development labels plus synthetic rows, with the settings saved
by step 7a (nothing is tuned here). Holdout rows are never trained on.
Prints counts only.
"""

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import joblib
import pandas as pd

import lookahead.categorizer_v2 as categorizer_v2
import lookahead.llm_fallback as llm_fallback
from lookahead.categories import category_by_rules, load_category_preferences
from lookahead.categorizer import HybridCategorizer, examples
from lookahead.corrections import corrections
from lookahead.db import connect
from lookahead.flows import Txn, flow_types
from lookahead.merchants import extract_merchant, is_private_path
from lookahead.pseudonymize import PseudonymKeyError, pseudonym_key
from lookahead.resolve import load_user_decisions, name_infos, resolve
from lookahead.synthetic_training import synthetic_examples

DEFAULT_CHOICE = Path("data/private/eval/categorizer_choice.json")
SOURCES = ("rule", "your label", "known merchant", "model", "LLM", "netted", "none")
_ROUTES = {
    "your preference": "rule",
    "known merchant": "known merchant",
    "known merchant, labels disagree": "known merchant",
    "new merchant": "model",
}


@dataclass(frozen=True)
class Settings:
    """Model settings chosen in advance (step 7a). `threshold` None = measured in training."""

    params: dict[str, object] = field(
        default_factory=lambda: {"model__C": 1.0, "features__name__ngram_range": (2, 5)}
    )
    threshold: float | None = None
    synthetic_months: int = 12


def load_settings(path: Path = DEFAULT_CHOICE) -> Settings:
    """The settings saved by step 7a, or the defaults when there is no saved choice."""
    if not path.exists():
        return Settings()
    saved = json.loads(path.read_text(encoding="utf-8"))
    params = {k: tuple(v) if isinstance(v, list) else v for k, v in saved["params"].items()}
    threshold = saved.get("flag_threshold")
    return Settings(
        params,
        math.inf if threshold is None else float(threshold),
        int(saved.get("synthetic_months", 0)),
    )


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
    )


def training_rows(conn: sqlite3.Connection, account: str) -> pd.DataFrame:
    """Your rows the model may learn from: development rows, and rows without a split
    (imported after the splits were frozen, so they can never be holdout rows). The label
    is your correction if there is one, else your label from the review page.
    Holdout rows (Test A, Test B) are never included, whatever was corrected."""
    from lookahead.corrections import corrections
    from lookahead.labels import _categorizer_rows, current_labels  # labels imports us

    if not _has_table(conn, "category_labels"):
        return pd.DataFrame()
    rows = _categorizer_rows(conn, account)
    split = dict(
        conn.execute(
            "SELECT fingerprint, split FROM label_splits WHERE account = ?", (account,)
        ).fetchall()
    )
    labels = current_labels(conn, account)
    corrected = corrections(conn, account)

    def label(fingerprint: str) -> str | None:
        if split.get(fingerprint, "dev") != "dev":
            return None
        if fingerprint in corrected:
            return corrected[fingerprint][0]
        return labels[fingerprint].category if fingerprint in labels else None

    rows["label"] = [label(f) for f in rows["fingerprint"]]
    return rows[rows["label"].notna()].reset_index(drop=True)


def train(
    conn: sqlite3.Connection, account: str, settings: Settings
) -> tuple[HybridCategorizer, int]:
    """The hybrid, trained on your development labels plus synthetic rows. Without any
    label (the synthetic demo), the synthetic rows are the training set. Returns the model
    and the number of your labelled rows used."""
    mine = training_rows(conn, account)
    synthetic = (
        synthetic_examples(settings.synthetic_months) if settings.synthetic_months > 0 else None
    )
    options = categorizer_v2.load_options(conn)  # all off by default: today's hybrid
    model = categorizer_v2.make_hybrid(
        options, params=settings.params, threshold=settings.threshold
    )
    if mine.empty:
        if synthetic is None:
            raise ValueError("nothing to train on: no labels and no synthetic rows")
        return model.fit(synthetic, synthetic["label"]), 0
    if synthetic is None:
        return model.fit(mine, mine["label"]), len(mine)
    model.fit(mine, mine["label"], extra_x=synthetic, extra_y=synthetic["label"])
    return model, len(mine)


def predict(conn: sqlite3.Connection, account: str, model: HybridCategorizer) -> pd.DataFrame:
    """One row per stored transaction: fingerprint, merchant, flow_type, category, source,
    detail, flagged, confidence."""
    from lookahead.corrections import corrections
    from lookahead.labels import current_labels

    raw = conn.execute(
        "SELECT id, posted_date, amount_cents, description, sub_description, fingerprint"
        " FROM transactions WHERE account = ?",
        (account,),
    ).fetchall()
    txns = [Txn(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in raw]
    fingerprint = {r[0]: r[5] for r in raw}
    user = load_user_decisions(conn)
    prefs = load_category_preferences(conn)
    decisions = category_by_rules(txns, user, prefs)
    flows = flow_types(txns, user)
    resolution = resolve(name_infos(t.text for t in txns), user)
    labels = current_labels(conn, account) if _has_table(conn, "category_labels") else {}
    corrected = corrections(conn, account)

    # row_id -> (category, source, detail, flagged, confidence)
    out: dict[int, tuple[str | None, str, str, bool, float | None]] = {}
    features = examples(txns, user, prefs)
    predictions = model.predict_rows(features) if len(features) else []
    for row_id, p in zip(features["row_id"], predictions, strict=True):
        label = labels.get(fingerprint[row_id])
        if fingerprint[row_id] in corrected:
            category = corrected[fingerprint[row_id]][0]
            out[row_id] = (category, "your label", "corrected by you", False, 1.0)
        elif label is not None and label.category is not None:
            out[row_id] = (label.category, "your label", "confirmed by you", False, 1.0)
        else:
            out[row_id] = (p.category, _ROUTES[p.route], p.route, p.flagged, p.confidence)
    waiting: list[int] = []
    for t in txns:
        d = decisions[t.row_id]
        if t.row_id in out:
            continue
        category, scope = corrected.get(fingerprint[t.row_id], (None, None))
        if scope == "row":  # a row a rule decided, corrected by you (single rows only)
            out[t.row_id] = (category, "your label", "corrected by you", False, 1.0)
        elif flows[t.row_id].netted:
            out[t.row_id] = (None, "netted", "fully reversed pair", False, None)
        elif d.category is not None:
            out[t.row_id] = (d.category, "rule", d.source, False, 1.0)
        elif d.inherit_from is not None:
            waiting.append(t.row_id)
        else:
            out[t.row_id] = (None, "none", "no merchant name to go on", True, None)
    for row_id in waiting:  # a refund takes the category of its purchase
        parent = out.get(decisions[row_id].inherit_from)  # type: ignore[arg-type]
        if parent is None or parent[0] is None:
            out[row_id] = (None, "none", "paired purchase has no category", True, None)
        else:
            out[row_id] = (parent[0], parent[1], "inherited from the paired purchase",
                           parent[3], parent[4])  # fmt: skip

    records = []
    for t in txns:
        name = extract_merchant(t.text).merchant
        category, source, detail, flagged, confidence = out[t.row_id]
        records.append(
            {
                "fingerprint": fingerprint[t.row_id],
                "merchant": resolution.group_of(name) if name else None,
                "flow_type": flows[t.row_id].flow_type,
                "category": category,
                "source": source,
                "detail": detail,
                "flagged": bool(flagged),
                "confidence": confidence,
            }
        )
    return pd.DataFrame.from_records(records)


@dataclass
class Summary:
    rows: int
    training_rows: int
    by_source: Counter[str]
    flagged: int
    changed: int | None  # rows whose category differs from the stored one (None: first run)
    model_version: str
    flagged_before: int | None = None  # None: first run
    training_before: int | None = None  # your rows used by the previous run
    model_file: Path | None = None  # where the trained model was saved, if anywhere
    corrections_trained: int = 0  # corrected rows the model learned from
    corrections_shown_only: int = 0  # corrected rows that only change the display


def stored_categories(conn: sqlite3.Connection, account: str) -> dict[str, str | None]:
    if not _has_table(conn, "category_predictions"):
        return {}
    return dict(
        conn.execute(
            "SELECT fingerprint, category FROM category_predictions WHERE account = ?", (account,)
        ).fetchall()
    )


def categorize(
    conn: sqlite3.Connection,
    account: str,
    settings: Settings | None = None,
    today: date | None = None,
    model: HybridCategorizer | None = None,
    training: int = 0,
    model_dir: Path | None = None,
) -> Summary:
    """Train (unless a model is given), predict every row and store the results, replacing
    the account's earlier ones, in one database transaction. This is also "Retrain": the
    settings are the saved ones, nothing is tuned, and holdout rows are never trained on.
    With `model_dir` (inside data/private/ only) the trained model is saved there, dated."""
    settings = settings or Settings()
    day = (today or date.today()).isoformat()
    if model is None:
        model, training = train(conn, account, settings)
    version = f"{day}, {training} labelled rows"
    before = stored_categories(conn, account)
    flagged_before = (
        conn.execute(
            "SELECT COALESCE(SUM(flagged), 0) FROM category_predictions WHERE account = ?",
            (account,),
        ).fetchone()[0]
        if before
        else None
    )
    last = conn.execute(
        "SELECT training_rows FROM categorizer_runs WHERE account = ? ORDER BY run_id DESC LIMIT 1",
        (account,),
    ).fetchone()
    model_file = None
    if model_dir is not None:
        if not is_private_path(model_dir):
            raise ValueError("a trained model holds merchant names: keep it in data/private/")
        model_dir.mkdir(parents=True, exist_ok=True)
        model_file = model_dir / f"categorizer_{account}_{day}.joblib"
        joblib.dump(model, model_file)
    df = predict(conn, account, model)
    df = categorizer_v2.apply_directory(df, categorizer_v2.load_options(conn))  # off: unchanged
    # pandas shows a missing category as NaN; the database and comparisons want None
    df["category"] = df["category"].astype("object").where(df["category"].notna(), None)
    df["merchant"] = df["merchant"].astype("object").where(df["merchant"].notna(), None)
    changed = (
        sum(before.get(f, c) != c for f, c in zip(df["fingerprint"], df["category"], strict=True))
        if before
        else None
    )
    with conn:
        conn.execute("DELETE FROM category_predictions WHERE account = ?", (account,))
        conn.executemany(
            "INSERT INTO category_predictions (fingerprint, account, merchant, flow_type,"
            " category, source, detail, flagged, confidence, model_version, predicted_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (r.fingerprint, account, r.merchant, r.flow_type, r.category, r.source, r.detail,
                 int(r.flagged), None if pd.isna(r.confidence) else float(r.confidence),
                 version, day)
                for r in df.itertuples()
            ],
        )  # fmt: skip
        conn.execute(
            "INSERT INTO categorizer_runs (account, run_at, model_version, training_rows,"
            " settings, rows, flagged) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (account, day, version, training,
             json.dumps({"params": {k: list(v) if isinstance(v, tuple) else v
                                    for k, v in settings.params.items()},
                         "threshold": None if settings.threshold in (None, math.inf)
                         else settings.threshold,
                         "synthetic_months": settings.synthetic_months}),
             len(df), int(df["flagged"].sum())),
        )  # fmt: skip
    corrected = set(corrections(conn, account))
    learned = corrected & set(training_rows(conn, account)["fingerprint"]) if corrected else set()
    return Summary(
        len(df), training, Counter(df["source"]), int(df["flagged"].sum()), changed, version,
        flagged_before, last[0] if last else None, model_file,
        len(learned), len(corrected) - len(learned),
    )  # fmt: skip


def model_dir_for(db_path: Path) -> Path | None:
    """data/private/models/ for a database kept in data/private/; None otherwise (the
    synthetic demo's model is not worth keeping)."""
    if not is_private_path(db_path):
        return None
    parts = db_path.resolve().parts
    index = max(i for i in range(len(parts) - 1) if parts[i : i + 2] == ("data", "private"))
    return Path(*parts[: index + 2]) / "models"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.categorize")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--account", default="scotia-chequing")
    parser.add_argument("--choice", type=Path, default=DEFAULT_CHOICE)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)
    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 2
    settings = load_settings(args.choice)
    conn = connect(args.db)
    s = categorize(conn, args.account, settings, model_dir=model_dir_for(args.db))
    from_llm = None
    if llm_fallback.is_on(conn):  # saved answers only: this command never sends anything
        try:
            from_llm = llm_fallback.apply(conn, args.account, pseudonym_key(args.env)).rows
        except PseudonymKeyError:
            print("LLM fallback is on but PSEUDONYM_KEY is missing: saved answers not applied")
    conn.close()
    saved = "saved by step 7a" if args.choice.exists() else "defaults (no saved choice)"
    print(f"Categorized {s.rows} rows, account {args.account}; settings: {saved}")
    print(f"model: trained on {s.training_rows} of your labelled rows plus synthetic rows "
          f"({settings.synthetic_months} months)")  # fmt: skip
    for source in SOURCES:
        print(f"  {source}: {s.by_source.get(source, 0)}")
    for source in sorted(set(s.by_source) - set(SOURCES)):  # categorizer v2, only when on
        print(f"  {source}: {s.by_source[source]}")
    if from_llm is not None:
        print(f"  of the model's rows, now from saved LLM answers (fallback is on): {from_llm}")
    print(f"flagged (worth a look, never blocking): {s.flagged}")
    if s.changed is not None:
        print(f"rows whose category changed since the last run: {s.changed} "
              f"(flagged before: {s.flagged_before})")  # fmt: skip
    if s.model_file is not None:
        print("model saved in data/private/models/")
    print(f"corrected rows: {s.corrections_trained} used for training, "
          f"{s.corrections_shown_only} shown only (holdout rows, or set by a rule)")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
