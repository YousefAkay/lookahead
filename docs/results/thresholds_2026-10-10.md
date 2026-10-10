# Alert threshold on calibrated probabilities (synthetic, development personas)

Made with `python -m eval.thresholds` from the leave-one-persona-out records of `python -m eval.calibrate` (probability: widened futures + isotonic map). 20 development personas, 726 ordinary origins, 52 shortfall episodes. Rule: alert when the calibrated chance is above the threshold (no median clause), through the app's alert engine. Objective (DECISIONS.md, 2026-10-10): highest recall with precision >= 70% and <= 0.1 false alerts per 30 days. The app's provisional rule is unchanged.

## The curve (all 20 personas)

| Threshold | Recall (caught/episodes) | Precision (true/alerts) | False alerts per 30 days | Lead time, days: mean (median) |
|---|---|---|---|---|
| 2% | 56% (29/52) | 12% (29/243) | 1.263 | 17.8 (21) |
| 5% | 52% (27/52) | 38% (24/63) | 0.230 | 17.3 (21) |
| 10% | 52% (27/52) | 42% (24/57) | 0.195 | 17.1 (19) |
| 15% | 52% (27/52) | 43% (25/58) | 0.195 | 16.8 (16) |
| 20% | 52% (27/52) | 48% (25/52) | 0.159 | 16.0 (15) |
| 25% | 50% (26/52) | 59% (23/39) | 0.094 | 15.6 (14) |
| 30% | 56% (29/52) | 68% (25/37) | 0.071 | 15.5 (15) |
| 35% | 67% (35/52) | 71% (29/41) | 0.071 | 16.0 (16) |
| 40% | 65% (34/52) | 88% (29/33) | 0.024 | 16.3 (16) |
| 45% | 65% (34/52) | 88% (29/33) | 0.024 | 16.3 (16) |
| 50% | 63% (33/52) | 88% (28/32) | 0.024 | 16.2 (16) |
| 55% | 63% (33/52) | 88% (28/32) | 0.024 | 16.2 (16) |
| 60% | 63% (33/52) | 88% (28/32) | 0.024 | 16.2 (16) |
| 65% | 63% (33/52) | 88% (29/33) | 0.024 | 16.2 (16) |
| 70% | 63% (33/52) | 88% (29/33) | 0.024 | 16.2 (16) |
| 75% | 63% (33/52) | 91% (30/33) | 0.018 | 16.8 (16) |
| 80% | 65% (34/52) | 91% (30/33) | 0.018 | 15.3 (14) |
| 85% | 63% (33/52) | 90% (28/31) | 0.018 | 13.8 (14) |
| 90% | 63% (33/52) | 93% (28/30) | 0.012 | 13.2 (14) |
| 95% **chosen** | 67% (35/52) | 97% (29/30) | 0.006 | 12.8 (14) |

For reference, the provisional rule on the raw (uncalibrated) chances, chance above 20% or median lowest below the buffer:

| Threshold | Recall (caught/episodes) | Precision (true/alerts) | False alerts per 30 days | Lead time, days: mean (median) |
|---|---|---|---|---|
| provisional, raw | 50% (26/52) | 77% (24/31) | 0.041 | 16.9 (21) |

## Result

On all 20 development personas the objective picks **95%**: recall 67% (35 of 52 episodes), precision 97%, 0.006 false alerts per 30 days, lead time 12.8 (14) days.

## Leave one persona out (threshold chosen on 19, applied to the 20th)

Thresholds chosen across the 20 folds: 75%, 95%.

| Threshold | Recall (caught/episodes) | Precision (true/alerts) | False alerts per 30 days | Lead time, days: mean (median) |
|---|---|---|---|---|
| held-back personas, added up | 62% (32/52) | 96% (27/28) | 0.006 | 13.2 (12) |

n is small: the shortfall episodes come from the few personas that have any, and origins within a persona overlap.
