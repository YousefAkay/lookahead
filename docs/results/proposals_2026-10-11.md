# Proposals run, 2026-10-11 (branch `proposals`): results

Plan: `docs/plans/overnight_2026-10-11.md`. Methods were written in DECISIONS.md and committed
before the code that computes them ran; results are in separate, later entries. Synthetic
development personas only (20 per set); no held-out persona, no final test, nothing read from
`data/private/`. Two additions from Yousef were applied: b) also needs 70 percent precision, and
a) is judged as a feature for the student personas with large family transfers and tuition
(IFAM, ITIGHT), with nothing claimed about the real account. This is a report, not a
recommendation to merge.

## What happened, line by line

| Line | Gate or check | Result | What it did |
|---|---|---|---|
| 0 Random lump arrivals as a switch | switch off = identical output; rule zero; own stream | **passed** | built, off by default |
| A 24-month set `v2L` | 12-month set unchanged; all reconcile; b) stop rules | **passed** | new set of results; ITIGHT-01 and -03 run past -$3,000 (limitation) |
| 1 c) "caught" when an alert is on ahead | four stop conditions | **passed** | threshold on raw chances: 90% (development result) |
| 2a Lump proposals | precision >= 70% and recall >= 50% on one set | **passed** (12-month set) | arms 2 and 3 allowed |
| 2b Repeated large outflows | recall >= 50% and precision >= 70% on the 24-month set | **failed** (46%, 68%) | **line b) stopped**: arms 4 and 5 not run |
| 3 Arms | sanity check against the recorded columns | **failed in its per-persona part** (cause found, no bug) | **line 3 stopped**: arms not read |

## 1. c) Both definitions of "caught" (12-month set)

20 development personas, 800 origins (726 ordinary), 52 shortfall episodes; development backtest
of the current forecaster (500 futures, seed 42, weekday method). Full curve:
`docs/results/thresholds_raw_2026-10-11.md`.

| Rule | Recall, opened ahead (original) | Recall, on ahead (new) | Precision | False alerts per 30 days | Lead time new, mean (median) days |
|---|---|---|---|---|---|
| 5% | 50% (26/52) | 58% (30/52) | 49% (22/45) | 0.136 | 20.1 (24) |
| 20% | 50% (26/52) | 58% (30/52) | 71% (22/31) | 0.053 | 19.7 (24) |
| 50% | 46% (24/52) | 52% (27/52) | 81% (21/26) | 0.030 | 19.4 (24) |
| 65% | 60% (31/52) | 62% (32/52) | 93% (25/27) | 0.012 | 17.2 (16) |
| **90% (chosen)** | 60% (31/52) | 62% (32/52) | 96% (24/25) | 0.006 | 15.2 (14) |
| 95% | 56% (29/52) | 56% (29/52) | 96% (22/23) | 0.006 | 14.9 (16) |
| provisional rule (20% or median low below the buffer) | 50% (26/52) | 58% (30/52) | 71% (22/31) | 0.053 | 19.7 (24) |

- Leave one persona out (folds chose 65% or 90%): recall (new) 56% (29/52), precision 91% (21/23),
  0.012 false alerts per 30 days.
- Where the new definition adds caught dips: DRENT-03 (+3) and TIGHT-03 (+1), at low and middle
  thresholds; none at 95%.
- Prediction (recall up at low thresholds, mostly hovering personas; little change at high
  thresholds; precision and counts unchanged): held, except that TIGHT-02 gains nothing and the
  gain is much smaller than the 15 dips that motivated the change. Cause: under my reading, an
  alert episode that opened while already below the buffer never counts. Under the other reading
  18 more dips would count at 5% and 9 more at 90% (question 1 for Yousef).
- Nothing changes in the app.

## 2. a) Lump proposals (scored on the answer key)

`python -m eval.proposals_score`; full tables in `docs/results/proposals_scores_2026-10-11.md`.
Counts are of (origin, proposal) and (origin, series) pairs; 40 weekly origins per persona.

| Set | Personas | Proposals | Precision | Findable (origin, series) | Recall | Date error, median | Size error, median | False proposals per persona-year |
|---|---|---|---|---|---|---|---|---|
| 12-month | 20 | 49 | **82%** (40/49) | 63 (7 series) | **56%** (35/63) | 3 days | 39% | 0.2 |
| 24-month | 20 | 81 | 69% (56/81) | 118 (9 series) | 38% (45/118) | 8 days | 29% | 0.7 |

By archetype (precision; recall):

| Archetype | 12-month | 24-month |
|---|---|---|
| ITIGHT (3 personas) | 79% (33/42); 55% (31/56) | 70% (39/56); 42% (33/78) |
| IFAM (3) | 100% (6/6); 50% (3/6) | 69% (9/13); 30% (6/20) |
| DHOME (3) | no proposal | 100% (8/8); 55% (6/11) |
| DRENT (3) | 100% (1/1); 100% (1/1) | 0% (0/4); 0% (0/9) |
| SAL, SEM, TIGHT, FREE (8) | no proposal | no proposal |

**Which archetypes a) helps:** the international students: ITIGHT (the stretched families'
transfers, most of the proposals) and IFAM (the tuition deposits), and DHOME-02's family
contributions on the long set. It is wrong for a sender who sends both a small regular amount and
big ones (the DRENT aid sender on the long set, v2L-IFAM-01). It does nothing for the salaried,
tight-budget and freelancer personas (no lumps by sender; FREE's deposits carry no sender name).
Unexpected: recall is lower on the 24-month set than on the 12-month set (predicted the other
way); not investigated beyond the per-persona counts.

## 3. b) Repeated large outflows (scored on the answer key)

| Set | Placements (all tuition) | Precision | Recall, payee with 2+ payments | First-time cases |
|---|---|---|---|---|
| 24-month (gate) | 57 | 68% (39/57) | 46% (37/81) | 0 |
| 12-month | 9 | 67% (6/9) | 26% (6/23) | 28 |

Per persona on the 24-month set: the four summer-term personas (three terms a year, about 120
days apart: v2L-IFAM-02, -ITIGHT-03, -DHOME-02, -DRENT-01) get 39 of 44 placements right; the
three two-term personas (January and September: v2L-IFAM-01, -ITIGHT-01, -DRENT-03) get 0 of 13.
The median of their alternating ~240- and ~125-day gaps falls between terms, and "a year after
the latest" is never the next term when there is more than one term a year (that rule alone
placed nothing). **Which archetypes b) would help:** students with three terms a year, in every
student archetype that has them; not two-term students, and no one else. **Gate failed; line b)
stopped**; nothing was tried to rescue it.

## 4. The arms (stopped)

The sanity check reran arms 0 and 1 on the 12-month set with the holiday fix reverted (scratch
copy, never committed). Pooled, both reproduce the recorded columns of 2026-10-10 exactly, to
every printed digit (errors, skill, coverage, width, path coverage, lowest-balance bias, Brier,
recall, precision, false alerts): the pipeline is the same, and the rebuilt random lump arrivals
draw exactly like the lost prototype. Per persona, the fix changes 103 of 800 records at 16 of
20 personas, not only SAL-01 and TIGHT-01 as the check required. In every one of them the only
change is 1 or 2 fewer placed items (106 in all), each a same-day duplicate of a money-in
payment (an income date shifted earlier onto the first placed day, by weekends as well as
holidays). No bug; the plan's estimate of where the fix shows was too narrow. By the stopping
rule I did not change the check, and I did not read or report the arms. Arms 2 and 3 were never
summarized.

## What is unexpected

- c) adds far fewer caught dips than the 15 that motivated it (reading of "on", question 1).
- a) recall is lower on the 24-month set than on the 12-month set.
- b)'s default next-date rule cannot place the next term of a two-term student at all.
- The holiday fix touches 16 of 20 personas, not 2.
- Nothing above 95 percent except groups of 1 to 8 proposals (IFAM 6 of 6, DHOME 8 of 8, DRENT 1
  of 1); no sign of leakage (rule-zero tests and mutation checks for every new piece).

## Not done

- Arms 0 to 3 compared (stopped), arms 4 and 5 (b stopped).
- The 2x2 of a) and b) alone and together, and where pass-through pairing goes wrong (needs
  b) placed).
- Buttons or pages for "Looks right" / "Not this month" (out of scope).
- Anything about the real account: only the commands are written.
