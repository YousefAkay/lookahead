# Diagnosis: where the 30-day error comes from (development personas, synthetic)

Made with `python -m eval.diagnose` (seed 42, 500 futures, weekday method, 13 personas, 520 origins). Method: DECISIONS.md, 2026-10-10. All origins, ordinary and already in shortfall together. Dollars are rounded. No change was made to the forecaster.

Mean signed 30-day error by part (expected minus happened; positive = the forecast was too optimistic about that part), then the mean absolute error in brackets.

| Part | ITIGHT (3 personas, 120 origins) | IFAM (3 personas, 120 origins) | FREE (1 personas, 40 origins) | SAL (3 personas, 120 origins) | TIGHT (3 personas, 120 origins) |
|---|---|---|---|---|---|
| Tuition payments | $5,410 ($5,410) | $1,129 ($1,129) | $0 ($0) | $0 ($0) | $0 ($0) |
| Family deposits and top-ups | -$4,501 ($4,750) | -$1,499 ($1,499) | $0 ($0) | $0 ($0) | $0 ($0) |
| Other irregular income | $0 ($0) | $0 ($0) | $312 ($1,438) | $0 ($0) | $0 ($0) |
| Regular income (pay, allowance) | -$456 ($503) | $761 ($947) | $0 ($0) | $215 ($215) | $92 ($92) |
| Recurring bills (rent, bills, subscriptions) | $58 ($62) | $70 ($92) | $21 ($37) | $102 ($122) | $62 ($74) |
| Big one-time events | $13 ($101) | -$26 ($76) | -$11 ($31) | -$3 ($97) | -$15 ($53) |
| Fees | $2 ($15) | $0 ($6) | $0 ($0) | $0 ($0) | -$0 ($4) |
| Everyday spending | -$20 ($162) | -$56 ($149) | $101 ($243) | -$7 ($226) | $122 ($247) |
| **Mean future, all parts** | $507 ($6,561) | $379 ($2,809) | $423 ($1,440) | $308 ($532) | $260 ($395) |
| Middle future (p50), for comparison | $485 ($6,564) | $390 ($2,817) | $269 ($1,344) | $330 ($533) | $274 ($402) |

Rows in the 30 days after each origin, by part: share of windows with at least one row, rows per window, and the median and largest row size (absolute).

| Part | ITIGHT | IFAM | FREE | SAL | TIGHT |
|---|---|---|---|---|---|
| Tuition payments | 22/120 windows, 0.2 rows, median $29,900, max $29,900 | 29/120 windows, 0.2 rows, median $4,200, max $6,150 | none | none | none |
| Family deposits and top-ups | 115/120 windows, 1.3 rows, median $850, max $29,500 | 20/120 windows, 0.2 rows, median $8,450, max $10,900 | none | none | none |
| Other irregular income | none | none | 40/40 windows, 2.8 rows, median $800, max $1,700 | none | none |
| Regular income (pay, allowance) | 120/120 windows, 2.2 rows, median $371, max $1,262 | 116/120 windows, 1.0 rows, median $2,810, max $3,100 | none | 120/120 windows, 2.0 rows, median $2,350, max $2,500 | 120/120 windows, 2.2 rows, median $1,400, max $1,600 |
| Recurring bills (rent, bills, subscriptions) | 120/120 windows, 6.9 rows, median $45, max $750 | 120/120 windows, 10.6 rows, median $36, max $1,400 | 40/40 windows, 3.9 rows, median $117, max $825 | 120/120 windows, 5.0 rows, median $65, max $1,225 | 120/120 windows, 3.6 rows, median $55, max $2,090 |
| Big one-time events | 32/120 windows, 0.3 rows, median $279, max $378 | 13/120 windows, 0.1 rows, median $179, max $379 | 2/40 windows, 0.1 rows, median $204, max $204 | 28/120 windows, 0.2 rows, median $258, max $361 | 10/120 windows, 0.1 rows, median $232, max $370 |
| Fees | 119/120 windows, 3.1 rows, median $10, max $75 | 119/120 windows, 1.3 rows, median $15, max $15 | none | 11/120 windows, 0.1 rows, median $0, max $2 | 86/120 windows, 1.3 rows, median $1, max $5 |

Lowest-balance bias (forecast median lowest minus observed lowest; positive = too optimistic), by what the 30 days after the origin held. Cell: bias (origins).

| Window held | ITIGHT | IFAM | FREE | SAL | TIGHT |
|---|---|---|---|---|---|
| tuition, no irregular income | - | $4,371 (22) | - | - | - |
| tuition and irregular income | $18,764 (22) | -$1,162 (7) | - | - | - |
| irregular income, no tuition | -$306 (93) | -$355 (13) | $73 (40) | - | - |
| neither | $194 (5) | -$155 (78) | - | $69 (120) | $76 (120) |
| all | $3,211 (120) | $594 (120) | $73 (40) | $69 (120) | $76 (120) |

What the forecaster did with the rows of the 13 weeks before each origin (row-origin pairs, then their absolute dollars). Each row is seen by about 13 origins.

**ITIGHT** (3 personas, 120 origins)

| Part | placed as a series | resampled (weekly draw) | set aside: large deposit | set aside: large payment | set aside: pass-through | not used |
|---|---|---|---|---|---|---|
| Tuition payments | 0 | 0 | 0 | 16 ($466,050) | 23 ($687,700) | 0 |
| Family deposits and top-ups | 104 ($85,800) | 122 ($65,700) | 177 ($703,350) | 0 | 25 ($653,800) | 0 |
| Big one-time events | 0 | 85 ($19,877) | 0 | 0 | 0 | 0 |

**IFAM** (3 personas, 120 origins)

| Part | placed as a series | resampled (weekly draw) | set aside: large deposit | set aside: large payment | set aside: pass-through | not used |
|---|---|---|---|---|---|---|
| Tuition payments | 0 | 0 | 0 | 62 ($285,750) | 0 | 0 |
| Family deposits and top-ups | 0 | 0 | 43 ($395,000) | 0 | 0 | 0 |
| Big one-time events | 0 | 65 ($17,716) | 0 | 0 | 0 | 0 |

**FREE** (1 personas, 40 origins)

| Part | placed as a series | resampled (weekly draw) | set aside: large deposit | set aside: large payment | set aside: pass-through | not used |
|---|---|---|---|---|---|---|
| Other irregular income | 0 | 322 ($323,350) | 4 ($4,650) | 0 | 0 | 0 |
| Big one-time events | 0 | 13 ($2,652) | 0 | 0 | 0 | 0 |

**SAL** (3 personas, 120 origins)

| Part | placed as a series | resampled (weekly draw) | set aside: large deposit | set aside: large payment | set aside: pass-through | not used |
|---|---|---|---|---|---|---|
| Big one-time events | 0 | 82 ($22,650) | 0 | 0 | 0 | 0 |

**TIGHT** (3 personas, 120 origins)

| Part | placed as a series | resampled (weekly draw) | set aside: large deposit | set aside: large payment | set aside: pass-through | not used |
|---|---|---|---|---|---|---|
| Big one-time events | 0 | 43 ($10,215) | 0 | 0 | 0 | 0 |


Per persona: mean signed 30-day error of the mean future, and its three largest parts (mean signed).

| Persona | Origins | Error | Largest parts |
|---|---|---|---|
| v2-ITIGHT-01 | 40 | $436 | Tuition payments $6,514; Family deposits and top-ups -$5,705; Regular income (pay, allowance) -$440 |
| v2-ITIGHT-02 | 40 | -$877 | Regular income (pay, allowance) -$917; Recurring bills (rent, bills, subscriptions) $92; Family deposits and top-ups -$55 |
| v2-ITIGHT-03 | 40 | $1,961 | Tuition payments $9,718; Family deposits and top-ups -$7,744; Everyday spending -$63 |
| v2-IFAM-01 | 40 | $204 | Family deposits and top-ups -$1,576; Tuition payments $1,076; Regular income (pay, allowance) $729 |
| v2-IFAM-02 | 40 | $45 | Family deposits and top-ups -$2,920; Tuition payments $2,310; Regular income (pay, allowance) $702 |
| v2-IFAM-03 | 40 | $889 | Regular income (pay, allowance) $852; Recurring bills (rent, bills, subscriptions) $163; Everyday spending -$68 |
| v2-FREE-01 | 40 | $423 | Other irregular income $312; Everyday spending $101; Recurring bills (rent, bills, subscriptions) $21 |
| v2-SAL-01 | 40 | $301 | Regular income (pay, allowance) $235; Recurring bills (rent, bills, subscriptions) $139; Everyday spending -$97 |
| v2-SAL-02 | 40 | $421 | Regular income (pay, allowance) $250; Recurring bills (rent, bills, subscriptions) $155; Everyday spending $56 |
| v2-SAL-03 | 40 | $200 | Regular income (pay, allowance) $160; Everyday spending $21; Recurring bills (rent, bills, subscriptions) $13 |
| v2-TIGHT-01 | 40 | $365 | Recurring bills (rent, bills, subscriptions) $155; Regular income (pay, allowance) $135; Everyday spending $89 |
| v2-TIGHT-02 | 40 | $302 | Regular income (pay, allowance) $140; Everyday spending $121; Recurring bills (rent, bills, subscriptions) $40 |
| v2-TIGHT-03 | 40 | $113 | Everyday spending $154; Big one-time events -$31; Recurring bills (rent, bills, subscriptions) -$9 |
