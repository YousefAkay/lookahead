# Experiment 3: how many origins meet the minimum history (counts only)

Made with `python -m eval.anchor_history report`. Method in DECISIONS.md (2026-10-17). Nothing is scored; no held-out account is read.

## Berka, primary development accounts

- Origins: 8,000. Meeting the minimum history (at least 91 days before the origin and at least 20 unscheduled rows in the 91 days): 561 (7.0%).
- Below it: 0 with under 91 days of history, 7,439 with enough history but under 20 unscheduled rows.
- accounts: 200; with every origin qualifying: 0; with no origin qualifying: 153; with at least half of their origins qualifying: 7.
- Unscheduled rows in the 91 days, origins with the history (min / 10% / 25% / median / 75% / 90% / max): 2 / 6 / 7 / 9 / 13 / 18 / 35.

| minimum unscheduled rows | origins qualifying | accounts with every origin |
|---|---|---|
| 5 | 7,626 (95.3%) | 173 |
| 8 | 5,329 (66.6%) | 59 |
| 10 | 3,456 (43.2%) | 32 |
| 12 | 2,507 (31.3%) | 13 |
| 15 | 1,545 (19.3%) | 5 |
| 20 (the rule) | 561 (7.0%) | 0 |

## Synthetic, 12-month set (20 development personas)

- Origins: 800. Meeting the minimum history (at least 91 days before the origin and at least 20 unscheduled rows in the 91 days): 700 (87.5%).
- Below it: 100 with under 91 days of history, 0 with enough history but under 20 unscheduled rows.
- personas: 20; with every origin qualifying: 0; with no origin qualifying: 0; with at least half of their origins qualifying: 20.
- Unscheduled rows in the 91 days, origins with the history (min / 10% / 25% / median / 75% / 90% / max): 48 / 87 / 99 / 112 / 132 / 164 / 216.

| minimum unscheduled rows | origins qualifying | personas with every origin |
|---|---|---|
| 5 | 700 (87.5%) | 0 |
| 8 | 700 (87.5%) | 0 |
| 10 | 700 (87.5%) | 0 |
| 12 | 700 (87.5%) | 0 |
| 15 | 700 (87.5%) | 0 |
| 20 (the rule) | 700 (87.5%) | 0 |

## Synthetic, 24-month set (20 development personas)

- Origins: 800. Meeting the minimum history (at least 91 days before the origin and at least 20 unscheduled rows in the 91 days): 800 (100.0%).
- Below it: 0 with under 91 days of history, 0 with enough history but under 20 unscheduled rows.
- personas: 20; with every origin qualifying: 20; with no origin qualifying: 0; with at least half of their origins qualifying: 20.
- Unscheduled rows in the 91 days, origins with the history (min / 10% / 25% / median / 75% / 90% / max): 24 / 84 / 102 / 116 / 139 / 159 / 214.

| minimum unscheduled rows | origins qualifying | personas with every origin |
|---|---|---|
| 5 | 800 (100.0%) | 20 |
| 8 | 800 (100.0%) | 20 |
| 10 | 800 (100.0%) | 20 |
| 12 | 800 (100.0%) | 20 |
| 15 | 800 (100.0%) | 20 |
| 20 (the rule) | 800 (100.0%) | 20 |

