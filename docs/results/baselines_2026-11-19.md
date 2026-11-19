# Experiment 1: baselines and scores (synthetic, development personas)

Made with `python -m eval.baselines --tag 2026-11-19 --report`. Method in DECISIONS.md (2026-10-17). 20 development personas per set (never pooled), 40 weekly origins, 500 futures, seed 42, the default forecaster. The random walk uses the 365 days before each origin and only windows that ended on or before it. Nothing is adopted from this report; it fixes what the later experiments are scored against.

## 12-month set

Origins: 800; ordinary (not already below the buffer): 726; with a random walk (at least 20 windows): 726. All scores below are on those 726. This rerun equals the earlier baselines run at 794 of 800 records (p10, p50, p90, dip chance, realized).

### CRPS (mean over the 30 days, in dollars; lower is better) and skill

| Archetype (n origins, personas) | forecast | balance stays | random walk | skill vs stays | skill vs walk | day 30: forecast / stays / walk |
|---|---|---|---|---|---|---|
| DHOME (120, 3) | $327 | $481 | $371 | +32.0% | +11.7% | $502 / $617 / $527 |
| DRENT (102, 3) | $530 | $634 | $513 | +16.4% | -3.3% | $850 / $843 / $714 |
| FREE (40, 1) | $605 | $755 | $586 | +19.8% | -3.3% | $992 / $1,053 / $883 |
| IFAM (120, 3) | $1,274 | $1,686 | $1,361 | +24.4% | +6.4% | $2,223 / $2,388 / $2,085 |
| ITIGHT (86, 3) | $4,794 | $4,942 | $4,654 | +3.0% | -3.0% | $7,505 / $7,675 / $7,267 |
| SAL (120, 3) | $165 | $1,201 | $505 | +86.3% | +67.4% | $250 / $1,642 / $222 |
| SEM (40, 1) | $165 | $550 | $395 | +70.0% | +58.3% | $239 / $267 / $220 |
| TIGHT (98, 3) | $171 | $676 | $472 | +74.8% | +63.8% | $241 / $419 / $337 |
| **mean of archetypes (equal weight)** | | | | +40.8% | +24.8% | |

### Brier score of the dip chance (lower is better) and skill

| Archetype (n, dips) | forecast | balance stays | random walk | skill vs stays | skill vs walk |
|---|---|---|---|---|---|
| DHOME (120, 0) | 0.0154 | 0.0000 | 0.0121 | too few dips | too few dips |
| DRENT (102, 23) | 0.0666 | 0.2255 | 0.0653 | +70.5% | -2.0% |
| FREE (40, 0) | 0.0000 | 0.0000 | 0.0000 | too few dips | too few dips |
| IFAM (120, 0) | 0.0000 | 0.0000 | 0.0000 | too few dips | too few dips |
| ITIGHT (86, 38) | 0.2942 | 0.4419 | 0.2734 | +33.4% | -7.6% |
| SAL (120, 0) | 0.0000 | 0.0000 | 0.0000 | too few dips | too few dips |
| SEM (40, 0) | 0.0002 | 0.0000 | 0.0142 | too few dips | too few dips |
| TIGHT (98, 64) | 0.0972 | 0.6531 | 0.2474 | +85.1% | +60.7% |
| **mean of the 3 archetypes with 5+ dips** | | | | +63.0% | +17.0% |

Origins are weekly and a dip lasts for weeks, so neighbouring origins share outcomes: the effective n is smaller than the n shown.

### Dips caught at the same precision (all origins, those already below the buffer included, as the alert engine sees them; threshold chosen on the whole set for each method: highest recall on ahead with precision 70 percent or more and at most 0.1 false alerts per 30 days; in-sample and descriptive)

| | forecast | random walk | balance stays |
|---|---|---|---|
| chosen threshold | 0.35 | 0.7 | none (never alerts) |
| all archetypes | recall on ahead 47/52; precision 76% (22/29); 0.041 false per 30 days | recall on ahead 38/52; precision 83% (19/23); 0.024 false per 30 days | recall on ahead 0/52; precision - (0/0); 0.000 false per 30 days |
| DHOME | recall on ahead -; precision 0% (0/1); 0.036 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days |
| DRENT | recall on ahead 14/15; precision 57% (4/7); 0.126 false per 30 days | recall on ahead 14/15; precision 80% (4/5); 0.042 false per 30 days | recall on ahead 0/15; precision - (0/0); 0.000 false per 30 days |
| FREE | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days |
| IFAM | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days |
| ITIGHT | recall on ahead 11/14; precision 80% (8/10); 0.100 false per 30 days | recall on ahead 8/14; precision 60% (3/5); 0.100 false per 30 days | recall on ahead 0/14; precision - (0/0); 0.000 false per 30 days |
| SAL | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days |
| SEM | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days |
| TIGHT | recall on ahead 22/23; precision 91% (10/11); 0.044 false per 30 days | recall on ahead 16/23; precision 92% (12/13); 0.044 false per 30 days | recall on ahead 0/23; precision - (0/0); 0.000 false per 30 days |

## 24-month set

Origins: 800; ordinary (not already below the buffer): 717; with a random walk (at least 20 windows): 717. All scores below are on those 717. This rerun equals the earlier baselines run at 799 of 800 records (p10, p50, p90, dip chance, realized).

### CRPS (mean over the 30 days, in dollars; lower is better) and skill

| Archetype (n origins, personas) | forecast | balance stays | random walk | skill vs stays | skill vs walk | day 30: forecast / stays / walk |
|---|---|---|---|---|---|---|
| DHOME (120, 3) | $303 | $488 | $346 | +37.9% | +12.5% | $465 / $619 / $457 |
| DRENT (120, 3) | $531 | $679 | $514 | +21.8% | -3.3% | $824 / $888 / $665 |
| FREE (30, 1) | $336 | $523 | $368 | +35.7% | +8.5% | $450 / $523 / $397 |
| IFAM (120, 3) | $1,145 | $1,681 | $1,254 | +31.9% | +8.7% | $1,973 / $2,353 / $1,768 |
| ITIGHT (81, 3) | $4,449 | $4,649 | $3,995 | +4.3% | -11.4% | $7,128 / $7,427 / $6,162 |
| SAL (120, 3) | $112 | $1,217 | $494 | +90.8% | +77.3% | $167 / $1,682 / $217 |
| SEM (40, 1) | $116 | $553 | $405 | +78.9% | +71.3% | $165 / $279 / $267 |
| TIGHT (86, 3) | $120 | $645 | $427 | +81.4% | +71.9% | $177 / $413 / $285 |
| **mean of archetypes (equal weight)** | | | | +47.8% | +29.4% | |

### Brier score of the dip chance (lower is better) and skill

| Archetype (n, dips) | forecast | balance stays | random walk | skill vs stays | skill vs walk |
|---|---|---|---|---|---|
| DHOME (120, 0) | 0.0032 | 0.0000 | 0.0000 | too few dips | too few dips |
| DRENT (120, 10) | 0.0130 | 0.0833 | 0.0645 | +84.4% | +79.8% |
| FREE (30, 16) | 0.1490 | 0.5333 | 0.1318 | +72.1% | -13.0% |
| IFAM (120, 0) | 0.0000 | 0.0000 | 0.0000 | too few dips | too few dips |
| ITIGHT (81, 14) | 0.1728 | 0.1728 | 0.1489 | -0.0% | -16.0% |
| SAL (120, 0) | 0.0000 | 0.0000 | 0.0000 | too few dips | too few dips |
| SEM (40, 0) | 0.0000 | 0.0000 | 0.0000 | too few dips | too few dips |
| TIGHT (86, 83) | 0.0465 | 0.9651 | 0.2217 | +95.2% | +79.0% |
| **mean of the 4 archetypes with 5+ dips** | | | | +62.9% | +32.4% |

Origins are weekly and a dip lasts for weeks, so neighbouring origins share outcomes: the effective n is smaller than the n shown.

### Dips caught at the same precision (all origins, those already below the buffer included, as the alert engine sees them; threshold chosen on the whole set for each method: highest recall on ahead with precision 70 percent or more and at most 0.1 false alerts per 30 days; in-sample and descriptive)

| | forecast | random walk | balance stays |
|---|---|---|---|
| chosen threshold | 0.4 | 0.7 | none (never alerts) |
| all archetypes | recall on ahead 45/51; precision 76% (16/21); 0.030 false per 30 days | recall on ahead 41/51; precision 77% (20/26); 0.036 false per 30 days | recall on ahead 0/51; precision - (0/0); 0.000 false per 30 days |
| DHOME | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days |
| DRENT | recall on ahead 3/3; precision 33% (1/3); 0.071 false per 30 days | recall on ahead 1/3; precision 33% (2/6); 0.143 false per 30 days | recall on ahead 0/3; precision - (0/0); 0.000 false per 30 days |
| FREE | recall on ahead 8/8; precision 60% (3/5); 0.286 false per 30 days | recall on ahead 7/8; precision 100% (3/3); 0.000 false per 30 days | recall on ahead 0/8; precision - (0/0); 0.000 false per 30 days |
| IFAM | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days |
| ITIGHT | recall on ahead 0/5; precision - (0/0); 0.000 false per 30 days | recall on ahead 0/5; precision - (0/0); 0.000 false per 30 days | recall on ahead 0/5; precision - (0/0); 0.000 false per 30 days |
| SAL | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days |
| SEM | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days | recall on ahead -; precision - (0/0); 0.000 false per 30 days |
| TIGHT | recall on ahead 34/35; precision 92% (12/13); 0.050 false per 30 days | recall on ahead 33/35; precision 88% (15/17); 0.100 false per 30 days | recall on ahead 0/35; precision - (0/0); 0.000 false per 30 days |

