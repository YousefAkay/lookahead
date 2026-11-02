"""Experiment 4: online combination with "balance stays" (synthetic development personas and Berka
development accounts).

The rule is in DECISIONS.md (2026-10-17, "Experiment 4", commit e510b1b). It reads the default
forecaster's saved futures (eval.futures_store) and simulates nothing.

* The combination (recentre). With s the balance at the origin, x_f(h) future f on day h and m(h)
  the forecast's median that day, the combined future is x_f(h) - (1 - w)(m(h) - s). Every future
  slides by the same amount each day, so the band width is unchanged and the combined median is
  w m(h) + (1 - w) s. w = 1 is today's forecast, w = 0 is "balance stays".
* The weights come from the grid 0, 0.1, ..., 1; ties go to the larger weight.
* The loss of a weight at one origin is the mean over days 1 to 30 of the exact CRPS of the
  combined futures.

The median m(h) is the forecast's p50 (the simulator's nearest-rank percentile, as saved in the
records). Held-out personas and held-out Berka accounts are refused.
"""

import json
from pathlib import Path
from typing import Any

import numpy as np

from eval.baselines import crps_samples
from eval.berka_split import assert_development_only as berka_development_only
from eval.futures_store import load, nearest_rank
from lookahead.generator.archetypes import assert_development_only

GRID = tuple(k / 10 for k in range(11))

Record = dict[str, Any]


# --- The combination ---


def shift(start_cents: int, median: np.ndarray, w: float) -> np.ndarray:
    """What is taken off every future on days 1 .. 30: (1 - w)(m(h) - s)."""
    return (1 - w) * (np.asarray(median, dtype=float) - start_cents)


def combined(paths: np.ndarray, start_cents: int, median: np.ndarray, w: float) -> np.ndarray:
    """The combined futures (futures x days), in cents, not rounded."""
    return np.asarray(paths, dtype=float) - shift(start_cents, median, w)


def losses(
    paths: np.ndarray, start_cents: int, median: np.ndarray, realized: np.ndarray
) -> np.ndarray:  # fmt: skip
    """The loss of each weight of the grid at one origin: the mean over the 30 days of the exact
    CRPS of the combined futures."""
    return np.array([
        float(crps_samples(combined(paths, start_cents, median, w), realized).mean()) for w in GRID
    ])  # fmt: skip


def best_weight(mean_losses: np.ndarray) -> float:
    """The weight of the grid with the lowest loss; a tie goes to the larger weight."""
    best = len(GRID) - 1
    for i in range(len(GRID) - 2, -1, -1):
        if mean_losses[i] < mean_losses[best]:
            best = i
    return GRID[best]


def view(
    paths: np.ndarray, start_cents: int, buffer_cents: int, median: np.ndarray,
    realized: np.ndarray, w: float,
) -> Record:  # fmt: skip
    """The numbers of one origin as the combination with weight `w` makes them. The band and the
    lowest balances are read from the combined futures; w = 1 gives the default exactly."""
    x = combined(paths, start_cents, median, w)
    lows = x.min(axis=1)

    def cents(values: np.ndarray) -> list[int]:
        return np.rint(values).astype(int).tolist()

    return {
        "w": w,
        "p10": cents(nearest_rank(x, 0.1)),
        "p50": cents(nearest_rank(x, 0.5)),
        "p90": cents(nearest_rank(x, 0.9)),
        "shortfall_probability": float((lows < buffer_cents).mean()),
        "median_lowest_cents": int(np.rint(nearest_rank(lows, 0.5))),
        "crps": crps_samples(x, np.asarray(realized, dtype=float)).round(1).tolist(),
        "moved": bool(w < 1 and np.any(np.asarray(median) != start_cents)),
    }


# --- Reading the saved futures ---

# The fields of an experiment 3 record that say what happened (and who and when); the forecast's
# own numbers come from the saved futures.
OUTCOME_FIELDS = (
    "persona", "archetype", "origin", "days", "start_balance_cents", "buffer_cents", "point",
    "realized", "realized_lowest_cents", "realized_first_below_buffer", "in_shortfall_at_origin",
    "crps_stays",
)  # fmt: skip


def read_records(path: Path) -> dict[str, Record]:
    """An experiment 3 records file, by origin date."""
    rows = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x]
    return {r["origin"]: r for r in rows}


def load_persona(pid: str, store: Path, records: Path) -> list[Record]:
    """One development persona's origins, oldest first: its saved futures with the outcome fields
    of its experiment 3 records. A held-out persona is refused before any file is opened."""
    assert_development_only([pid])
    data = load(store / f"{pid}.npz")
    if str(data["account"]) != pid:
        raise ValueError(f"{store / pid}.npz holds another persona")
    by_origin = read_records(records / f"{pid}.jsonl")
    out = []
    for i, origin in enumerate(data["origin"].tolist()):
        rec = by_origin[origin]
        if rec.get("held_out"):
            raise ValueError("a held-out record is not for tuning or checking")
        if (rec["start_balance_cents"], rec["buffer_cents"]) != (
            int(data["start_cents"][i]), int(data["buffer_cents"][i])
        ):  # fmt: skip
            raise ValueError(f"{pid} {origin}: the store and the records disagree")
        out.append({**{k: rec[k] for k in OUTCOME_FIELDS}, "paths": data["paths"][i],
                    "account": pid})  # fmt: skip
    return sorted(out, key=lambda r: r["origin"])


def load_account(account: int, store: Path) -> dict[str, Any]:
    """One Berka development account's saved futures. A held-out account is refused before any
    file is opened."""
    berka_development_only([account])
    return load(store / f"{account}.npz")
