"""Generation pipeline: turns a persona into its full list of synthetic transactions.

Each persona gets its own random streams: the seed is mixed with a stable hash of the
persona id, so two personas run with the same seed are still independent.

Stages (each with its own random stream, so changing one never shifts another's draws):
1. scheduled money (pay, rent, bills, tuition, one-offs): no balance needed;
2+3. day-by-day simulation (simulate.py): everyday spending that reacts to the balance
     (overdraft limit, cutting back when low) and balance-triggered top-ups;
(quirks) planted card quirks: pre-authorization holds, returns, duplicates (only adds
     rows, plus link/quirk on the rows they pair with);
4. bank description text: changes only the two text fields.
"""

import zlib
from datetime import date

import numpy as np

from lookahead.generator.descriptions import describe
from lookahead.generator.personas import Persona
from lookahead.generator.quirks import plant_card_quirks
from lookahead.generator.schedules import GeneratedTxn
from lookahead.generator.simulate import simulate


def generate_persona(persona: Persona, start: date, end: date, seed: int) -> list[GeneratedTxn]:
    """All of a persona's transactions posted in [start, end], sorted by date."""
    scheduled_rng, spending_rng, topup_rng, text_rng, quirk_rng = (
        np.random.default_rng(s)
        for s in np.random.SeedSequence([seed, zlib.crc32(persona.persona_id.encode())]).spawn(5)
    )
    rows = persona.generate(start, end, scheduled_rng)
    rows += simulate(persona, rows, start, end, spending_rng, topup_rng)
    rows = sorted(rows, key=lambda t: t.posted_date)
    rows = plant_card_quirks(rows, persona.persona_id, start, end, quirk_rng)
    return describe(rows, text_rng)
