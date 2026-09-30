"""Generation pipeline: turns a persona into its full list of synthetic transactions.

Stages (each with its own random stream, so changing one never shifts another's draws):
1. scheduled money (pay, rent, bills, tuition, one-offs): no balance needed;
2. everyday spending: no balance needed;
3. balance-driven income (top-ups): added in step 2c-ii.
"""

from datetime import date

import numpy as np

from lookahead.generator.personas import Persona
from lookahead.generator.schedules import GeneratedTxn


def generate_persona(persona: Persona, start: date, end: date, seed: int) -> list[GeneratedTxn]:
    """All of a persona's transactions posted in [start, end], sorted by date."""
    scheduled_rng, spending_rng = (
        np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(2)
    )
    rows = persona.generate(start, end, scheduled_rng)
    if persona.spending is not None:
        rows += persona.spending.generate(start, end, spending_rng, persona.activity)
    return sorted(rows, key=lambda t: t.posted_date)
