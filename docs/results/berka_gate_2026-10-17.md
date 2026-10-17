# The Berka gate: counts only (nothing is scored)

Made with `python -m eval.berka_gate` from `data/berka/trans.asc` (not committed). Method in DECISIONS.md (2026-10-17, experiment 2). Quantiles are min / 10% / 25% / median / 75% / 90% / max across accounts.

- 1,056,320 transactions, 4,500 accounts, 1993-01-01 to 1998-12-31; amounts in koruna (CZK).
- Stored balance equals the previous balance plus the amount, rows in file order: 49.7% of rows. The file lists same-day rows in a wrong order at times and the balances drift by 0.1 to 0.2 koruna (interest rounding), so a day's close is the balance recorded that day nearest to the previous close plus the day's total: 100.00% of 814,768 account-days lie within one koruna of it, 100.0% of accounts on every day.
- Mechanical rows (interest credited, the statement fee, penalty interest): 32.2% of all rows.

## How sparse

| | min / 10% / 25% / median / 75% / 90% / max |
|---|---|
| months of history | 10.8 / 17.9 / 25.8 / 35.9 / 60.1 / 67.2 / 71.9 |
| transactions in total | 9 / 87 / 133 / 208 / 330 / 411 / 675 |
| transactions per month | 0.5 / 4.4 / 4.8 / 5.5 / 6.4 / 7.5 / 11.7 |
| transactions per month, mechanical rows left out | 0.5 / 2.6 / 3.0 / 3.7 / 4.5 / 5.5 / 9.8 |
| longest stretch without any posting (days) | 12 / 18 / 20 / 24 / 30 / 31 / 510 |
| longest stretch without a non-mechanical posting (days) | 16 / 24 / 27 / 30 / 31 / 31 / 510 |

## What the outflow is made of

Per account, the share of outflow by value and by count (everyday-style = cash and card withdrawals; monthly = household, insurance and loan payments; fees = statement fee and penalty interest; other = transfers out and the rest).

| | by value: min / 10% / 25% / median / 75% / 90% / max | by count: median |
|---|---|---|
| everyday | 0.07 / 0.37 / 0.50 / 0.67 / 0.88 / 1.00 / 1.00 | 0.43 |
| monthly | 0.00 / 0.00 / 0.04 / 0.23 / 0.43 / 0.56 / 0.89 | 0.25 |
| fee | 0.00 / 0.00 / 0.00 / 0.00 / 0.00 / 0.00 / 0.01 | 0.25 |
| other_out | 0.00 / 0.00 / 0.00 / 0.00 / 0.04 / 0.23 / 0.92 | 0.00 |

All accounts pooled, share of the value of all outflows: everyday 77.0%, fee 0.1%, monthly 19.1%, other_out 3.9%.

## Accounts passing each candidate filter

History in months, and non-mechanical transactions per month (each row also needs the longest stretch without a non-mechanical posting to be at most 90 days).

| | 12+ months | 24+ months | 36+ months | 48+ months |
|---|---|---|---|---|
| 1+ per month | 4426 | 3537 | 2200 | 1551 |
| 2+ per month | 4326 | 3472 | 2168 | 1533 |
| 4+ per month | 1620 | 1387 | 882 | 645 |
| 8+ per month | 3 | 3 | 1 | 0 |

## Dips after weekly origins, for the 3472 accounts with 24+ months, 2+ non-mechanical transactions a month and no quiet stretch over 90 days

A buffer is a fraction of the account's median 30-day outflow over the year before the origin (past data only). Counts of events, not forecasts.

| buffer | origins at or above it | followed by a dip below it | accounts with 3+ such origins |
|---|---|---|---|
| 25% of a month's outflow | 629,693 | 6,710 (1.1%) | 441 |
| 50% of a month's outflow | 622,626 | 16,717 (2.7%) | 842 |
| 100% of a month's outflow | 587,115 | 50,525 (8.6%) | 1570 |

