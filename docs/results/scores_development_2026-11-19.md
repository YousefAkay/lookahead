# Development scores, synthetic and Berka (2026-11-19)

Made with `python -m eval.berka_scores report`. The default forecaster (final, DECISIONS 2026-11-19), 40 weekly origins per persona or account, 500 futures, seed 42. Development data only: nothing held out was opened. Scores are on ordinary origins (not already below the line) where the random walk is defined; dips caught use all origins, as the alert engine sees them. Synthetic: the 2026-11-19 baselines rerun. Berka amounts are in scaled units (1 unit = 20 koruna: 5 cents per koruna, shown in hundreds of cents), not dollars, and its line is "fell below one month's outflow".

| | Synthetic, 12-month set | Synthetic, 24-month set | Berka, primary development | Berka, zero-estimate origins (only) |
|---|---|---|---|---|
| origins (units) | 800 (20) | 800 (20) | 8,000 (200) | 512 (83) |
| scored origins n (units) | 726 (20) | 717 (20) | 7,135 (200) | 466 (81) |
| dips among them | 125 | 123 | 818 | 44 |
| CRPS skill vs balance stays, mean of 30 days | +28.3% | +33.6% | +27.8% | +25.4% |
| CRPS skill vs random walk, mean of 30 days | +10.0% | +9.2% | +3.0% | +0.4% |
| CRPS skill vs balance stays, day 30 | +16.9% | +22.9% | +10.7% | +6.7% |
| CRPS skill vs random walk, day 30 | -4.2% | -11.4% | -20.5% | -28.6% |
| CRPS forecast / stays / walk, mean of 30 days | 1,000 / 1,395 / 1,111 | 888 / 1,336 / 977 | 305 / 422 / 314 | 205 / 274 / 205 |
| 80% band coverage, day 30 | 49.0% | 58.7% | 75.3% | 44.2% |
| 80% band coverage, all 30 days | 58.9% | 63.1% | 57.0% | 38.2% |
| Brier of the dip chance: forecast / stays / walk | 0.0599 / 0.1722 / 0.0777 | 0.0340 / 0.1715 / 0.0597 | 0.0772 / 0.1146 / 0.0729 | 0.0675 / 0.0944 / 0.0625 |
| Brier skill vs stays / vs walk | +65.2% / +23.0% | +80.2% / +43.0% | +32.7% / -5.9% | +28.5% / -8.1% |
| dips caught, forecast (in-sample threshold) | threshold 0.35: recall on ahead 47/52; precision 76% (22/29); 0.041 false per 30 days | threshold 0.4: recall on ahead 45/51; precision 76% (16/21); 0.030 false per 30 days | threshold 0.75: recall on ahead 123/283; precision 70% (94/134); 0.024 false per 30 days | threshold 0.65: recall on ahead 8/27; precision 70% (7/10); 0.028 false per 30 days |
| dips caught, random walk (in-sample threshold) | threshold 0.7: recall on ahead 38/52; precision 83% (19/23); 0.024 false per 30 days | threshold 0.7: recall on ahead 41/51; precision 77% (20/26); 0.036 false per 30 days | threshold 0.85: recall on ahead 58/283; precision 73% (45/62); 0.010 false per 30 days | threshold 0.7: recall on ahead 6/27; precision 71% (5/7); 0.018 false per 30 days |
| (zero-estimate column: its dips caught are computed on non-consecutive origins, so episodes are cut or joined; not comparable with the other columns) | | | | |
| lowest-balance bias: mean (mean absolute) | +708 (987) | +625 (910) | +136 (399) | +277 (327) |

All numbers are pooled over origins, so larger balances weigh more in CRPS and bias (the 2026-11-19 baselines report gives the synthetic per-archetype averages). The zero-estimate column is the origins where the forecast's everyday spending was zero, reported apart as committed on 2026-10-17; it can only veto, never decide. CRPS and bias are in dollars for the synthetic sets and in scaled units for Berka; compare skills, coverage, Brier and dips across datasets, not amounts. A positive bias means the forecast's median lowest balance was above the realized lowest (too optimistic). Origins are weekly and a dip lasts for weeks, so neighbouring origins share outcomes: the effective n is smaller than the n shown.
