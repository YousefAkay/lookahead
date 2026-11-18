# Categorizer v2, adopt rule (DRAFT, 2026-11-18)

**DRAFT. Not yet in force.** Every number below is a proposal for me and my advisor to decide. Nothing has been scored.

To put it in force: I settle the numbers, save the final text as `docs/specs/2026-11-18-categorizer-v2-adopt-rule.md` (without the draft marks), and commit it before I label the blind set. `eval/categorizer_v2.py` refuses to score until that file is committed and unchanged since its commit.

## The test set
- A fresh blind set: about 150 rows of new merchants (first seen on or after 2026-10-01, no label of mine in training), picked with `python -m eval.categorizer_v2 candidates`, labelled by me after v2 is built and before any variant is scored.
- No row from the old holdout (Test A, Test B). The harness refuses them.
- Known-merchant rows, for the guard below: rows of merchants that do have training labels, from the same period. I label them in the same file.

## Primary metric
- Accuracy on the new-merchant rows of the blind set, always reported with n.

## What a variant must show to be adopted
Variants: (a) today's model, (b) (a) + cleaner + directory, (c) (b) + RAG (opt-in).
- **(b) over (a):** a gain of at least **[proposed: 3 points]** on new-merchant rows, and the 2.5th percentile of the paired bootstrap gain above 0 (2,000 resamples of merchants, seed 0, as in the harness).
- **(c) over (b):** a gain of at least **[proposed: 3 points]** on new-merchant rows, same bootstrap condition. RAG sends names out, so it has to earn that on its own, not just on top of (a).
- **Guard:** known-merchant accuracy may not drop by more than **[proposed: 1 point]** against (a).

## Checks
- **Leakage:** if any variant scores above 95% on either part, I stop and look for leakage (a blind row in training, a merchant already labelled, a directory entry written after seeing the rows) before reading anything else.
- **Every variant counted:** every run of every variant is in the ledger, including runs I would rather forget.
- **Scored once:** the blind set is scored once. A second scoring needs `--again`, is recorded, and does not replace the first result.
- **No tuning on the blind set:** the cleaner, the directory, the similarity threshold (0.60) and the RAG prompt are frozen before scoring.

## If nothing passes
v2 stays off by default, as it is now. I write down the result with n in DECISIONS either way.

## Open for me and my advisor
1. The gain needed for (b) and for (c) (3 points each is a placeholder).
2. Whether the bootstrap bound is the 2.5th percentile (two-sided 95%) or the 5th (one-sided).
3. The known-merchant guard (1 point) and how many known-merchant rows the guard needs.
4. Whether the directory's similarity matching is part of (b) or tested on its own.
