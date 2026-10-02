"""Synthetic training rows for the categorizer: the generator's personas with their answer
keys. Used as extra training data ("pre-taught chains", see DECISIONS.md) and by the evals.
"""

from datetime import date

import pandas as pd

from lookahead.categorizer import examples
from lookahead.flows import Txn
from lookahead.generator.personas import PERSONAS
from lookahead.generator.writer import export_rows

EXPORT_DATE = date(2026, 9, 30)


def _start(months: int) -> date:
    """First day of the month `months - 1` months before EXPORT_DATE's month."""
    index = EXPORT_DATE.year * 12 + EXPORT_DATE.month - 1 - (months - 1)
    return date(index // 12, index % 12 + 1, 1)


def synthetic_examples(months: int = 12, seed: int = 42) -> pd.DataFrame:
    """Rows the rules leave for the categorizer, from each persona (a separate account),
    with the answer key's category (`label`) and merchant (`merchant`)."""
    frames = []
    offset = 0
    for pid in "ABC":
        rows = export_rows(PERSONAS[pid], _start(months), EXPORT_DATE, seed)
        txns = [
            Txn(offset + i, t.posted_date, t.amount_cents, t.description, t.sub_description)
            for i, t in enumerate(rows)
        ]
        df = examples(txns)
        truth = {offset + i: t for i, t in enumerate(rows)}
        df["label"] = [truth[r].category for r in df["row_id"]]
        df["merchant"] = [truth[r].merchant for r in df["row_id"]]
        df["persona"] = pid
        frames.append(df)
        offset += len(rows)
    return pd.concat(frames, ignore_index=True)
