# Berka: does it run and look sane (development accounts only, nothing scored)

Made with `python -m eval.berka sanity`. Method in DECISIONS.md (2026-10-17). Counts only: no forecast is compared with what happened. The dip line is called "fell below one month's outflow" and was chosen from dip counts only, not from any forecast result. Held-out accounts are not read.

## primary development accounts

- Accounts: 200. Accounts where all 40 origins ran: 200 (100.0%); the gate needs 90% or more.
- Origins: 8,000; a forecast came back at 8,000 (100.0%). Crashes or no forecast: none.
- Origins that started below the dip line: 865 of 8,000.

How a forecast is made up over its 30 days (origins where a forecast ran; quantiles are min / 10% / 25% / median / 75% / 90% / max):

| | value |
|---|---|
| recurring placements' share of the money going out | 0.00 / 0.00 / 0.13 / 0.32 / 0.57 / 0.81 / 1.00 |
| origins with no recurring outflow placed | 1,156 of 8,000 |
| origins with no recurring income placed | 245 of 8,000 |
| origins whose forecast has an income series | 7,841 of 8,000 |
| recurring series detected per origin | 0 / 2 / 3 / 4 / 5 / 6 / 7 |
| scheduled items per origin | 0 / 1 / 3 / 4 / 5 / 5 / 7 |
| origins with deposit arrivals drawn (keeps coming) | 784 of 8,000 |
| origins with payment arrivals drawn | 305 of 8,000 |
| origins with a big deposit or payment set aside | 732 of 8,000 |

### Origins whose everyday spending estimate is zero

- 512 of 8,000 origins (6.4%), in 83 of 200 accounts. A share above 5% is flagged; this one is **FLAGGED**.
- Accounts where more than half of the origins are zero: 1.

| why the estimate is zero | origins |
|---|---|
| every money-out row belongs to a recurring series | 464 |
| every money-out row was set aside as big one-time money or a pass-through | 27 |
| only bank fees | 12 |
| rows were counted but net to zero or less (returns) | 9 |

Money-out rows in the 8 weeks before those origins (min / 10% / 25% / median / 75% / 90% / max): 2 / 4 / 5 / 6 / 8 / 10 / 12.

### Cash withdrawals and recurring series

Counted at every origin over the 8 weeks before it (overlapping windows, so a row is counted at several origins).

| | value |
|---|---|
| origins with at least one cash withdrawal in the 8 weeks | 7,542 of 8,000 |
| cash withdrawal rows in those windows | 23,840, of which in a recurring series 115 (0.5%) |
| origins with a cash withdrawal in a recurring series | 80 of 8,000 |
| origins with a recurring series made mostly of cash withdrawals | 427 of 8,000 |
| accounts where such a series appears at some origin | 16 of 200 |
| origins with a zero everyday estimate that had a cash withdrawal in the 8 weeks | 65 of 512 (of those, with the cash in a series: 29) |

The adapter's closing balance (the running total) against the file's own closing balance, the largest distance over each account's days, in koruna (min / 10% / 25% / median / 75% / 90% / max over accounts): 0.1 / 0.1 / 0.2 / 0.3 / 0.5 / 0.7 / 2.2.

## sensitivity development accounts

- Accounts: 200. Accounts where all 40 origins ran: 200 (100.0%); the gate needs 90% or more.
- Origins: 8,000; a forecast came back at 8,000 (100.0%). Crashes or no forecast: none.
- Origins that started below the dip line: 576 of 8,000.

How a forecast is made up over its 30 days (origins where a forecast ran; quantiles are min / 10% / 25% / median / 75% / 90% / max):

| | value |
|---|---|
| recurring placements' share of the money going out | 0.00 / 0.00 / 0.00 / 0.35 / 0.64 / 0.99 / 1.00 |
| origins with no recurring outflow placed | 2,812 of 8,000 |
| origins with no recurring income placed | 107 of 8,000 |
| origins whose forecast has an income series | 7,920 of 8,000 |
| recurring series detected per origin | 0 / 2 / 2 / 3 / 3 / 3 / 4 |
| scheduled items per origin | 0 / 1 / 2 / 3 / 3 / 3 / 5 |
| origins with deposit arrivals drawn (keeps coming) | 427 of 8,000 |
| origins with payment arrivals drawn | 241 of 8,000 |
| origins with a big deposit or payment set aside | 349 of 8,000 |

### Origins whose everyday spending estimate is zero

- 1,178 of 8,000 origins (14.7%), in 130 of 200 accounts. A share above 5% is flagged; this one is **FLAGGED**.
- Accounts where more than half of the origins are zero: 11.

| why the estimate is zero | origins |
|---|---|
| every money-out row belongs to a recurring series | 985 |
| only bank fees | 104 |
| rows were counted but net to zero or less (returns) | 71 |
| every money-out row was set aside as big one-time money or a pass-through | 18 |

Money-out rows in the 8 weeks before those origins (min / 10% / 25% / median / 75% / 90% / max): 1 / 3 / 3 / 4 / 4 / 6 / 7.

The adapter's closing balance (the running total) against the file's own closing balance, the largest distance over each account's days, in koruna (min / 10% / 25% / median / 75% / 90% / max over accounts): 0.1 / 0.1 / 0.2 / 0.3 / 0.4 / 0.5 / 1.2.
