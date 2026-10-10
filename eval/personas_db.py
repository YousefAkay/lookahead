"""A development persona in a throw-away database, with the generator's answer key per row.

Used by the diagnosis and the experiments that need to know what each row really was. The
database is built exactly as the backtest builds it (same export, same import, same buffer), and
every stored row is matched to its generated row by (day, amount, balance after the row).
Held-out personas are refused: nothing here is for the final test.
"""

import gc
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lookahead.db import connect
from lookahead.generator.archetypes import END, START, MixPersona, assert_development_only
from lookahead.generator.archetypes import build_persona as _build
from lookahead.generator.schedules import GeneratedTxn
from lookahead.generator.writer import export_rows, write_export
from lookahead.ingest.scotiabank import parse_export
from lookahead.ingest.store import store_exports
from lookahead.settings import set_safety_buffer_cents


@dataclass
class PersonaDb:
    conn: sqlite3.Connection
    persona: MixPersona
    labels: dict[int, GeneratedTxn]  # stored row id -> the generated row (its answer key)


def _match(conn: sqlite3.Connection, account: str, rows: list[GeneratedTxn],
           opening: int) -> dict[int, GeneratedTxn]:  # fmt: skip
    keyed: dict[tuple[str, int, int], GeneratedTxn] = {}
    balance = opening
    for t in rows:
        balance += t.amount_cents
        keyed[(t.posted_date.isoformat(), t.amount_cents, balance)] = t
    out: dict[int, GeneratedTxn] = {}
    for row_id, day, cents, after in conn.execute(
        "SELECT id, posted_date, amount_cents, balance_after_cents FROM transactions"
        " WHERE account = ?", (account,)
    ):  # fmt: skip
        t = keyed.get((day, cents, after))
        if t is None:
            raise ValueError(f"stored row {row_id} has no generated row")
        out[row_id] = t
    return out


@contextmanager
def persona_db(persona_id: str, work_dir: Path, seed: int = 42) -> Iterator[PersonaDb]:
    """The persona imported into `work_dir/<id>/run.db`, deleted afterwards."""
    assert_development_only([persona_id])
    persona = _build(persona_id)
    folder = work_dir / persona_id
    folder.mkdir(parents=True, exist_ok=True)
    conn = None
    try:
        rows = export_rows(persona.persona, START, END, seed)
        export = folder / f"persona_{persona_id}.csv"
        write_export(rows, persona.persona.opening_balance_cents, export)
        conn = connect(folder / "run.db")
        store_exports(conn, persona_id, [(export.name, parse_export(export))],
                      datetime(2026, 10, 1))  # fmt: skip
        set_safety_buffer_cents(conn, persona.buffer_cents)
        labels = _match(conn, persona_id, rows, persona.persona.opening_balance_cents)
        yield PersonaDb(conn, persona, labels)
    finally:
        if conn is not None:
            conn.close()
        gc.collect()
        shutil.rmtree(folder, ignore_errors=True)
