# Lookahead

Lookahead is a small app I built to forecast the balance of one chequing account for the next 30
days. It reads bank CSV exports, sorts every transaction into a category, works out which payments
repeat, and simulates 500 possible futures from the account's own past weeks. From those it shows
a likely range for the balance, the chance of dipping below a buffer you choose, and a warning with
the evidence behind it. It also has a "safe to spend" figure and what-if scenarios. Everything runs
on your own computer.

It is a personal project and a study in testing a model honestly. It is not financial advice.

## Categorizer results

I labelled my own transactions by hand (17 categories) and fixed two test sets before tuning
anything. Each was scored once, and nothing was changed after.

| Test set | n | Accuracy |
|---|---|---|
| Later rows, mostly merchants seen in training | 114 rows (35 merchants) | 91.2% |
| The same, rows the model was confident about (not flagged for review) | 104 rows | 98.1% |
| Merchants never seen in training | 48 rows (26 merchants) | 72.9% |
| The same, rows the model was confident about | 18 rows | 94.4% |
| Never-seen merchants, with the opt-in LLM fallback | 48 rows (26 merchants) | 83.3% |

The never-seen merchants are the honest number for a new account. When the model is unsure it
flags the row for review instead of guessing, and the confident rows are right 94 to 98% of the
time. The LLM fallback is off by default; when I turn it on, only the merchant name of a card
purchase is sent, never an amount, a date or a person's name. The test sets are small: most
categories have fewer than 10 test rows, and macro-F1 is much lower (69.6% and 49.8%).

## How I evaluate

- **Rules written first.** Before each experiment I write down what I will measure and the rule
  for adopting the change, in [DECISIONS.md](DECISIONS.md), commit it, and only then run it. A
  change that does not pass its rule is not adopted, and every variant I try is counted. So far
  the experiments on anchoring the forecast to recent flow and on blending it with "the balance
  stays the same" failed their rules, and nothing was adopted.
- **Synthetic personas.** I generate accounts from rules I wrote: students with family money,
  students renting, salaried people, tight budgets, a freelancer. 20 are for development, and every
  setting is chosen on those. 10 are held out for one final test.
- **Rolling-origin backtest.** The forecast is replayed every week of the history. At each date it
  may use only the rows posted by then, and it is scored against the 30 days that followed. Tests
  rewrite or delete later rows and check that the forecast does not change.
- **Berka.** As an outside check I use the public PKDD'99 Berka bank data (a Czech bank in the
  1990s), split into development and held-out accounts. It is not committed here.
- **The held-out test has not been run.** I made this repository public before the forecaster's
  final held-out test. The held-out personas and Berka accounts are unopened, and every forecaster
  number below is on development data only. The test runs once, when the forecaster is final, and
  nothing is tuned after it.
- **My own account is never scored in public.** I use it to find problems, but no forecast
  error, balance or amount from it is in this repository.

## Forecaster results (development data only)

**Synthetic personas.** CRPS skill against "the balance stays the same" over 30 days (higher is
better; 0 means no better than staying put). Mean over 8 kinds of persona, equal weight, 20
development personas, 40 weekly origins each, 500 futures per origin
([docs/results/baselines_2026-10-17.md](docs/results/baselines_2026-10-17.md)):

| Synthetic set | Origins scored | Skill vs "balance stays" |
|---|---|---|
| 12 months of history | 726 | +40.8% |
| 24 months of history | 717 | +47.8% |

The gain is uneven. By kind of persona, 12-month set then 24-month set:

| Kind of persona | 12 months: skill (origins, personas) | 24 months: skill (origins, personas) |
|---|---|---|
| SAL, salaried, paid every two weeks | +86.3% (120, 3) | +90.8% (120, 3) |
| TIGHT, worker on a tight budget | +74.8% (98, 3) | +81.4% (86, 3) |
| SEM, salaried, paid twice a month | +70.0% (40, 1) | +78.9% (40, 1) |
| ITIGHT, international student, stretched family money | +3.0% (86, 3) | +4.3% (81, 3) |

**Berka.** So far only a check that the forecaster runs and looks sane: on 400 development
accounts (200 primary, 200 for sensitivity) and 40 origins each, a forecast came back at all 16,000
origins ([docs/results/berka_sanity_2026-10-17.md](docs/results/berka_sanity_2026-10-17.md)).
No accuracy on Berka has been scored yet.

**Safe to spend.** The largest everyday spending a day that keeps 80% of the futures above the
buffer until the next expected income. On the 20 development personas (800 origins,
[docs/results/safe_to_spend_check_2026-11-17.md](docs/results/safe_to_spend_check_2026-11-17.md)),
where the amount was tight (at most 2 times the usual spending) and the actual spending stayed at
or below it, 89% of the origins stayed above the buffer (31 of 35 origins, 10 personas). By the
rule I wrote before running it, the 80% holds near the edge. 35 origins is a small n, so this says
the amount is not clearly optimistic, not that it is calibrated.

Not reported yet: the alert numbers (shortfalls caught, precision, false alerts per 30 days). The
alert threshold is provisional until the final test.

## Run it on the demo data

The demo uses made-up accounts from my generator. No bank data and no keys are needed. Python 3.13:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
LOOKAHEAD_DEMO=1 streamlit run app/Home.py
```

On Windows, activate with `.venv\Scripts\activate` and set `$env:LOOKAHEAD_DEMO = "1"` first.

`LOOKAHEAD_DEMO=1` turns on demo mode: synthetic accounts only, no CSV import, no outside calls,
and code that refuses to open anything under `data/private`. The Time Machine page replays past
dates of the synthetic accounts from snapshots kept in `data/demo/timemachine`.

To check the code: `pip install -r requirements-dev.txt`, then `pytest`. The full run takes a few
minutes; `pytest -m "not slow"` is the quick one.

Without demo mode the app only runs when served on localhost (`--server.address localhost`), so
that an account never ends up on a network by accident.

## Limits

- The synthetic personas follow rules I wrote, so they test the method on the kinds of accounts I
  thought of, not on the real world. Berka is the only outside data, it is old and Czech, and it
  has not been scored yet.
- The forecast does not expect a first or one-off large payment (tuition, say). For students paid
  in lumps the error is still large.
- The 80% band is too narrow, and the dip chance is overconfident at the low end.
- One chequing account at a time. Credit cards and other accounts are not modelled.
- The categorizer numbers rest on small test sets labelled by one person.
- The held-out test has not been run, so none of the forecaster numbers are final.

Estimates for personal budgeting. Not financial advice.
