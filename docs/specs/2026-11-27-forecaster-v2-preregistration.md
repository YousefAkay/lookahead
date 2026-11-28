# Forecaster v2: pre-registration (approved 2026-11-27)

Written before any v2 code exists and before anything below has been run. The fresh held-out set
was locked first (DECISIONS 2026-11-27, `docs/fresh_set.json`, commits eab7fb8 and 8098b42).
Nothing here was run before this file was committed.

## 1. Why: the diagnosis on development data

All numbers come from the saved futures of the default forecaster (v1, the 2026-11-19 final), 500
futures × 30 days per origin, origins already below the line left out, as in
`docs/results/scores_development_2026-11-19.md`. My script reproduces that report's origin counts
and day-30 coverage exactly. n: 12-month synthetic 726 origins (20 personas), 24-month synthetic
717 origins (20 personas), Berka 7,135 origins (200 development accounts).

**80% band coverage by day ahead.**

| day | 12-month | 24-month | Berka |
|---|---|---|---|
| 1 | 67.2% | 66.1% | 11.5% |
| 3 | 66.3% | 67.4% | 27.4% |
| 7 | 62.8% | 63.9% | 44.4% |
| 14 | 59.0% | 63.0% | 62.3% |
| 21 | 56.5% | 60.8% | 70.2% |
| 30 | 49.0% | 58.7% | 75.3% |
| all 30 days | 58.9% | 63.2% | 57.1% |

**Too thin, not off centre.** At day 30 the misses are close to even: actual above the band 168
and below 202 (12-month), 146 and 150 (24-month), 790 and 972 (Berka). The median error (forecast
median minus actual) is +$31, +$6 and +7.5 scaled units, 0.1 to 0.4 percent of the starting
balance; the forecast median is above the actual in 53, 51 and 52 percent of origins. The means
(+$201, +$135, +109 units) are pulled by a few large, mostly optimistic misses.

**Where the actual falls among the 500 futures** (percent of origins in each tenth, lowest
first). Synthetic: U-shaped at day 7 and deeper at day 30 (12-month day 30: 27.4 in the lowest
tenth, 23.1 in the highest; 24-month: 20.8 and 20.1), so the band is too narrow, nearly
symmetric. Berka day 7: 42.1 percent above every future; Berka day 30: ends close to 10, the
middle slightly overfull.

**What drives the misses** (day-30 coverage by whether the realized balance had a one-day
change outside every future's change that day by at least $100 or 100 units; my cut-off, not
tuned, descriptive only). No such day: 70.2% (315), 70.3% (445), 74.7% (1,977). Unforeseen money
in only: 44.4% (117), 42.6% (61), 58.6% (623). Unforeseen money out only: 31.4% (207), 42.8%
(173), 79.4% (2,971). Both: 20.7% (87), 21.1% (38), 74.9% (1,564). On synthetic, unforeseen money
causes most of the misses, and everyday spending alone is still about 10 points short.

**The certain first-day deduction (traced).** On Berka, 69 percent of origins had a day-1 band of
zero width with every future below the starting balance, while the real balance did not move on
84 percent. Traced on 40 development accounts (1,600 origins): 596 origins had every future the
same and below the start on day 1; in all 596 the forecaster's bank fees were above zero on day 1,
and in 569 fees were the only cause (in 27 a detected payment was also placed on day 1). Fees are
the last 28 days' fees spread evenly over every forecast day (`recent_fee_rate`), a median of 3
scaled cents a day here, so every future starts a few cents below a balance that has not moved,
and a band of zero width misses it. On the synthetic sets it is rare: every future the same and below
the start on day 1 at 0 of 800 origins (12-month) and 13 of 800 (24-month; fees above zero in all
13, a median of $4.79 a day, from the generator's event-driven bank fees). Their "every future
below the start" on day 1 (39 and 24 percent) is mostly drawn spending, not a certain deduction.

**How the futures are drawn today.** For each week ahead, each future draws one whole past week
(the same weekday to start) from the last 13, independently and with replacement, and takes that
week's everyday spending and irregular deposits together. No future has a spending rate of its
own: about four independent weeks average out over 30 days, which narrows the band, and nothing
stands for "the next month could run hotter or cooler than the last 13 weeks". Recurring payments
and their amounts and the fee rate are the same in every future; rent and detected payments move
within their windows; random lump arrivals are off.

## 2. The five variants (the only ones counted)

- **F, fees on their real dates.** A fee charged within 2 days of the same day of the month in at
  least two of the last three months is placed on that day of the coming month (all futures).
  Every other fee is no longer spread over the days: it stays in its own past week and is drawn
  with that week. Nothing else changes. On an account without fee rows F equals v1.
- **F+a, own spending rate per future.** On top of F, each future first draws its own 13 weeks
  from the last 13 with replacement (a resample of the history window), then draws its weeks ahead
  from that resample. Spending stays in whole weeks.
- **F+b, a fixed stretch per day ahead.** On top of F, every future is stretched around that day's
  median: x'(h) = m(h) + k(h) (x(h) - m(h)), one k per day ahead (h = 1..30), the same for every
  account: the smallest k(h) at which pooled 80% coverage on day h reaches 80 percent (equal weight
  per set), never below 1 (it widens or leaves alone, never narrows), then made non-decreasing in
  h. It is applied to the futures, so the band, the dip chance, the lowest balances and Safe to
  spend are all read from the stretched futures.
- **F+a+b.** F+a with its own k(h), fitted the same way on F+a futures.
- **F+a+b+d, random-walk futures mixed in.** F+a+b, with a fixed share w of the 500 futures
  replaced by random-walk paths from day 1 (the account's own balance changes over 1 to 30 days in
  the 365 days before the origin, as `eval.baselines` builds them), so no future switches mid-path.
  w is chosen from 0, 0.1, ..., 0.5 by cross-fitted development CRPS (section 3). With fewer than
  365 days of history, w is 0.

Every variant is a run in the ledger, with its development numbers, whatever they are.

## 3. Choosing the winner on development data

**Cross-fitting.** Each development set (12-month, 24-month, Berka primary development) is split
into 5 groups by persona or account. For each group, k(h) (and w for d) is fitted on the other 4
groups and the variant is scored on the held-back group; the five scored groups together give the
variant's development numbers. Eligibility and the winner use only these cross-fitted numbers.
The final k(h) and w are then fitted on all development data and frozen, in a commit, before the
fresh run.

A variant is eligible if, on each development set, its cross-fitted average 80% coverage over days
1-30 is between 75 and 85 percent and its day-30 coverage is at least 70 percent. Among eligible
variants the winner has the best pooled CRPS: the mean over the three sets of its cross-fitted
CRPS skill against v1 (mean of 30 days), each set weighted equally. A tie within 0.1 point goes to
the variant with fewer parts. If no variant is eligible, nothing goes to the fresh set and v1
stays.

## 4. The adopt rule, on each fresh set

The winner and v1 are run once, by the same code, on each fresh set: Berka (200 accounts × 40
origins), 12-month (10 personas × 40 origins), 24-month (10 personas × 40 origins). The winner is
adopted only if:

1. on each of the three sets, its average 80% coverage over days 1-30 is between 75 and 85
   percent;
2. on each of the three sets, its day-30 coverage is at least 70 percent;
3. on each of the three sets, its CRPS skill against the random walk (mean of 30 days) is at most
   1 point below v1's on the same set;
4. pooled over the three sets (the counts added across the sets, then one rate each), its dips
   caught and its precision are each at most 3 percentage points below v1's, counted as on
   2026-11-19 (an alert on in the 30 days before the dip); the winner at an
   alert threshold re-picked on development data by the existing procedure (43cc156 as applied on
   2026-11-19: the 12-month development set, leave one persona out, the highest recall of
   shortfalls with precision of at least 70 percent and at most 0.1 false alerts per 30 days), v1
   at 0.35.

The results also give, for v1 and the winner on each set, the CRPS skill against "balance stays"
and the dips caught and precision, for reporting only (not conditions).

If any condition fails, v1 stays, and I say so in DECISIONS and the README with every number and
its n.

## 5. Running it once

As the 2026-11-19 protocol: one command, a dry run on development data first, every attempt in a
tracked ledger committed before the next one, results written to the ledger before any number is
shown, no second run once a number is shown. The run is void, and shows nothing, if any set lacks
its units × 40 origins, any origin lacks 500 futures, any score is NaN, or the forecaster's logic
version is not the one committed for v2. A fix may change only the harness, is committed and
disclosed, and the whole command runs again.

## 6. Disclosures

- The synthetic generator was tuned while looking at development balances, and the fresh
  personas reuse the recorded specs of the held-out slots (their generated balances were seen on
  2026-10-08/09 and their held-out results are known since 2026-11-20); only the drawn parameters
  and transactions are new.
- The fresh Berka accounts were chosen by a count-only pass over every account, and the 2026-10-17
  Berka gate report pooled dip events over a group that includes them.
- The five variants were designed after seeing the development diagnosis above.
- k(h) and w are fitted on development data; the development numbers that choose the winner are
  cross-fitted (section 3), and the frozen final values are fitted on all of it.
- The alert threshold of the winner is re-picked on development data before the fresh run.

## 7. My prediction, written before any v2 run

I expect F+a+b to win on development data and pass the coverage conditions on the fresh sets; I'm
least sure about the alert condition, and I'd put the chance of adoption slightly above 50%.

## 8. Readings, recorded 2026-11-27 before any scoring

Points the text above leaves open, read by me before any development number exists:

- **k(h) out of reach.** If on some day no finite k brings pooled 80% coverage to 80 percent (more
  than a fifth of the weight has a band of no width on the side the actual falls), that fit has
  no k: a variant with such a fit in any fold is not eligible, and the final fit of a winner that
  hits it voids the stage. There is no cap.
- **"The last three months" (F)** are calendar months: the month of the forecast date and the two
  before it, up to the forecast date.
- **A repeating fee** is found from any day of the month (not only the latest fee's): a fee kind
  repeats on a day if its fees within 2 days of that day (counted from the start, or from the end,
  of the month) fall in at least two of those three months. Only the fees that match the pattern
  leave the drawn weeks; a fee of the same kind on another day is drawn with its week. The amount
  placed is the latest matched charge. "A day" is a day one of its fees was charged on (two fees
  4 days apart do not match through a day between them), and the fee is placed on the day of its
  latest matched charge (read 2026-11-27, before any F futures were saved).
- **b and d apply to the main futures only** (band, dip chance, lowest balance, Safe to spend,
  scores). The "without irregular deposits" futures stay as F or F+a draws them: k is fitted on
  the main futures, and walk paths contain deposits.
- **Ties.** "Within 0.1 point" means pooled CRPS skill against v1 within 0.1 percentage point; an
  exact tie between walk shares goes to the smaller share.
