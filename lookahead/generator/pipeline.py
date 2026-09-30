"""Generation pipeline: turns a persona into its full list of synthetic transactions.

Stages (each with its own random stream, so changing one never shifts another's draws):
1. scheduled money (pay, rent, bills, tuition, one-offs): no balance needed;
2. everyday spending: no balance needed;
3. balance-driven income (top-ups): walks the running balance of stages 1 and 2.
"""

from datetime import date

import numpy as np

from lookahead.generator.personas import Persona
from lookahead.generator.schedules import GeneratedTxn
from lookahead.generator.topups import apply_topups


def generate_persona(persona: Persona, start: date, end: date, seed: int) -> list[GeneratedTxn]:
    """All of a persona's transactions posted in [start, end], sorted by date."""
    scheduled_rng, spending_rng, topup_rng = (
        np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(3)
    )
    rows = persona.generate(start, end, scheduled_rng)
    if persona.spending is not None:
        rows += persona.spending.generate(start, end, spending_rng, persona.activity)
    if persona.topups:
        rows += apply_topups(
            rows, persona.opening_balance_cents, persona.topups, start, end, topup_rng
        )
    return sorted(rows, key=lambda t: t.posted_date)
