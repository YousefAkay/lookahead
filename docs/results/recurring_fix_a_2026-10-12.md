# The recurring-placement fix, (a) and (b): synthetic development personas

20 development personas, 40 weekly origins, 500 futures, seed 42, raw chances, the original episode definition (the provisional rule). Held-out personas not run.

| Metric | no fix | (b) possibly ended not placed (the default) | (a) weekly card series not placed | (a) and (b) |
|---|---|---|---|---|
| n (ordinary origins) | 726 origins, 20 personas | 726 origins, 20 personas | 726 origins, 20 personas | 726 origins, 20 personas |
| Error, middle future, mean: 7 / 14 / 30 days | $593 / $1,056 / $1,727 | $592 / $1,052 / $1,725 | $593 / $1,056 / $1,727 | $592 / $1,052 / $1,725 |
| Skill vs balance-stays: 7 / 14 / 30 days | 0.40 / 0.21 / 0.10 | 0.40 / 0.22 / 0.10 | 0.40 / 0.21 / 0.10 | 0.40 / 0.22 / 0.10 |
| 80% band coverage: 7 / 14 / 30 days / all | 62% / 58% / 48% / 58% | 62% / 58% / 48% / 58% | 62% / 58% / 48% / 58% | 62% / 58% / 48% / 58% |
| Band width: 7 / 14 / 30 days | $415 / $619 / $903 | $413 / $617 / $901 | $415 / $619 / $903 | $413 / $617 / $901 |
| Path-level coverage | 10% | 10% | 10% | 10% |
| Lowest balance: bias / typical miss | $716 / $994 | $703 / $991 | $716 / $995 | $703 / $991 |
| Brier | 0.058 | 0.060 | 0.057 | 0.060 |
| Alert rule | provisional (20% or median) | provisional (20% or median) | provisional (20% or median) | provisional (20% or median) |
| Recall | 48% (25/52) | 48% (25/52) | 48% (25/52) | 48% (25/52) |
| Precision | 70% (21/30) | 69% (22/32) | 70% (21/30) | 69% (22/32) |
| False alerts per 30 days | 0.053 | 0.059 | 0.053 | 0.059 |
| Lead time, days (mean) | 15.8 | 15.8 | 15.8 | 15.8 |

Per archetype (ordinary origins): 30-day error / lowest-balance bias.

| Archetype | no fix | (b) possibly ended not placed (the default) | (a) weekly card series not placed | (a) and (b) |
|---|---|---|---|---|
| DHOME (120 origins, 3 personas) | $601 / $186 | $580 / $155 | $601 / $186 | $580 / $155 |
| DRENT (102 origins, 3 personas) | $953 / $119 | $938 / $103 | $953 / $119 | $938 / $103 |
| FREE (40 origins, 1 personas) | $1,338 / $77 | $1,338 / $77 | $1,338 / $77 | $1,338 / $77 |
| IFAM (120 origins, 3 personas) | $2,306 / $594 | $2,306 / $595 | $2,306 / $594 | $2,306 / $595 |
| ITIGHT (86 origins, 3 personas) | $7,782 / $4,588 | $7,812 / $4,534 | $7,781 / $4,588 | $7,811 / $4,533 |
| SAL (120 origins, 3 personas) | $334 / $64 | $334 / $64 | $334 / $64 | $334 / $64 |
| SEM (40 origins, 1 personas) | $336 / $69 | $333 / $73 | $336 / $69 | $333 / $73 |
| TIGHT (98 origins, 3 personas) | $322 / $64 | $321 / $65 | $322 / $64 | $321 / $65 |

## (b) against no fix
- origins where it placed fewer payments: 34 of 800
- personas identical to no fix in every forecast field: 5 (v2-DHOME-01, v2-DRENT-01, v2-FREE-01, v2-IFAM-02, v2-SAL-02)
- personas that differ (origins differing): v2-DHOME-02 (1), v2-DHOME-03 (3), v2-DRENT-02 (1), v2-DRENT-03 (1), v2-IFAM-01 (4), v2-IFAM-03 (4), v2-ITIGHT-01 (1), v2-ITIGHT-02 (4), v2-ITIGHT-03 (1), v2-SAL-01 (3), v2-SAL-03 (2), v2-SEM-01 (2), v2-TIGHT-01 (3), v2-TIGHT-02 (2), v2-TIGHT-03 (2)
- archetypes worse on the 30-day error (more than 1 percent): 0 of 8 (-)
- archetypes worse on the lowest-balance bias: 2 of 8 (SEM, TIGHT)
- **drop rule: not triggered**

## (a) added to (b), against the default
- origins where it placed fewer payments: 7 of 800
- personas identical to (b) possibly ended not placed (the default) in every forecast field: 16 (v2-DHOME-01, v2-DHOME-03, v2-DRENT-01, v2-DRENT-02, v2-DRENT-03, v2-FREE-01, v2-IFAM-01, v2-IFAM-02, v2-ITIGHT-01, v2-ITIGHT-03, v2-SAL-02, v2-SAL-03, v2-SEM-01, v2-TIGHT-01, v2-TIGHT-02, v2-TIGHT-03)
- personas that differ (origins differing): v2-DHOME-02 (6), v2-IFAM-03 (6), v2-ITIGHT-02 (15), v2-SAL-01 (10)
- archetypes worse on the 30-day error (more than 1 percent): 0 of 8 (-)
- archetypes worse on the lowest-balance bias: 0 of 8 (-)
- **drop rule: not triggered**

## (a) alone against no fix (for information)
- origins where it placed fewer payments: 11 of 800
- personas identical to no fix in every forecast field: 16 (v2-DHOME-01, v2-DHOME-03, v2-DRENT-01, v2-DRENT-02, v2-DRENT-03, v2-FREE-01, v2-IFAM-01, v2-IFAM-02, v2-ITIGHT-01, v2-ITIGHT-03, v2-SAL-02, v2-SAL-03, v2-SEM-01, v2-TIGHT-01, v2-TIGHT-02, v2-TIGHT-03)
- personas that differ (origins differing): v2-DHOME-02 (6), v2-IFAM-03 (6), v2-ITIGHT-02 (15), v2-SAL-01 (10)
- archetypes worse on the 30-day error (more than 1 percent): 0 of 8 (-)
- archetypes worse on the lowest-balance bias: 0 of 8 (-)
- **drop rule: not triggered**

Ordinary origins: 726, 20 personas.
- no fix: lowest-balance bias $716, Brier 0.058, coverage over all days 58%
- (b) possibly ended not placed (the default): lowest-balance bias $703, Brier 0.060, coverage over all days 58%
- (a) weekly card series not placed: lowest-balance bias $716, Brier 0.057, coverage over all days 58%
- (a) and (b): lowest-balance bias $703, Brier 0.060, coverage over all days 58%