"""Generation pipeline: turns a persona into its full list of synthetic transactions.

Each persona gets its own random streams: the seed is mixed with a stable hash of the
persona id, so two personas run with the same seed are still independent.

Stages (each with its own random stream, so changing one never shifts another's draws):
1. scheduled money (pay, rent, bills, tuition, one-offs): no balance needed;
2. everyday spending: no balance needed;
3. balance-driven income (top-ups): walks the running balance of stages 1 and 2;
4. bank description text: changes only the two text fields.
"""

import zlib
from datetime import date

import numpy as np

from lookahead.generator.descriptions import describe
from lookahead.generator.personas import Persona
from lookahead.generator.schedules import GeneratedTxn
from lookahead.generator.topups import apply_topups


def generate_persona(persona: Persona, start: date, end: date, seed: int) -> list[GeneratedTxn]:
    """All of a persona's transactions posted in [start, end], sorted by date."""
    scheduled_rng, spending_rng, topup_rng, text_rng = (
        np.random.default_rng(s)
        for s in np.random.SeedSequence([seed, zlib.crc32(persona.persona_id.encode())]).spawn(4)
    )
    rows = persona.generate(start, end, scheduled_rng)
    if persona.spending is not None:
        rows += persona.spending.generate(start, end, spending_rng, persona.activity)
    if persona.topups:
        rows += apply_topups(
            rows, persona.opening_balance_cents, persona.topups, start, end, topup_rng
        )
    return describe(sorted(rows, key=lambda t: t.posted_date), text_rng)
