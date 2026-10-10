# Step 2: proposals scored on the answer key (synthetic, development personas)

Made with `python -m eval.proposals_score`. Method, answer key and gates in DECISIONS.md (2026-10-11, step 2). 40 weekly origins per persona; every count is of (origin, proposal), (origin, series) or (origin, payment) pairs, since origins within a persona overlap. Each set on its own; never pooled.

## 12-month set

#### a) Lump proposals

| Group | Personas | Proposals | Precision | Findable (origin, series) | Recall | Date error, median days | Size error, median | False proposals per persona-year | Lumps without a sender |
|---|---|---|---|---|---|---|---|---|---|
| all | 20 | 49 | 82% (40/49) | 63 (7 series) | 56% (35/63) | 3.0 | 39% | 0.2 | 1% (4/651) |
| DHOME | 3 | 0 | - | 0 (0 series) | - | - | - | 0.0 | 0% (0/38) |
| DRENT | 3 | 1 | 100% (1/1) | 1 (1 series) | 100% (1/1) | 0 | 0% | 0.0 | 0% (0/36) |
| FREE | 1 | 0 | - | 0 (0 series) | - | - | - | 0.0 | 100% (4/4) |
| IFAM | 3 | 6 | 100% (6/6) | 6 (3 series) | 50% (3/6) | 17.0 | 2% | 0.0 | 0% (0/97) |
| ITIGHT | 3 | 42 | 79% (33/42) | 56 (3 series) | 55% (31/56) | 3 | 75% | 1.3 | 0% (0/476) |
| SAL | 3 | 0 | - | 0 (0 series) | - | - | - | 0.0 | - |
| SEM | 1 | 0 | - | 0 (0 series) | - | - | - | 0.0 | - |
| TIGHT | 3 | 0 | - | 0 (0 series) | - | - | - | 0.0 | - |

#### b) Repeated large outflows (default rule: earliest of a year after the latest and the typical gap)

| Group | Personas | Placements (tuition) | Precision | Recall, payee with 2+ payments | Recall, all tuition payments in a horizon | First-time cases | Date error, median days |
|---|---|---|---|---|---|---|---|
| all | 20 | 9 (9) | 67% (6/9) | 26% (6/23) | 7% (6/81) | 28 | 4.5 |
| DHOME | 3 | 1 (1) | 0% (0/1) | 0% (0/3) | 0% (0/11) | 4 | - |
| DRENT | 3 | 2 (2) | 0% (0/2) | 0% (0/3) | 0% (0/19) | 8 | - |
| FREE | 1 | 0 (0) | - | - | - | 0 | - |
| IFAM | 3 | 3 (3) | 100% (3/3) | 23% (3/13) | 10% (3/29) | 7 | 2 |
| ITIGHT | 3 | 3 (3) | 100% (3/3) | 75% (3/4) | 14% (3/22) | 9 | 7 |
| SAL | 3 | 0 (0) | - | - | - | 0 | - |
| SEM | 1 | 0 (0) | - | - | - | 0 | - |
| TIGHT | 3 | 0 (0) | - | - | - | 0 | - |

#### b) The two next-date rules alone and the default, all personas

| Rule | Placements | Precision | Recall, payee with 2+ payments |
|---|---|---|---|
| earliest | 9 | 67% (6/9) | 26% (6/23) |
| year | 0 | - | 0% (0/23) |
| gap | 9 | 67% (6/9) | 26% (6/23) |

a) proposals by the number of lumps behind them (proposals, right): 2: 14, 10, 3: 0, 0, 4+: 35, 30

## 24-month set

#### a) Lump proposals

| Group | Personas | Proposals | Precision | Findable (origin, series) | Recall | Date error, median days | Size error, median | False proposals per persona-year | Lumps without a sender |
|---|---|---|---|---|---|---|---|---|---|
| all | 20 | 81 | 69% (56/81) | 118 (9 series) | 38% (45/118) | 8.0 | 29% | 0.7 | 0% (0/1379) |
| DHOME | 3 | 8 | 100% (8/8) | 11 (1 series) | 55% (6/11) | 13.0 | 11% | 0.0 | 0% (0/101) |
| DRENT | 3 | 4 | 0% (0/4) | 9 (1 series) | 0% (0/9) | - | - | 0.4 | 0% (0/80) |
| FREE | 1 | 0 | - | 0 (0 series) | - | - | - | 0.0 | - |
| IFAM | 3 | 13 | 69% (9/13) | 20 (2 series) | 30% (6/20) | 19 | 21% | 0.4 | 0% (0/206) |
| ITIGHT | 3 | 56 | 70% (39/56) | 78 (5 series) | 42% (33/78) | 7 | 36% | 3.5 | 0% (0/992) |
| SAL | 3 | 0 | - | 0 (0 series) | - | - | - | 0.0 | - |
| SEM | 1 | 0 | - | 0 (0 series) | - | - | - | 0.0 | - |
| TIGHT | 3 | 0 | - | 0 (0 series) | - | - | - | 0.0 | - |

#### b) Repeated large outflows (default rule: earliest of a year after the latest and the typical gap)

| Group | Personas | Placements (tuition) | Precision | Recall, payee with 2+ payments | Recall, all tuition payments in a horizon | First-time cases | Date error, median days |
|---|---|---|---|---|---|---|---|
| all | 20 | 57 (57) | 68% (39/57) | 46% (37/81) | 46% (37/81) | 0 | 1 |
| DHOME | 3 | 9 (9) | 89% (8/9) | 64% (7/11) | 64% (7/11) | 0 | 3.5 |
| DRENT | 3 | 15 (15) | 73% (11/15) | 58% (11/19) | 58% (11/19) | 0 | 1 |
| FREE | 1 | 0 (0) | - | - | - | 0 | - |
| IFAM | 3 | 17 (17) | 71% (12/17) | 38% (11/29) | 38% (11/29) | 0 | 2.0 |
| ITIGHT | 3 | 16 (16) | 50% (8/16) | 36% (8/22) | 36% (8/22) | 0 | 0.0 |
| SAL | 3 | 0 (0) | - | - | - | 0 | - |
| SEM | 1 | 0 (0) | - | - | - | 0 | - |
| TIGHT | 3 | 0 (0) | - | - | - | 0 | - |

#### b) The two next-date rules alone and the default, all personas

| Rule | Placements | Precision | Recall, payee with 2+ payments |
|---|---|---|---|
| earliest | 57 | 68% (39/57) | 46% (37/81) |
| year | 0 | - | 0% (0/81) |
| gap | 57 | 68% (39/57) | 46% (37/81) |

a) proposals by the number of lumps behind them (proposals, right): 2: 10, 2, 3: 21, 20, 4+: 50, 34

## Gates (fixed before computing)

- a) precision >= 70% and recall >= 50% on at least one set: **pass** (precision / recall: 12-month 82% (40/49) / 56% (35/63); 24-month 69% (56/81) / 38% (45/118)).
- b) on the 24-month set, recall >= 50% among origins where the payee had 2+ payments and precision >= 70% (Yousef's addition): **fail** (recall 46% (37/81), precision 68% (39/57)).
