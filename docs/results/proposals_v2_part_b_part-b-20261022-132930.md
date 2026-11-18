# Proposals for recurring settings: part (b) on the planted development copies

Run part-b-20261022-132930: planter version 3, seed 2026, code 936e3d0, 40 personas, 40 weekly origins, 2340 s. Synthetic, development only; part (c) is not run here. The real account only vetoes (a separate, counts-only script).

Caution on the possible-subscription precision: the card-pair rule of the proposer (exactly equal amounts a calendar month apart; commit a968bfc) was made after the planter was written and before any scoring, and it matches exactly what the planter plants, so precision on planted card pairs is likely flattering.

Not an independent check: the rent cluster rule, the 5 percent tolerance of the possible-subscription proposer and the planter additions of version 3 (noise and currency pairs) were chosen on 2026-10-21, and the veto's rent definition, the bug fix for the newest rent amount and the rent window over every send day on 2026-10-22, because of what my real-account veto run showed, so a veto rerun after them is expected to pass and shows that the changes work on my case, not that they would have worked blind.

## Per kind, both sets together (the adopt rule (b) is judged here)

| kind | planted events (n) | counted for recall | excluded (not yet observable) | proposals shown | precision | recall | adopt rule (b), (e) |
|---|---|---|---|---|---|---|---|
| rent | 30 | 30 | 0 | 25 | 44% (11/25) | 43% (13/30) | VETOED BY MY ACCOUNT (the definition of 3861996, the window): not adopted whatever (b) shows. (b) fails: precision 44% is under 80%; recall 43% is under 60% |
| link | 24 | 24 | 0 | 24 | 100% (24/24) | 100% (24/24) | (b) fails: precision 100% is above 95%: LEAKAGE CHECK FIRST |
| possible | 56 | 56 | 0 | 188 | 30% (56/188) | 100% (56/56) | (b) fails: precision 30% is under 80% |

Leakage flag (precision above 95 percent): link: a leakage check comes before anything is claimed.

Rule (b) per kind: precision at least 80 percent, recall at least 60 percent, at least 20 planted events. Nothing is adopted until part (c) is done as well.

## 12-month set alone (for information: n per set can be under 20)

| kind | planted events (n) | counted for recall | excluded (not yet observable) | proposals shown | precision | recall | adopt rule (b), (e) |
|---|---|---|---|---|---|---|---|
| rent | 15 | 15 | 0 | 13 | 54% (7/13) | 67% (10/15) | VETOED BY MY ACCOUNT (the definition of 3861996, the window): not adopted whatever (b) shows. (b) fails: only 15 planted events (need 20): cannot pass; precision 54% is under 80% |
| link | 12 | 12 | 0 | 12 | 100% (12/12) | 100% (12/12) | (b) fails: only 12 planted events (need 20): cannot pass; precision 100% is above 95%: LEAKAGE CHECK FIRST |
| possible | 26 | 26 | 0 | 146 | 18% (26/146) | 100% (26/26) | (b) fails: precision 18% is under 80% |

## 24-month set alone (for information: n per set can be under 20)

| kind | planted events (n) | counted for recall | excluded (not yet observable) | proposals shown | precision | recall | adopt rule (b), (e) |
|---|---|---|---|---|---|---|---|
| rent | 15 | 15 | 0 | 12 | 33% (4/12) | 20% (3/15) | VETOED BY MY ACCOUNT (the definition of 3861996, the window): not adopted whatever (b) shows. (b) fails: only 15 planted events (need 20): cannot pass; precision 33% is under 80%; recall 20% is under 60% |
| link | 12 | 12 | 0 | 12 | 100% (12/12) | 100% (12/12) | (b) fails: only 12 planted events (need 20): cannot pass; precision 100% is above 95%: LEAKAGE CHECK FIRST |
| possible | 30 | 30 | 0 | 42 | 71% (30/42) | 100% (30/30) | (b) fails: precision 71% is under 80% |

## By sub-kind (recall over the events counted; matched proposals)

| sub-kind | planted events (n) | counted | recovered | recall | proposals matching |
|---|---|---|---|---|---|
| rent: increase below 15% | 13 | 13 | 7 | 54% | 9 |
| rent: increase above 15% | 0 | 0 | 0 | - | 0 |
| rent: late payments | 17 | 17 | 6 | 35% | 2 |
| link: rename | 24 | 24 | 24 | 100% | 24 |
| possible: plain pairs | 35 | 35 | 35 | 100% | 53 |
| possible: currency pairs | 21 | 21 | 21 | 100% | 3 |

Precision is reported per kind only: a proposal that matches nothing cannot be assigned to a sub-kind. The planted rent increases are 5 to 12 percent before rounding, so none is expected above 15 percent.


## Variant ledger (docs/results/proposals_v2_ledger.json)
1. change: variant 1: rent judged on a cluster (from my real-account veto, 2026-10-21) (code 3aed29d, planter version 3)
2. change: variant 2: possible-subscription tolerance 5 percent (from my real-account veto, 2026-10-21) (code 3aed29d, planter version 3)
3. change: variant 3: planter version 3, noise and currency pairs (2026-10-21) (code 3aed29d, planter version 3)
4. bug fix: rent proposer: the latest amount of a size is allowed (2026-10-22) (code 1e5ad8a, planter version 3)
5. change: rent proposer: the window covers every send day when 14 days or less allow it (from my real-account veto, 2026-10-22) (code 1747eaa, planter version 3)
6. scored run: part (b), first scored run: planter version 3, proposers as committed at d668df5 (code d668df5, planter version 3, run part-b-20261022-132930)
