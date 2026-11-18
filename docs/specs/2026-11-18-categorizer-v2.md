# Categorizer v2 and RAG, spec (2026-11-18)

**Status: DRAFT, awaiting my approval.** Nothing here is in force until I approve it.

## What it is
Groundwork to raise accuracy on new merchants (merchants with no label of mine yet). Three opt-in pieces sit on top of today's categorizer: a merchant-text cleaner, a public directory of big Canadian chains, and a RAG step that asks Haiku 4.5 with retrieved examples. With all three off, the app behaves exactly as today.

Today: rules, then my labels, then the known-merchant lookup, then logistic regression on character TF-IDF plus the frozen FR/EN lexicon, then the opt-in LLM fallback (merchant name only).

## Decisions I already made
- **What the RAG prompt may contain:** the new merchant's cleaned name, retrieved merchant names from my own labels with their categories, and public directory entries. Never amounts, dates, raw descriptions, account names or anything else.
- **Public chain directory:** national Canadian chains plus Quebec/Montreal ones, about 250 entries, with French and English names.
- **Everything opt-in and off by default.** CSV import and local-first stay the defaults.
- **A setup question, not a hidden toggle (4b).** The first time categories run, and in the settings, the app asks once: "Smarter categories for new merchants: sends merchant names and your category labels (never amounts, dates or descriptions) to Haiku 4.5. Turn on?" The answer is remembered. If no API key is configured, the app says what is needed instead of asking. It stays off until I answer yes.

## Plan
### 2. Merchant-text cleaner
- New pure module `lookahead/merchant_clean.py`. Input: a merchant name (after today's extraction). Output: the display name (unchanged), a match key (lowercase, no accents, single spaces) and the list of what was removed.
- Strips, conservatively: OPOS / FPOS / APOS prefixes, processor prefixes ("SQ *", "TST*", "PAYPAL *"), store numbers ("#1234", trailing digit runs), city and province codes at the end ("MONTREAL QC", "MTL", "LAVAL"), reference numbers.
- Never strips a name to empty; if a step would, that step is skipped.
- Wired in behind the setting `categorizer_v2_cleaner` (off): when on, the model sees cleaned names for training and prediction.

### 3. Public chain directory
- `data/reference/canadian_chains.csv` (not git-ignored). Columns: name, aliases (`|`-separated EN/FR names and common bank spellings), category (one of the 17), region (`national` or `QC`), verified (always `no` until I spot-check).
- Big chains only, no small local businesses.
- Lookup `lookahead/chain_directory.py`: cleaned name -> entry by exact name, then alias, then character similarity at or above a threshold. The threshold is chosen from synthetic merchants only (the rule is written in the module and in DECISIONS).
- Behind the setting `categorizer_v2_directory` (off): when on, a flagged new-merchant row that matches an entry gets the entry's category, source "directory". Known merchants and my labels always win.

### 4. RAG categorizer scaffold
- `lookahead/rag_categorizer.py`.
- Retrieval: top-k labelled merchants by character TF-IDF over cleaned names, plus top directory matches.
- Prompt builder takes only the allowed fields; anything else raises. A test checks the prompt holds no digits beyond those in the merchant names.
- The answer must be strict JSON: one of the 17 categories plus the ids of the examples it relied on. Anything invalid or unsure gives no category and the row goes to "To check".
- The client is injected. Tests use a fake. The real client reuses the fallback's settings: Haiku 4.5 pinned, temperature 0, answers cached in `data/private/`.
- Behind the setting `categorizer_v2_rag` (off), set only by the setup question. Retraining applies saved answers only; sending is a separate explicit step.

### 4b. Setup question
- `app/smarter_categories.py`, shown on the To check page (where categories run) and in the sidebar settings. Not shown for the synthetic demo.
- No API key: the question is replaced by what is needed (ANTHROPIC_API_KEY in .env). Answer stored in `app_settings`; "not answered" counts as off.

### 5. Evaluation harness (built, not run on anything real)
- `eval/categorizer_v2.py`. Compares (a) today's model, (b) + cleaner + directory, (c) + RAG.
- `--db` and `--labels` required, no default pointing at private data.
- Refuses to score unless the adopt-rule file exists, is committed and is unchanged since that commit.
- Refuses rows from the old used holdout (Test A, Test B). Prints counts and accuracy with n only, never row text. Every variant run is counted in a ledger.
- `--synthetic` dry run on personas A, B and C checks the plumbing and prints "dry run, not a result".
- A helper picks candidate blind-set rows: new merchants first seen on or after a date and not in the training labels.

### 6. Adopt rule, DRAFT
- `docs/specs/2026-11-18-categorizer-v2-adopt-rule-DRAFT.md`, marked not in force. Numbers are proposals for me and my advisor.

## Out of scope
- Scoring anything, on real or synthetic data. No accuracy numbers, no threshold tuned on results.
- Any real API call. Any change to the forecaster, rules, labels or splits.
- README changes.

## Definition of done
| # | Item | Check |
|---|------|-------|
| 1 | This spec, marked DRAFT | file committed |
| 2 | With everything off, outputs on personas A, B and C identical before and after | identity test against a snapshot taken on origin/main before any change |
| 3 | Cleaner: pure, never empty, French and near-miss cases | unit tests |
| 4 | Cleaner wired behind an off-by-default setting | test: off by default, on changes only the model's names |
| 5 | Directory: about 250 big chains, valid categories, no shared alias, no empty name, verified = no | tests |
| 6 | Directory lookup: exact, alias, similarity; threshold chosen on synthetic merchants only and recorded; off by default | tests |
| 7 | RAG: retrieval, prompt builder that rejects other fields, no stray digits, strict parsing with citations, fake client only | tests |
| 8 | Setup question: asks once, remembers, explains a missing key, off until yes | tests |
| 9 | Eval harness: explicit paths, adopt-rule commit check, old holdout refused, counts and n only, ledger, synthetic dry run, blind-set helper | tests |
| 10 | Adopt rule DRAFT written, not in force | file committed |
| 11 | ruff clean, full suite passes, DECISIONS entry appended | commands run |
