# Alert threshold on raw chances, both "caught" definitions (synthetic, development)

Made with `python -m eval.thresholds_raw` from the development backtest of the merged forecaster (keeps-coming on, options off; 500 futures, seed 42, weekday method). 20 development personas, 726 ordinary origins (of 800), 52 shortfall episodes. Rule: alert when the raw chance is above the threshold (no median clause), through the app's alert engine. Objective (DECISIONS.md, 2026-10-11 step 1, again 2026-10-12 step 3): highest recall *on ahead (new)*, in the reading of 2026-10-12, with precision >= 70% and <= 0.1 false alerts per 30 days. Shortfall episodes walk calendar days (R4). Nothing changes in the app.

## The curve (all development personas)

| Rule | Recall, opened ahead (original) | Recall, on ahead (new) | Precision (true/alerts; alerts that opened below the buffer are counted apart) | False alerts per 30 days | Lead time, original: mean (median) | Lead time, new: mean (median) |
|---|---|---|---|---|---|---|
| 2% | 48% (25/52) | 92% (48/52) | 43% (22/51; 38 opened below the buffer) | 0.171 | 17.0 (21) | 20.9 (24) |
| 5% | 48% (25/52) | 92% (48/52) | 47% (21/45; 38 opened below the buffer) | 0.142 | 16.4 (21) | 20.6 (22) |
| 10% | 48% (25/52) | 92% (48/52) | 62% (21/34; 38 opened below the buffer) | 0.077 | 16.4 (21) | 20.6 (22) |
| 15% | 48% (25/52) | 92% (48/52) | 66% (21/32; 38 opened below the buffer) | 0.065 | 16.1 (19) | 20.5 (22) |
| 20% | 48% (25/52) | 92% (48/52) | 69% (22/32; 37 opened below the buffer) | 0.059 | 15.8 (19) | 20.3 (22) |
| 25% | 46% (24/52) | 90% (47/52) | 68% (21/31; 37 opened below the buffer) | 0.059 | 16.2 (20) | 20.6 (23) |
| 30% | 46% (24/52) | 90% (47/52) | 70% (21/30; 37 opened below the buffer) | 0.053 | 15.3 (16) | 20.3 (21) |
| 35% **chosen** | 48% (25/52) | 90% (47/52) | 76% (22/29; 37 opened below the buffer) | 0.041 | 16.2 (16) | 20.0 (21) |
| 40% | 48% (25/52) | 88% (46/52) | 75% (21/28; 36 opened below the buffer) | 0.041 | 16.0 (16) | 20.0 (22) |
| 45% | 46% (24/52) | 87% (45/52) | 74% (20/27; 36 opened below the buffer) | 0.041 | 15.9 (16) | 20.0 (23) |
| 50% | 46% (24/52) | 87% (45/52) | 78% (21/27; 36 opened below the buffer) | 0.035 | 16.8 (18) | 20.0 (23) |
| 55% | 54% (28/52) | 83% (43/52) | 79% (23/29; 35 opened below the buffer) | 0.035 | 17.0 (18) | 19.5 (21) |
| 60% | 54% (28/52) | 83% (43/52) | 83% (24/29; 34 opened below the buffer) | 0.030 | 16.8 (18) | 19.3 (21) |
| 65% | 56% (29/52) | 83% (43/52) | 88% (23/26; 33 opened below the buffer) | 0.018 | 14.8 (15) | 18.0 (18) |
| 70% | 56% (29/52) | 83% (43/52) | 88% (22/25; 33 opened below the buffer) | 0.018 | 14.3 (14) | 17.7 (17) |
| 75% | 50% (26/52) | 77% (40/52) | 86% (19/22; 35 opened below the buffer) | 0.018 | 15.0 (14) | 17.9 (18) |
| 80% | 54% (28/52) | 77% (40/52) | 88% (21/24; 33 opened below the buffer) | 0.018 | 15.0 (14) | 17.9 (18) |
| 85% | 54% (28/52) | 77% (40/52) | 92% (22/24; 32 opened below the buffer) | 0.012 | 14.8 (14) | 17.1 (17) |
| 90% | 58% (30/52) | 75% (39/52) | 92% (23/25; 32 opened below the buffer) | 0.012 | 14.0 (14) | 16.1 (16) |
| 95% | 54% (28/52) | 65% (34/52) | 95% (21/22; 33 opened below the buffer) | 0.006 | 13.9 (15) | 16.0 (17) |
| provisional: above 20% or median lowest below the buffer | 48% (25/52) | 92% (48/52) | 69% (22/32; 37 opened below the buffer) | 0.059 | 15.8 (19) | 20.3 (22) |

Precision, false alerts per 30 days, alert episodes and shortfall episodes are the same for both definitions: only the caught count and the lead time depend on the definition.

## Where the new definition adds caught episodes

- at 5%: v2-DRENT-03 +8, v2-ITIGHT-01 +1, v2-ITIGHT-03 +3, v2-TIGHT-02 +4, v2-TIGHT-03 +7
- at 20%: v2-DRENT-03 +8, v2-ITIGHT-01 +1, v2-ITIGHT-03 +3, v2-TIGHT-02 +4, v2-TIGHT-03 +7
- at 50%: v2-DRENT-03 +7, v2-ITIGHT-01 +1, v2-ITIGHT-03 +2, v2-TIGHT-02 +4, v2-TIGHT-03 +7
- at 95%: v2-DRENT-03 +3, v2-TIGHT-02 +1, v2-TIGHT-03 +2
- provisional rule: v2-DRENT-03 +8, v2-ITIGHT-01 +1, v2-ITIGHT-03 +3, v2-TIGHT-02 +4, v2-TIGHT-03 +7

## Stop conditions (fixed before computing)

None of the three holds: a threshold is chosen.

The proposals run's fourth condition (information, not a stop check on 2026-10-12): recall rises by more than 5 points at 80%; recall rises by more than 5 points at 85%; recall rises by more than 5 points at 90%; recall rises by more than 5 points at 95%. **Under the stricter reading that keeps it as a stop check, no threshold would be chosen.**

## Result

On all development personas the objective picks **35%**: recall (new) 90% (47 of 52), precision 76%, 0.041 false alerts per 30 days, lead time 20.0 (21) days.

## Leave one persona out (threshold chosen on the others, applied to the held-back one)

Thresholds chosen across the 20 folds: 20%, 35%.

| Rule | Recall, opened ahead (original) | Recall, on ahead (new) | Precision (true/alerts; alerts that opened below the buffer are counted apart) | False alerts per 30 days | Lead time, original: mean (median) | Lead time, new: mean (median) |
|---|---|---|---|---|---|---|
| held-back personas, added up | 48% (25/52) | 90% (47/52) | 73% (22/30; 37 opened below the buffer) | 0.047 | 15.4 (16) | 20.0 (21) |

n is small: the shortfall episodes come from the few personas that have any, and origins within a persona overlap.
