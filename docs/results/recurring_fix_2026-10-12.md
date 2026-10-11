# The recurring-placement fix (b): synthetic development personas

20 development personas, 40 weekly origins, 500 futures, seed 42, raw chances, the original episode definition (the table is the provisional rule; the metric counts caught under both definitions elsewhere). Held-out personas not run.

| Metric | current forecaster (keeps-coming on) | with (b): possibly ended not placed |
|---|---|---|
| n (ordinary origins) | 726 origins, 20 personas | 726 origins, 20 personas |
| Error, middle future, mean: 7 / 14 / 30 days | $593 / $1,056 / $1,727 | $592 / $1,052 / $1,725 |
| Skill vs balance-stays: 7 / 14 / 30 days | 0.40 / 0.21 / 0.10 | 0.40 / 0.22 / 0.10 |
| 80% band coverage: 7 / 14 / 30 days / all | 62% / 58% / 48% / 58% | 62% / 58% / 48% / 58% |
| Band width: 7 / 14 / 30 days | $415 / $619 / $903 | $413 / $617 / $901 |
| Path-level coverage | 10% | 10% |
| Lowest balance: bias / typical miss | $716 / $994 | $703 / $991 |
| Brier | 0.058 | 0.060 |
| Alert rule | provisional (20% or median) | provisional (20% or median) |
| Recall | 48% (25/52) | 48% (25/52) |
| Precision | 70% (21/30) | 69% (22/32) |
| False alerts per 30 days | 0.053 | 0.059 |
| Lead time, days (mean) | 15.8 | 15.8 |

Per archetype (ordinary origins): 30-day error / lowest-balance bias.

| Archetype | current forecaster (keeps-coming on) | with (b): possibly ended not placed |
|---|---|---|
| DHOME (120 origins, 3 personas) | $601 / $186 | $580 / $155 |
| DRENT (102 origins, 3 personas) | $953 / $119 | $938 / $103 |
| FREE (40 origins, 1 personas) | $1,338 / $77 | $1,338 / $77 |
| IFAM (120 origins, 3 personas) | $2,306 / $594 | $2,306 / $595 |
| ITIGHT (86 origins, 3 personas) | $7,782 / $4,588 | $7,812 / $4,534 |
| SAL (120 origins, 3 personas) | $334 / $64 | $334 / $64 |
| SEM (40 origins, 1 personas) | $336 / $69 | $333 / $73 |
| TIGHT (98 origins, 3 personas) | $322 / $64 | $321 / $65 |

- origins where (b) placed fewer payments: 34 of 800
- personas identical to the current forecaster in every forecast field: 5 (v2-DHOME-01, v2-DRENT-01, v2-FREE-01, v2-IFAM-02, v2-SAL-02)
- personas that differ (origins differing): v2-DHOME-02 (1), v2-DHOME-03 (3), v2-DRENT-02 (1), v2-DRENT-03 (1), v2-IFAM-01 (4), v2-IFAM-03 (4), v2-ITIGHT-01 (1), v2-ITIGHT-02 (4), v2-ITIGHT-03 (1), v2-SAL-01 (3), v2-SAL-03 (2), v2-SEM-01 (2), v2-TIGHT-01 (3), v2-TIGHT-02 (2), v2-TIGHT-03 (2)
- archetypes worse on the 30-day error (more than 1 percent): 0 of 8 (-)
- archetypes worse on the lowest-balance bias: 2 of 8 (SEM, TIGHT)
- **drop rule: not triggered**

Ordinary origins: 726, 20 personas. Lowest balance, bias: $716 to $703; Brier 0.058 to 0.060; coverage over all days 58% to 58%.