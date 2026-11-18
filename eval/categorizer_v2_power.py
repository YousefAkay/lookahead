"""What the categorizer v2 adopt rule can detect, on made-up numbers only.

    python -m eval.categorizer_v2_power

150 group A rows in 75 merchants (2 rows each), (a) right on 60% of rows. For a true gain g
and a share `hurt` of the rows (a) gets right that the variant breaks, it simulates 200 blind
sets and counts how often the variant passes the rule's gain test: at least 3 points and the
merchant-level bootstrap 2.5th percentile above 0 (2,000 draws, seed 0, as in the harness).
Each merchant's rows move together. Not a result: no real row is read.
"""

import numpy as np

from eval.categorizer_v2 import MIN_GAIN, paired_bootstrap

MERCHANTS, ROWS_EACH, BASE_ACCURACY, TRIALS = 75, 2, 0.6, 200


def pass_rate(gain: float, hurt: float, rng: np.random.Generator) -> float:
    """Share of simulated blind sets on which the variant passes the gain test."""
    groups = [f"m{i}" for i in range(MERCHANTS) for _ in range(ROWS_EACH)]
    n = MERCHANTS * ROWS_EACH
    passed = 0
    for _ in range(TRIALS):
        a = rng.random(n) < BASE_ACCURACY
        fix = np.repeat(rng.random(MERCHANTS), ROWS_EACH) < (gain + hurt * BASE_ACCURACY) / (
            1 - BASE_ACCURACY
        )
        broken = np.repeat(rng.random(MERCHANTS), ROWS_EACH) < hurt
        b = np.where(a, ~broken, fix)
        got, low = paired_bootstrap(a, b, groups)
        passed += got >= MIN_GAIN - 1e-9 and low > 0
    return passed / TRIALS


def main() -> None:
    rng = np.random.default_rng(1)
    print(f"made-up data: {MERCHANTS * ROWS_EACH} rows in {MERCHANTS} merchants, {TRIALS} trials")
    for hurt in (0.0, 0.05):
        for gain in (0.03, 0.05, 0.08, 0.10):
            print(f"  breaks {hurt:.0%} of (a)'s right rows, true gain {gain * 100:.0f} points: "
                  f"passes {pass_rate(gain, hurt, rng):.0%}")  # fmt: skip


if __name__ == "__main__":
    main()
