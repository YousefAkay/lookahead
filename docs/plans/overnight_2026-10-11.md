# Overnight plan, 2026-10-11 (branch `proposals`)

Written 2026-10-10 for a cloud session that works alone overnight. Read the project instructions at the repository root, this file, and the
last entries of `DECISIONS.md` first. Everything here was agreed with Yousef on 2026-10-10 except where a
line says "my proposal" (a number or gate I chose; he may change it in the morning).

## What this run is for

Three ideas for the forecaster, none built yet:

- **c) The alert-episode definition.** A shortfall episode counts as *caught* if an alert was on at some
  ordinary origin before its first day. Today only an alert episode that *opened* in the 30 days before it counts.
- **a) Expected lump income as proposals.** Detect irregular repeat deposits by sender (typical size, typical gap)
  and propose "expecting about $X from <sender> around <date>?" with *Looks right* / *Not this month*.
  Confirmed: a known item with a narrow band. Unconfirmed: the random arrivals of the income prototype
  (resampling) as the honest fallback.
- **b) Repeated large outflows (tuition and similar).** A payee with repeated large payments in a semester or
  yearly pattern is placed on its expected date by default, labelled "detected, not confirmed", with
  *Looks right* / *Not this time*. With only one payment seen, the app proposes a planned item prefilled from it.

The run builds the means to test them, tests them on synthetic development personas against the generator's
answer key, and writes up what happened. It does not decide anything for Yousef.

## Hard rules for this run

1. Work only on the branch `proposals`. Push only that branch. Never touch `main`, `calibration`,
   `lightgbm-prototype`; never merge, never rebase, never force-push, never delete a branch.
2. Never open, read, list or run anything on `data/private/`. No real numbers anywhere in the repository (not in
   DECISIONS.md, results files, tests, commit messages). Real-data commands are written for Yousef to run; the
   run only writes them.
3. Synthetic development personas only. Held-out personas (the held-out list in `lookahead/generator/archetypes.py`,
   and the same ids in the 24-month set) are never generated for evaluation, never used to choose anything;
   code that tunes calls `assert_development_only`. No `--held-out`, no `--final-test`.
4. The frozen 12-month mix (`v2-...`) is not changed. Its results stay as they are.
5. Rule zero: every feature, proposal, assumption and series used for a forecast on date *t* uses only rows posted
   on or before *t* (and decisions dated on or before *t*). Each new piece gets a test that rewrites or deletes
   everything after *t* and checks the output does not change, and a mutation check (break the rule on purpose,
   see tests fail).
6. **Every method is written in DECISIONS.md and committed before the code that computes it runs**: the
   definitions, the metrics, the prediction, what would count as unexpected. Results go in a separate later entry.
   If a number looks too good (above 95 percent) or too bad, look for leakage or a bug and say so before anything else.
7. No new libraries. Money is integer cents. pandas 3. Type hints, short docstrings, ruff.
8. Before each commit: `ruff check .`, `ruff format --check .`, `pytest -m "not slow"`. Run the full suite
   (`pytest -q`) at the end of each numbered step. Commit after each step with a clear message, push the branch.
9. App wording: "forecast", "estimate", "possible", "expected", "detected, not confirmed". Never "guarantee",
   "fraud", "you missed a payment".
10. No names of AI tools or vendors in any file or commit message of this repository.
11. Keep synthetic and real results in separate columns. Report n (personas, origins, shortfall episodes) with every number.
12. **Stopping rule.** Each step has a "continue only if" check. If it fails, write what you saw (counts and a short
    explanation, in `docs/results/overnight_2026-10-11.md` and DECISIONS.md), commit it, and **stop that line**. Do not
    tune, adjust the generator, change a gate or try another method to rescue it. Lines that do not depend on it
    carry on (see the dependency table).

## Order and dependencies

| Step | What | Depends on |
|---|---|---|
| 0 | Check state | nothing |
| A | The 24-month persona set (built first) | 0 |
| 1 | c) the alert-episode metric, threshold on raw chances | 0 |
| 2 | Detection and proposals for a) and b), scored on the answer key | A (for b), 0 (for a) |
| 3 | The arms, backtested on development personas | 1 (alert columns), 2 (the arms it needs) |
| 4 | Report and the commands for Yousef | whichever of the above finished |

Steps 1 and 2a do not depend on each other. If step A fails, b) is not scored on the long set (it can still be
built, and the run says it was only scored on the 12-month set, where it is weak by construction).

## Step 0: state

- `git switch proposals`; confirm it contains the lump detection (`lump_rows` in `lookahead/forecast/spending.py`, with tests) and
  `eval/lump_senders.py` (the counts-only report), and that `lookahead/forecast/schedule.py` has the holiday fix.
- `pytest -m "not slow"` and ruff pass. If not, stop and write it up.
- Create `docs/results/overnight_2026-10-11.md`: a status table (step, state, one line), updated after every step.
- **Rebuild the random lump arrivals (the income prototype's one change) as a switch, default off.** The prototype branch is deleted and its
  code was not brought over, so this run writes it again from Appendix A, with tests, **before anything else uses it**. With the switch off
  the forecaster must give exactly what `main` gives (test: identical `Simulation` output on three synthetic personas).
  Continue only if the switch-off identity holds and the rule-zero and "own random stream" tests pass; otherwise stop everything that needs arrivals
  (arms 1 to 5) and say so.

## Step A: the 24-month persona set (a new set of results, not a change to the frozen mix)

**Why.** Tuition falls due around Jan 20, May 20 (summer-term personas) and Sep 12. In 12 months of history a
tuition payee has been paid once, or twice for the six summer-term personas, so a repeat pattern cannot be
detected for most personas, and a sender of family money has 2 or 3 big deposits. b) cannot be tested on the 12-month set.

**Definition (write in DECISIONS.md first).**
- Same 30 persona specs, same archetypes, ranges, assignments, buffers and held-out list as the frozen mix. Ids
  `v2L-<ARCH>-<nn>` (L for long). The only change: history from 2024-10-01 to 2026-09-30 (24 months), seed 42. A persona
  of this set is not "the same persona with more rows": the day-by-day simulation runs longer, so its last 12 months differ from the 12-month set.
- Origins: the same 40 weekly Mondays, 2025-12-01 to 2026-08-31 (so the history at an origin is 14 to 23 months).
  Results on this set are never pooled with the 12-month set.
- Held out: the same ten ids, `v2L-IFAM-04` and so on. They are not generated for any evaluation here, and the
  development-only guard refuses them (add the test).
- No modelling change is made for the longer history. Known limitation to write down: tuition amounts do not rise
  from year to year in the generator, while real tuition does; a payee-based rule that uses the latest amount is therefore easier here than in life.
- Acceptance checks, as for the frozen mix (`python -m eval.persona_report --check`, adapted to the long set, generated
  balances only): report lowest, closing and origins starting below the buffer for each development persona.
  **Nothing is adjusted whatever comes out** (as agreed for the frozen mix: a failing persona is a recorded limitation).

**Build.** `lookahead/generator/archetypes.py`: a `long` option that moves START back 12 months and prefixes ids;
`eval/persona_report.py` and the backtest runner accept the set (its own output folder, its own record `FORMAT` marker so it
can never be mixed with the 12-month records). Tests: ids, held-out refusal, the 12-month set is byte-for-byte unchanged
(compare a hash of a few generated exports before and after the change), tuition payments per persona per year.

**Continue only if.** The 12-month personas are unchanged (hash test passes) and every development persona of the long set generates
and reconciles (importer PASS). Unexpected, so stop line b): fewer than two tuition payments from one payee before the last origin at most
of the personas that pay tuition from chequing, or a development persona that ends below -$20,000 (my proposal): write up the table and stop;
the 12-month set stays the only set.

## Step 1: c) the alert-episode definition

**The agreed change.** A shortfall episode, first day *D*, is caught if an alert was on at some ordinary origin *O* with
*O* < *D* and *D* - *O* <= 30. "On" means an alert episode that is open at *O*, from the day it opened until the day
it closed. An origin already in shortfall cannot warn (it is not ahead of time). The false-alert definition, precision and
false alerts per 30 days are unchanged. Lead time is *D* minus the earliest such *O* inside the 30-day window.

**Recording protocol (the agreed one, followed exactly).**
1. DECISIONS.md entry first, stating exactly what Yousef and the run had seen before the change: the provisional-rule results, the
   threshold curve on calibrated chances, the cause of the 15 dips at three personas (DRENT-03, TIGHT-02, TIGHT-03: one alert
   stayed open across several dips). It says the change is motivated by that cause, not by a target number. Also: the real
   account has been seen, so its numbers stay development.
2. The prediction, written before computing: recall rises at low thresholds (mostly for the personas that hover near the buffer)
   and moves little at high thresholds; precision, false alerts per 30 days and the number of shortfall episodes do not change at all.
3. Applied to everything at once: the provisional rule, every threshold, and every arm of step 3. Not only where it helps.
4. Both definitions printed side by side in every report; the original is labelled "opened ahead (original)", the new one "on ahead (new)".
5. The threshold is chosen again, **on raw chances** (calibration is shelved), with the new definition, on development personas only. The
   objective is unchanged (highest recall with precision at least 70 percent and at most 0.1 false alerts per 30 days; ties: higher
   precision, then the higher threshold; thresholds 2, 5, 10, ..., 95 percent), chosen leave one persona out as before.
   The candidates also include the provisional rule's 20 percent with its median clause, printed as its own row.
   The result is a development result: nothing changes in the app, and the held-out personas get the definition and threshold
   fixed by then, in one run at the very end.

**Build.** `eval/metrics.py`: the new definition next to the old one (`MERGE_GAP_DAYS` and the shortfall-episode merge are untouched).
Tests, with made-up records: one alert episode open across two dips (old: second missed; new: both caught); an episode that opened
inside a shortfall (not ahead of time) does not count; an origin already in shortfall does not count as *O*; lead time; the new
caught set always contains the old one; false alerts unchanged. Mutation check: make *O* allowed to be in shortfall and see a test fail.
Then `python -m eval.thresholds`-style run on raw chances (a new, clearly named entry point; do not overwrite the calibrated results).

**Continue only if (unexpected means stop line c) and write it up, and do not choose a threshold).** Any of: recall under the new
definition is lower than under the old at any threshold (cannot happen if the code is right: it is a bug); precision, false alerts
per 30 days, alert episodes or shortfall episodes differ between the two definitions; recall at the highest thresholds (80 percent and
above) rises by more than 5 percentage points; the leave-one-out threshold folds disagree widely (more than three different
thresholds across the 20 folds). In any of these, report the table and a diagnosis, nothing else. Step 3 then reports the alert
columns under the original definition only, and says why.

## Step 2: detection and proposals for a) and b)

DECISIONS.md first (method, answer key, gates, prediction), then code.

### 2a) Proposals for lump income

- **Lumps:** `lump_deposits` as on this branch (a big deposit in no series, not a pass-through, in the last 365 days). `eval/lump_senders.py`
  already counts how many carry a sender name; extend it with the sender grouping below.
- **Sender:** the keyed token of the sender text (never the name), as for e-Transfer recipients. A lump with no sender text goes only to
  the pooled resampling. Two names that are one person are not merged (a limitation, said in the entry).
- **A proposal** needs at least 2 big deposits from one sender with the next expected date inside the 30-day horizon. Two deposits is
  "possible", three or more "likely". Typical size is the median size. Typical gap is the median gap. Window: expected date +/- the larger of 10 days and a quarter of the gap.
  Expected date: last deposit plus the typical gap, moved to a business day the way income is.
- **What a confirmation does:** `Looks right`: placed at the proposed size on a date moved inside the window in each future, with its sender's lumps taken out of the
  resampling pool. `Not this month`: nothing from that sender until the window passes or a deposit arrives, and none from the resampling either.
  Unconfirmed: the random arrivals (resampling) as on the income prototype. A confirmation is a dated row in the decision log, replayed
  as of *t* (same pattern as the recurring decisions). A proposal object carries its evidence (the fingerprints of the deposits) and
  its confidence ("possible" / "likely"), and is labelled "detected, not confirmed" until confirmed.
- **Answer key** (the generator's schedule objects, never the detector's thresholds): a proposal is *right* if its sender is the merchant of a planted
  income series (tuition funding, top-up, allowance, aid, irregular family transfers) and that series' next planted date falls inside the proposed window.
  A planted series is *findable* at an origin if at least 2 of its big deposits were posted by then.
- **Scores, development personas, both sets (12-month and long, reported apart):** precision (right proposals / proposals), recall (findable series proposed before
  their next arrival / findable series), mean absolute date error and size error, how many deposits were needed, false proposals per persona-year, and
  how often the sender text was missing (so no proposal was possible).
- **Gate (my proposal):** continue to the a) arms only if precision is at least 70 percent and recall at least 50 percent on at least one set. Otherwise
  write up the table with the reasons (too few deposits, merged senders, sizes mixed with small transfers) and stop line a): arms 2, 3 and 5 are not run.
- **Prediction, written first:** precision is high where a sender sends one kind of deposit and falls where one sender sends both small regular
  transfers and big ones; recall is low on the 12-month set (few repeats) and better on the long set.

### 2b) Repeated large outflows

- **Payee:** the payee of a big outflow (bill payments name one), the keyed token if it is a person. A big outflow is as in the current big-row rule.
- **Detection:** at least 2 big payments to one payee, 60 to 400 days apart. The next date is the same calendar position one year after the latest one, or the latest
  plus the median gap, whichever is the next one still ahead of the origin and inside the horizon (write the choice and test both on the long set). Amount: the latest
  payment (tuition rises, so the latest, not the median). Window for jitter: +/- 5 days (my proposal; the generator draws tuition dates within 5 days).
- **Placement:** by default, without any click, labelled "detected, not confirmed". `Looks right` makes it "confirmed"; `Not this time` skips that occurrence only.
  Both are dated decisions replayed as of *t*. This reverses the 2026-10-02 choice "no semester cadence; two irregular payees are not a pattern"; the entry says so.
- **First time:** with only one big payment from a payee, no placement; the app offers a planned item prefilled from that payment (payee, amount, a date one year on).
  The planned-items code already exists; the run builds only the proposal object, not the page.
- **Pass-through pairing:** as it stands, a deposit paired with the outflow that paid it out is set aside together with it. A placed tuition payment without its funding deposit
  makes the forecast pessimistic if the deposit has not arrived yet, and optimistic if proposals for a) place a deposit without tuition. Do not change the pairing in this run:
  report the 2x2 (a) and b) alone and together) and show where it goes wrong. Linking a deposit to the payment it funds is a later step.
- **Answer key:** the tuition payments in the generator's schedule objects (its `link_id`s). Scored on the long set (12-month set reported apart, expected to be weak):
  precision and recall of placements (placed within 10 days of the real payment and within 20 percent of its amount), date error, and the number of first-time cases (no placement possible).
- **Gate (my proposal):** continue to the b) arms only if recall of placements is at least 50 percent on the long set among the origins where the payee had at least 2 payments. Otherwise
  write up and stop line b).
- **Prediction, written first:** recall is high for the two-installment and summer-term personas (a clear gap) and for any persona with a second year of tuition, and zero for first-time cases
  by design; precision falls where installments 30 to 45 days apart look like a second payment (the 60-day minimum gap should exclude them; check it does).

**Tests for both:** rule zero (deleting or rewriting every later row leaves the proposals identical; a decision dated after *t* is ignored), a mutation check, determinism with a fixed seed,
names never appear in a proposal key (only tokens), `Not this month` / `Not this time` behave as defined, one deposit or payment is never counted twice (a confirmed item and its pool).

## Step 3: the arms

Raw chances only (calibration is shelved). Development personas, the same 40 origins, 500 futures, seed 42, weekday method, so every arm is comparable
with the earlier results. Arms (each reports both episode definitions):

| Arm | What | Needs |
|---|---|---|
| 0 | the current forecaster (large deposits and tuition set aside) | - |
| 1 | the income prototype: lump deposits as random arrivals | - |
| 2 | a), every proposal confirmed (a naive user) | 2a gate |
| 3 | a), only proposals that match the answer key confirmed (a best-case user) | 2a gate |
| 4 | b) alone (repeated large outflows placed by default) | 2b gate |
| 5 | a) and b) together, a) as in arm 3 | both gates |

- The switch for the random arrivals (Step 0), so that arm 0 and arm 1 are the same code. **Sanity checks before reading anything:** arm 0 equals the numbers recorded for the
  current forecaster in `docs/results/overnight_2026-10-10.md` (Task 4 table, "current raw" column) for every persona except SAL-01 and TIGHT-01, where the holiday fix is
  the only expected difference (4 of 40 origins each); arm 1 equals that table's "prototype raw" column on the same terms. If either does not, stop and find out why.
- Metrics (existing code): balance error at 7, 14 and 30 days and skill, lowest-balance bias and typical miss (also split into windows with a tuition payment and windows without), band coverage and width,
  path-level coverage, Brier overall and per archetype, alerts at the provisional rule and at the threshold chosen in step 1, per archetype. n beside every number.
- Run on the 12-month set and on the long set, reported apart. Per archetype, ITIGHT always all five ids named as development ones (-01, -02, -03 here; -04, -05 are held out), with the known limitation text.
- **Read with suspicion:** any improvement above what the proposal quality scores allow; a persona that gets better only because of the answer key (arm 3 is an upper bound, say so); any metric above 95 percent.
- **Prediction, written first:** arm 2 (naive) is worse than arm 3 where false proposals exist; arm 1 and a) alone move the forecast towards optimism in windows with tuition (as before); b) alone moves it
  towards pessimism where the funding deposit has not arrived yet; the two together are best in windows with both.
- **Continue only if** the sanity checks pass. Unexpected (write up, no further arms): an arm that makes the 30-day error or the lowest-balance bias worse for most archetypes on both sets; or a proposal-based arm that beats arm 3's
  upper bound.

## Step 4: report and the morning commands

- `docs/results/proposals_2026-10-11.md`: every table with n, both episode definitions side by side, the gates and what each did, the stopped lines with reasons, what is unexpected. No recommendation to merge.
- DECISIONS.md: a results entry for each step (separate from the method entries written first), including methods that lost and lines that stopped.
- `docs/results/overnight_2026-10-11.md`: final status table, decisions made on the run's own, open questions.
- Commands for Yousef, in PowerShell, counts and summary numbers only, for his real account (the run writes them, never runs them):
  1. `python -m eval.lump_senders <database>`: counts of big deposits with and without a sender name, distinct senders, senders with 2 or more and 3 or more big deposits.
  2. The real-account version of the proposal checks (how many proposals at each origin, how many were followed by a deposit from the sender inside the window, date and size misses; how many placements of repeated large payments and how many were followed by a payment), to be added only if a) or b) passed its gate.
  3. The arms on his real backtest folder, each in its own folder, never mixed with the earlier real results.
  Each prints counts and summaries only, never rows or text. The notes say that real numbers are development, because the design was made while looking at his account.
- Final: full suite, ruff, push the branch `proposals`. Do not open a pull request. Do not merge.

## Out of scope for this run

Buttons or pages for Looks right / Not this month (design is Yousef's call), the pairing of a deposit with the payment it funds, calibration of any kind, the final held-out test, the alert threshold in the app,
other alert types, anything on `data/private/`, deleting or merging any branch.

## If something is unclear

Write the question in `docs/results/overnight_2026-10-11.md` under "Questions for Yousef", take the more cautious reading, say which one you took, and go on with a line that does not depend on it.

## Appendix A: the random lump arrivals (the income prototype), as it was specified and tested

This is the design that was recorded before the prototype was tested. Rebuild it exactly, as an option that is off by default.

- **Lump:** a deposit the forecaster would set aside as a large deposit (`lump_rows`: in no series; at least $500 and 5 times the typical row of the 13 weeks before the
  forecast date, the typical row being that of the deposits when there are at least 5, else that of everything; not paid straight back out within 14 days), posted in the 365 days
  up to the forecast date. Pass-through deposits are not lumps: their outflow is set aside too, so bringing back the deposit alone would be optimistic. Tuition and other big payments stay set aside.
- **Rate:** number of lumps divided by the days in the window (from the later of the first posted row and 365 days back, to the forecast date, both included). A forecast with no history gives rate 0.
- **Draw:** in each simulated future, each forecast day gets a lump with probability equal to the rate (at most one a day), its size drawn at random from the observed lump sizes. Each future uses its own
  random stream named by the seed, the future and the word "lumps", so no other draw (weeks, dates, payments) moves when lumps are switched on.
- **Where it counts:** lumps are irregular deposits: they are in the futures "with deposits", not in the median "without irregular deposits"; the what-if "no other money expected" turns them off (rate 0).
  The single-path estimate does not change. Rule zero: only rows posted by the forecast date.
- **Inputs:** `SimulationInputs` gains `lump_rate: float = 0.0` and `lump_sizes: tuple[int, ...] = ()`; `build_forecast` fills them from `lump_rows` when the option is on; `run_scenario` zeroes `lump_rate`
  when `no_other_money` is set.
- **Tests (the prototype's):** off = identical to the forecaster without the option; a lump of the last year comes back in the futures with deposits and not in the median without them; the draws are
  reproducible with the same seed; adding lumps does not change the other draws; nothing posted after the forecast date changes the rate or the sizes (delete or rewrite later rows); "no other money" gives exactly the
  "without irregular deposits" median again; pass-throughs and series deposits are not drawn. The old test that said a large deposit is absent from the futures stays true with the option off.
