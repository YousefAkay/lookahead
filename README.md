# Lookahead

Lookahead is a small app I built to forecast the balance of one chequing account for the next 30
days. It reads bank CSV exports, works out which payments repeat, and simulates 500 possible
futures from the account's own past weeks. From those it shows a likely range for the balance, the
chance of dipping below a buffer you choose, and a warning with the evidence behind it. It also
has a "safe to spend" figure and what-if scenarios (spend less, a deposit arrives late). Everything
runs on your own computer.

It is a personal project and a study in testing a forecast honestly. It is not financial advice.

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
dates of the synthetic accounts from snapshots kept in `data/demo/timemachine`. To put the demo
online, see `docs/DEPLOY.md`.

To check the code: `pip install -r requirements-dev.txt`, then `pytest`. The full run takes a
while; `pytest -m "not slow"` is the quick one.

Without demo mode the app only runs when served on localhost (`--server.address localhost`), so
that an account never ends up on a network by accident.

## How it is evaluated

- **Synthetic personas.** I generate accounts from rules I wrote (students with family money,
  students renting, salaried people, a tight budget, a freelancer). 20 are for development, and
  every setting is chosen on those. 10 are held out for one final test.
- **Rolling-origin backtest.** The forecast is replayed every week of the history. At each origin
  it may use only the rows posted by that day, and it is scored against the 30 days that followed.
  Tests rewrite or delete later rows and check that the forecast does not change.
- **Berka.** As a second, outside dataset I use the public PKDD'99 Berka bank data (a Czech bank in
  the 1990s). It is a different population, so it checks that the method is not tuned to my
  generator. It is not committed here and I do not redistribute it.
- **Rules written first.** Before each experiment I write down the measure and the rule for
  adopting the change, in [DECISIONS.md](DECISIONS.md), and then I run it. A change that does not
  pass its rule is not adopted, and every variant I try is counted. So far the experiments on
  anchoring the forecast to recent flow and on combining it with "the balance stays the same"
  failed their rules and nothing was adopted.
- **Held-out data not opened yet.** The 10 held-out personas and the held-out Berka accounts are
  for one final test, after the forecaster is final. As of the last note in DECISIONS.md they are
  unopened. I do not tune anything after opening them.

## Results

All forecast numbers below are on development data, not on the held-out sets. They are the numbers
in `DECISIONS.md` and `docs/results/`, with their n.

**Categorizer** (rows labelled by hand, scored once on a holdout that was fixed before any
tuning):

| Test set | n | Accuracy |
|---|---|---|
| Later rows, mostly merchants seen in training | 114 rows (35 merchants) | 91.2% |
| Merchants never seen in training | 48 rows (26 merchants) | 72.9% |

The second row is the honest number for new merchants. Most categories have fewer than 10 test
rows, and macro-F1 is much lower (69.6% and 49.8%).

**Forecaster**, as CRPS skill against "the balance stays the same" over 30 days (higher is
better; 0 means no better than staying put). Mean over 8 kinds of persona, equal weight, 20
development personas and 40 weekly origins each, 500 futures per origin
(`docs/results/baselines_2026-10-17.md`):

| Synthetic set | Origins scored | Skill vs "balance stays" |
|---|---|---|
| 12 months of history | 726 | +41% (40.8%) |
| 24 months of history | 717 | +48% (47.8%) |

The gain is uneven. It is large for the SAL, SEM and TIGHT kinds of persona (+70% to +91%) and
small for ITIGHT, the tight-budget student with irregular family money (+3% and +4%).

Still to fill in:

- TODO: forecaster skill on Berka (development accounts). The experiment records are in
  `docs/results/`, but I have not put a headline figure in DECISIONS.md.
- TODO: the final held-out results, synthetic and Berka. Not run yet.
- TODO: the alert numbers (caught shortfalls, precision, false alerts per 30 days). The alert
  threshold is provisional until the final test.

## Limits

- The synthetic personas follow rules I wrote, so they test the method on the kinds of accounts I
  thought of, not on the real world. Berka is the only outside check, and it is old and Czech.
- The forecast does not expect a first or one-off large payment (tuition, say). For students paid
  in lumps the error is still in the thousands of dollars.
- The 80% band is too narrow, and the dip chance is overconfident at the low end.
- Only one chequing account at a time. Credit cards and other accounts are not modelled.
- The categorizer numbers rest on small test sets, and labels from one person.
- "Safe to spend" is an estimate from past weeks. Its check on development personas says it is not
  optimistic, not that it is calibrated.
- The held-out test has not been run, so none of the numbers above are final.

Estimates for personal budgeting. Not financial advice.
