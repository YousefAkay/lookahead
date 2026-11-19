# Categorizer RAG, adopt rule version 4 (2026-11-19)

In force from its commit. It replaces version 3 (commit 1654555), which was never scored: its one run was void. It tests the same thing: does RAG improve today's categorizer?

**Background.**
- **Version 2** (commit 8254126) could not answer this: in its one public scoring (2026-11-18), all 332 of RAG's answers were wrapped in a code fence and refused, and 7 were also cut off at 100 tokens. Both were fixed after scoring. The version 2 result stands. This rule does not rescore it, and does not change its verdict on the cleaner and directory.
- **Version 3's one run** (2026-11-19) was void by its own rule, and stopped before any accuracy was computed. RAG was asked about 323 rows and gave a category for 257. Of the 302 answers, 244 were usable ("ok") and 58 were refused (more than 5%):
  - 45 "no citation": every one a category with an empty citation list.
  - 13 "not JSON": every one a fenced `{"category": null, ...}` followed by an explanation. One of these had two JSON objects ("Travel", then null).
  - The largest answer was 137 tokens, and 0 hit the 300-token limit.
- **What I saw before writing this rule:** those counts, and 10 raw replies (5 per refused reason). I saw no labels and no accuracy. I did not check whether any answer was right, so the two changes below were chosen without knowing their effect on accuracy. They were made after the void run.

The only changes from version 3 are the two parser changes in "What counts as an answer". Everything else is exactly as in version 3:
- the set, the split, the sample and the variants;
- the prompt, the retrieval and the model;
- the test, the void rule (5% per answer), scored once, and the veto.

## Part 1: the deciding run, on public data only

**The set.** DoDataThings "US Bank Transaction Categories v2", exactly as pinned in version 2: same revision, file and sha256, same generator name list and sha256, same merchant key (`MerchantKeys`), same category table (Transfer, Income, Fees, Insurance and Travel dropped; Food delivery and Restaurants & cafés the same when scoring). 48,000 rows kept, from 1,072 merchants. Nothing of mine is used.

**The split, as in version 3.**
- The 356 merchants of version 2's deciding sample are never test merchants here. They go to the train part.
- The other 716 merchants, sorted by key, are drawn with `numpy.random.default_rng(1).choice(..., size=286, replace=False)`: those 286 (40%) are the test part, with all their rows (11,007 rows). Every other merchant is the train part: 786 merchants, 36,993 rows.
- **Sample:** at most 3 rows per test merchant, then 600 of those, both drawn with one new `numpy.random.default_rng(1)` and the same row order as version 2 (sorted by description, then key). That gives 600 rows from 275 merchants. None of their cleaned names occur in version 2's deciding sample.
- **Disclosed:** I computed these counts on 2026-11-19, before writing version 3, to choose the split: rows and merchants of the test part and the sample, and the shared cleaned names (0). Version 3's void run computed (a)'s predictions and RAG's answers on this sample, but no accuracy, and I saw no label.

**Variants.**
- **(a)** today's design, logistic regression plus the lexicon, with the cleaner and the directory off. It is trained on every row of the new train part, and its settings and review threshold are tuned there by grouped cross-validation, as in version 2.
- **(a)+RAG:** the same trained (a). Each row (a) flags as unsure goes to RAG.
  - The examples are the new train part's merchants with their mapped categories, plus the closest chain-directory entries, as in the app.
  - RAG looks up the cleaned description, as in the app. The cleaner is used only for that lookup, never for the model.

**Frozen:** the RAG prompt and the retrieval (8 examples, 3 directory entries), the model `claude-haiku-4-5-20251001`, the merchant key, the lexicon and the cleaner. **Changes to RAG since version 2,** all to how answers are read, never to the prompt:
- one code fence around the whole answer is removed (after version 2);
- the answer limit is 300 tokens instead of 100 (after version 2);
- the two parser changes below (after version 3's void run).

**What counts as an answer** (new in version 4; the app's RAG uses the same parser):
1. **Text around the answer:** a reply with exactly one JSON object and text around it, inside or outside a code fence, is read from that object, and the text is ignored. A reply with more than one JSON object counts as "unsure" only if at least one of them has the answer shape (exactly "category" and "cited"); if none does, it is refused ("several objects, none an answer"). A reply with no JSON object stays refused ("not JSON"). A `{...}` that is not valid JSON, or a `{` that is never closed, is not a JSON object.
2. **No citation:** a category with an empty citation list counts as "unsure", and the row keeps (a)'s guess. RAG must cite its evidence. Answering from Haiku's own knowledge is the separate opt-in LLM fallback, not RAG.

Every other check is unchanged and still refuses: an object that is not exactly "category" and "cited", citations that are not a list of ids, a category not written exactly as in the list, and an unknown or repeated citation. An "unsure" answer is usable for the void rule. A saved reply is always read again with this parser, never with the verdict it got when it was saved.

**What (a)+RAG must show against (a),** on the deciding sample:
- a gain of at least 3 points, AND
- the 2.5th percentile of the merchant-level paired bootstrap gain above 0 (2,000 draws, seed 0, whole merchants per draw).

**What this test can detect:**
- The setup is made-up data on this split's own layout: 600 rows in 275 merchants, (a) right on 75% of rows, 200 trials.
- If RAG breaks nothing, a true gain of about 4 points is likely to pass (87%), and 5 points almost surely (99%).
- If it also breaks about 5% of the rows (a) got right, it needs about 5 to 8 points (78% / 98%).
- A true gain of 3 points passes only about half the time; that is accepted.
- **My prediction, written before scoring:** (a)+RAG about 80–88% against (a) on this split.
- RAG can only change rows (a) flags. On version 2's sample, (a) flagged 297 of 600 rows and got 54.2% of them right, so most of a gain would have to come from there.
- In version 3's void run, 45 of 302 answers had no citation. Under this rule they count as "unsure", so RAG can change fewer rows.

## The run

- **One run, `score --rag --send`.** A run without both flags is refused and logged. The RAG cache is a new, empty file of its own under `data/private/`: every answer is asked again.
- **Void run:** the run is void, and stops before printing any accuracy, if either:
  - RAG was asked about rows and gave a category for none of them, or
  - more than 5% of its answers were refused as not usable: not JSON, several objects none of them an answer, not the expected object, citations not a list of ids, not one of the categories, or an unknown or repeated citation. "Unsure" counts as usable, including the two kinds above.
  - A void run is recorded in the ledger with its counts by reason, and does not count as the scoring.
  - A fix after a void run is committed and disclosed in DECISIONS before the next run, and that run starts with an empty cache: every answer is asked again.
- **Scored once:** the first valid run under this exact rule text decides. A second scoring needs `--again`, is recorded, and decides nothing.
- **Leakage:** if either variant scores above 95%, I stop and look for leakage before reading anything else.
- **Every run counted:** every run is in the ledger, refused and void runs included.

## Also disclosed

- 263 of the 286 test merchants were train merchants in version 2. (a) and (b) were trained on them in version 2's scoring and in the diagnostic of 2026-11-19, but nothing was ever measured on them.
- My dry run of (a) before version 2 (68.8%, n = 475 rows, 201 merchants) measured on held-back train merchants of an earlier grouping. Some of them may be test merchants here; I did not count them. Nothing was tuned from it.
- In the 2026-11-19 diagnostic, I looked at (a)'s and (b)'s row-by-row results on version 2's sample. All of those merchants are in this rule's train part.

## Part 2: the veto, on my real rows

If (a)+RAG passes Part 1, it still needs the veto before it can be offered in the app. The veto is the same as version 2's Part 2, with one variant:
- **The variant:** my app's model as deployed, plus RAG with my own labelled merchants as examples (my blind rows never among them), against the deployed model alone.
- **Unchanged from version 2:**
  - the draw: groups A and K, seed 0, drawn once, only after Part 1 is scored;
  - the minimums;
  - the exclusions and the blind labelling;
  - the thresholds: on group A at most 2 rows net worse, on group K at most 1 row net worse.
- Version 2's draw was never used. This is that one draw.

The same void rule applies to the veto run.

## Adoption

RAG is adopted (offered in the app, still off by default) only if it passes Part 1 AND is not vetoed in Part 2. If it fails, it stays off, and the result goes in DECISIONS with n.
