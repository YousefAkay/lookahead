# Step 1: b) by calendar position, scored on the answer key (synthetic, development personas)

Made with `python -m eval.proposals_score --calendar`. Method, answer key and gate in DECISIONS.md (2026-10-12, step 1). 40 weekly origins per persona; counts are of (origin, placement) and (origin, tuition payment) pairs, since origins within a persona overlap. Recall is among the origins where the payee had 2 or more big payments by the origin. Date error: median days, right placements. Each set on its own; never pooled. `earliest` is the earlier default (the proposals run), rerun here as a check that it reproduces.

## 12-month set

| Group | Personas | calendar: placements (tuition) / precision / recall, payee with 2+ payments / date error | earliest: placements (tuition) / precision / recall, payee with 2+ payments / date error |
|---|---|---|---|
| all | 20 | 0 (0) / - / 0% (0/23) / - | 9 (9) / 67% (6/9) / 26% (6/23) / 4.5 |
| two terms (January, September) | 3 | 0 (0) / - / - / - | 0 (0) / - / - / - |
| three terms (with summer) | 4 | 0 (0) / - / 0% (0/23) / - | 9 (9) / 67% (6/9) / 26% (6/23) / 4.5 |
| DHOME | 3 | 0 (0) / - / 0% (0/3) / - | 1 (1) / 0% (0/1) / 0% (0/3) / - |
| DRENT | 3 | 0 (0) / - / 0% (0/3) / - | 2 (2) / 0% (0/2) / 0% (0/3) / - |
| FREE | 1 | 0 (0) / - / - / - | 0 (0) / - / - / - |
| IFAM | 3 | 0 (0) / - / 0% (0/13) / - | 3 (3) / 100% (3/3) / 23% (3/13) / 2 |
| ITIGHT | 3 | 0 (0) / - / 0% (0/4) / - | 3 (3) / 100% (3/3) / 75% (3/4) / 7 |
| SAL | 3 | 0 (0) / - / - / - | 0 (0) / - / - / - |
| SEM | 1 | 0 (0) / - / - / - | 0 (0) / - / - / - |
| TIGHT | 3 | 0 (0) / - / - / - | 0 (0) / - / - / - |

Tuition payments inside a horizon (origin, payment): 81; of them with the payee seen once by the origin (first-time cases, no placement possible): 28; never seen: 30. First-time offers for a tuition payee (origin, offer): 158.

## 24-month set

| Group | Personas | calendar: placements (tuition) / precision / recall, payee with 2+ payments / date error | earliest: placements (tuition) / precision / recall, payee with 2+ payments / date error |
|---|---|---|---|
| all | 20 | 79 (79) / 96% (76/79) / 93% (75/81) / 3.5 | 57 (57) / 68% (39/57) / 46% (37/81) / 1 |
| two terms (January, September) | 3 | 23 (23) / 91% (21/23) / 91% (21/23) / 4 | 13 (13) / 0% (0/13) / 0% (0/23) / - |
| three terms (with summer) | 4 | 56 (56) / 98% (55/56) / 93% (54/58) / 3 | 44 (44) / 89% (39/44) / 64% (37/58) / 1 |
| DHOME | 3 | 12 (12) / 100% (12/12) / 100% (11/11) / 3.0 | 9 (9) / 89% (8/9) / 64% (7/11) / 3.5 |
| DRENT | 3 | 20 (20) / 90% (18/20) / 95% (18/19) / 3.0 | 15 (15) / 73% (11/15) / 58% (11/19) / 1 |
| FREE | 1 | 0 (0) / - / - / - | 0 (0) / - / - / - |
| IFAM | 3 | 27 (27) / 96% (26/27) / 90% (26/29) / 4.0 | 17 (17) / 71% (12/17) / 38% (11/29) / 2.0 |
| ITIGHT | 3 | 20 (20) / 100% (20/20) / 91% (20/22) / 1.0 | 16 (16) / 50% (8/16) / 36% (8/22) / 0.0 |
| SAL | 3 | 0 (0) / - / - / - | 0 (0) / - / - / - |
| SEM | 1 | 0 (0) / - / - / - | 0 (0) / - / - / - |
| TIGHT | 3 | 0 (0) / - / - / - | 0 (0) / - / - / - |

Tuition payments inside a horizon (origin, payment): 81; of them with the payee seen once by the origin (first-time cases, no placement possible): 0; never seen: 0. First-time offers for a tuition payee (origin, offer): 0.

## Gate (fixed before computing)

- b) by calendar position, on the 24-month set: recall >= 50% among origins where the payee had 2+ payments, and precision >= 70%: **pass** (recall 93% (75/81), precision 96% (76/79)).
