# Calibration: before and after (leave-one-persona-out, synthetic development personas)

Made with `python -m eval.calibrate`: 20 development personas, 800 origins, 500 futures, seed 42, weekday method. Method: DECISIONS.md, 2026-10-10. Each persona is scored with a calibration fitted on the other 19. Held-out personas were not used. The app is not switched to it.

## Band multipliers (fitted on all 20 development personas)

s(h) by horizon day: 1: 1.89, 2: 1.46, 3: 1.51, 4: 1.57, 5: 1.64, 6: 1.64, 7: 1.73, 8: 1.70, 9: 1.81, 10: 1.76, 11: 1.78, 12: 1.84, 13: 1.84, 14: 1.95, 15: 2.04, 16: 2.09, 17: 2.07, 18: 2.20, 19: 2.18, 20: 2.27, 21: 2.59, 22: 2.64, 23: 2.70, 24: 2.67, 25: 2.94, 26: 2.93, 27: 2.96, 28: 3.06, 29: 3.35, 30: 3.46

Across the 20 leave-one-out fits: day 7 from 1.72 to 1.81; day 14 from 1.88 to 2.01; day 30 from 2.86 to 3.67.

## Probability map

Leave-one-persona-out Brier score, origins not already in shortfall (726 origins): widened only 0.0665, widened + logistic 0.0789, widened + isotonic 0.0616.

Chosen by the rule fixed before fitting: **isotonic**. Final map: isotonic.

## Before and after (widened + isotonic)

| Metric | Before | After |
|---|---|---|
| **origins not already in shortfall** (n) | 726 origins, 20 personas | 726 origins, 20 personas |
| 80% band coverage, 7 days | 62% | 80% |
| 80% band coverage, 14 days | 58% | 80% |
| 80% band coverage, 30 days | 48% | 80% |
| 80% band coverage, all  | 58% | 80% |
| band width (mean), 7 days | $415 | $729 |
| band width (mean), 14 days | $619 | $1,220 |
| band width (mean), 30 days | $903 | $3,135 |
| path-level coverage | 10% | 42% |
| lowest balance: bias / typical miss | $716 / $994 | $537 / $1,041 |
| Brier (reference, skill) | 0.058 (0.143, 0.60) | 0.062 (0.143, 0.57) |
| **all origins** (n) | 800 origins, 20 personas | 800 origins, 20 personas |
| 80% band coverage, 7 days | 62% | 80% |
| 80% band coverage, 14 days | 59% | 80% |
| 80% band coverage, 30 days | 49% | 80% |
| 80% band coverage, all  | 58% | 80% |
| band width (mean), 7 days | $415 | $730 |
| band width (mean), 14 days | $625 | $1,229 |
| band width (mean), 30 days | $911 | $3,147 |
| path-level coverage | 10% | 41% |
| lowest balance: bias / typical miss | $638 / $927 | $462 / $976 |
| Brier (reference, skill) | 0.060 (0.184, 0.67) | 0.066 (0.184, 0.64) |

## By archetype (origins not already in shortfall)

| Archetype | Origins (personas) | Brier before | Brier after | band coverage, all days, before / after | lowest-balance bias before / after |
|---|---|---|---|---|---|
| DHOME | 120 (3) | 0.015 | 0.020 | 50% / 78% | $186 / $108 |
| DRENT | 102 (3) | 0.067 | 0.071 | 48% / 67% | $119 / $53 |
| FREE | 40 (1) | 0.000 | 0.012 | 74% / 97% | $77 / -$1,032 |
| IFAM | 120 (3) | 0.000 | 0.003 | 58% / 74% | $594 / $322 |
| ITIGHT | 86 (3) | 0.276 | 0.264 | 38% / 60% | $4,588 / $4,319 |
| SAL | 120 (3) | 0.000 | 0.002 | 68% / 92% | $64 / $29 |
| SEM | 40 (1) | 0.000 | 0.003 | 69% / 93% | $69 / -$22 |
| TIGHT | 98 (3) | 0.096 | 0.114 | 72% / 93% | $64 / $2 |

## Reliability of the shortfall probability

Cell: origins, mean forecast, observed share (origins not already in shortfall).

| Forecast bin | Before | After: widened | After: logistic | After: isotonic |
|---|---|---|---|---|
| 0-20% | 605, 0%, 4% | 519, 2%, 4% | 498, 4%, 4% | 582, 4%, 5% |
| 20-40% | 21, 32%, 62% | 69, 29%, 9% | 126, 30%, 15% | 43, 27%, 28% |
| 40-60% | 15, 51%, 60% | 53, 49%, 32% | 59, 47%, 73% | 13, 46%, 23% |
| 60-80% | 16, 66%, 69% | 33, 70%, 91% | 14, 68%, 100% | 4, 69%, 50% |
| 80-100% | 69, 97%, 96% | 52, 95%, 98% | 29, 93%, 97% | 84, 96%, 94% |

## Comparison only: a map on the raw chance (added after seeing the results above)

Not part of the method written before fitting and never chosen by it. Brier score, leave-one-persona-out, origins not already in shortfall.

| Probability | all | DHOME | DRENT | FREE | IFAM | ITIGHT | SAL | SEM | TIGHT |
|---|---|---|---|---|---|---|---|---|---|
| raw | 0.0576 | 0.015 | 0.067 | 0.000 | 0.000 | 0.276 | 0.000 | 0.000 | 0.096 |
| raw + logistic | 0.0577 | 0.027 | 0.069 | 0.002 | 0.001 | 0.257 | 0.001 | 0.005 | 0.091 |
| raw + isotonic | 0.0582 | 0.025 | 0.082 | 0.002 | 0.002 | 0.251 | 0.002 | 0.002 | 0.088 |
| widened + isotonic (the method) | 0.0616 | 0.020 | 0.071 | 0.012 | 0.003 | 0.264 | 0.002 | 0.003 | 0.114 |
