"""Calibration of the simulated futures (fitted by eval/calibrate.py on synthetic data only).

Two steps, both recorded in DECISIONS.md (2026-10-10, "Calibration ... method"):
- Bands: a multiplier s(h) for each horizon day widens every future around the middle future on
  that day: balance' = p50 + s(h) * (balance - p50). The 10th-90th band, each future's lowest
  balance and the shortfall probability are then read from the widened futures.
- The shortfall probability can then go through a monotone map (logistic or isotonic), if the
  fitting step kept one.

Nothing in the app uses this yet. Everything here is plain arithmetic on saved numbers, so a fitted
calibration can be applied to a saved backtest folder for evaluation without fitting anything.
"""

import json
import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EPS = 0.001  # probabilities are clipped to [0.1%, 99.9%] before taking log-odds


def _percentile(values: Sequence[int], share: float) -> int:
    """The same order statistic the simulation uses."""
    ordered = sorted(values)
    return ordered[round(share * (len(ordered) - 1))]


def needed_multiplier(p10: int, p50: int, p90: int, observed: int) -> float:
    """The smallest multiplier that puts `observed` inside the widened band (inf if the band has
    no spread on that side)."""
    if observed == p50:
        return 0.0
    if observed < p50:
        spread = p50 - p10
        return (p50 - observed) / spread if spread > 0 else math.inf
    spread = p90 - p50
    return (observed - p50) / spread if spread > 0 else math.inf


def fit_multiplier(needed: Sequence[float], target: float = 0.80) -> float:
    """The smallest multiplier that covers at least `target` of these cases."""
    ordered = sorted(needed)
    k = max(math.ceil(target * len(ordered)) - 1, 0)
    return ordered[k]


@dataclass(frozen=True)
class Recalibration:
    kind: str  # "none", "logistic" or "isotonic"
    coef: float = 1.0  # logistic: p = sigmoid(coef * logit(p') + intercept)
    intercept: float = 0.0
    x: tuple[float, ...] = ()  # isotonic: the step function's knots
    y: tuple[float, ...] = ()

    def __call__(self, p: float) -> float:
        if self.kind == "logistic":
            q = min(max(p, EPS), 1 - EPS)
            z = self.coef * math.log(q / (1 - q)) + self.intercept
            return 1 / (1 + math.exp(-z))
        if self.kind == "isotonic":
            return _interpolate(self.x, self.y, p)
        return p


def _interpolate(xs: Sequence[float], ys: Sequence[float], p: float) -> float:
    """Linear interpolation between knots, flat outside them (as scikit-learn's isotonic with
    out_of_bounds="clip")."""
    if not xs:
        return p
    if p <= xs[0]:
        return ys[0]
    if p >= xs[-1]:
        return ys[-1]
    i = bisect_right(xs, p)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 if x1 == x0 else y0 + (y1 - y0) * (p - x0) / (x1 - x0)


@dataclass(frozen=True)
class Calibration:
    multipliers: tuple[float, ...]  # s(h) for h = 1..30
    recalibration: Recalibration = field(default_factory=lambda: Recalibration("none"))
    fitted_on: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        r = self.recalibration
        return json.dumps({
            "multipliers": list(self.multipliers),
            "recalibration": {"kind": r.kind, "coef": r.coef, "intercept": r.intercept,
                              "x": list(r.x), "y": list(r.y)},
            "fitted_on": self.fitted_on,
        }, indent=2)  # fmt: skip

    @classmethod
    def from_json(cls, text: str) -> "Calibration":
        d = json.loads(text)
        r = d["recalibration"]
        return cls(
            tuple(d["multipliers"]),
            Recalibration(r["kind"], r["coef"], r["intercept"], tuple(r["x"]), tuple(r["y"])),
            d.get("fitted_on", {}),
        )

    @classmethod
    def load(cls, path: Path) -> "Calibration":
        return cls.from_json(path.read_text(encoding="utf-8"))


def widen_band(p10: Sequence[int], p50: Sequence[int], p90: Sequence[int],
               multipliers: Sequence[float]) -> tuple[list[int], list[int]]:  # fmt: skip
    """The widened 10th and 90th percentiles. Exact: widening every future around the median
    moves each percentile the same way."""
    lo = [round(m - s * (m - a)) for a, m, s in zip(p10, p50, multipliers, strict=False)]
    hi = [round(m + s * (b - m)) for b, m, s in zip(p90, p50, multipliers, strict=False)]
    return lo, hi


def widened_lows(paths: Sequence[Sequence[int]], p50: Sequence[int],
                 multipliers: Sequence[float]) -> list[int]:  # fmt: skip
    """Each widened future's lowest balance, sorted."""
    lows = []
    for path in paths:
        lows.append(min(round(m + s * (x - m))
                        for x, m, s in zip(path, p50, multipliers, strict=False)))  # fmt: skip
    return sorted(lows)


def calibrate_record(record: dict[str, Any], paths: Sequence[Sequence[int]] | None,
                     calibration: Calibration) -> dict[str, Any]:  # fmt: skip
    """A backtest record as the calibrated forecast would have read. Without paths only the band
    is changed (and the record says so)."""
    new = dict(record)
    s = calibration.multipliers
    new["p10"], new["p90"] = widen_band(record["p10"], record["p50"], record["p90"], s)
    if not paths:
        new["calibrated"] = "band only"
        return new
    lows = widened_lows(paths, record["p50"], s)
    buffer = record["buffer_cents"]
    raw = sum(low < buffer for low in lows) / len(lows)
    new["lowest_cents"] = lows
    new["median_lowest_cents"] = _percentile(lows, 0.5)
    new["widened_probability"] = raw
    new["shortfall_probability"] = calibration.recalibration(raw)
    new["calibrated"] = "full"
    return new
