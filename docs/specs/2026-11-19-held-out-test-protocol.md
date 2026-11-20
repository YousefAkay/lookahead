# Held-out test of the forecaster, protocol (2026-11-19)

In force from its commit, approved by Yousef on 2026-11-19. Nothing held out may be opened before that commit.

## What is tested

The final default forecaster (DECISIONS 2026-11-19), as on main at one commit: 40 weekly origins per persona or account, 500 futures, seed 42, the persona's or account's own past only at each origin. The alert threshold is `lookahead.alerts.PROBABILITY_THRESHOLD`, and the harness refuses to run unless it equals 0.35. Nothing is tuned, refitted or chosen on held-out data.

## The pools

- **Synthetic, 12-month set:** the 10 held-out personas (`lookahead/generator/archetypes.py`).
- **Synthetic, 24-month set:** the 10 held-out personas of that set.
- **Berka, primary:** the 200 held-out accounts of the primary pool (`docs/berka_split.json`).
- **Berka, sensitivity (secondary result, reported apart):** the 200 held-out accounts of the sensitivity pool. It decides nothing and is never pooled with the primary result.

The two synthetic sets are never pooled. My own account is not part of this test.

## The only route to held-out data

The harness `eval/held_out.py` is the only code that may read, forecast or score a held-out persona or account. In the harness's own commit, before the dry run:
- `eval.backtest --held-out` and `eval.metrics --final-test` are removed;
- `eval.berka_split.held_out_accounts()` and every held-out list refuse unless called by the harness;
- nothing else prints any held-out outcome.

## The numbers reported

All records come from the same per-origin code as development (`eval/berka_scores.account_records` for Berka; `eval/baselines.scored_record` for synthetic personas).

**Scored origins.** An origin is scored only if both hold:
- the balance was not already below the line at the origin (the buffer for synthetic personas; "fell below one month's outflow" for Berka);
- the random walk is defined: at least 20 windows of 30 days in the 365 days before the origin.

On development, every unscored origin was already below the line: Berka primary 865 of 8,000, 12-month 74 of 800, 24-month 83 of 800. CRPS, coverage, Brier and bias use scored origins only (the 2026-11-19 method, not experiment 3's "CRPS uses every origin").

**Pooled over scored origins** (as `eval.berka_scores.summary_core`):
- CRPS of the forecast, "balance stays" and the random walk: the mean over origins of the mean over the 30 days, and the mean on day 30. Skill = 1 − the forecast's mean CRPS / the baseline's mean CRPS.
- 80 percent coverage: the share of days with p10 ≤ realized ≤ p90, on day 30 and over all 30 days.
- Brier of the dip chance; "balance stays" predicts 0; skill as for CRPS.
- Lowest-balance bias: the mean, and the mean absolute, of the median lowest future minus the realized lowest.

**Averaged per unit** (a unit is an archetype for the synthetic sets and an account for Berka):
- Each pooled quantity above is computed within each unit, then averaged over units with equal weight.
- A skill is included only for units whose baseline mean is above 0.
- A Brier skill is included only for units with at least 5 dips (as in the 2026-10-17 baselines report).
- The number of units included is shown beside every per-unit average.

**Dips caught.**
- The call is `eval.metrics.alert_metrics(records, PROBABILITY_THRESHOLD, median_rule=False)`, on every origin with a defined walk, those already below the line included. At 0.35 the median clause adds no alert.
- It is reported two ways:
  - `caught_on`: an alert was on within the 30 days before the dip.
  - `caught`: an alert opened at an ordinary origin within the 30 days before the dip.
  - Each comes with `shortfall_episodes`, `precision` and `false_per_30_days`.
- **Per unit:** recall is averaged over units with at least 1 episode, precision over units with at least 1 alert episode, and false alerts per 30 days over all units.
- **The random walk** is scored the same way at the same fixed threshold, never at a threshold of its own.

Every number has its n: origins, scored origins, units, units included, and dips or episodes. The Berka zero-estimate origins are reported apart, as on development. Not reported: the 50 percent dip-line sensitivity and the result without the 16 mostly-cash accounts (both pre-declared for experiments, not for this test). Synthetic amounts are dollars; Berka amounts are scaled units (1 unit = 20 koruna), so only skills, coverage, Brier and dips compare across datasets.

## One invocation: the dry run, then the held-out run

One command runs everything on one code commit, in this order:

1. **The dry run.** The same code runs on the development pools: both synthetic sets and Berka primary and sensitivity development. It must reproduce exactly, to the printed precision, these rows of `docs/results/scores_development_2026-11-19.md` for the three columns it has:
   - origins;
   - scored origins;
   - dips;
   - every CRPS and CRPS skill;
   - both coverages;
   - every Brier and Brier skill;
   - the bias.

   The dips-caught rows are excluded, because that report chose them in-sample. If any row differs, the command stops before any held-out data is opened, and I tell Yousef.
2. **The held-out run,** in the same process on the same commit, all four pools.

The dry run's full output (both aggregations, dips at the fixed threshold counted both ways, the sensitivity development pool included) is the development reference. It is committed together with the held-out result, and each held-out number is shown next to it.

## Once

- **The ledger** is the tracked file `docs/results/held_out_ledger.jsonl`. Every attempt is written to it: refused, void, crashed and completed. Each entry holds the code commit, this protocol's commit and sha256, and the threshold value. The command refuses unless that file is unchanged since its last commit, so every attempt is committed before the next one. It also refuses if the file's git history holds a completed entry, so moving or deleting the file cannot reset it.
- **Void run.** After all four held-out pools are computed and before any number is printed, the command checks:
  - 10 personas × 40 origins per synthetic set, and 200 accounts × 40 origins per Berka pool;
  - 500 futures at every origin;
  - no NaN in any score;
  - the forecaster's logic version is 3.

  A failure in any pool voids the whole attempt: it is recorded as void, and nothing is shown. A fix may change only `eval/held_out.py`, never `lookahead/` or the scoring code (`eval/berka_scores.py`, `eval/baselines.py`, `eval/metrics.py`, `eval/backtest.py`, `eval/berka.py`). It is committed and disclosed in DECISIONS, and the whole command (dry run first) runs again.
- **Recorded before shown.** All results are written to the ledger before any number is printed. A crash before that point showed nothing, and the command may run again. Once a number has been shown, the held-out data is used: there is no second run and no `--again`.
- **Refused if:**
  - this file is not committed, still says DRAFT, or has changed since its commit;
  - tracked code has uncommitted changes;
  - `PROBABILITY_THRESHOLD` is not 0.35;
  - the ledger is uncommitted;
  - a completed run is in the ledger's history.

## After

- Nothing is tuned, refitted or rechosen after the run, whatever it shows. Bad results are reported as they are.
- DECISIONS gets the result with n. The README shows development next to held-out, synthetic and Berka only, and labels which meaning of "caught" each dips line uses.
- The held-out personas and accounts stay out of every later experiment.

## Disclosed

- **Development numbers known before this protocol:** `docs/results/baselines_2026-11-19.md`, `docs/results/scores_development_2026-11-19.md` and `docs/results/thresholds_raw_2026-11-19.md`.
  - On Berka primary development (7,135 scored origins, 200 accounts), the random walk beats the forecast at day 30 (CRPS skill -20.5 percent) and on the dip chance (Brier skill -5.9 percent).
  - The 80 percent band covers 57.0, 58.9 and 63.1 percent of days on Berka primary and the 12-month and 24-month sets (7,135, 726 and 717 scored origins).
- **The alert threshold** (0.35) was chosen on development data on 2026-11-19, before this protocol.
- **The held-out data:**
  - The held-out personas' rows come from the same generator as development, with the 2026-11-17 brand renames. Their definitions were written before the split, but no transaction or forecast of theirs has been made.
  - No records or results file names a held-out Berka account, primary or sensitivity (checked 2026-11-19; only the split file lists them).
