# Categorizer v2, adopt rule (2026-11-18)

In force from its commit. My decisions, settled with my advisor's input on 2026-11-18. `eval/categorizer_v2.py` refuses to draw, label or score until this file is committed and unchanged since its commit.

## The blind set
Two groups, drawn together at random with a fixed seed (0) by `python -m eval.categorizer_v2 candidates`, and frozen before I label anything. The draw is made once, only after this rule is committed: it is written to the ledger, and a second draw is refused. Labelling also waits for the committed rule. Both groups are labelled in blind mode in the same session, shuffled together, so the order does not tell which group a row is in. No row of the old holdout (Test A, Test B) is in either group, and no row that already has a label of mine. Rows I marked "Skip / don't know" in the label review are left out too: they are rows I already found hard.
- **Group A, new merchants:** up to 150 rows from 2026-10-01 onward, at most 3 rows per merchant, from merchants with no training labels and first seen on or after 2026-10-01. The cap of 3 is applied first (3 rows drawn at random per merchant), then up to 150 rows are drawn from what is left.
- **Group K, known merchants:** 60 rows from 2026-10-01 onward whose merchant has training labels but the row itself is unlabelled (all of them if fewer than 60 qualify). No cap per merchant: K looks like real use, where frequent merchants are most of the rows.
- **Minimum for A:** if fewer than 100 rows qualify for group A (after the cap of 3), nothing is drawn and I wait for more data. At scoring, if fewer than 100 group A rows are labelled and scorable, the harness refuses to score and says to wait for more data.
- **Minimum for K:** at scoring, if fewer than 40 group K rows are labelled and scorable, the harness refuses to score: the guard would test too little.
- If a group A merchant has training labels by the time of scoring, the harness refuses to score.

## Primary metric
Accuracy on group A, always reported with n.

## Variants
- (a) today's model.
- (b) (a) + the merchant-text cleaner + the chain directory, exact and alias matches only. The directory's similarity matching is not part of (b) and is not tested in this run.
- (c) (b) + RAG (opt-in).

## What a variant must show to be adopted
- **(b) against (a):** on group A, a gain of at least 3 points, AND the 2.5th percentile of the merchant-level paired bootstrap gain above 0 (2,000 draws, seed 0, whole merchants per draw: all rows of a merchant move together).
- **(c) against (b), not against (a):** the same two conditions, with (b) as the base. RAG sends names out, so it has to earn its place on top of (b). (c) is adopted only if (b) passes too: (c) contains (b).
- **Guard on group K:** (b) and (c) may each get at most 1 more group K row wrong than (a). Failing the guard means not adopted, whatever the gain.
- The harness computes all of this in code (`compare` in `eval/categorizer_v2.py`), prints each comparison with n and "passes: yes/no", and writes it to the ledger.
- **What this test can detect** (made-up numbers, `python -m eval.categorizer_v2_power`, 150 rows in 75 merchants, 200 trials): if (b) breaks no row that (a) got right, a true 5-point gain passes about 83% of the time. If it also breaks about 5% of the rows (a) got right, it needs about 8 to 10 points (55% / 76%). Small real gains may still fail; that is accepted.

## Checks
- **Leakage:** if any variant scores above 95% on either group, I stop and look for leakage (a blind row in training, a merchant already labelled, a directory entry written after seeing the rows) before reading anything else.
- **Every variant counted:** every run of every variant is in the ledger, refused runs included.
- **Scored once:** the blind set is scored once. A second scoring needs `--again`, is recorded, and does not replace the first result.
- **No tuning on the blind set:** the cleaner, the directory, the similarity threshold (0.60) and the RAG prompt are frozen before scoring.

## If nothing passes
v2 stays off by default, as it is now. Either way, the result goes in DECISIONS with n.
