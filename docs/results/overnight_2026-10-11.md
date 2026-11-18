# Overnight work, 2026-10-11 (branch `proposals`)

The plan is `docs/plans/overnight_2026-10-11.md`. Synthetic development personas only. No held-out
persona, no `--final-test`, no pull request, nothing merged, `main` untouched, no history
rewritten, nothing read from `data/private/`. Every method is written in DECISIONS.md and
committed before the code that computes it runs. This file is updated after every step.

Two additions from Yousef for this run (recorded in DECISIONS.md):
1. b) (repeated large outflows placed by default) also needs a precision of at least 70 percent on
   the answer key, because a wrong detection placed by default becomes a false alarm.
2. a) is aimed at the student personas with large family transfers and tuition (IFAM, ITIGHT),
   not at the real account. a) and b) are judged
   on the synthetic answer key; this report says which archetypes they help and claims nothing
   about the real account.

## Status

| Step | State | One line |
|---|---|---|
| 0 State and random lump arrivals | done, check passed | switch off = identical output on 3 personas x 3 origins; rule-zero and own-stream tests pass |
| A 24-month persona set | done, checks passed | 12-month set byte-for-byte unchanged; 20 dev personas reconcile; b) stop rules not triggered; ITIGHT-01 and -03 run past -$3,000 (recorded limitation) |
| 1 c) alert-episode definition | done, checks passed | new definition beside the old everywhere; on raw chances the objective picks 90% (recall new 62%, 32 of 52; precision 96%); folds chose 65% or 90% |
| 2 a) and b) proposals on the answer key | a) passed its gate; **b) stopped** | a) 12-month precision 82% (40/49), recall 56% (35/63); b) 24-month recall 46% (37/81), precision 68% (39/57): below both bars |
| 3 Arms | **stopped at its sanity check** | pooled numbers reproduce both recorded columns exactly; per persona the holiday fix shows at 16 of 20 personas, not 2 (cause found: same-day duplicate income items); arms not read |
| 4 Report and commands | done | `docs/results/proposals_2026-10-11.md`; commands below; full suite and ruff pass; pushed |

## Step 0: state

- Branch `proposals` at 1cd4728 (lump_stages; lump detection reads saved decisions). It is `main`
  (8457077) plus three commits; the forecaster code is the same as on `main` (the diff in
  `lookahead/forecast` is the added lump functions only).
- `lump_rows` is in `lookahead/forecast/spending.py` with tests; `eval/lump_senders.py` exists;
  the holiday fix is in `lookahead/forecast/schedule.py` (`later = [d for d in later if d > first]`).
- `ruff check .`, `ruff format --check .` and `pytest -m "not slow"` pass (1,712 passed, 2 skipped).

### Random lump arrivals, rebuilt as a switch (off by default)

Rebuilt from Appendix A of the plan (the prototype's code was not available). Method in
DECISIONS.md before the code. `build_forecast(..., options=ForecastOptions(lump_arrivals=True))`
fills `SimulationInputs.lump_rate` and `lump_sizes` from `lump_rows` (365 days); each future
draws, day by day from its own "lumps" stream, a lump with probability `lump_rate` and a size
from the observed ones. Lumps count as irregular deposits ("no other money" turns them off).

Check (continue only if): **passed.**
- Switch off: the simulated output (every field, every future's path) of v2-ITIGHT-01,
  v2-IFAM-02 and v2-SAL-01 at 3 origins each (100 futures) is byte-for-byte what the code gave
  before the change (fingerprints taken first, `tests/test_lump_arrivals.py`).
- Rule zero: deleting or rewriting every row after the date leaves the rate, the sizes and the
  futures identical; rows after the date passed straight to `lump_arrivals` are ignored.
- Own stream: with lumps on, every future equals the same future without lumps plus whole lumps
  on some days, and the median without deposits is unchanged.
- Mutation checks: reading 60 days past the date made the direct rule-zero test fail; drawing an
  extra number from the weeks stream when lumps are on made the own-stream test fail. One
  mutation was not caught at first (the 60-day leak through `build_forecast`, because that
  function only reads rows up to the date), so I added the direct test above. Drawing the lumps
  from the weeks stream *after* the weeks are drawn is not caught; it moves no other draw, so it
  is not observable as a change to anything else.

## Step A: the 24-month set `v2L`

Method in DECISIONS.md before the code. The generator's calendar is one academic year (Oct 2025
to Sep 2026), so moving the start back alone would give a first year with no pay, rent or
tuition. The long option applies the same rules to the year before: recurring schedules start 12
months earlier (biweekly ones 52 weeks earlier), breaks, exam dips and months away repeat, and
the Winter 2025, (Summer 2025) and Fall 2025 tuition, family money and aid are added by the same
rules, with the same tuition amount. The parameters are the 12-month namesake's; earlier-year
draws come from a separate stream; the daily simulation is seeded by the long id.

Built: `build_persona("v2L-...")`, `LONG_IDS`, `LONG_HELD_OUT` (refused by
`assert_development_only` and the runner), `python -m eval.backtest --set long` (folder
`data/backtest/long`, record format `"2L"`; the metrics refuse to pool it with the 12-month set),
`python -m eval.persona_report --check --set long`. Tests in `tests/test_long_set.py`.

Checks (n = 20 development personas): **passed.**
- 12-month exports and answer keys: identical hashes for all 20 development personas.
- Every long development persona imports and reconciles.
- Tuition payers with 2 or more payments to one payee by 2026-08-31: 7 of 7 (3 to 10 each).
- Closing balance below -$20,000: none (lowest closing: v2L-ITIGHT-03, -$18,699).
- Frozen-mix acceptance checks: IFAM pass; DHOME/DRENT/TIGHT/FREE pass; ITIGHT fails the
  no-runaway half at v2L-ITIGHT-01 (lowest -$5,155) and v2L-ITIGHT-03 (lowest -$18,877, below its
  buffer at 35 of 40 origins), the same two as in the frozen mix. Recorded, nothing adjusted.

The full per-persona table is in DECISIONS.md ("Step A result").

## Step 1: c) caught when an alert is on ahead of the dip

Method and prediction in DECISIONS.md, committed before computing. `eval/metrics.py` now gives
both definitions ("opened ahead (original)", "on ahead (new)") in every alert report;
`python -m eval.thresholds_raw` re-chooses the threshold on raw chances and writes
`docs/results/thresholds_raw_2026-10-11.md`. Tests: one alert open across two dips; an episode
opened inside a shortfall does not count; an in-shortfall origin is never O; the closing look is
not on; the new caught set contains the old one (60 random scenarios); lead time. Mutation checks:
allowing O in shortfall, and counting the closing day as on, each made a test fail.

Development backtest of the current forecaster, 12-month set: 20 personas, 800 origins (726
ordinary), 52 shortfall episodes.

| Rule | Recall, opened ahead (original) | Recall, on ahead (new) | Precision | False per 30 days | Lead time new, mean (median) |
|---|---|---|---|---|---|
| 5% | 50% (26/52) | 58% (30/52) | 49% (22/45) | 0.136 | 20.1 (24) |
| 20% | 50% (26/52) | 58% (30/52) | 71% (22/31) | 0.053 | 19.7 (24) |
| 50% | 46% (24/52) | 52% (27/52) | 81% (21/26) | 0.030 | 19.4 (24) |
| **90% (chosen)** | 60% (31/52) | 62% (32/52) | 96% (24/25) | 0.006 | 15.2 (14) |
| 95% | 56% (29/52) | 56% (29/52) | 96% (22/23) | 0.006 | 14.9 (16) |
| provisional rule | 50% (26/52) | 58% (30/52) | 71% (22/31) | 0.053 | 19.7 (24) |

- Stop conditions: none holds (new set contains old everywhere; at 80%+ recall moves 0-2
  points; folds chose 2 thresholds).
- Leave one persona out: recall (new) 56% (29/52), precision 91% (21/23), 0.012 false per 30 days.
- The new definition adds caught dips at DRENT-03 (+3) and TIGHT-03 (+1) only, far fewer than
  the 15 that motivated it, because of my reading (below). See the question.
- Provisional rule precision is 71% (9 false of 31) against 77% (7 of 31) in the first backtest:
  this run has the holiday fix. Checked per persona in step 3.

## Step 2: proposals scored on the answer key

Method in DECISIONS.md before the code. Built: `lookahead/proposals.py` (a) `lump_proposals`
by sender token, b) `repeated_outflows`, first-time offers, the dated `proposal_decisions` log),
forecaster options (`ForecastOptions.lump_proposals`, `confirm_all_lumps`, `confirmed_keys`,
`outflow_placements`; all off by default, the app is unchanged), a day drawn inside a window for
confirmed lumps and the +/-5 day jitter of placements. `python -m eval.proposals_score` scores both
on the answer key. Tests in `tests/test_proposals.py` (rule zero by deleting/rewriting later rows,
decision after the date ignored, not-this-time, looks-right, tokens not names, a confirmed lump
leaves the random pool, determinism). Mutation checks: a decision read past its date, lumps read
30 days past the date, and big payments read 30 days past the date each made a test fail.

### a) Lump proposals (n = 20 development personas per set, 40 origins each)

| Set | Proposals | Precision | Findable (origin, series) | Recall | Date error, median | Size error, median |
|---|---|---|---|---|---|---|
| 12-month | 49 | **82%** (40/49) | 63 (7 series) | **56%** (35/63) | 3 days | 39% |
| 24-month | 81 | 69% (56/81) | 118 (9 series) | 38% (45/118) | 8 days | 29% |

**Gate passed** (on the 12-month set). Which archetypes it helps: ITIGHT (most proposals: 42 of 49
and 56 of 81) and IFAM; a little for DHOME-02 (family contributions) and one DRENT aid sender. No
proposal at SAL, SEM, TIGHT or FREE. Unexpected: recall is lower on the long set, not higher.

### b) Repeated large outflows (gate on the 24-month set)

| Set | Placements | Precision | Recall, payee with 2+ payments | First-time cases |
|---|---|---|---|---|
| 24-month | 57 (all tuition) | 68% (39/57) | 46% (37/81) | 0 |
| 12-month | 9 (all tuition) | 67% (6/9) | 26% (6/23) | 28 |

**Gate failed: line b) stops** (recall below 50% and precision below your 70%). Arms 4 and 5 are
not run. The reason is the next-date rule, not detection: summer-term personas (three terms a
year, ~120-day gaps) get 39 of 44 placements right; the two-term personas (Jan and Sep) get 0 of
13, because the median of alternating ~240/~125-day gaps lands between terms and "a year after
the latest" is never the next term. I did not try another rule (stopping rule). A natural next
idea for you to decide: "a year after the payment of the same term" (the latest payment 330 to
400 days before the expected date), declared and tested as a new method.

## Step 3: the arms (stopped at the sanity check)

Method in DECISIONS.md before computing. Built: `eval/arms.py` (arms 0 to 3; 4 and 5 refused
because b) stopped), `run_origin(..., options=...)` in `eval/backtest.py`, tests in
`tests/test_arms.py`.

Sanity check, 12-month set, 20 development personas, 800 origins (726 ordinary), 52 shortfall
episodes. The recorded columns were made before the holiday fix, so I reran arms 0 and 1 with the
fix reverted in a scratch copy (never committed):

| | Recorded "current raw" | Pre-fix arm 0 | Recorded "prototype raw" | Pre-fix arm 1 |
|---|---|---|---|---|
| Error 7 / 14 / 30 days | $667 / $1,204 / $1,901 | same | $666 / $1,199 / $1,881 | same |
| Skill | 0.32 / 0.10 / 0.01 | same | 0.32 / 0.11 / 0.02 | same |
| Coverage 7 / 14 / 30 / all | 57 / 51 / 41 / 52% | same | 62 / 56 / 49 / 57% | same |
| Width 7 / 14 / 30 | $394 / $591 / $849 | same | $542 / $1,852 / $2,931 | same |
| Path-level coverage | 8% | same | 10% | same |
| Lowest-balance bias | +$724 | same | +$749 | same |
| Brier | 0.059 | same | 0.061 | same |
| Recall / precision / false per 30 days | 50% (26/52) / 77% / 0.041 | same | 40% (21/52) / 74% / 0.041 | same |

- (i) **passed exactly**: the pipeline is the one that made the recorded numbers, and the rebuilt
  random lump arrivals draw exactly like the lost prototype.
- (ii) **failed**: per persona, the fixed code differs from the pre-fix code at 16 of 20 personas
  (103 of 800 origins), not only at SAL-01 and TIGHT-01.
- **Why:** in all 103 records the only change in the placed payments is 1 or 2 fewer items (106
  in all), each a second copy of a money-in payment on the same day for the same series. The fix
  removes any later due date that shifting earlier moves onto the first placed day, which happens
  with weekends too and at every income series, not only at the two holiday paycheques the
  diagnosis saw. No bug.
- By your stopping rule I did not change the check and **did not read or report the arms**. The
  check itself printed the fixed code's pooled arm 0 and arm 1 numbers (in DECISIONS.md, not
  read as a comparison); arms 2 and 3 were never summarized.

## Step 4: report

`docs/results/proposals_2026-10-11.md` has every table with n, both alert definitions side by
side, the gates and what each did, the stopped lines and why, and what was unexpected. No
recommendation to merge. Which archetypes a) and b) help (your addition 2): a) helps the
international students (ITIGHT most, IFAM, and DHOME-02's family contributions on the long set)
and nothing for SAL, SEM, TIGHT, FREE; b) would help three-term students only and failed its gate.

## Decisions I made on my own (all recorded in DECISIONS.md before computing)

- Step 0: for each forecast day one uniform draw decides a lump, and only then a second draw
  picks its size (Appendix A did not fix the order; this order reproduced the prototype exactly).
- Step A: the generator's one-year calendar is repeated one year earlier (schedules start 12
  months earlier, biweekly ones 52 weeks earlier; breaks, exams, months away, tuition, family
  money and aid by the same rules, same tuition amount); parameters from the 12-month namesake,
  earlier-year draws from a separate stream; "ends below -$20,000" read as the closing balance.
- Step 1: an alert episode that opened while already below the buffer never counts (question 1);
  the closing look is not "on"; the threshold is chosen on the 12-month set only.
- Step 2: an overdue lump proposal (expected date before the horizon) is not proposed; a
  confirmed lump's day is drawn uniformly in its window; a confirmed or declined sender leaves the
  random-arrival pool; big outflows are read over 730 days with the big-row cutoff at the origin;
  "right" for a) means the next row of one of the evidence's planted income series falls inside
  the window, and findable pairs need that next row inside the horizon; "right" for b) needs a
  tuition payment within 10 days and 20 percent; the synthetic evaluation uses a made-up
  pseudonym key; `sender_of` moved from `eval/lump_senders.py` into `lookahead/proposals.py`
  (shared, same rule).
- Step 3: the sanity check was run as (i) pooled numbers with the fix reverted and (ii) a
  per-persona comparison; (ii) failed and the line stopped.
- Docs-only commits (the method entries) were made right after a full suite run on identical
  code, not after a fresh one.

## What I could not do

- Read or report the arms (stopped), so there is no forecast-quality result for a) yet, only its
  proposal scores.
- Arms 4 and 5, and the a)/b) 2x2 (b stopped).
- Anything on the real account (by design). The records of the synthetic runs live in
  `data/backtest/` (not committed); this container is temporary, so the commands below rebuild
  them.

## Commands for you (PowerShell, in the project folder, venv active)

Get the branch and check it:

```powershell
git fetch origin
git switch proposals
pip install -r requirements.txt
pytest -m "not slow"
```

Your real account, counts and summary numbers only (development numbers: the design was made
while looking at your account):

```powershell
# 1. Big deposits by sender: with and without a sender name, distinct senders, senders with 2+ and 3+
python -m eval.lump_senders

# 2. a) on your account: proposals at each origin, how many were followed by a deposit from the
#    same sender inside the window, day and size misses (needs PSEUDONYM_KEY in .env)
python -m eval.proposals_real

# 3. c) on your account: the backtest summary now prints both definitions side by side.
#    Re-run the backtest first if your saved one predates the holiday fix (10 Oct):
python -m eval.backtest_real --out data/private/backtest/arms/arm0/full
python -m eval.metrics --no-synthetic --real data/private/backtest/arms/arm0
```

Only if you accept the step 3 explanation (question 2), the arms on your account, each in its
own folder (arm 3 needs the answer key, so it cannot run on real data):

```powershell
python -m eval.backtest_real --arm 1
python -m eval.metrics --no-synthetic --real data/private/backtest/arms/arm1
python -m eval.backtest_real --arm 2
python -m eval.metrics --no-synthetic --real data/private/backtest/arms/arm2
```

Only if you accept the step 3 explanation, the synthetic arms and their report (about an hour on
4 cores; nothing on your data):

```powershell
python -m eval.backtest                         # arm 0, 12-month set (data/backtest/full)
python -m eval.backtest --set long              # arm 0, 24-month set (data/backtest/long/full)
python -m eval.arms --arms 1 2 3 --set both --jobs 4
python -m eval.arms --report                    # writes docs/results/arms_2026-10-11.md
```

To reproduce the other results of this run (synthetic only):

```powershell
python -m eval.persona_report --check --set long   # step A table and checks
python -m eval.thresholds_raw                      # step 1 (needs python -m eval.backtest first)
python -m eval.proposals_score                     # step 2, both sets, about 5 minutes
```

## Questions for Yousef

1. **c), which alert episodes may count as "on".** I read the plan's test "an episode that opened
   inside a shortfall does not count" as: an alert episode that opened at an origin already below
   the buffer never counts, even when it is still on at a later ordinary origin. Under the other
   reading (it counts from the first ordinary origin at which it is still on), 18 more dips would
   be caught at 5% and 9 more at 90% (TIGHT-03, DRENT-03, TIGHT-02, ITIGHT-03); those are exactly
   the hovering dips that motivated the change. I took the cautious reading and computed the
   other only as a count, after seeing the table. If you want the other reading, it is a one-line
   change in `alert_metrics` (pass every span, not only the ordinary ones, to `caught_on_ahead`),
   but precision and false alerts would then need a matching rule for those episodes.
2. **Step 3, the sanity check.** The per-persona part failed only because the holiday fix
   touches more personas than expected (same-day duplicate income items, 16 of 20 personas); the
   pooled numbers reproduce both recorded columns exactly. If you accept this, the arms can be
   run and reported with the commands above (about an hour on 4 cores); nothing else needs to
   change. I took the cautious reading and stopped.
3. **b), next step.** The default next-date rule cannot place the next term for a payee paid
   twice a year (January and September). A rule "a year after the payment of the same term"
   would be a new method, to declare before testing; I did not try it.
