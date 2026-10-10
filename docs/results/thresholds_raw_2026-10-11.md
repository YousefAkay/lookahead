# Alert threshold on raw chances, both "caught" definitions (synthetic, development)

Made with `python -m eval.thresholds_raw` from the development backtest of the current forecaster (`python -m eval.backtest`: 500 futures, seed 42, weekday method). 20 development personas, 726 ordinary origins (of 800), 52 shortfall episodes. Rule: alert when the raw chance is above the threshold (no median clause), through the app's alert engine. Objective (DECISIONS.md, 2026-10-11, step 1): highest recall *on ahead (new)* with precision >= 70% and <= 0.1 false alerts per 30 days. Nothing changes in the app.

## The curve (all development personas)

| Rule | Recall, opened ahead (original) | Recall, on ahead (new) | Precision (true/alerts) | False alerts per 30 days | Lead time, original: mean (median) | Lead time, new: mean (median) |
|---|---|---|---|---|---|---|
| 2% | 50% (26/52) | 58% (30/52) | 45% (23/51) | 0.165 | 17.5 (21) | 20.6 (24) |
| 5% | 50% (26/52) | 58% (30/52) | 49% (22/45) | 0.136 | 16.9 (21) | 20.1 (24) |
| 10% | 50% (26/52) | 58% (30/52) | 63% (22/35) | 0.077 | 16.9 (21) | 20.1 (24) |
| 15% | 50% (26/52) | 58% (30/52) | 67% (22/33) | 0.065 | 16.7 (20) | 19.9 (24) |
| 20% | 50% (26/52) | 58% (30/52) | 71% (22/31) | 0.053 | 16.4 (20) | 19.7 (24) |
| 25% | 48% (25/52) | 56% (29/52) | 71% (22/31) | 0.053 | 16.8 (21) | 20.1 (24) |
| 30% | 48% (25/52) | 56% (29/52) | 73% (22/30) | 0.047 | 15.9 (16) | 19.6 (24) |
| 35% | 48% (25/52) | 56% (29/52) | 79% (22/28) | 0.035 | 16.5 (16) | 19.4 (24) |
| 40% | 48% (25/52) | 54% (28/52) | 78% (21/27) | 0.035 | 16.3 (16) | 19.4 (24) |
| 45% | 46% (24/52) | 52% (27/52) | 77% (20/26) | 0.035 | 16.2 (16) | 19.4 (24) |
| 50% | 46% (24/52) | 52% (27/52) | 81% (21/26) | 0.030 | 17.0 (18) | 19.4 (24) |
| 55% | 56% (29/52) | 58% (30/52) | 83% (24/29) | 0.030 | 17.5 (19) | 19.2 (22) |
| 60% | 58% (30/52) | 60% (31/52) | 84% (26/31) | 0.030 | 17.4 (20) | 19.1 (22) |
| 65% | 60% (31/52) | 62% (32/52) | 93% (25/27) | 0.012 | 15.5 (15) | 17.2 (16) |
| 70% | 60% (31/52) | 62% (32/52) | 92% (24/26) | 0.012 | 15.1 (15) | 16.8 (16) |
| 75% | 54% (28/52) | 56% (29/52) | 91% (21/23) | 0.012 | 15.3 (14) | 17.2 (16) |
| 80% | 58% (30/52) | 58% (30/52) | 92% (23/25) | 0.012 | 15.3 (14) | 17.1 (16) |
| 85% | 58% (30/52) | 58% (30/52) | 96% (24/25) | 0.006 | 15.0 (14) | 16.2 (16) |
| 90% **chosen** | 60% (31/52) | 62% (32/52) | 96% (24/25) | 0.006 | 13.8 (14) | 15.2 (14) |
| 95% | 56% (29/52) | 56% (29/52) | 96% (22/23) | 0.006 | 13.7 (14) | 14.9 (16) |
| provisional: above 20% or median lowest below the buffer | 50% (26/52) | 58% (30/52) | 71% (22/31) | 0.053 | 16.4 (20) | 19.7 (24) |

Precision, false alerts per 30 days, alert episodes and shortfall episodes are the same for both definitions: only the caught count and the lead time depend on the definition.

## Where the new definition adds caught episodes

- at 5%: v2-DRENT-03 +3, v2-TIGHT-03 +1
- at 20%: v2-DRENT-03 +3, v2-TIGHT-03 +1
- at 50%: v2-DRENT-03 +2, v2-TIGHT-03 +1
- at 95%: none
- provisional rule: v2-DRENT-03 +3, v2-TIGHT-03 +1

## Stop conditions (fixed before computing)

None holds: line c) continues.

## Result

On all development personas the objective picks **90%**: recall (new) 62% (32 of 52), precision 96%, 0.006 false alerts per 30 days, lead time 15.2 (14) days.

## Leave one persona out (threshold chosen on the others, applied to the held-back one)

Thresholds chosen across the 20 folds: 65%, 90%.

| Rule | Recall, opened ahead (original) | Recall, on ahead (new) | Precision (true/alerts) | False alerts per 30 days | Lead time, original: mean (median) | Lead time, new: mean (median) |
|---|---|---|---|---|---|---|
| held-back personas, added up | 54% (28/52) | 56% (29/52) | 91% (21/23) | 0.012 | 13.5 (14) | 15.2 (14) |

n is small: the shortfall episodes come from the few personas that have any, and origins within a persona overlap.
