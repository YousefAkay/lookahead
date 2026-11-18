# Overnight plan, 2026-10-12 (branch `overnight-1012`, from `main`)

Written 2026-10-11 for a cloud session that works alone overnight. Read the project instructions at
the repository root, `docs/PLAN.md`, this file, and the last entries of `DECISIONS.md` first. Where a
line says "my proposal" it is a number or gate chosen by the writer; Yousef may change it in the
morning.

## What this run is for

`main` now holds everything: keeps-coming is the forecaster's default (random arrivals for big
money that keeps coming; transfers from my own accounts counted), and the cloud run's options are
there too, **off by default**: random lump arrivals (`ForecastOptions.lump_arrivals`), a) lump
proposals by sender and b) repeated large payments (`lump_proposals`, `outflow_placements`), the
24-month set `v2L`, the "on ahead" definition of caught, and the arms runner. The run does five
things, in this order:

- **R. Review fixes.** A review of the code as of 9 October (4cd020d..57b0592) found small and not so
  small problems. Each is checked against current `main` first.
- **1. b) by calendar position.** The proposals run found that "last payment plus the median gap"
  fails for tuition paid in two terms. Pre-register and score "about a year after the payment of
  the same term".
- **2. The arms on the merged code**, with keeps-coming as the baseline.
- **3. The alert threshold again**, on raw chances, on the merged forecaster.
- **4. The Model report's synthetic numbers**, rebuilt (everything computed before the holiday fix is stale).
- **5. The report**, with the exact commands for Yousef's real account.

## Hard rules for this run

1. Work only on the branch `overnight-1012` (create it from `main`). Push only that branch. Never
   touch `main`, `proposals`, `income-stream` or any other branch; never merge, rebase, force-push or
   delete a branch. Do not open a pull request.
2. Never open, read, list or run anything on `data/private/`, and never ask for it. Every test uses
   made-up or synthetic databases created in a temporary folder. The commands for the real account
   are written for Yousef to run.
3. **Real data in the repository.** No real number, date, name or transaction amount of Yousef's
   accounts anywhere in the repository (code, tests, DECISIONS.md, results files, commit messages).
   (The rule for what he may print in his terminal and share in a prompt is his own, recorded in
   DECISIONS.md; it does not change what is written into the repository.)
4. Synthetic development personas only. Held-out personas (the 12-month held-out list and the same
   ids in the 24-month set) are never generated for evaluation or used to choose anything; code
   that tunes calls `assert_development_only`. No `--held-out`, no `--final-test`.
5. The frozen 12-month mix is not changed; results of the two sets are never pooled.
6. Rule zero: every feature, proposal, assumption and series used for a forecast on date *t* uses
   only rows posted on or before *t* and decisions dated on or before *t*. Each new piece gets a test
   that rewrites or deletes everything after *t*, and a mutation check (break the rule on purpose
   and see a test fail).
7. **Every method is written in DECISIONS.md, with its prediction and what would count as unexpected,
   and committed before the code that computes it runs.** Results go in a separate later entry.
   If a number looks too good (above 95 percent), or the current numbers do not reproduce, look for
   leakage or a bug and say so before anything else.
8. Keeps-coming stays the default of `build_forecast`; the options of the proposals run stay off by
   default. `keeps=None` must keep giving the old forecaster exactly (a golden test exists:
   `tests/test_keeps_coming.py`).
9. No new libraries. Money is integer cents. pandas 3. Type hints, short docstrings, ruff.
10. Before each commit: `ruff check .`, `ruff format --check .`, `pytest -m "not slow"`. After each
    numbered step run the full suite (`pytest -q`, slow tests included). One commit per fix or step,
    with a clear message; push the branch after each step.
11. App wording: "forecast", "estimate", "possible", "expected", "detected, not confirmed". Never
    "guarantee", "fraud", "you missed a payment".
12. No names of AI tools or vendors in any file or commit message of this repository, and no
    attribution lines in commits.
13. Report n (personas, origins, shortfall episodes) with every number; synthetic and real results
    in separate columns.
14. **Stopping rule.** Each step has "continue only if" checks. If one fails, write what you saw
    (counts and a short explanation) in `docs/results/overnight_2026-10-12.md` and DECISIONS.md,
    commit it, and **stop that line**. Do not tune, change a gate, or try another method to rescue
    it. Lines that do not depend on it carry on (table below).

## Order and dependencies

| Step | What | Depends on |
|---|---|---|
| R | Review fixes R1 to R7 | nothing (items are independent; R4 must precede 2 and 3) |
| 1 | b) by calendar position, scored on the 24-month answer key | nothing (R6 first if it touches `build_persona`) |
| 2 | The arms | R4, and 1 for the arms that use b) |
| 3 | The alert threshold on raw chances | R4 |
| 4 | The Model report's synthetic numbers | 3 (it shows the threshold curve), R4, R5 |
| 5 | The report and the commands | whichever of the above finished |

## Step 0: state

- `git switch main && git pull`, then `git switch -c overnight-1012`. Confirm `main` contains
  `lookahead/forecast/spending.py` with `DEFAULT_KEEPS`, `ForecastOptions` in
  `lookahead/forecast/path.py`, `eval/arms.py`, `lookahead/proposals.py`, and the "on ahead"
  metrics in `eval/metrics.py`.
- `ruff check .`, `ruff format --check .`, `pytest -m "not slow"` pass. If not, stop and write it up.
- Create `docs/results/overnight_2026-10-12.md` with a status table (step, state, one line), and
  update it after every step.

## Step R: review fixes

The review agent scanned the code as of 9 October. **For each item, in this order:** (1) check
whether it still applies on current `main` (read the code; write the finding in the report: "applies",
"partly", or "no longer applies, because ..."); (2) if it applies, write a failing test first (made-up
or synthetic data only); (3) make the smallest fix; (4) one commit per item, message naming the item.
If an item no longer applies, change nothing, and say why in the report (add a small test that pins
the good behaviour only if it costs one test). If a fix would change a forecast number or a recorded
definition, write a DECISIONS.md entry first saying so and what the old and new numbers are on a
synthetic persona, and list the reports that become stale.

**Continue only if** the full suite is green after each item. If an item's fix breaks something you
cannot repair with a smaller change, revert that item (`git revert`, never rewrite), write it up and
go to the next.

- **R1. `eval/backtest_real.py` writes only into private paths.** Refuse `--out`, and the default
  output, unless `lookahead.merchants.is_private_path(out)` is true; refuse `--db` outside
  `data/private`. Replace the existing test that allows a non-private `--out` with tests that it is
  refused (with a clear message, before anything is created) and that a private one works. Check the
  `--arm` and `--keeps-coming` folders too.
- **R2. The Time Machine's saved steps.** `lookahead/timemachine.py` and
  `lookahead/timemachine_snapshots.py`: a saved step must not contain the account name, item names
  or series keys (keep counts and cents only). `precompute` refuses a non-private path when the
  database is private. Its meta file carries the format and logic versions, and a resume across
  versions is refused. Tests with a made-up database.
- **R3. Real result files named by a stable key.** Files such as `account-1.jsonl` are named by list
  position, so adding an account that sorts first would silently attach old results to another
  account. Name them with a short salted hash kept under `data/private` (created on first use), or
  refuse to resume when the account list changed. Test: adding an account that sorts first does not
  change an existing account's file. The record's `persona` field must keep not containing the account
  name.
- **R4. `eval/metrics.py`, `shortfall_episodes`.** Walk calendar days: an unobserved day ends a run
  and never merges. Test: two dips with 5 unobserved days between them are two episodes. This changes
  a definition, so: DECISIONS.md entry first (the old and the new episode counts on the synthetic
  development set, side by side), then the fix. Steps 2 and 3 must be run after it.
- **R5. The breakdowns promised in DECISIONS.md, "Week 4 step 4".** Every forecast metric by history
  bucket and by archetype for all three origin groups (all, ordinary, already in shortfall); alert
  counts by archetype; the median of the single-path error. Check what `python -m eval.metrics`
  prints now; add what is missing, with tests on made-up records.
- **R6. Small fixes** (one commit each): `restate()` on an empty `lowest_cents`; `build_persona` rejects
  ids that are not in `PERSONA_IDS` (and not a `v2L` id); `persona_report --check` with `--only` says
  which checks were skipped instead of passing them; `eval.backtest --held-out` writes to its own
  folder; the "39 origins" in a docstring (40); `run_real` does not create empty files.
- **R7. Records, no code.** DECISIONS.md: record as known generator limitations, without
  regenerating: 7 of 13 hourly students are paid on Mondays and Tuesdays, and the away personas are
  paid in early December. Correct "$200-930" to "$402-930" where the 30-day errors of the calm
  archetypes are quoted. Update `docs/PLAN.md`'s week table to week 4 as actually built (Time Machine,
  backtest, calibration shelved, keeps-coming default, the proposals options).

## Step 1: b) by calendar position

DECISIONS.md first: the method, the answer key, the gates, the prediction, then the code.

**The method (pre-registered; one rule, not a menu).** For a payee with at least 2 big payments in the
last 730 days (the big-row cutoff of the origin), each past payment *p* proposes the date *p* plus 365
days. The next date is the earliest proposed date after the origin and inside the 30-day horizon; if
several past payments propose it, the latest of them is the one used. The amount is the amount of
that same-term payment. Nothing is placed when no proposed date falls in the horizon. A payee seen
once gets the existing first-time offer. Keep the +/-5 day jitter and "detected, not confirmed" as on
`main`, the dated decision log (Looks right / Not this time), and the option name
`ForecastOptions.outflow_rule="calendar"` (the current rule stays `"earliest"`; nothing else
changes). A placed payment must not also be drawn by keeps-coming (test: its rows are not members of
`Arrivals`).

**Answer key and gates.** The generator's tuition schedule on the 24-month set, development personas
only; the same scoring as the proposals run (`eval.proposals_score`): a placement is right if a
tuition payment of the answer key is within 10 days and 20 percent of it. **Gate: recall at least 50
percent among payees with 2 or more payments, and precision at least 70 percent**, on the 24-month
set. Report also, apart, the two-term personas (January and September) and the three-term personas,
and the 12-month set (expected weak, 28 first-time cases).

**Prediction, written first.** Recall rises mainly at the two-term personas (the earlier rule got
0 of 13); the three-term personas stay about as they were; precision does not fall below the earlier
rule's. A persona whose tuition changed amount, or a payee with installments about a month apart,
is where it can go wrong.

**Continue only if** the tests (rule zero by rewriting later rows, decision after the date ignored,
determinism) pass. **If the gate fails: write the table and the reasons, and stop line b).** Do not
try a second rule. Arms that need b) are then not run.

## Step 2: the arms on the merged code

DECISIONS.md first. Raw chances; the 20 development personas; the same 40 origins; 500 futures;
seed 42; the 12-month set and the 24-month set, reported apart; each alert result under both
definitions of caught (opened ahead, on ahead), the new reading of "on" being the one recorded on
2026-10-12 (an episode that opened while already below the buffer counts from the first ordinary
origin at which it is still on).

| Arm | What | Needs |
|---|---|---|
| 0 | the forecaster as merged: keeps-coming on, options off (the baseline) | - |
| 1 | a) on top: every lump proposal confirmed (a naive user); keeps-coming still draws the rest | - |
| 2 | a) on top: only proposals that match the answer key confirmed (a best-case user, an upper bound) | - |
| 3 | b) by calendar position placed by default | Step 1 passed |
| 4 | a) as in arm 2, and b) as in arm 3 | Step 1 passed |

`lump_arrivals` (random lump arrivals as a separate option) is **not** an arm: keeps-coming is its
replacement and the two would double count; when both are on, rows keeps-coming draws are excluded
from `lump_arrivals` (this is already so in `build_forecast`; add a test if there is none).

**Sanity checks before reading anything (continue only if both pass).** (i) Arm 0's pooled numbers
equal the keeps-coming column recorded in `docs/results/keeps_coming_2026-10-11.md` (the run with
own-account transfers counted) on the 12-month set, to the dollar; the `proposals` run's own
recorded columns are not the comparison, because they were computed before keeps-coming. (ii) With
the options off, arm 0's records equal a run with `keeps` as the default, field for field. If a check
fails, find the cause (a changed default, a changed seed, a test edit) and write it up; do not change
the check, do not read the arms.

**Metrics, with n.** Balance error at 7, 14 and 30 days and skill, lowest-balance bias and typical
miss (all windows, and windows with a tuition payment, with family money and no tuition, and
neither), 80 percent coverage and width, whole-path coverage, Brier overall and per archetype,
alerts at the provisional rule under both definitions, per archetype.

**Drop rule, written now.** An arm is *worse* than arm 0 for an archetype if its mean absolute
30-day error is more than 1 percent above arm 0's, or the absolute value of its mean lowest-balance
miss is more than 1 percent above. An arm is dropped if more than half of the archetypes (5 or more of
8) are worse on either measure. Arms 2 and 4 are upper bounds (they use the answer key): say so and
never present them as what a user would get.

**Prediction, written first.** a) helps the international students (ITIGHT, IFAM) a little and does
nothing for SAL, SEM, TIGHT, FREE; b) helps the students who pay tuition from chequing in windows with
a tuition payment (the lowest balance there is the largest remaining optimism) and is neutral
elsewhere; a) and b) together are best in windows with both. **Unexpected, so write it up and stop that
line:** an arm worse than arm 0 for most archetypes; an arm with an answer-key arm (2 or 4) *worse*
than its non-key version; any persona that changes without a proposal or placement.

## Step 3: the alert threshold, again

On raw chances (calibration stays shelved), on the merged forecaster (arm 0 of Step 2, so after it),
development personas only, with the new reading of "on ahead" and with R4 in. The objective is
unchanged: the highest recall subject to precision at least 70 percent and at most 0.1 false alerts
per 30 days; ties to the higher precision, then the higher threshold; thresholds 2, 5, 10, ... 95
percent, chosen leave one persona out as before; the provisional rule's 20 percent with its median
clause is printed as its own row. Use `python -m eval.thresholds_raw`; write
`docs/results/thresholds_raw_2026-10-12.md`. Prediction: the chosen threshold is high (90 percent
last time) and recall under the new reading is higher than under the old at low thresholds, nearly
equal at high ones. **Stop checks** (as in the proposals run): the new caught set contains the old
one at every threshold; precision, false alerts and episode counts are the same under both
definitions of caught; the leave-one-out folds choose no more than three different thresholds. If a
check fails, report the table and the diagnosis and **do not choose a threshold.** Nothing changes
in the app.

## Step 4: the Model report's synthetic numbers

`docs/results/report_synthetic.json` and the page `app/pages/7_Model_report.py` still hold numbers
computed before the holiday fix. Rebuild them from the merged forecaster with the existing commands
(`python -m eval.calibrate`, which reruns the development backtest, then `python -m eval.report_data`),
keeping the calibration labelled "shelved: not used by the app". Check that the page still opens
(its tests), that the real column says it needs a local refresh (add the words "run locally on the
current forecaster; the saved results may be from an older version"), and that the held-out column
still says "not run yet". **Continue only if** the rebuilt arm-0-equivalent numbers equal Step 2's arm 0
(same forecaster, same seed): if not, find out why before saving anything.

## Step 5: report and commands

- `docs/results/overnight_2026-10-12.md`: every step's state, every table with n, the gates and what
  each did, the stopped lines and why, what was unexpected, decisions made on the run's own,
  questions for Yousef. No recommendation to merge.
- DECISIONS.md: a results entry for each step, separate from the method entries written first.
- **Commands for Yousef's real account** (PowerShell, from the repository root, after
  `git switch overnight-1012`): the current forecaster's backtest and metrics with his buffer and at
  $0; the arms that can be run on real data and what each prints; `eval.diagnose_real` before and
  after; and anything new that R1 to R3 change (a renamed folder, a refused path, a file naming
  that needs a one-off re-run). Say which printed numbers are aggregates (fine to share) and which
  commands print nothing else. The run only writes the commands; it never runs them.
- Final: full suite, ruff, push `overnight-1012`.

## Out of scope for this run

Any proposal UI (Looks right / Not this time buttons), merging, the final held-out test, calibration,
the alert threshold in the app, own-account transfers as a separate stream, recurring placement
(missed and extra payments), blending with "the balance stays", what the app shows for lumpy arrivals
(these three are candidate experiments to be pre-registered by Yousef), anything on `data/private/`.

## If something is unclear

Write the question in `docs/results/overnight_2026-10-12.md` under "Questions for Yousef", take the
more cautious reading, say which one you took, and go on with a line that does not depend on it.
