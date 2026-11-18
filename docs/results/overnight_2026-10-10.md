# Overnight work, 2026-10-10

Branch `calibration` (from main), plus `income-prototype` and `lightgbm-prototype` (from
`calibration`). Synthetic development personas only. No held-out persona, no `--final-test`, no
pull request, nothing merged into main, no history rewritten. Every method was written in
DECISIONS.md before it was run, with two exceptions said there plainly: a comparison-only map on
the raw chance in Task 2 (added after seeing the calibrated Brier score), and one change to the
history scan after a trial run (Task 8). The app is unchanged in behaviour: no calibration, no new
threshold, no prototype; it only gained two pages.

This file is updated after each task.

## Status

| Task | State |
|---|---|
| 1 Diagnosis | done |
| 2 Calibration | done |
| 3 Alert threshold | done |
| 4 Income prototype | done (branch `income-prototype`) |
| 5 Time Machine page | done |
| 6 Model report page | done |
| 7 README draft | done |
| 8 History scan | done (report only) |
| 9 LightGBM experiment | done (branch `lightgbm-prototype`): baseline stays |

## Task 1: diagnosis

`python -m eval.diagnose` writes `docs/results/diagnosis_2026-10-10.md`. 13 development personas
(ITIGHT-01..03, IFAM-01..03, FREE-01, SAL-01..03, TIGHT-01..03), 520 origins. The 30-day change of
the mean future is split into parts by the generator's answer key and compared with what happened.

Mean signed 30-day error by part (positive = too optimistic), mean absolute in brackets:

| Part | ITIGHT (3, 120) | IFAM (3, 120) | FREE (1, 40) | SAL (3, 120) | TIGHT (3, 120) |
|---|---|---|---|---|---|
| Tuition payments | $5,410 ($5,410) | $1,129 ($1,129) | - | - | - |
| Family deposits and top-ups | -$4,501 ($4,750) | -$1,499 ($1,499) | - | - | - |
| Other irregular income | - | - | $312 ($1,438) | - | - |
| Regular income | -$456 ($503) | $761 ($947) | - | $215 ($215) | $92 ($92) |
| Recurring bills | $58 ($62) | $70 ($92) | $21 ($37) | $102 ($122) | $62 ($74) |
| Everyday spending | -$20 ($162) | -$56 ($149) | $101 ($243) | -$7 ($226) | $122 ($247) |
| **All parts** | $507 ($6,561) | $379 ($2,809) | $423 ($1,440) | $308 ($532) | $260 ($395) |

What the forecaster does with irregular income (row-origin pairs over the 13 weeks before each
origin):
- IFAM family deposits: all 43 set aside as "large deposit". Never forecast.
- ITIGHT family money: 177 set aside as large deposits ($703k), 25 as pass-throughs, 122 resampled
  by the weekly draw ($66k), 104 placed as a detected series ($86k).
- FREE deposits: 322 of 326 resampled; 4 set aside.
- Tuition (out): always set aside (large payment or pass-through). Never forecast.

Mechanisms:
- **Optimism on synthetic**: tuition is set aside as a one-time event, so the futures never
  contain it, and the family money that pays for it has often arrived before the origin.
  Lowest-balance bias in windows with a tuition payment: ITIGHT +$18,764 (22 origins), IFAM
  +$4,371 (22 origins).
- **Pessimism on an account paid in large deposits**: big deposits are set aside as "large
  deposits" (or pass-throughs), so that money is removed from the futures while rent and spending
  stay in.
  On synthetic this shows as the family-deposit part (ITIGHT -$4,501, IFAM -$1,499), and in
  windows with irregular income but no tuition the lowest balance is pessimistic (ITIGHT -$306,
  93 origins; IFAM -$355, 13). An account with large deposits in and no outflow of the same size
  keeps only this part.

Bug found, not fixed: a pay date moved earlier by a holiday (Christmas, Good Friday) is placed
twice. SAL-01 and TIGHT-01 get one extra paycheque at 4 of 40 origins. Proposed fix (one line, in
`lookahead/forecast/schedule.py`, `_pattern_items`): drop later dates equal to `first`, e.g.
`later = [d for d in later if d != first]`. I did not apply it because the calibration is fitted
on the current forecaster; after I apply it, I rerun `python -m eval.calibrate`.

## Task 2: calibration

`python -m eval.calibrate` reruns the development backtest from scratch (20 personas, 40 origins,
500 futures, keeping every future's path; about 1.5 minutes on 4 cores), fits, evaluates
leave-one-persona-out, and writes `docs/results/calibration.json` and
`docs/results/calibration_2026-10-10.md`. `--reuse` refits on the saved run. The app is not
switched.

Method (DECISIONS.md, written before fitting): one multiplier per horizon day widens every future
around the middle future; the chance and the lowest balance are read from the widened futures;
then a monotone map (logistic or isotonic, the simpler unless isotonic is better by > 0.002, kept
only if it lowers the Brier score by >= 0.002).

Before → after, leave-one-persona-out, ordinary origins (726 origins, 20 personas):

| | Before | After |
|---|---|---|
| 80% band coverage 7 / 14 / 30 days / all | 57 / 51 / 41 / 52% | 80 / 80 / 79 / 80% |
| Band width 7 / 14 / 30 days | $394 / $591 / $849 | $946 / $2,542 / $5,520 |
| Path-level coverage | 8% | 46% |
| Lowest-balance bias (typical miss) | +$724 ($1,007) | +$257 ($1,231) |
| Brier (reference 0.143) | 0.059 | 0.064 |

Multipliers: 2.4 at 7 days, 4.1 at 14, 6.4 at 30. Chosen map: isotonic (widened 0.0919, + logistic
0.0943, + isotonic 0.0642). Brier per archetype before → after: ITIGHT 0.302 → 0.263, TIGHT 0.089 →
0.108, DRENT 0.063 → 0.073, DHOME 0.015 → 0.021, FREE 0.000 → 0.063, IFAM 0.000 → 0.006, SAL 0.000 →
0.001, SEM 0.000 → 0.003. Reliability tables are in the results file.

Honest reading:
- Coverage is 80% on average only. Per archetype after widening: ITIGHT 51%, IFAM 68%, the others
  85-96%. One multiplier per day covers the tuition misses by being far too wide for everyone else.
- The calibrated chance is slightly *worse* than the raw one (0.064 vs 0.059). I added, after
  seeing that, a comparison-only map on the raw chance: raw + logistic 0.0584, raw + isotonic
  0.0588. No map helps the raw chance by more than 0.001.
- How it does on my own account is checked with the apply command below (evaluation only).

## Task 3: alert threshold

`python -m eval.thresholds` writes `docs/results/thresholds_2026-10-10.md`. Calibrated
(leave-one-persona-out) chances, probability-only rule, the app's own alert engine. 726 ordinary
origins, 52 shortfall episodes, 20 personas.

| Threshold | Recall | Precision | False per 30 days | Lead time mean (median) |
|---|---|---|---|---|
| 5% | 52% (27/52) | 38% | 0.230 | 17.3 (21) |
| 20% | 52% (27/52) | 48% | 0.159 | 16.0 (15) |
| 30% | 56% (29/52) | 68% | 0.071 | 15.5 (15) |
| 35% | 67% (35/52) | 71% | 0.071 | 16.0 (16) |
| 40% | 65% (34/52) | 88% | 0.024 | 16.3 (16) |
| 60% | 63% (33/52) | 88% | 0.024 | 16.2 (16) |
| 80% | 65% (34/52) | 91% | 0.018 | 15.3 (14) |
| 95% (chosen) | 67% (35/52) | 97% | 0.006 | 12.8 (14) |
| provisional rule, raw chances | 50% (26/52) | 77% | 0.041 | 16.9 (21) |

- The objective picks 95%. Every threshold from 35% up meets both constraints.
- Leave one persona out (threshold picked on 19, applied to the 20th; folds picked 75% or 95%):
  recall 62% (32/52), precision 96% (27/28), 0.006 false per 30 days, lead 13.2 days.
- Recall goes up with the threshold. Cause (checked): at low thresholds one alert episode stays
  open across several dips (often opened while already below the buffer), and a dip only counts
  as caught by an episode that opens in the 30 days before it. All 15 such dips are at DRENT-03,
  TIGHT-02, TIGHT-03 (balances that hover near the buffer). Worth deciding whether the metric
  should credit an alert that is already on when a dip starts.
- The app's provisional rule is unchanged.

## Task 4: income prototype (branch `income-prototype`, not merged)

The one fix (DECISIONS.md on that branch, written before testing): deposits the forecaster sets
aside as "large deposits" (big, in no series, not paid straight back out) come back in the
futures as random arrivals, at their own observed rate over the last 365 days and with sizes drawn
from the observed ones. Pass-throughs and big payments (tuition) are unchanged. Rule zero kept
(tests). Lumps count as irregular deposits ("no other money" turns them off).

Development personas, 726 ordinary origins, 52 shortfall episodes:

| Metric | current raw | current calibrated | prototype raw | prototype calibrated |
|---|---|---|---|---|
| Error 7 / 14 / 30 days | $667 / $1,204 / $1,901 | same | $666 / $1,199 / $1,881 | same |
| Skill vs balance-stays | 0.32 / 0.10 / 0.01 | same | 0.32 / 0.11 / 0.02 | same |
| Coverage 7 / 14 / 30 / all | 57 / 51 / 41 / 52% | 80 / 80 / 79 / 80% | 62 / 56 / 49 / 57% | 79 / 79 / 79 / 79% |
| Width 7 / 14 / 30 days | $394 / $591 / $849 | $946 / $2,542 / $5,520 | $542 / $1,852 / $2,931 | $1,017 / $5,293 / $10,842 |
| Path-level coverage | 8% | 46% | 10% | 45% |
| Lowest-balance bias | +$724 | +$257 | +$749 | +$449 |
| Brier | 0.059 | 0.064 | 0.061 | 0.062 |
| Alerts (rule) | provisional | > 95% | provisional | > 95% |
| Recall | 50% (26/52) | 67% (35/52) | 40% (21/52) | 60% (31/52) |
| Precision | 77% | 97% | 74% | 96% |
| False per 30 days | 0.041 | 0.006 | 0.041 | 0.006 |

- ITIGHT coverage 24% → 48% raw. ITIGHT windows with family money and no tuition: lowest-balance
  bias -$306 → -$139 (93 origins), less pessimistic as intended. Tuition windows stay far too
  optimistic (unchanged). SAL, SEM, TIGHT unchanged.
- On synthetic data the fix moves things the optimistic way, which hurts recall here. That is
  expected: the synthetic personas are optimistic because tuition is not forecast. An account paid
  in large deposits is the opposite case, so a real backtest on this branch is the test that
  matters.

## Task 5: Time machine page

`app/pages/6_Time_machine.py`, on top of `lookahead/timemachine.py`. Pick a synthetic persona
(the three demo personas and four development ones: TIGHT-03, ITIGHT-01, FREE-01, SAL-01), step
through dates with Week/Day buttons or the slider, and see the band and middle future from that
day, the alert state and its evidence, four number cards, a toggle for "what really happened next"
(dotted), and a what-if at the current date (spend less, one purchase or deposit, money in late, no
other money). "Synthetic data" is in the top bar, beside the title and in the footer.

- Snapshots: `python -m lookahead.timemachine_snapshots` (every day, all seven personas, about 7
  minutes; `--every 7` for a faster run). Files go to `data/timemachine/` (ignored by git), one
  database per persona, so the page never touches the demo database or my data.
- Held-out personas are refused. An empty what-if gives exactly the saved band (tested).
- Tests: `tests/test_timemachine_page.py` (pieces and the page, headless).

## Task 6: Model report page

`app/pages/7_Model_report.py`. Categorizer holdout results (from DECISIONS.md, with n), forecast
metrics, alerts, reliability, threshold curve, by archetype and by history length, and known
limitations. Columns: synthetic development | synthetic held out ("not run yet") | real | real at
$0. Synthetic numbers come from `docs/results/report_synthetic.json` (`python -m eval.report_data`
after `python -m eval.calibrate`). Real numbers are read from `data/private/backtest/full` when
the page opens and never written; without it the column says "run locally".

## Task 7: README draft

`README.md` rewritten in first person: what it is, the central interaction, features, a Mermaid
diagram, how to run it, how it is evaluated (rolling-origin backtest, synthetic personas with a
held-out set, rule zero), results (categorizer with n; forecast and alerts marked TO FILL AFTER
FINAL TEST), limitations, privacy and the disclaimer. I left out the optional language-model
fallback for merchant names and its numbers for now; the privacy section says only that an
optional feature, off by default, can send something, and points to DECISIONS.md.

## Task 8: pre-public history scan (report only, nothing changed)

`python -m eval.history_scan` writes `docs/results/history_scan.md`: 64 commits on all branch refs,
478 distinct file contents, plus every commit message. Matches are masked; dollar amounts are
counted, not printed.

| What | Found | My reading |
|---|---|---|
| Files under data/private, .env, databases, model or key files | 0 | clean |
| Secret-like strings (API keys, private keys, filled-in keys) | 0 | clean |
| Email addresses in files | 0 | clean |
| Commit author email | 1 address in 128 commit fields | public with the repo; consider a noreply address |
| Phone numbers | 2 (tests/test_merchants.py) | made-up test strings |
| Card-number-like | 3 (tests/test_pseudonymize_report.py) | made-up test strings |
| Account-number-like digit runs | 27 (synthetic CSVs and tests) | synthetic or made-up |
| Possible people's names | 0 | none outside the synthetic lists |
| Large files (1 MB+) | 0 | clean |
| AI assistant or vendor mentions | 148 | older DECISIONS.md lines, the optional fallback modules and tests, docs/PLAN.md, requirements-experiments.txt, eval/backtest_real.py's first line, and branch names |
| Dollar amounts added to DECISIONS.md | 166 lines | 13 lines also talk about my own data; I read those first. Three were added tonight (eb01fea, 31671c4, 8c3a224) and their only amount is "$0" |

To handle before going public:
- Older DECISIONS.md lines with amounts from my own data. They are in the history whatever the
  current file says.
- A slip: commit `31671c4` on `income-prototype` quoted two real-account rates in DECISIONS.md. I
  removed them in `8c3a224`, but they stay in that branch's history. The branch is not merged, so
  deleting it before going public drops them. Nothing was rewritten.
- A stray remote branch made by a setup step (no work on it) is to be deleted.

## Task 9: LightGBM spending model (branch `lightgbm-prototype`, not merged)

Design written first (DECISIONS.md on that branch): a direct model of cumulative everyday spending
D(t, h) at h = 7, 14, 21, 30 (interpolated between), features known at t (trailing spending and its
spread, weekday mix, paydays, balance minus buffer, month), normalized by each persona's trailing
median week, fixed LightGBM settings, leave one persona out with a 30-day gap (trained only on other
personas' origins at least 30 days earlier). Rule fixed first: a candidate only if D error at 30
days is at least 5% lower and not worse at 7 days.

640 test origins (32 Mondays from Jan 26, 20 personas):

| 7 / 14 / 30 days | Weekday baseline | LightGBM |
|---|---|---|
| Error of D (mean absolute) | $95 / $140 / $229 | $116 / $170 / $275 |
| Balance error, single path | $511 / $1,017 / $1,672 | $523 / $1,025 / $1,688 |
| D band coverage (nominal 80%) | 72 / 73 / 67% | 63 / 60 / 63% |
| Lowest balance bias / typical miss | $357 / $822 | $356 / $819 |

**The weekday baseline stays.** LightGBM is worse at every horizon and in 7 of 8 archetypes. Note
that spending is a small part of the balance error; the large parts are tuition and family
deposits (Task 1).

## Decisions I made on my own (all recorded in DECISIONS.md)

- Diagnosis by the *mean* future, not the median (only a mean splits into parts); added "regular
  income" and "fees" parts so the parts add up.
- Band multipliers per day, not smoothed; fitted on all origins; probability map on ordinary
  origins; pre-set margins of 0.002 Brier for keeping a map and preferring isotonic.
- Threshold rule studied without the median clause (it would be a second, unmapped rule).
- Income prototype: lumps from the last 365 days, pass-throughs excluded, own random stream; its
  own output folders so nothing mixes with my current results.
- Time machine personas: the 3 demo personas plus TIGHT-03, ITIGHT-01, FREE-01, SAL-01.
- LightGBM: horizons 7/14/21/30 with interpolation; test origins only where 28 days of training
  origins exist before the gap.

## Not done / open

- The holiday double-paycheque bug (Task 1) is reported, not fixed. Proposed one-line fix in
  `lookahead/forecast/schedule.py`, `_pattern_items`: `later = [d for d in later if d != first]`.
  Then rerun `python -m eval.calibrate`, `python -m eval.thresholds`, `python -m eval.report_data`.
- Whether the alert metric should credit an alert that is already on when a dip starts (Task 3).
- The real-data runs are mine, done separately. The commands are below.

## Commands for the morning (PowerShell, in the project folder, venv active)

Get the three branches:

```powershell
git fetch origin
git switch calibration
pip install -r requirements.txt
pytest -m "not slow"
```

Apply the calibration to my real backtest (evaluation only; nothing is fitted on my data).
The first command reruns the real backtest keeping every future's path, into its own folder
`data/private/backtest/paths` (a few minutes; my `full/` run is untouched):

```powershell
python -m eval.backtest_real --paths
python -m eval.metrics --no-synthetic --real data/private/backtest/paths
python -m eval.calibrate apply --real data/private/backtest/paths
```

`apply` also works on my existing folder (`--real data/private/backtest`), but then only the
band can be calibrated; the chance and the lowest balance need the paths.

The income prototype on my real data (its own folder, never mixed with the others):

```powershell
git switch income-prototype
python -m eval.backtest_real --paths
python -m eval.metrics --no-synthetic --real data/private/backtest/income-prototype/paths
python -m eval.calibrate apply --real data/private/backtest/income-prototype/paths
git switch calibration
```

What to look for: on the prototype, does the chance of going below $0 match how often it really
happened, does skill against "balance stays" improve, and are the real dips still
caught?

See the new pages:

```powershell
git switch calibration
python -m lookahead.timemachine_snapshots      # once; about 7 minutes (add --every 7 to go faster)
streamlit run app/Home.py
```

Time machine and Model report are in the top bar. The Model report reads my real results from
`data/private/backtest/full` when the page opens; nothing is written.

Refit the calibration after I change the forecaster (for example after the holiday fix):

```powershell
python -m eval.calibrate          # about 2 minutes on 4 cores
python -m eval.thresholds
python -m eval.report_data        # refreshes the Model report's synthetic numbers
```

LightGBM experiment (synthetic only, nothing to run on my data):

```powershell
git switch lightgbm-prototype
pip install lightgbm==4.7.0
python -m eval.lgbm_spending
```
