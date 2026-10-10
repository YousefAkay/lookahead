# Calibration: before and after (leave-one-persona-out, synthetic development personas)

Made with `python -m eval.calibrate`: 20 development personas, 800 origins, 500 futures, seed 42, weekday method. Method: DECISIONS.md, 2026-10-10. Each persona is scored with a calibration fitted on the other 19. Held-out personas were not used. The app is not switched to it.

## Band multipliers (fitted on all 20 development personas)

s(h) by horizon day: 1: 1.89, 2: 1.49, 3: 1.59, 4: 2.00, 5: 1.90, 6: 1.95, 7: 2.38, 8: 2.61, 9: 2.62, 10: 2.89, 11: 3.53, 12: 3.55, 13: 3.59, 14: 4.11, 15: 4.66, 16: 4.69, 17: 4.53, 18: 4.61, 19: 4.50, 20: 4.50, 21: 5.23, 22: 5.44, 23: 5.36, 24: 5.42, 25: 5.58, 26: 5.50, 27: 5.50, 28: 6.00, 29: 6.43, 30: 6.42

Across the 20 leave-one-out fits: day 7 from 2.17 to 2.64; day 14 from 3.79 to 4.61; day 30 from 4.86 to 6.92.

## Probability map

Leave-one-persona-out Brier score, origins not already in shortfall (726 origins): widened only 0.0919, widened + logistic 0.0943, widened + isotonic 0.0642.

Chosen by the rule fixed before fitting: **isotonic**. Final map: isotonic.

## Before and after (widened + isotonic)

| Metric | Before | After |
|---|---|---|
| **origins not already in shortfall** (n) | 726 origins, 20 personas | 726 origins, 20 personas |
| 80% band coverage, 7 days | 57% | 80% |
| 80% band coverage, 14 days | 51% | 80% |
| 80% band coverage, 30 days | 41% | 79% |
| 80% band coverage, all  | 52% | 80% |
| band width (mean), 7 days | $394 | $946 |
| band width (mean), 14 days | $591 | $2,542 |
| band width (mean), 30 days | $849 | $5,520 |
| path-level coverage | 8% | 46% |
| lowest balance: bias / typical miss | $724 / $1,007 | $257 / $1,231 |
| Brier (reference, skill) | 0.059 (0.143, 0.58) | 0.064 (0.143, 0.55) |
| **all origins** (n) | 800 origins, 20 personas | 800 origins, 20 personas |
| 80% band coverage, 7 days | 57% | 80% |
| 80% band coverage, 14 days | 52% | 80% |
| 80% band coverage, 30 days | 43% | 79% |
| 80% band coverage, all  | 52% | 80% |
| band width (mean), 7 days | $388 | $931 |
| band width (mean), 14 days | $586 | $2,513 |
| band width (mean), 30 days | $841 | $5,452 |
| path-level coverage | 8% | 45% |
| lowest balance: bias / typical miss | $642 / $940 | $189 / $1,164 |
| Brier (reference, skill) | 0.062 (0.184, 0.66) | 0.069 (0.184, 0.62) |

## By archetype (origins not already in shortfall)

| Archetype | Origins (personas) | Brier before | Brier after | band coverage, all days, before / after | lowest-balance bias before / after |
|---|---|---|---|---|---|
| DHOME | 120 (3) | 0.015 | 0.021 | 48% / 86% | $189 / -$60 |
| DRENT | 102 (3) | 0.063 | 0.073 | 46% / 77% | $126 / -$83 |
| FREE | 40 (1) | 0.000 | 0.063 | 74% / 96% | $73 / -$3,064 |
| IFAM | 120 (3) | 0.000 | 0.006 | 51% / 68% | $594 / -$1 |
| ITIGHT | 86 (3) | 0.302 | 0.263 | 24% / 51% | $4,568 / $4,017 |
| SAL | 120 (3) | 0.000 | 0.001 | 64% / 91% | $69 / -$51 |
| SEM | 40 (1) | 0.000 | 0.003 | 49% / 85% | $129 / -$65 |
| TIGHT | 98 (3) | 0.089 | 0.108 | 68% / 92% | $96 / -$119 |

## Reliability of the shortfall probability

Cell: origins, mean forecast, observed share (origins not already in shortfall).

| Forecast bin | Before | After: widened | After: logistic | After: isotonic |
|---|---|---|---|---|
| 0-20% | 611, 1%, 5% | 408, 4%, 4% | 467, 6%, 4% | 566, 4%, 5% |
| 20-40% | 19, 31%, 63% | 110, 29%, 3% | 206, 29%, 27% | 55, 28%, 24% |
| 40-60% | 15, 51%, 60% | 110, 50%, 18% | 25, 44%, 92% | 20, 41%, 5% |
| 60-80% | 16, 66%, 75% | 54, 68%, 78% | 7, 67%, 100% | 8, 77%, 88% |
| 80-100% | 65, 97%, 97% | 44, 94%, 98% | 21, 92%, 95% | 77, 95%, 95% |

## Comparison only: a map on the raw chance (added after seeing the results above)

Not part of the method written before fitting and never chosen by it. Brier score, leave-one-persona-out, origins not already in shortfall.

| Probability | all | DHOME | DRENT | FREE | IFAM | ITIGHT | SAL | SEM | TIGHT |
|---|---|---|---|---|---|---|---|---|---|
| raw | 0.0592 | 0.015 | 0.063 | 0.000 | 0.000 | 0.302 | 0.000 | 0.000 | 0.089 |
| raw + logistic | 0.0584 | 0.030 | 0.067 | 0.003 | 0.001 | 0.272 | 0.001 | 0.006 | 0.081 |
| raw + isotonic | 0.0588 | 0.026 | 0.078 | 0.002 | 0.002 | 0.271 | 0.002 | 0.003 | 0.078 |
| widened + isotonic (the method) | 0.0642 | 0.021 | 0.073 | 0.063 | 0.006 | 0.263 | 0.001 | 0.003 | 0.108 |
