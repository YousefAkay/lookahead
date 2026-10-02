# Decision log

My notes: what I chose, why, and the result. Options that lost stay in, briefly.
Numbers from my own data are counts and rates only. Personal details are in a private log.

## Project setup

### 2026-09-30: One chequing account in version 1
- Decision: I deferred credit cards and multiple accounts.
- Why: it keeps the money model simple and avoids double counting. Later, a card purchase counts as spending and the card payment from chequing is a transfer.
- Known limit: for people who spend on a credit card, chequing shows only one large monthly payment. Card accounts are my first extension after the demo.

### 2026-09-30: Python 3.13, exact version pins
- Decision: I use Python 3.13 and pin exact versions of top-level packages.
- Why: 3.12 gets security fixes only and has no Windows installer; pins keep evaluation numbers reproducible. Lost: minimum versions (`>=`).

### 2026-09-30: Money as integer cents
- Decision: I store amounts as integer cents and convert to dollars only for display and CSV output.
- Why: exact arithmetic, so reconciliation can use `==`. Lost: floats (inexact), `Decimal` (slow in pandas).

## Calendar and posting days

### 2026-09-30: Hand-written holiday list
- Decision: I wrote the bank holiday list by hand in `calendar_rules.py`. Lost: the `holidays` library (a new dependency that still would not say which days banks skip).
- Why: posting depends on when the payment system is closed, which is not the same as Quebec statutory holidays.
- Result: in two years of my export, no row posts on any listed date; June 24 and Easter Monday do have postings, so they stay out. Civic Holiday: no evidence either way.

### 2026-09-30: Two posting-day rules
- Decision: scheduled bills and payroll use business days (Mon–Fri); card purchases and incoming e-Transfers use card posting days (Mon–Sat). Income shifts earlier, bills later (assumption, not yet verified).
- Why: my export has no Sunday postings, Saturdays post normally, and Mondays are heavier. Lost: one business-day rule for everything (it would move Saturday purchases to Monday).
- Result: days after a holiday often show extra card postings, so holidays apply to card purchases too (supported, not proven).

## Financial model

### 2026-09-30: Forecast horizon
- Decision: the horizon ends at the next expected income or at 30 days, whichever is sooner.
- Why: not everyone is paid on a schedule. With irregular income the horizon falls back to 30 days.

### 2026-09-30: Large one-off amounts and earmarked inflows
- Decision: large one-off flows are scheduled items, kept out of the everyday-spending estimate. An inflow that funds a known large payment stays flow type income but is linked to that payment. Lost: flow type `planned` for it (that means items the user entered by hand).
- Why: one large payment would inflate the spending average for weeks, and the matching inflow is not spendable money.
- Open: how the forecaster recognises an earmarked inflow (pairing or user confirmation).

### 2026-09-30: Recurring series with irregular timing
- Decision: semester payments and irregular transfers in are recurring series, not one-offs. `one_off` is only for items with no schedule.
- Why: they repeat, but not on a weekly or monthly cadence. The recurring detector (week 3) needs gap statistics or user confirmation for them.

### 2026-09-30: Seasonality
- Decision: I evaluate baselines separately on quiet and busy periods.
- Why: activity can be uneven across the year, and a plain average hides that. Flagged, not solved.

### 2026-09-30: No single income pattern is assumed
- Decision: the app works from whatever income patterns are detected and confirmed. Generator v1 (week 5) gives most personas regular paycheques; results are reported per income type.
- Why: tuning to one unusual profile would make the models look better than they are.

### 2026-09-30: Shortfall episodes from real data
- Decision: if real data has balances below the buffer, those are real episodes for alert evaluation, reported separately from synthetic, with n.

## Bank format

### 2026-09-30: Export format, copied exactly by the generator
- Decision: synthetic exports match the real file: UTF-8 with a BOM, CRLF, every field quoted, newest row first, no final newline, one row dated the day after the export.
- Why: the importer is tested on synthetic files, so they must fail the same way real ones would. The importer reads with `utf-8-sig`, or the first header is wrong.
- Card text is fixed width: a prefix (`Apos` in person, `Opos` online; observed, no official meaning), a 20-character merchant field, a 5-character location. Accents become `?` in card text.

### 2026-09-30: Transaction types I rely on
- Own-account transfers are flow type transfer. Overdraft charges and service charges are Fees & interest. ABM withdrawals are category Cash, everyday spending.
- Incoming and returned e-Transfers are both `deposit` rows; only the text tells them apart ("Rtn"), so flow typing reads the text.
- Payroll rows are an assumption (`deposit` with the employer name).

## Synthetic generator (v0, frozen)

### 2026-09-30: Three personas as test fixtures
- Decision: A (tight payroll cycle, the main source of shortfall episodes), B (comfortable, the no-alert control), C (irregular top-up income, large term payments).
- Why: each exercises one behaviour. They say nothing about typical users; generator v1 samples 30–50 personas from weighted archetypes.
- Result: A dips below zero in 9 of 20 seeds, never past the overdraft limit.

### 2026-09-30: Staged pipeline with separate random streams
- Decision: stages run in order (scheduled money, everyday spending and balance-driven income in one day-by-day simulation, card quirks, fees, bank text). Each stage and each persona has its own random stream.
- Why: changing one stage must not reshuffle another. Lost: one shared generator.
- Bug fixed: every persona was seeded with the same number, so personas were correlated. The seed is now mixed with a stable hash of the persona id.

### 2026-09-30: Everyday spending model
- Decision: purchases per day are Poisson (rate × weekday × month), merchants picked by weight, amounts log-normal, posted on the next card posting day.
- Lesson: a calibration check on two simulated years was 15% off by chance. I now compute the expected value first and measure over five years.

### 2026-09-30: Balance-triggered top-up income (persona C)
- Decision: a top-up posts a few business days after the balance, minus scheduled payments due in the next few days, drops below a threshold. Lost: random gaps of several weeks; a balance-only trigger (too many top-ups arrived after going negative).
- Why: it reproduces income that follows the balance, not the calendar.
- Result: some top-ups still arrive after the balance goes negative. Accepted for v0.

### 2026-09-30: Overdraft limit and cutting back
- Decision: everyday purchases that would pass the overdraft limit are not generated; scheduled payments always post. Spending halves while the available balance is low.
- Why: without it one persona reached about -$1,000, which is unrealistic. Payday spending bumps are still missing.

### 2026-09-30: Planted card quirks
- Decision: the generator plants pre-authorization holds with a same-day correction, returns, one genuine double charge and one pair of identical legitimate purchases per persona.
- Why: the importer and alerts need them. The double charge and the legitimate pair look identical in the export, so a duplicate alert can only say "possible duplicate, please confirm".
- A foreign-currency refund keeps the purchase's CAD amount.

### 2026-09-30: Bank fees are event-driven
- Decision: fees follow events (an incoming transfer, an ATM withdrawal, a scheduled payment that overdraws, interest on the statement date). No monthly fee.
- Why: that is the pattern in the real export. The interest rate and statement day are assumptions.

### 2026-09-30: Foreign-currency purchases
- Decision: a merchant can have its own currency; the text shows the original amount and the CAD amount uses a rate drawn from a range per currency.

## Importer

### 2026-09-30: Schema and duplicate detection
- Decision: two text columns (`description`, `sub_description`) and `balance_after_cents`. The fingerprint hashes date, amount, normalised text, balance after the row, and an occurrence number.
- Why: two identical purchases on one day have different balances, so both are kept; the same row in two overlapping exports matches once.
- Result: two overlapping real exports matched on all 1,000 shared rows. Known limit: a run of identical $0.00 rows cut by a file boundary (not seen in my data).

### 2026-09-30: Balance anchors and reconciliation
- Decision: each file stores an end-of-day balance for every date except its newest. Reconciliation checks that the anchors and the stored amounts agree, and reports breaks by date. Lost: one anchor per file (cannot locate a break).
- Why: the newest date of an export can be incomplete. One import is one database transaction.
- Blind spot: $0.00 rows change no balance, so reconciliation cannot see them.

### 2026-09-30: Counts-only import command
- Decision: `python -m lookahead.ingest` prints counts and PASS/FAIL only. I run it on my data myself; automated runs use synthetic files only.
- Result: 1,311 real rows, balance chain PASS, 405 anchors reconciled. A second run added 0 rows.

## Privacy

### 2026-09-30: Pseudonymize at the boundary
- Decision: the database keeps raw text; anything leaving `data/private/` is pseudonymized first, with keyed HMAC tokens for people's names, payment references and long digit runs. Lost: pseudonymizing at import (protects little, and losing the key would break dedupe).
- Result: a counts-only report scans for identifiers left. On my data it flagged 4 rows, all businesses, so I added a private allowlist. Lost: excluding those row types from the scan; hard-coding the texts.
- It is a heuristic check, not proof.

### 2026-09-30: Tests never read private files
- Decision: every test points allowlists, databases and keys at temporary paths.

## App

### 2026-09-30: Transactions page
- Decision: logic lives in `views.py` and `ui.py`; pages only display. Databases open read-only. The pseudonymize toggle is on by default. The server listens on localhost only.
- Why: Streamlit's default listens on every network interface.
- The balance chart is a step line; a diagonal line showed balances that never existed.

### 2026-10-01: The app never blocks on review
- Decision: every row gets a category automatically; low-confidence rows are only flagged.
- Each prediction carries a confidence, which gives one ordering for a coverage-accuracy curve.

## Merchant extraction (week 2, step 1)

### 2026-09-30: Rules, not a model
- Decision: card rows are cut by position, then rules strip amounts, star codes, processor prefixes, phone and store numbers, payment words, cities and reference codes. Lost: regex-only splitting; stripping `.com`; a learned tagger.
- Why: position is the only reliable separator when a 20-character name touches the city.
- Result: synthetic exact match 75.1% (555 rows), 0 noisy names; every miss is a naming difference for resolution. Optimistic: I wrote the rules knowing the generator's templates.

### 2026-09-30: Checking real leftovers without showing them
- Decision: the real report prints counts per leftover pattern and can write the names to a private file. I describe the shapes; rules are tested on made-up names of the same shape.
- Result: 14 leftover rows became 1, which I accepted. Added: a country code plus a lone digit at the end, a leading store number with underscores, cities cut to 5+ letters. Lost: stripping every leading number.
- Two private lists (brands containing digits, accepted leftovers) keep the check at PASS.

### 2026-09-30: Delivery platforms
- Decision: a name that starts with a delivery platform becomes that platform, with the restaurant kept as a detail. Platform subscriptions are separate merchants.
- Why: over 100 resolution proposals were one platform glued to a different restaurant each time.
- Result: distinct names 158 → 145.

### 2026-10-01: One brand, several services
- Decision: for a brand whose star-coded names cover rides, delivery and holds, the word after the star picks the service; unknown ones get their own merchant.
- Why: I found one merchant group mixing all three while labelling. The star rule kept only the brand, and the alias list mapped the brand to rides.
- Result: frozen splits undisturbed (`check-splits` PASS); the wrong labels were cleared with a new dated `cleared` status.

### 2026-10-01: Trailing 2-digit store numbers
- Decision: I measured first, then fixed: the end-of-name number rule now strips 2+ digits (was 3+).
- Why: branches of one chain could become separate merchants, but a fix that merged groups across splits would break Test B.
- Result: 13 rows renamed, 0 groups merged, 0 mixing Test B. Known limit: a store number cut to one digit stays.

## Merchant resolution (week 2, step 2)

### 2026-09-30: Rules merge, similarity only proposes
- Decision: automatic merges for the same key, `?` accents, a small alias list, and a name cut at exactly 20 characters that starts one other name. Everything else is a proposal I accept or split. Lost: auto-merging every prefix or high similarity; a trained matcher (no labelled pairs).
- Why: similar is not the same.
- Result (synthetic test half, run once): rules alone 100% precision (19/19), 39.6% recall; with all proposals accepted 100% recall. Optimistic: rules and variants were written together.

### 2026-09-30: TF-IDF similarity, threshold chosen on dev
- Decision: similarity is TF-IDF cosine on character 3-grams, threshold 0.40, chosen on a dev half of a variant test set committed before any threshold. Lost: raw 3-gram overlap.
- Why: with raw overlap, a common word shared by two names counted as much as a shared brand.
- Result: my real run went from 141 waiting pairs to 7 after this and the delivery rule. Of 34 pairs I decided, 10 were merges (29%). Too low to ever auto-merge.

### 2026-09-30: Decisions are a dated log
- Decision: every merge, split and category preference is a dated row; `load_user_decisions(as_of=t)` replays the log up to a date. Lost: a date column on the merchant tables (wrong when two merchants are joined).
- Why: the Time Machine must only use decisions made by each replay date.
- Bugs fixed: a split left a one-name merchant behind; two groups could share a display name.

## Flow typing (week 2, step 3)

### 2026-09-30: Per-row rules now, recurring detection later
- Decision: flow type comes from the bank's transaction type and direction. A pair with no rule falls back to everyday or refund, never income. Lost: a simple recurring detector now.
- Result: synthetic 94.8% (555 rows); all misses are subscriptions and rent, which need the week 3 detector. Too good to mean much: the generator uses the same notes.
- On my data, 0 rows without a rule after I added "a bill payment coming in is a refund".

### 2026-09-30: Pairing looks back only
- Decision: a correction pairs with a same-amount purchase up to 7 days earlier, a card return up to 90 days, a returned e-Transfer up to 30 days, all within the resolved merchant group.
- Why: rule zero. A row's type never depends on later rows.
- Result: corrections 73 of 73, all same-day; returns 2 of 4; returned e-Transfer 0 of 1. I left the unpaired ones (missing history, different merchant, amount too far off).
- Bug fixed: "earlier" meant database order, which paired 0 of 9 on synthetic. It now means the same day or before.

### 2026-10-01: Fully reversed pairs are netted
- Decision: when a credit equals its whole purchase, both rows are marked netted: no category, excluded from spending totals. Partly reversed pairs are not.
- Why: a hold and its reversal are not spending, and the hold sat in my labelling queue.
- Netting is known only once the credit posts.

### 2026-09-30: Notes for week 3
- Fees get their own model, outside the everyday-spending bootstrap.
- The recurring detector upgrades subscriptions and e-Transfer rent, and downgrades one-off bill payments.

## Categories (week 2, step 4)

### 2026-09-30: Rules decide the clear cases
- Decision: in order: netted pair, transaction type, inherit from the paired purchase, my preference, delivery platform, merchant defaults, then the categorizer.
- Result: synthetic: rules decide 36.4% of rows, 95.5% correct (misses are e-Transfer rent). On my data about 46% of rows, 0 flow/category conflicts.
- Too good to mean much on synthetic: the defaults are the generator's own choices.

### 2026-10-01: 17 categories
- Decision: I added Personal care and Gifts & donations during labelling, before any label used them.
- e-Transfers to people stay Transfers by default; I override single rows.

### 2026-10-01: Mixed merchants are labelled by what I buy there
- Decision: one label per merchant by my main use; single-row overrides only when I remember the purchase; skip when unsure.
- Consequence: the answer key is personal, and accuracy at mixed merchants has a ceiling without receipts.

## Categorizer (week 2, steps 5–7)

### 2026-09-30: What the model sees
- Decision: only rows the rules leave. Features: character n-grams of the merchant name (TF-IDF) plus one-hot channel, type, direction and amount bucket.
- Synthetic results are a pipeline check, not evidence: 29 merchants, each with one category.

### 2026-09-30: Hybrid: lookup for known merchants, model for new ones
- Decision: a merchant with training labels uses its most common label (flagged if labels disagree, unless I stored a preference). A new merchant uses the tuned logistic regression and a flag threshold. Lost: one model for both.
- Why: tuning for unseen merchants (grouped cross-validation) made the model much worse on known ones (synthetic Test A 95.0% → 82.7%).
- Result (synthetic): Test A 98.0% accuracy; Test B 70.9% accuracy, 28.0% macro-F1.

### 2026-09-30: Splits frozen before labelling, holdout scored once
- Decision: Test B is 25% of merchant groups, Test A the latest 20% of the remaining rows, dev the rest. Frozen once per account in the database, keyed by transaction fingerprint. Holdout merchants are reviewed blind.
- Why: so the model cannot anchor its own answer key, and no choice is made on the holdout.
- Result: 124 merchants labelled (668 rows, 5 skipped). Every setting chosen on synthetic data is provisional.

### 2026-10-01: Every choice on dev only (step 7a)
- Decision: two protocols inside dev (unseen merchants by grouped folds; future rows by date). The choice rule is macro-F1 on unseen merchants, fixed in advance.
- Leak found before the real run: synthetic training rows contained the "unseen" merchants (stand-in: 100%). I chose overlap-aware evaluation: synthetic rows of a held-out merchant are dropped. Lost: allowing the overlap; dropping synthetic training.
- Result: real + synthetic 77.0% accuracy, 32.0% macro-F1; real only 75.4% / 26.4%. Future rows 95.5%. Confidence did not separate right from wrong on new merchants.

### 2026-10-01: Pre-declared experiments (dev: 447 rows, 82 merchant groups)
- Decision: I declared the variants, a frozen keyword lexicon, the combination rule and the flag rule before running, and committed them first.

| Variant | Unseen merchants: accuracy / macro-F1 | Future rows: accuracy / macro-F1 |
|---|---|---|
| before | 77.0% / 32.0% | 93.3% / 84.5% |
| class weights | 75.4% / 33.1% | 89.6% / 82.0% |
| lexicon (chosen) | 79.2% / 38.3% | 94.0% / 90.7% |
| calibrated | 68.0% / 23.6% | 92.5% / 83.0% |
| fold-averaged (control) | 75.8% / 26.7% | 93.3% / 84.5% |
| class weights + lexicon | 67.1% / 32.2% | 93.3% / 82.7% |

- Chosen: the lexicon model. A confidence threshold is kept (the declared test passed: +7.2 points at 90% coverage).
- Negative results: class weights broke the confidence; calibration and fold averaging both hurt (probably too few merchants per rare category in each fold, not verified); the combination was worse than the lexicon alone.
- Per category, restaurants and groceries work; most small categories were at 0% recall.

### 2026-10-01: Three dev labels corrected
- Decision: I fixed three labelling mistakes on dev merchants. No holdout label was looked at.
- The experiment table above is not rerun; later runs use the corrected labels.

### 2026-10-01: MiniLM embeddings as a candidate (step 5c, declared before the run)
- Decision: `paraphrase-multilingual-MiniLM-L12-v2`, alone and added to the current features, on the same dev protocol. Lost: the English-only model.
- Rule: it becomes the candidate only with at least +1 point of macro-F1 on unseen merchants, and enters the app only if it also wins on Test B.
- Why: a tie does not justify a 1 GB dependency. The model runs locally; the cache stays in `data/private/`.
- Result: embeddings alone 64.7% accuracy, 32.1% macro-F1 (-6.7 points vs current); added to the current features 81.7% / 37.5% (-1.4). Neither passes the rule, so the lexicon model stays. I moved torch and sentence-transformers to `requirements-experiments.txt`; the app does not need them.

### 2026-10-01: LLM as a measured candidate (point 4a, declared before the run)
- Decision: I benchmark Claude Haiku 4.5 (`claude-haiku-4-5-20251001`, temperature 0) on the same dev protocol, zero-shot, as a fallback for new merchants: for all of them, and for flagged ones only. Lost: Opus 5.5 (its API rejects a temperature setting, and it costs more).
- Rule: it becomes a fallback candidate only with at least +1 point of macro-F1 on unseen merchants. In the app it would be opt-in, off by default; the headline result stays the trained model.
- Enforced in code, with tests: only the extracted name of a card row is sent (no amount, date, location or type); rows a person rule or the name scan would catch are dropped; one call per name, cached in `data/private/cache/`; the answer must be one of the 17 categories; nothing writes labels; 4a sends dev names only.
- This is a narrow exception to my rule that real data never goes to a cloud LLM. Reported with accuracy and macro-F1: cost per 1,000 merchants and median latency.
- Bug fixed: the first real run stopped with a TypeError before sending anything, because the SDK (1.x) no longer has a `temperature` argument; my fake test client accepted any argument and hid it. Temperature now goes in the request body, tests check the request against the real SDK with the HTTP layer replaced, and a smoke command sends one made-up name first.

## Tests

### 2026-10-01: Slow marker
- Decision: 22 tests are marked `slow`. I run `pytest -m "not slow"` routinely (about 46 s) and the full suite before commits (about 3 min).
- Lost: skipping slow tests by default; pytest-xdist.
