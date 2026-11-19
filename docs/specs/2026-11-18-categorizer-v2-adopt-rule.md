# Categorizer v2, adopt rule, version 2 (2026-11-18)

In force from its commit. It replaces version 1 (commit dfb1fc6), which was never used: nothing was drawn, labelled or scored under it. The only thing run under it was a count on my account (`candidates --count`), which drew nothing: group A had 18 rows from 16 new merchants, and version 1 needed 100. My account is too small to decide v2, and by my own rules my real data should only veto. So the deciding run moves to a public set, and my rows become a veto.

`eval/categorizer_v2_external.py` (the deciding run) and `eval/categorizer_v2.py` (the veto) refuse to draw, label or score until this file is committed and unchanged since its commit.

## Part 1: the deciding run, on public data only

**The set.** DoDataThings "US Bank Transaction Categories v2" (Hugging Face, MIT): 68,000 synthetic US bank descriptions in 17 categories, made by an open-source generator. Pinned:
- dataset revision `3e8d052cb7b362fb1a04b6064d2331982553ea56`, file `transactions-synthetic.csv`, sha256 `0424aed6e76f74a5b3b1ff61ccec43bc321622e6806da353b910b3b2c8108f6e`;
- the generator's merchant-name list, read as text and never run: wnstnb/foliome `scripts/generate-training-data.js` (Apache-2.0) at commit `edde93d3550e9a2699fa8be4ceea2ce205d4ab43`, sha256 `d380e4bbdd0d34ecee025f029ca713ae75577d4eb46a0eed0aa7f82fd5d3f547`. It is the only public version of that file, and it is dated 2026-10-04, after the dataset (2026-04-06). No earlier version exists publicly, so the number of rows that would group differently under the dataset's own version cannot be counted. 10 of its 1,008 names never occur in the dataset (probably added later). Every other name groups the dataset's rows.

Both files are downloaded into `data/public/dodatathings/` (git-ignored) and checked against their hashes. They are not committed.

**Nothing of mine is used.** No label of mine, no saved setting, no synthetic row from my generator. (a), (b) and (c) are trained, tuned and given RAG examples from the train part only.

**The merchant of a row** (the file has no merchant column and no amounts). The key is frozen in code, exactly as `MerchantKeys` in `eval/categorizer_v2_external.py` at this rule's commit. Before matching, the "[debit]"/"[credit]" tag and a leading payment wrapper (PAYPAL INST XFER, SQ *, TST *, CLV *, PP *) are removed; wrapper names are never merchants; names of 2 characters or more count, and squashed forms only from 5 characters. Then, in this order:
1. a generator name as a whole word, the earliest in the text, then the longest;
2. the name with spaces and punctuation removed, covering whole words ("CHICKFILA"); a name of two or more words may run into the next field ("BATH & BODY WORKSWEB ID");
3. a truncated name: the first word, punctuation removed, of 7 or more letters, that begins exactly one generator name ("TRADERJOE", "STOP&SHO");
4. the fixed spellings written in the generator's own code (Amazon, Whole Foods, Trader Joe's, Save Mart, Smart & Final, FoodMaxx, Grocery Outlet, Apple);
5. otherwise the first two words.

The key is used only for the split, the cap per merchant, the bootstrap and the known-merchant lookup. It is never a model feature, and the directory and RAG look up the cleaned description, not the key. Known residue: about 6 rows of the 48,000 kept still get a key of their own although they are a generator chain. A counts-only check (`summary`) finds 0 identical cleaned descriptions in both the train part and the deciding sample (a weak check: the cleaner keeps US city names). Short names count too: all 88 kept rows keyed "76" are the 76 gas station.

**Categories.**

| Their category | Mine | In the run |
|---|---|---|
| Restaurants | Restaurants & cafés | kept |
| Groceries | Groceries | kept |
| Shopping | Shopping | kept |
| Transportation | Transport | kept |
| Entertainment | Entertainment | kept |
| Utilities | Phone, internet & utilities | kept |
| Subscription | Subscriptions | kept |
| Healthcare | Health & pharmacy | kept |
| Education | Education | kept |
| Personal Care | Personal care | kept |
| Mortgage | Rent & housing | kept |
| Rent | Rent & housing | kept |
| Transfer | Transfers | dropped: my bank rules decide it, not the model |
| Income | Income | dropped: my bank rules decide it |
| Fees | Fees & interest | dropped: my bank rules decide it |
| Insurance | none | dropped |
| Travel | none | dropped |

When scoring, Food delivery and Restaurants & cafés count as the same category (this set has no Food delivery). 48,000 rows are kept, from 1,072 merchants. A category not in this table stops the run.

**Split and sample.**
- **Split:** 40% of the merchants, drawn with seed 0, go to the test part with all their rows: 19,344 rows from 429 merchants. The rest is the train part: 28,656 rows from 643 merchants. No merchant is in both.
- **Training:** (a), (b) and (c) train on every row of the train part. Settings and the review threshold are tuned there by grouped cross-validation.
- **Sample:** the deciding sample is 600 rows of test merchants, at most 3 rows per merchant (356 merchants). Both draws use seed 0.

**Inputs.** This set has no bank transaction type, amount or channel. Each gets the same fixed value for every row and every variant. The model reads the description without its "[debit]" / "[credit]" tag, plus the direction that tag gives.

**Variants.**
- (a) today's design, logistic regression plus the lexicon, trained as above.
- (b) (a) + the merchant-text cleaner + the chain directory, exact and alias matches only. The directory's similarity matching is not part of (b) and is not tested in this run.
- (c) (b) + RAG. The examples are the train part's merchants with their mapped categories, plus the closest chain-directory entries, as in the app.

**What a variant must show.**
- **(b) against (a):** on the deciding sample, a gain of at least 3 points, AND the 2.5th percentile of the merchant-level paired bootstrap gain above 0 (2,000 draws, seed 0, whole merchants per draw).
- **(c) against (b), not against (a):** the same two conditions, with (b) as the base. (c) passes only if (b) passes too: (c) contains (b).
- **What this test can detect** (made-up numbers, 600 rows in 200 merchants, 200 trials): if (b) breaks nothing, a true gain of about 4 points is likely to pass (88%). If it also breaks about 5% of the rows (a) got right, it needs about 5 to 8 points (76% / 96%). Small real gains may still fail; that is accepted.
- **Known limits:** the set is synthetic and US. The directory lists Canadian and Québec chains, and the cleaner keeps US city names ("Starbucks Seattle"), so (b)'s directory step will answer fewer rows here than on Québec data. Nothing is tuned for this.

## Part 2: the veto, on my real rows

**What runs on my rows** is what I would switch on in the app:
- (a) my app's model as deployed today, trained on my labels with my saved settings and the synthetic rows;
- (b) that + the cleaner + the directory (exact and alias only);
- (c) (b) + RAG with my own labelled merchants as examples (my blind rows never among them).

**My rows** are drawn once, with seed 0, only after the deciding run is scored, right before the veto, so newer months are included.
- **Group A, new merchants:** up to 150 rows from 2026-10-01 onward, at most 3 per merchant, from merchants with no training labels and first seen on or after 2026-10-01.
- **Group K, known merchants:** 60 unlabelled rows from 2026-10-01 onward whose merchant has training labels. No cap per merchant.
- **Never drawn:** rows of the old holdout (Test A, Test B), rows with a label of mine, and rows I marked "Skip / don't know" in the label review.
- **Labelling:** both groups are labelled blind, in one shuffled pass, so the order does not tell the group.
- **Minimums:** at least 15 group A rows to draw and to score, and at least 40 labelled group K rows to score. Otherwise nothing is drawn or scored.
- **Refused at scoring:** a group A merchant that has gained training labels, a labels file that is not exactly the drawn rows, or a rule that changed after the draw.

**The veto.** (b) and (c), each against (a):
- on group A, at most 2 rows net worse than (a);
- on group K, at most 1 row net worse than (a).

A variant that fails either is vetoed.

## Adoption

A variant is adopted only if it passes Part 1 AND is not vetoed in Part 2. (c) is adopted only if (b) is adopted too: (c) contains (b). The harness prints and logs both, and "adopted: yes / no".

## Checks

- **Leakage:** if any variant scores above 95% in either part, I stop and look for leakage (a test merchant in training, a merchant key shared by both parts, a directory entry written after seeing the rows) before reading anything else.
- **Every run counted:** every run is in the ledger, refused runs included.
- **Scored once:** the public sample is scored once, and my rows are scored once. A second scoring needs `--again`, is recorded, and does not replace the first result: the first scored run under this exact rule text decides. A public result scored under another rule text counts for nothing. The one public scoring is `score --rag --send`, with all three variants in the same run; a public run without both flags is refused.
- **Dry run before this rule** (disclosed): before writing this rule I ran (a) only, never (b) or (c), on held-back train merchants of an earlier grouping, to see whether a 3-point gain had room: 68.8% (n = 475 rows, 201 merchants). Under the frozen key, 58 of its merchants are now test merchants, 44 are in the deciding sample, and 10 of its rows are deciding-sample rows. Nothing was tuned from it. The sample stays as drawn.
- **Frozen before scoring:** the cleaner, the directory, the similarity threshold (0.60), the lexicon, the RAG prompt and the merchant key.

## If nothing passes

v2 stays off by default, as it is now. Either way, the result goes in DECISIONS with n, written as: decided on an independent synthetic US benchmark; the real-account check is a veto only.
