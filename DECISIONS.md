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
- Result (dev, 83 names sent, 0 invalid answers, $0.018 in total, $0.22 per 1,000 merchants, median 521 ms):

| Variant | Unseen merchants: accuracy / macro-F1 | Future rows: accuracy / macro-F1 |
|---|---|---|
| current | 83.2% / 38.8% | 94.0% / 89.6% |
| LLM for new merchants | 85.7% / 63.1% | 91.0% / 89.5% |
| LLM for flagged new merchants | 83.0% / 58.1% | 92.5% / 92.1% |

- By the rule, the fallback candidate is "LLM for new merchants" (+24.2 points of macro-F1).
- How I read it: the macro-F1 gain is inflated by tiny categories (several go from 0% to 100% recall on 1 to 3 rows of a single merchant). The accuracy gain is +2.5 points, and future-row accuracy drops 3 points for the full-LLM variant. Shopping is the solid gain (25% to 100%, 8 rows, 6 merchants). Personal care stays at 0%: it follows my own labelling rule, which an outside model cannot know.
- Product decision unchanged: the trained hybrid is the default and the headline result. The LLM is an opt-in fallback, off by default, reported as a separate line.

### 2026-10-01: Holdout protocol (step 7b, declared before the run)
- Decision: I train once on all dev labels with the settings saved by 7a (real + synthetic, C 100, n-grams 2-4, flag threshold 97%) and score Test A and Test B once. Candidates: the trained hybrid (default, headline) and the hybrid with the LLM for every new merchant. The flagged-only LLM variant is printed as an extra line, not a candidate.
- Test B is reported in two parts: merchants in the synthetic list (pre-taught, not generalization) and merchants not in it. Skipped rows are not scored. Every figure has n.
- The script refuses a second run unless I pass `--again`, which it records. A dry run sends and scores nothing. Holdout card-merchant names go to the LLM once, under the 4a rules.
- Why: every choice was made on dev, so the holdout number is an honest estimate only if nothing is changed after seeing it.
- Result (run 1, final; dev 447 rows, 62 holdout names sent, 0 invalid answers):

| Test set | n | Trained hybrid: accuracy / macro-F1 | With LLM fallback |
|---|---|---|---|
| Test A | 114 rows, 35 groups | 91.2% / 69.6% | 96.5% / 87.8% |
| Test B, all | 107 rows, 27 groups | 87.9% / 52.6% | 92.5% / 67.7% |
| Test B, pre-taught | 59 rows, 1 group | 100% / 100% | 100% / 100% |
| Test B, not pre-taught | 48 rows, 26 groups | 72.9% / 49.8% | 83.3% / 67.7% |

- "Test B, all" is inflated: one pre-taught chain has 59 of its 107 rows. My generalization number is Test B not pre-taught: 72.9% / 49.8%.
- Accuracy on unflagged rows: 98.1% on Test A (8.8% flagged), 94.4% on new merchants (62.5% flagged, so 18 rows).
- LLM cost: $0.014, $0.224 per 1,000 merchants, median 516 ms. The flagged-only variant gave the same accuracy as the candidate on both sets.
- Small n everywhere. I made no choice from these numbers.

## Categories in the app (week 2, step 8)

### 2026-10-01: Predictions are stored, pages only read them (8a)
- Decision: `python -m lookahead.categorize` trains the model with the saved 7a settings, categorizes every row and stores the result (merchant, flow type, category, source, flag, confidence) in `category_predictions`. Each run is recorded with its date, model version and settings. Lost: predicting inside the page (slow, and the pages open the database read-only).
- Why: the Transactions page stays fast, and a later Time Machine replay can tell which model produced what.
- A confirmed label is shown as is, for holdout rows too, but only development labels train the model.
- With "Pseudonymize text" on, the merchant column is hidden on rows where a name or reference was replaced, since the merchant of such a row can be a person.
- The demo model is trained on the generator's answer keys, so demo categories are nearly always right; the page says this is not evidence.

### 2026-10-01: Corrections are separate from evaluation labels (8b)
- Decision: a correction made in the app is a dated row in its own table. It changes what the app shows at once. It trains the model only for development rows and rows imported after the freeze; holdout rows change on screen only. Lost: writing corrections into the label table (a correction on a holdout row would have changed the answer key).
- Decision: a whole-merchant correction asks once ("this changes N rows") and is not stored as a category preference. Lost: a preference, which would make a rule decide the merchant and take its rows out of the frozen splits. Future rows of the merchant are covered once the model is retrained, because it reuses the merchant's label.
- A single-row correction also works on rows a rule decided (an e-Transfer that was a gift). A category that contradicts the flow type is refused (Income on spending).
- The "To check" page lists merchants with a low-confidence category, least confident first. Checking is optional; every row already has a category.

### 2026-10-01: Retrain uses the saved settings and tunes nothing (8c)
- Decision: the Retrain button (and `python -m lookahead.categorize`) trains on my development rows, my corrections on eligible rows and the synthetic rows, with the C, n-grams and flag threshold saved by 7a, then refreshes every stored category. It reports rows used before and after, rows whose category changed, and rows to check before and after.
- Why: re-tuning on every click would change the model in ways I never evaluated. The holdout stays out of training, which a test checks.
- The trained model is saved, dated, under `data/private/models/` (it holds merchant names). The demo model is not kept.

### 2026-10-01: LLM fallback setting, off by default (8d)
- Decision: a stored setting, off unless I turn it on, and not offered for the synthetic demo. When on, it covers only flagged rows of new merchants, under the 4a rules (card-merchant names only, one cached call per name).
- Nothing is sent until I click the send button, which first shows how many names would go and lists them on screen. Retraining and the categorize command reuse saved answers and never send anything; a test fails if they try.
- A row answered by the LLM shows "LLM" as its source and keeps its flag, because nobody has checked it. Turning the setting off restores the trained model's own answers.
- Why: the app stays local-first, and I can always see what left the machine.

## Recurring payments (week 3, step 3a)

### 2026-10-02: Calendar-aware detection by rules (3a-i)
- Decision: for each merchant I try weekly, biweekly, semimonthly, monthly (same day, clamped to the month length) and yearly patterns, shift the due date like the bank does (income earlier, payments later) and match rows within 3 business days. A series needs 3 payments (2 for yearly), 75% of its due dates paid and 80% of the group's rows explained. Lost: fixed gaps between payments (a month is 28 to 31 days and weekends move dates).
- Amounts: fixed if all are within 3% or $1 of the median (the same threshold as the amount-changed alert); otherwise variable, with the 10th to 90th percentile as the normal range, at least +/- 10% while there are fewer than 6 payments. Lost: median +/- 2 standard deviations.
- e-Transfers have no merchant, so they are grouped by recipient (when the bank prints one) and amount. They stay candidates and never change flow type until I confirm them. Regular transfers to my own accounts are found the same way and stay transfers.
- Fees, refunds and fully reversed pairs are left out. Irregular income and semester payments are not detected; I add them by hand.
- Detection never rewrites the per-row flow types. An override layer makes a detected card subscription a recurring outflow and a bill payment to a payee paid once a one-off.
- Found on the synthetic personas before any tuning: habits passed as schedules (a weekly grocery trip, three same-price coffees that line up). I added two rules: card purchases need amounts within 10% of the median and 75% of payments within a day of the due date; a series found among other rows of its merchant needs 4 payments, not 3. Semimonthly pay was also missing from the plan's cadence list.
- Result (pipeline check, 3 personas, 4 months): 13 of 15 planted series found, 1 false series (4 same-price purchases a week apart). The 2 missed are the irregular income and the semester payment, which I chose not to detect. Proper evaluation is 3a-ii.

### 2026-10-02: Price changes, recipient tokens, card posting days (3a-i, after review)
- Decision: a fixed series whose amount changes once (stable before, stable after) stays one fixed series at the new amount, and remembers the old amount and the first payment at the new one. Why: otherwise a +15% subscription increase failed the 10% card rule and the series vanished, so "amount changed" could never fire.
- Decision: an e-Transfer recipient enters a series key only as the pseudonymizer's keyed token. Without the key, named recipients are not grouped at all. Lost: a plain hash of the name (guessable from a list of names).
- Decision: card purchases and e-Transfers are expected on card posting days (Monday to Saturday), not business days. Found by the evaluation: a subscription due on a Saturday was detected a day off.

### 2026-10-02: Evaluation against the generator's schedules (3a-ii)
- Decision: the answer is the generator's schedule objects (rows, cadence, next due date), never the detection rules. A detected series is true if most of its rows come from one planted schedule.
- Result (3 personas, 12 months, seed 42): 13 of 13 detected series are planted; 13 of 13 fixed-cadence series found, cadence right on 13, next date right on 13; rows 62 of 62 correct, 62 of 63 found. The 2 irregular series are not targeted.
- Too good to mean much: clean schedules, the same calendar rules on both sides, and I tuned two rules on these personas. The schedules also start in June 2026, so a longer window adds no payments.
- Other seeds are less clean: 4 months gives 1 false series (13 of 14); 24 months with another seed misses 1 subscription because a letters-only reference code left in the name split the merchant into three groups. That is a week 2 extraction limit, not fixed here.
- The real check is my confirmed list. `python -m lookahead.recurring report` prints counts only.

### 2026-10-02: First real run of the detector, and bill payments
- Result (my account, counts only): 4 series, all monthly card charges (3 fixed, 1 variable, 1 with a price change, 3 active); 0 e-Transfer candidates; no bill payment in any series. I expected 4 to 5 monthly charges.
- Decision: the report now breaks bill payments down by payee: number of payments, why it is not a series (too few payments, irregular timing, part of them form a series, no payee name) and the typical gap between payments. Counts only.
- Why: bill payments that follow no calendar pattern still move the balance, so the forecast has to handle them some other way (entered by hand, or a planned item).

### 2026-10-02: Stored series and a dated decision log (3a-iii)
- Decision: detection is recomputed from the transactions; what I decide (confirm, not recurring, ended, a changed date or amount, a series added by hand) is a dated log replayed on top. Lost: editing a stored series row in place (a replay at an earlier date would use later decisions).
- An edit holds until the next payment posts, then the detected values take over. "Ended" holds until a later payment. A confirmed series keeps a snapshot of its pattern, so it stays if detection later loses it.
- Evidence is stored as transaction fingerprints. Detection runs as of the last posting date in the data, not today, so stale data does not make every series look ended.
- The stored table holds one JSON object per series, not one column per field as first planned: only the app reads it, and the fields are still changing.
- On the Recurring page a series whose payments show a replaced name keeps its own name hidden while pseudonymizing; a name I typed myself is shown.

### 2026-10-02: After the first real report (review)
- Bug fixed: the report's "card purchases that become recurring outflows" counted every row of every merchant series, bill payments included (41 rows; one of my 4 series is a bill payee with 11 rows). An override is now written only where the flow type really changes: an everyday card purchase, or a confirmed e-Transfer. The report also says what each series is paid by.
- Decision: no semester cadence. Two irregular payees are not a pattern.
- Decision: a bill payment to one of my own accounts (a credit card, a line of credit) is a transfer, not a recurring outflow. Before this, every outgoing bill payment was typed as a recurring outflow. I mark such a payee with `python -m lookahead.flows own-account`; the marking is dated, can be undone, and is part of the merchant decisions that flow typing already reads. Its category becomes Transfers.
- Decision: the forecast and the Time Machine read series through `series_as_of(date)`, which replays rows and decisions up to that date. The stored table is only a cache for the pages, so it stays one JSON object per series.
- Decision: a series I ended stays ended. A payment posting later is flagged for an alert, not used to switch the series back on. A confirmed series that detection no longer finds is kept and marked, so it can raise "expected payment not observed".
- Measurement rule: my confirm, reject and add counts on real data are the measurement for this step. If I change the detector after seeing them, scoring it again on the same data is development, not evaluation.

### 2026-10-02: Rent sent by hand: a loose-timing e-Transfer candidate (changed after the real-data measurement)
- This is development, not evaluation. My real counts for 3a (4 of 4 detected series real, 3 of 4 regular bills found) were taken before it and stay the measurement.
- Found: rent sent by hand as e-Transfers can fall more than 3 business days from any pattern.
- Decision: I did not widen the date tolerance for all series. A set of 4 or more same-amount outgoing e-Transfers with a monthly median gap that fails only on timing becomes a candidate on the Recurring page, matched with a 7 business day tolerance. It is never confirmed automatically and changes nothing until I confirm it. Lost: a wider tolerance everywhere (habits would pass as schedules again).
- Once confirmed it is a monthly series anchored on its typical day of the month. Every series now stores how many days early or late each past payment was, so the forecast can move the date by that spread in its simulated paths.
- Not offered for bill payments, card purchases, incoming e-Transfers, fewer than 4 payments, or gaps that are not about a month.
- Fixed in the report: the "why not a series" lines put amounts of different payees together and said "fewer than 4" without saying when 4 applies. Payments are now grouped per payee. The rule itself is unchanged: a payee always paid about the same amount needs 3 payments; one amount among others at a payee, or among e-Transfers, needs 4.

### 2026-10-02: Real-data result for recurring detection (one person)
- Measured before any detector change: 4 series detected, all real recurring payments. One subscription was split across a merchant rename, so 3 distinct series. Rent was not detected. Income was not detected, by design. This is one person's account, a case study.
- The loose-timing e-Transfer rule was added after this measurement. It found nearly all of the same-amount payments. That is development, not a second evaluation.

### 2026-10-02: Series category, continuation links, and what the pages show
- Decision: a confirmed fixed-amount series can carry a category, stored in the dated log. Every payment of the series shows it; other purchases at the same merchant keep their own. Why: a membership billed by a store where I also shop is a subscription, my shopping there is not.
- Decision: "this series continues an ended one" is a link in the dated log. The two series show as one, with one history, one category and the price change between them. Lost: merging the two merchant names, which would have put all my shopping at that store into the subscription. An automatic suggestion can come later.
- Decision: the Transactions page shows what series change: a card purchase in a series is a recurring outflow; an e-Transfer only once its series is confirmed. The category filter runs after these changes.
- Rule extended: a Prime name with a site domain between the two words counts as Prime (Subscriptions).
- Bug fixed: after confirming a series the Recurring page jumped to another one, because the selection held the series object, which changes when confirmed. It now holds the series key. The edit form is keyed by series, so it shows the selected series' own values.
- Editor setting: my editor tools are denied Read and Edit on `data/private/**`. Shell commands are left allowed, so append-only notes still work.

### 2026-10-06: Rent rule: allowed amounts and a window of days (entered by me)
- Found while reviewing my own data: rent is sent by hand, in more than one fixed amount and over a spread of days around the turn of the month. One median day (the loose-timing candidate) and one amount cannot describe that.
- Decision: a confirmed or hand-added outgoing series can carry a rent rule: a list of allowed amounts and a from-day and to-day, typed in the app and stored in the dated log (the latest rule wins; it can be removed). The window is named by the month it starts in; from-day greater than to-day wraps into the next month (and year). A from-day the month lacks (the 30th in February) is clamped to the last day of that month. Lost: treating the window as "the 30th plus a spread" (it cannot say "never on the 15th"), and detecting the amounts automatically (the amounts are my decision, so the app does not guess them).
- Evidence: outgoing e-Transfers of an allowed amount posted inside a window now count as payments of the series, so the second amount is found too. Rows posted after the date being replayed never count (rule zero), and a rule applies only from the day it was decided.
- Forecast rules (functions and tests exist; the forecaster itself is not built yet): the amount is always the larger allowed one (cautious, and said on screen); where one date is needed, the earliest day of the window that is still ahead; in simulated paths the date is drawn inside the window, each day weighing 1 plus my past rent payments on that day of the month (only those posted on or before the forecast date). The 1 keeps days I never paid on possible. A payment on the clamped last day of February counts as a 30th.
- Not done yet: a missed-month check for rent (that belongs to the recurring alerts, 3a-iv).
- The decision table gained a `rent` action; an older database is rebuilt on first connect, keeping every row and id.

### 2026-10-06: Real-data review of the Recurring page (3a-iii), one person
- Counted by hand before confirming anything and before any detector change: 5 regular payments, all bills or subscriptions. This is a case study of one account.
- The detector found 4 of the 5, with 0 false series. The fifth has only 2 charges so far, so it is below the detector's minimum and was added by hand.
- My decisions in the log: 5 confirms, 0 rejects, 1 add, 1 continuation link (one subscription under two merchant names), several rent-rule saves.
- Rent: outgoing e-Transfers, all in the rule; once it was saved, the rule added no payment the series did not already show, because it matched only payments inside its window.
- Two gaps found by using it: a hand-added series does not pick up its own past charges (shows none), and a series has no name of my own for display.
- Consequence for the rent rule: matching must not depend on the day (late payers exist). Design change planned next, see the next entry.

### 2026-10-06: Rent rule v2: any day counts, the window only places the next payment
- Why: the first version matched only payments inside the window, so a payment sent outside it was missed. A rule that only works for people who pay on time is the wrong rule.
- Decision: any outgoing e-Transfer of an allowed amount counts as rent, whatever the day. Each payment belongs to a rent cycle (the latest window that started on or before it, so a mid-month payment is late rent for the cycle before). At most one payment per cycle: if two land in one, the extra one goes to the nearest cycle with none (the earlier one if two are equally near, since a month paid late is more likely than a month paid in advance). Payments are taken oldest first and each looks only at earlier ones, so rows posted later never change an earlier assignment (rule zero; there is a test for it). Lost: counting only payments inside the window.
- The window is for prediction only: where the forecast places rent. A cycle whose window ended with no payment is skipped when choosing the next date; that case is for the recurring alerts (3a-iv), which should wait for the window to end plus a grace period.
- A series added by hand now finds its own past payments by name: a whole-word, any-case match on the transaction text, in the series' direction, the amount not needed (a price can change). Rows posted after the date being replayed are never used. Lost: matching on the amount too (a subscription changes price), and substring matching ("Rent" would match "Current").
- A series can be given a name of my own (a dated entry in the log). It changes the label only; the matching never reads it, and hand-added matching uses the name typed at the time. A name I typed is my own text, so it stays visible when text is pseudonymized.

### 2026-10-06: Product direction: nothing to configure; my manual entries are the answer key
- Direction: the user should never have to configure anything for the forecast to work. The forecast runs on what the app detects, with zero confirmations. Every setting I entered by hand during the review (the rent amounts and window, the link between two merchant names for one subscription, the hand-added subscription) should become something the app proposes, prefilled from history, with one "Looks right" button, and editing only when it is wrong. Nothing is ever applied without that click.
- To propose: allowed rent amounts from the clusters of amounts (the latest one used going forward) and the window from the day range that covers most past payments, with late payments matched by amount; a link for one subscription under two names; a subscription with only 2 charges shown as "possible", with lower confidence.
- To evaluate: forecast accuracy with zero confirmations against accuracy after the one-click review.
- My manual entries are the answer key for these proposals. I design them while looking at my own data, so a score on my data is development, not evaluation. The real test is on held-out synthetic personas that the proposal code never saw (planted late payers, renamed subscriptions, rent increases). Real and synthetic results stay in separate columns.
- Not built. Placement and size are proposed in the session; this entry records the direction only.

### 2026-10-06: Saved series refresh themselves; series categories; data date on the page
- Found by using it: after the code changed, the Recurring page still showed the series saved by the older code (rent and a hand-added series among them) until I clicked "Detect again". The saved series are a cache, and nothing said when it was out of date.
- Decision: the cache records the version of the code and the last posting date it was made from. The Recurring page rebuilds it by itself when either differs (older code, or newer rows imported). Whoever changes detection or the replay of decisions bumps `LOGIC_VERSION`. Lost: rebuilding on every page load (a detection run each time for no reason). Update: the Transactions page now rebuilds a stale copy too (it opens a separate writable connection just for that; everything else on the page stays read-only). Not covered: merchant decisions made on other pages wait for the next rebuild ("Detect again").
- A series' category is mine if I gave one; otherwise the most common category among its payments (the latest payment wins a tie), shown as "(estimate)". Why: 3b and the alerts need a category per series, and nobody should have to set one. A hand-added or confirmed fixed series (rent included) can be given one on the page.
- "Next expected" is relative to the last posting date in the data, not to today or the import date. The page now shows "Data last updated: <date>" and says what an expected date between the data's end and today means (due since then, not yet seen).

## Forecast (week 3b)

### 2026-10-06: 3b step 1: where a forecast starts
- Decision: a live forecast starts the day after the last posting date in the data, not today, from the closing balance of that day. The days between the data's end and today are forecast, and the page says when the data was last updated. Horizon: 30 days (no income is assumed, so there is no earlier "next payday").
- A replay as of a date (for backtests and the Time Machine) has the data complete through that date, so it starts the day after that date, with the balance of the last row posted by then. Nothing posted later is read (rule zero).
- Closing balance of a day = the balance on the first row stored for that day, because an export lists a day's postings newest first (the balance chart uses the same rule). This holds for a single export; the importer's reconciliation check still warns on the page if balances do not add up.
- The safety buffer is a setting (`app_settings`), default $200, editable on the Forecast page.

### 2026-10-06: 3b step 2: recurring payments on the forecast days
- Decision: every active, non-rejected series is used as it stands, detected or confirmed or entered. The forecast needs no confirmations; each placed payment says which of the three it rests on (a series with my own date or amount counts as entered).
- A payment is placed on its next expected date, then on each later date of its pattern inside the 30 days. One due on or before the last data day and not seen is expected on the first forecast day (it may be late, or posted after the export): the cautious side for a shortfall warning.
- Rent (a series with a rent rule): always the larger allowed amount; one payment per cycle; the date is the earliest day of the window still ahead. The most recent window that ended unpaid, with no later payment, is expected on the first forecast day (data may be stale, or the payment late). Older unpaid windows are treated as skipped months, so a month I did not pay is not expected again. Only rows posted by the data's last day mark a cycle paid.
- Known limitation: a stored series does not say whether it is paid by card, so later dates of an e-Transfer series use the card-posting-day rule and every other series uses the business-day rule. The difference is at most a day or two around Sundays and holidays.
- Income: only series that exist. If there are none, none is assumed, and the page says so.

### 2026-10-06: 3b step 3: the everyday-spending stream and two baselines
- Decision: transfers out that belong to no recurring series (e-Transfers to people, money to savings) stay in the spending stream, in the same bootstrap as card purchases and cash. They lower the balance, so dropping them would make the forecast optimistic. Lost: a separate stream for transfers. It adds a second method before any evidence that one is needed; it can be a declared experiment later (compare error with and without it).
- Decision: large one-offs are not excluded. They really happen, and leaving them out makes the forecast optimistic, the risky direction for a shortfall warning. Excluding them can be a declared experiment later.
- Fees stay out of the stream, as planned: they follow events (an overdraft, an incoming transfer, a statement date) and get their own model. Until that model exists the forecast does not include fees, and the page says so.
- Also left out: payments of any recurring series (they are placed on their own dates, so counting them again would double them), fully reversed pairs, income and incoming transfers (no income is assumed). Card returns and corrections that are not fully reversed lower the stream; other credits do not.
- Baselines (rule zero: only rows posted by the forecast date): (1) the average of the last 28 days times the days ahead; (2) the average for each weekday over the last 8 weeks, added up over the weekdays of the forecast days. A short history is averaged over the days it has; a weekday never seen uses the overall average. An average below zero (more returns than spending) is treated as zero. The paths are cumulative D(t, h), rounded to whole cents after summing.

### 2026-10-06: 3b step 4: the balance path and the Forecast page
- Decision: balance(day) = the balance when the data ends + recurring money placed through that day - cumulative everyday spending through that day. One path, an estimate; ranges from simulated spending paths are the next step and are not shown yet.
- A live forecast reads every stored row and every decision made so far (a confirmation I made today counts, even though the data ends earlier). A replay as of a date reads only rows and decisions made by then (rule zero); a test checks that a later row does not change an earlier replay.
- The page shows, before anything else, when the data was last updated and the day the forecast starts; if today is later it says the days in between are forecast, not observed. It lists every assumption: the starting point, how many recurring payments rest on detected, confirmed or entered series, how everyday spending was estimated (including transfers and large one-offs, and that bank fees are not forecast yet), income ("no regular income found; none assumed" when there is no income series), and each rent rule (the larger amount, somewhere in the window).
- The shortfall wording is "possible shortfall" and "this estimate", measured against the safety buffer (default $200, saved in settings). No alert record is stored yet; the alert comes with the simulated paths.
- Choosing between the two baselines is a control on the page (the weekday average is the default). Neither is evaluated yet: the backtest waits for the replay in week 4. A look at three synthetic personas showed plausible numbers only; it is not a result.
- Streamlit renders `$...$` in text as a formula, so dollar amounts in captions and warnings are escaped; tables and metrics show them plainly.

### 2026-10-06: Weekly drops in the forecast on my data: series rows ruled out, a gap fixed, evidence added
- Found on my data: the weekday baseline dropped the balance sharply on one weekday each week. My first suspicion was that rows belonging to a series were also counted as everyday spending.
- Tested, not found: a test with rent by e-Transfer in two amounts (one of them paid late, in mid-month), a detected bill and a series added by hand, all inside the last 8 weeks, shows that none of their payments reach the everyday history. The test stays as a guard. So series rows are not the cause.
- Found instead, and fixed: a bill payment that is in no series is typed "recurring outflow", and the spending stream only counted everyday and transfer rows, so such a payment was counted nowhere. It now counts. This can only raise the estimate; it is not the cause of too high a drop.
- Added the evidence: the Forecast page now shows "What's in everyday spending": the average for each weekday over the last 8 weeks, by type (card purchases, cash, transfers to people, transfers to my own accounts, bill payments in no series, other withdrawals, returns, large one-offs). Its rows add up to the weekday baseline's averages (a test checks that). A large one-off here is a single row of at least $300 and at least 5 times the median row of the window; it is only a label, and those rows stay in the forecast.
- What the weekday average does with one large row: it is averaged over the 8 times that weekday occurred, so one large transfer on one weekday becomes a high expected spend on that weekday every week. That repeats a one-off at a weekly frequency. It is a property of the baseline, not a bug; whether to damp it (for example a median, or capping a row at a multiple of the typical row) is a decision to make after looking at the table on real data, as a declared experiment.

### 2026-10-06: Big one-time rows leave the everyday streams (replaces "keep large one-offs")
- This replaces my earlier choice (3b step 3) to keep large one-offs in everyday spending. Why: with weekday averaging, a one-off is repeated every week, and a large one-off is not everyday spending. The worry behind the old choice (leaving them out makes the forecast optimistic) is met in two other ways: they are listed on the Forecast page as "Big one-time events (not forecast)", and a payment I know is coming can be added as a planned payment.
- One rule for both directions: a row of at least $300 that is at least 5 times the typical (median) row of the same direction over the last 13 weeks is big. A big outflow leaves everyday spending (both baselines and the table). A big deposit leaves the irregular-deposit stream: otherwise a large one-off deposit would make the futures too optimistic. Lost: a fixed dollar line (it does not follow a person's usual row), and leaving rows out by type (a big transfer to savings and a big tuition payment have different types and the same effect).
- Series payments, fees, refunds and netted holds are never big events; they are handled elsewhere.
- Not built yet (proposed): recognizing transfers to my own savings, and a planned-payment entry for a term payment such as tuition.

### 2026-10-06: Bank fees in the forecast, at their recent daily average
- Decision: until fees have their own model, the forecast takes off the fees of the last 28 days (less any reversed) spread evenly over the days ahead, in the point forecast and later in the simulated paths. The assumptions on the page say so, and say why it is only a stand-in: fees follow events (an overdraft, a statement date) that an average cannot see.
- Fees stay out of the everyday-spending stream and its table, so the two are never counted twice. Only rows posted by the forecast date are used.
- Lost: leaving fees out until the real model exists (the forecast would then be optimistic for anyone who pays fees).

### 2026-10-06: Simulated futures (500) and irregular deposits
- An account without a regular income series still has money in: deposits, incoming e-Transfers and transfers from one's own accounts can come in irregularly. Assuming none would make every forecast warn, which is useless. They are now their own stream, drawn from the account's own history (rows posted by the forecast date only): everything that is money in, in no series, not a refund and not a netted hold. Big one-time deposits are left out by the same rule as big payments.
- Decision: 500 futures. Each draws, for every week ahead, one whole past week (7 days, starting on the same weekday as the forecast's first day) from the last 13 weeks, and takes both that week's everyday spending and that week's irregular deposits. They come from the same week so that a week that was heavy because money came in stays that way. Lost: drawing deposits and spending independently (it breaks that link and makes the range wider than my history shows).
- Not rescaled to the point forecast. HANDOFF said "scaled to the point forecast"; I left that out: it would add a step that forces the futures to agree with a baseline I have not evaluated yet. The futures show what my own weeks did; the point forecast is a separate, simpler estimate. To revisit if the backtest says the futures are miscalibrated.
- Dates in a future: rent is drawn inside its window weighted by my past rent dates posted by the forecast date (an overdue window stays on the first forecast day); a payment of a series that is only detected moves by one of its own past offsets; confirmed and entered dates do not move. Fees are the same in every future (their recent daily average).
- Output: a range (the 10th, 50th and 90th percentile of the balance each day), the median with and without the irregular deposits, the share of futures below the safety buffer on at least one day (the shortfall probability, shown as it comes out, not tuned), the share below zero, and the median lowest balance. A fixed seed means the same data gives the same answer.
- Known limit: the futures can only draw from the last 13 weeks, so about a dozen different weeks; a quiet or unusual quarter shapes all of them.
- On the page: the chance of dipping below the buffer and the chance of going below $0 are shown as plain percentages with the count ("In N of 500 simulated futures..."), next to the same figure without irregular deposits. The message is a warning whenever at least one future dips and an information note when none does; there is no threshold to tune. The chart shows the 10th-90th percentile range, the middle future with and without irregular deposits, and the no-randomness estimate. Looked at on the three synthetic personas for plausibility only (one starts with about $1.50, so every future dips); it is not an evaluation. Calibration is measured in the week 4 backtest.

### 2026-10-06: Pass-through, large deposits, and what "typical row" means
- Pass-through: a big deposit that is paid straight back out is paired with the outflows that took it: some of the transfers, bill payments or big rows from its day to 14 days later that add up to 90 to 110 percent of it (the sum closest to 100 percent, then the fewest rows; at most the 12 largest candidates; each row is used for one deposit; series payments and small purchases are never candidates). The deposit and those outflows are all set aside, and shown on the Forecast page as a numbered pass-through. This only changes the streams for small transfers or bill payments that complete a pairing (they leave everyday spending); big rows were already set aside, so for them pairing changes what the page says.
- A big deposit with no such outflows is a "large deposit": it stays out of the deposit stream, because when it arrives cannot be predicted from history. The page lists it as not forecast and says: "You sometimes receive large deposits. If you expect one, add it as a planned deposit." (Planned items are not built yet; the page says so.)
- Found while testing: judged against the typical deposit alone, a single deposit in the window is never 5 times its own size, so one large deposit would never have been flagged, which is exactly the lump-sum-per-term case. Fix: the typical row is the typical deposit when there are at least 5 deposits in the last 13 weeks (a person who regularly receives a few hundred dollars should not have that flagged), and otherwise the typical row of everything, money in and out.
- The floor is now $500 (was $300) and the multiple stays 5. Decided on synthetic personas, not on my data: `python -m eval.deposit_rule` (30 seeds of each of 3 personas, a forecast date every 14 days, 3,479 ordinary deposit-windows and 636 planted large ones; the planted large deposits are persona C's family credit memos, series payments taken from the generator's labels). Results: with the typical deposit as yardstick, a multiple of 5 or less catches 100 percent of large deposits and a multiple of 3 flags 3 ordinary ones; multiples of 10 and 20 miss 13 and 51 percent of them. With everything (money in and out) as yardstick the floor decides: for a floor of $100 to $300 and a multiple of 3 to 10 it flags 27 to 86 percent of the ordinary deposits; at $500 and above it flags none. So the data rules out a multiple above 5 and a floor below $500, and cannot tell $500 from $1,000.
- Caveats, plainly: this is development, not evaluation. The ordinary synthetic deposits are small and the large ones are about ten times larger by construction, so the plateau is easy. Generator v1 (week 5) should add ordinary deposits above $500, mid-size irregular deposits, and payments only partly funded by the deposit, and the rule should be re-tested on its held-out personas.
- The pass-through band was not tuned. On the generator's planted funding-and-payment pairs the payment is a little more than the funding (the family covered most, not all): the 90 to 110 percent band finds 0 of 30, a band wide enough to reach the pair finds 30 of 30, and no band pairs a deposit that is not a planted pair. I kept 90 to 110 as asked. Widening it would change only the labels shown on the page; whether to widen it is a decision for later.

### 2026-10-06: Known limitation: irregular income in lump sums
- A design limit, for anyone: a large deposit that is not a pass-through is left out of the deposit stream, so the shortfall probability overstates the risk for a person whose money comes in occasional large deposits, until they say a deposit is expected.
- Not tuned on this case. The remedy is a planned deposit (a date and an amount I enter, labelled "entered"), planned for the what-if step, and later a runway view for people who live on one sum per term. The Forecast page already tells the person that large deposits are not forecast.
- Also seen in the same check: a pass-through that was not recognized. It is now recognized as one.
- Update to the entry above on big one-time rows: the floor is $500 (was $300), and the typical row is described in the pass-through entry.

### 2026-10-06: Pass-through band widened to 85-115 percent (decided on synthetic data)
- Decision: outflows within 14 days that add up to 85 to 115 percent of a big deposit make it a pass-through (it was 90 to 110). Decided on synthetic personas, not on my data: on the generator's planted funding-and-payment pairs the 90 to 110 percent band found 0 of 30, and 85 to 115 percent found 30 of 30, with no other big deposit paired in any band (`python -m eval.deposit_rule`). This replaces the 90 to 110 percent in the pass-through entry above.
- Caveat, plainly: all 30 planted pairs are the same pair. The generator's dates and amounts do not vary by seed, and the payment sits just inside the wider band. This is one pair type, not 30 independent cases. The edge is fragile: it is evidence that 90 to 110 is too tight for a deposit that covers most but not all of a payment, not that 115 is the right limit. Generator v1 should vary the funding share (see the list below).
- What it changes: only labels on the Forecast page, and small transfers or bill payments that complete a pairing (they leave everyday spending). Big rows were already set aside either way.

### 2026-10-06: Planned items: money in or out that I enter
- Decision: a planned item is a payment or a deposit I know is coming (a term payment, an expected deposit, a purchase): a day, an amount (money in is positive) and what it is. It is placed exactly on its day, labelled "entered", and is the same in every simulated future (nothing guessed, nothing moved). Only days inside the forecast period change the forecast; other items are listed with the reason ("the day has passed" or "after the last forecast day").
- Dated, so replays stay honest: an item carries the day I entered it, and a forecast as of an earlier date ignores it (rule zero; tests check both the point forecast and the simulation). Removing an item takes effect from the day it was removed and earlier replays still see it. Changing an item is removing it and entering it again: rows are never rewritten.
- Lost: giving a planned item a chance of not happening, or a range of days. It would add a second kind of uncertainty before the what-if step exists; "delay income by N days" in 3d covers the late-deposit case. Also lost: treating a term payment as a new recurring cadence (it is not one; the next is outside the 30 days).
- A planned item is not a history row, so the big-row rule and pass-through pairing never apply to it.

### 2026-10-06: Generator v1 (week 5): what real use showed it needs
- Ordinary deposits above $500 (a parent sending a regular sum), and mid-size irregular deposits ($500 to $2,000), so the big-deposit rule gets a harder test than the current ten-times gap between ordinary and large deposits.
- Deposits that cover only part of a payment, with the funding share varying across personas and seeds (for example 70 to 130 percent), so the pass-through band is tested on more than one ratio.
- A persona with few deposits (one lump sum per term and nothing in between), to test the rule that uses the typical row of everything when there are fewer than five deposits.
- Large deposits that are not paid out (the money stays and funds a whole term), and savings moves that pass through quickly, as separate cases.
- Report big-deposit and pass-through results per income type, as for the other income results.

### 2026-10-06: Evidence for the deposit part of the range, and a chart with no gap
- Found on my data: after planned items, the 10th to 90th range still jumped sharply in one week. The Forecast page now has "What's in irregular deposits": one row for each of the 13 weeks the futures can draw from (the exact weeks, starting on the forecast's first weekday), with the deposits in the stream by type and, in the last column, what was set aside that week (big deposits and pass-throughs, which are never drawn). The weeks add up to the deposit stream (a test checks it). A week with a large deposit in the stream adds it to about one in 13 of the weeks ahead; that is the behaviour this table makes visible, not a bug in the draw.
- Every forecast line and the band now start at the last observed day's closing balance, so the chart has no gap between the recent balance and the forecast. The balance stays flat from that day until the first forecast day, like any balance between postings.

### 2026-10-06: Common random numbers for scenarios
- Decision: every simulated week and every placed payment draws from its own random stream, named by the seed, the future and what is drawn (a payment's stream is named by its series and the day it was first placed). Before, one sequence was shared, so changing or adding one payment shifted every later draw and a scenario would differ from the original by luck as well as by the change. Now the original and a scenario use the same draws, so the difference between them is the change itself. Tests: the order of the items does not matter; adding an item does not change the other items' draws; a spending cut can only raise every point of the range.
- A cut in everyday spending for a period (a percent of each day, or dollars a day, never below zero; returns left alone) is applied to the drawn spending, so the draws themselves do not change.
- The forecast keeps what its futures were made from, so a scenario can change one part and run the futures again.

### 2026-10-06: The what-if scenario engine
- Decision: a scenario changes one part of what the futures were made from and runs the same futures again (same draws), so each difference is the change itself. It is conditional (what would happen if), nothing is saved, and the original is never altered. Controls: spend less by a percent or by dollars a week for a period (dollars a week is spread evenly over the days, never below zero a day); a purchase or deposit added for this scenario only; money in arriving N days later (0 to 60; income series and planned deposits, never a payment); moving a payment to another day (it then stays exactly there; it cannot move to a day that has passed); and "no other money expected" (irregular deposits off, so only income series, planned deposits and deposits added in the scenario count as money in).
- What "delay money in" does not cover: irregular deposits drawn from history (they have no date to move). A late planned deposit is the case it is for.
- Reported as differences: the change in the chance of dipping below the buffer, the chance of going below $0, the median lowest balance and the middle future's balance on the last day.
- Tests: an empty scenario gives exactly the original; "no other money" gives exactly the original's without-deposits median; a planned purchase lowers every point of the range by exactly its amount from its day; delaying a deposit lowers the days in between by exactly the deposit and nothing else; moving a bill changes only the days between the old and new dates; a cut in spending never lowers any point of the range. I broke the engine four ways on purpose and the tests failed each time.
- Lost: changing the point forecast (the no-randomness line) in a scenario. The comparison is on the futures; a second set of numbers would only add a way to disagree with them.

### 2026-10-06: The what-if section of the Forecast page
- The section "What if...?" has the five controls side by side; nothing is run until something is changed. Once it is, the page says in plain words that this is a conditional scenario ("what would happen if these changes were made ... not a forecast of what will happen"), lists the changes, and shows four numbers each with its change from the original: the chance of dipping below the buffer, the chance of going below $0, the median lowest balance, and the middle future's balance on the last day. The original numbers are written beneath. The chart shows both ranges and both middle futures from the same starting balance.
- A bad scenario (a cut of 0 percent, a deposit with no description, a payment moved into the past) is explained in a warning and not run. A purchase or deposit added in a scenario is not saved; saved items are the planned items further down the page.
- One scenario at a time, with at most one added item and one moved payment. More than that is what planned items are for; the limit keeps the page readable.

## Design

### 2026-10-07: The look of the app: light, green top bar, Manrope (replaces the dark-green attempt)
- Why: the first attempt (dark green and navy, Inter, a sidebar wordmark) was dropped before it was reviewed further; the target is an exported design mockup (a Forecast page and a Recurring page with made-up data), no longer kept in the repository. The first attempt's commits were replaced, not layered on: its fonts, wordmark, favicon and `docs/design/forecast-*` screenshots are deleted. Presentation only: no logic changes.
- What lost: dark theme (the design is light; dark would also have needed its own chart colours); Inter and JetBrains Mono (the design uses Manrope); Streamlit's sidebar page list (the design has a top bar).
- Built-in first: `.streamlit/config.toml` carries the colours, status colours, radius, chart colours and the bundled Manrope file. One module, `app/theme.py`, holds the palette, money and date formats, the HTML pieces (top bar, tag, callout, "coming up" list, result panel, chart legend), one CSS string and the Plotly chart template. Pages call these; none repeats a colour.
- Palette (from the design): top bar `#17603E`, page `#F6F8F6`, cards `#FFFFFF` with `#E2E8E4` borders, text `#14201A`, muted `#55665C`, green `#1F7A4F`, blue `#2E5F9A`, amber `#8A5300` on `#FDF3E1`. One deviation: the design's faint text `#7A8A80` is 3.4 to 3.6 to 1 against its backgrounds, below WCAG AA (4.5), so faint text is `#66756C`. A test checks every text and background pair.
- Fonts: Manrope (Latin subset, variable weight, tabular figures) is a file in `app/static/fonts` with its SIL licence, served by the app (`server.enableStaticServing`). Nothing is fetched from the internet; the screenshot script records every request and a test fails if a URL or font import appears in the theme.
- Navigation: the top bar is HTML with plain links, not `st.page_link`, which cannot run inside Streamlit's test harness. A click is a full page load, which I have not checked for keeping the account chosen in Settings. Streamlit's own sidebar navigation is switched off. Settings (data source, account, pseudonymize, method, buffer) stay in a collapsed sidebar opened by a "Settings" pill.
- Forecast page: title, one-sentence callout (amber for a possible shortfall, green when none), four number cards, the chart (actual line dark, middle future green, 80 percent band translucent blue, buffer dashed amber, the low point marked and labelled, the forecast area lightly shaded), "What if..." as pill buttons with a before and after result, "Coming up", and the explanations folded at the bottom. The first-attempt screenshots showed one bug that the keys now avoid: a container keyed like a card gets the card style, so colour-only wrappers are keyed `tone-*`, cards `card-*`.
- Charts: lines differ by dash style and width, not colour alone. The old zero line, the on-chart "safety buffer" label and the chart title are removed (the legend names the buffer). Two builder roles are hidden in the new look (the no-deposits line and the no-randomness estimate); their data and tests are unchanged.
- Deviations from the design, on purpose: extra links in the top bar (Label review, To check) because those pages exist; date pickers show numbers (Streamlit limit); tables size themselves; the design's "Settings" is a pill instead of a gear.
- Test edits (for the new labels only): the Forecast page test now looks for "Data to" and a card named "Balance on <date>" instead of "Data last updated:" and "Balance when the data ends". One earlier edit stands (the events table is picked by its exact columns, since irregular deposits now come before it).
- Dev only: Playwright (with the Edge already installed, no browser download) takes screenshots of the Synthetic demo, never of my data; it is in `requirements-dev.txt`.
- Screenshots are in `docs/design` (calm and shortfall; desktop, laptop, phone, and one with every expander open). Synthetic data only.

### 2026-10-07: After the Forecast review: layout fixes, "Rent", received planned items, the other pages
- Top bar: it was `position: fixed` with a guessed top padding, so wherever the bar wrapped to two or three rows (a laptop at 125 percent scaling, or the sidebar open) it covered the title. Now the bar is the first block of the page and sticky: it takes its own height, so nothing can hide under it. Full width comes from a huge spread shadow clipped above and below. The block that carries the stylesheet took 24 pixels of gap above the bar until it was hidden. What lost: a bigger fixed padding (breaks again at the next width), and measuring the bar with a script (fragile).
- Settings panel: 380 pixels wide, labels on whole words, the buffer number on one line.
- Data and account survive top-bar navigation. They did not: a plain link opens a new browser session and Streamlit forgets widget values. Fix: the three choices (data, account, pseudonymize) are written into the address (`?data=...&account=...&text=...`), read back before the widgets are drawn, and added to every top-bar link and Home button. Considered and not used: `st.page_link` (it cannot run in Streamlit's test harness), and a database table of "last choice" (state outside the browser for a purely visual need). The address holds only the account label and two flags, no transaction text. Tests check the helpers with a fake `st`; the browser check (Forecast, pick synthetic-C, click Recurring: the sidebar says synthetic-C) was run by hand in the screenshot probe.
- A series with a rent rule is called "Rent" unless I named it (`LOGIC_VERSION` 2, so saved copies rebuild). It applies wherever names show (Coming up, Recurring, tables, the forecast items) because it is set where the series is built, and a rename still wins; taking the name back gives "Rent" again, removing the rule gives the detected name.
- Planned items vs real data. A planned item is "received" when a real transaction has the same direction, an amount within 5 percent of it, a posting date within 7 days of the planned day, and was imported on or after the day the item was entered (an older row cannot be the money I was waiting for). The forecast stops placing a received item, so it is never counted twice; the item stays listed and says "received". One transaction matches one item: the closest pairs (days apart, then amount difference) are settled first. Rule zero: a replay only sees rows posted on or before its date. Chosen numbers are mine, not measured: 5 percent and 7 days come from the request; the import-date condition is an addition to stop an old look-alike row from consuming a new item. Limit: an item and a recurring series that both match the same real payment are each judged on their own. Status wording is "received" for money out too, as asked.
- Recurring page: cards in a grid (a see-through button over each card makes the whole card one click, and keeps it focusable), details panel with Confirm, Edit, Not recurring, It ended, a four-column payments table, and category, name, rent rule and linking folded under "More about this series". "Looks right, confirm all" confirms every series that is still "detected" and active, one decision each written exactly like a single confirm, so the log cannot tell them apart; ended and rejected series are left alone, and the number "to review" in the subtitle counts exactly those. "Add one I pay" opens the old add form in the panel. Categories that are only estimates say so on the card.
- Home: one sentence, a status card (data date, account, transactions, confirmed recurring of all found), links to the main pages. Transactions: search and filters on top, a table of date, merchant, category tag, amount and balance (first 50, "Show more"), every other column behind Details. Label review and To check: the same top bar and cards, plain headers, merchant names escaped (a test feeds a script tag). The `docs/design` screenshots for every page are synthetic demo only.
- Test edits, all for the new look and listed so they can be checked: Recurring tests read cards and click "Open <name>" instead of a table and a selectbox, the edit form opens on "Edit", the add form opens on "Add one I pay", "Mark as ended" is now "It ended"; Transactions tests use the search and filter widgets on the page instead of the sidebar, and a new test pins the trimmed table; the Forecast tests were edited earlier in this entry's predecessor. No assertion about numbers was loosened.

### 2026-10-07: 3c, the shortfall alert (provisional rule, not yet validated)
- **The rule is provisional, not yet validated.** Alert when the share of simulated futures that dip below the safety buffer is above 20 percent, or the median over futures of each future's lowest balance is below the buffer. The numbers (20 percent, strictly above; a balance exactly at the buffer is not below it) are the provisional ones I was given, not measured. The Forecast page says "provisional, not yet validated" on the alert card, and the week 4 backtest replaces the rule with a threshold chosen on validation periods only (HANDOFF section 08). Until then no precision or recall is claimed for it.
- **Episodes.** An episode is a run of alerts about the same predicted low: a new look belongs to the open episode when its low day is within 10 days of the open episode's. A different low (further away) closes the old episode and opens a new one; the condition going away closes it, and a later shortfall is a new episode.
- **Suppression.** A look is "notified" (worth telling me) when the episode is new, or when something material changed since the last alert, not since the last look: the low moved by $100 or more, the chance crossed a band (20, 50, 80 percent), or the low day shifted by 3 days or more. Measuring from the last alert stops a slow drift of many small steps from staying quiet forever (a test pins this). Looking again at numbers already recorded adds nothing, so reloading the page is harmless. The $100, 3 days and 10 days are provisional too, picked to be noticeable on a $200 buffer.
- **Dismissing.** I can dismiss an episode. It stays quiet for small moves and for good news (still tracked and listed), and it speaks again only if it gets materially worse in kind: the chance enters a higher band (20 to 50 percent, 50 to 80, or 80 and up), or the middle future's low drops below $0 when it was not below $0 at the last alert. Measured from the last alert, as for open episodes. When it speaks again it is shown again, and that look becomes the new baseline (dismissing it again starts quiet again). A low that was already below $0 when I dismissed it does not re-alert for going lower, and a $100 move inside the same band does not either. This replaced my first version, where a dismissed episode never re-alerted, after review: that hid the one thing worth hearing. I can also bring a dismissed episode back by hand. The band edges and the $0 line are provisional like the rest.
- **Evidence** is frozen with each look: the lowest-balance day and amount, the chance (with and without irregular deposits), the five largest payments placed before the low (with whether each is detected, confirmed or entered), how many assumptions are of each kind, the big one-time rows set aside, the data date, the forecast start, the everyday spending estimate and method, and the buffer. Names of series that show a person's name are hidden on screen as everywhere else.
- **Where it runs.** The Forecast page records a run each time it shows the default estimate (the weekday average); another estimate chosen in Settings is not recorded, so trying things out cannot open or close episodes. Scenarios never record. The callout on the page follows the rule: amber "Possible shortfall" when it alerts, green "On track" otherwise (with a sentence when a few futures still dip). Rule zero: a run uses only its forecast, which was built from rows posted on or before its own data date; runs must be recorded in order of data date, which a replay does.
- Storage: `alert_episodes` and `alert_events` in the same database (schema in `lookahead/db.py`); an event row is never edited, an episode row only to close or dismiss it.
- Not done on purpose: other alert types (duplicate, amount changed, new recurring, expected payment not seen), emailing or notifying anything, and any claim about accuracy.

### 2026-10-07: Test hygiene, unclosed database warning
- Python 3.13 warns when an unclosed SQLite connection is garbage collected, at a random later moment, so the warning appeared in `test_embeddings` although the leak was in another test (fixtures and app pages leave connections open). It did not reproduce when that test ran alone. A shared autouse fixture in `tests/conftest.py` now records every connection and closes it after each test.

### 2026-10-07: Demo preparation (presentation only)
- `python -m lookahead.demo reset` rebuilds the synthetic demo database from the persona files, so a take starts clean: no alerts, planned items, decisions, corrections or changed buffer; the recurring series are detected and saved (Home can count them before Recurring is opened) and still all "to review"; the label-review splits are frozen so that page opens on a merchant instead of a command to run. It deletes the file, or, when the running app holds it open (Windows refuses), empties every table in place. It refuses a path inside a folder called "private" and anything that is not a .db file. The Forecast alert for synthetic-C is not stored by the reset: it is recorded the first time the page opens, so it is fresh.
- Checked every page for each synthetic account by loading it in a browser and scanning all its text (with every section opened) for file paths, names, e-mail addresses, long digit runs, error words and empty pages. Found and fixed: the Label review footer told a synthetic-demo viewer not to screenshot "real merchant text" (now shown only for My data); Home said "None found yet" for the recurring count until Recurring had been opened (the reset now saves the series); a jump link ("See why") scrolled the heading under the sticky top bar (anchors now keep a margin). Left as they are on purpose: people's names show as "(hidden)" because pseudonymize is on by default, and the demo says it is made-up data.
- The click path is in `docs/DEMO.md`.

### 2026-10-08: Week 4 backtest design, fixed before any backtest runs (generator v1 persona mix)
**Superseded on the same day by "Persona mix revised: four student archetypes" below. Kept for the record; none of its personas (ids `v1-...`) is used any more. The origins, the split and the laptop limits in this entry still stand.**

This entry is written before the generator v1 code and before any backtest has run. **The mix, the parameter ranges, the held-out list, the origins and the split below are final.** Changing any of them after seeing a result needs a new entry that says so and starts a new set of results; the old results stay. The only allowed change to the generator afterwards is fixing a bug (a persona that breaks its own rules, such as a balance below the overdraft limit), recorded here with the number of personas affected.

- **Why a mix at all.** The three hand-written personas (A, B, C) each show one behaviour and say nothing about typical people; with 4 months they give about 15 forecast periods in total. Generator v1 draws 30 personas from 7 archetypes, each with 12 months of history (2025-10-01 to 2026-09-30). A, B and C stay as demo personas and are **development only**; they are not in the backtest.
- **The 30 personas** (ids `v1-<ARCH>-<nn>`; each persona's numbers are drawn once from its own seeded stream, from the ranges below, and never re-drawn):

| Archetype | n | Held out | Income | Rent | Everyday spending (card purchases per day, merchants) | Opening balance | Buffer |
|---|---|---|---|---|---|---|---|
| SAL salaried, comfortable | 5 | 04, 05 | biweekly pay $1,500-$2,600 | $900-$1,500 | 0.9-1.3 (worker merchants) | $1,500-$4,000 | $300 |
| SEM salaried, semimonthly | 3 | 03 | pay on the 15th and last day $1,300-$2,200 | $800-$1,400 | 0.9-1.3 (worker) | $1,200-$3,500 | $300 |
| PTS part-time student | 4 | 04 | biweekly pay $450-$900 | $500-$800 | 1.0-1.4 (student merchants, delivery) | $300-$900 | $150 |
| TIGHT tight budget | 7 | 06, 07 | biweekly pay $1,100-$1,700 | 50-62 percent of monthly pay | 1.1-1.5 (worker) | $100-$500 | $100 |
| LUMP lump-sum income | 6 | 05, 06 | no paycheque: one large credit memo at the start of each term (Jan, May, Sep), $6,000-$12,000, set to tuition plus 4 months of costs times a factor drawn from 0.80-1.15 | $600-$1,100 | 1.0-1.6 (C-like merchants) | $3,000-$8,000 | $500 |
| FREE freelancer, irregular deposits | 3 | 03 | e-Transfers every 5-20 days, $200-$1,500 | $700-$1,200 | 1.0-1.4 (worker) | $500-$2,000 | $200 |
| AWAY student away in summer and December | 2 | 02 | monthly bursary $900-$1,400 on the 1st | $500-$800 | 1.0-1.4, times 0.15 in July, August and December | $400-$1,200 | $200 |

  Every persona also has 2-4 recurring bills or subscriptions (phone, internet, a subscription, sometimes hydro and a savings transfer) drawn from short fixed lists, and 1-3 larger one-off purchases in the year. LUMP personas pay tuition ($3,000-$6,000) a few days after each deposit.
- **Why these archetypes.** TIGHT (7) and LUMP (6) are the two that can produce real shortfall episodes (small buffer and frequent near-zero balances; one deposit a term and no paycheque, as for a student paid by family or a loan), so that alert recall and false alarms are measured on more than a handful of episodes. SAL and SEM (8 together) are the calm majority a threshold must not bother. PTS, FREE and AWAY cover thin pay, irregular deposits and seasonal quiet. I expect the numbers for LUMP to look bad (the forecast cannot know when a deposit arrives, see "Known limitation: irregular income in lump sums"); they are reported per archetype, not hidden in an average.
- **Held out: 10 of 30**, listed in the table (the last one or two of each archetype). Held-out personas are never used to choose a threshold, tune a rule or pick a method; code that tunes refuses their ids. They are used once, at the end, for the reported test numbers. The 20 other personas (and A, B, C) are the development set.
- **Origins.** Weekly, every Monday from 2025-12-01 to 2026-08-31 (40 origins; I first wrote 39, an arithmetic slip: 273 days is exactly 39 weeks, so both end dates are origins; the dates themselves did not change), so each origin has at least 8 weeks of history behind it and a full 30 observed days after it. The forecast at an origin uses only rows posted on or before that date: the recurring series are re-detected from those rows, decisions are replayed as of that date (the backtest makes none, so every series is "detected"), planned items are none. The safety buffer is each persona's own value from the table, fixed for the whole run (the app stores one buffer, not a dated history).
- **Split.** Thresholds and any tuning use **development personas only, all origins** (validation). The held-out personas get one run at the end. Within a persona the origins overlap (30-day horizons every 7 days), so results are reported by persona and period counts, not as independent rows, with n (personas, periods, shortfall episodes) beside every number.
- **Real data.** The same runner will run on my real account through a script I run myself: it prints counts and summaries only, never rows, and its numbers go in a separate column from the synthetic ones. One person's history is a case study.
- **Clarification, written after the generator code and before any backtest.** The exports of all 30 personas were generated once and their histories looked at as data (rows, lowest balance, closing balance), not as forecast results. Scheduled payments (rent, tuition, bills) post even past the overdraft limit by the generator's v0 design, so some TIGHT, LUMP, PTS and FREE personas go well below the $500 overdraft limit (the lowest is about -$2,500) and a few close the year negative. I count that as the intended behaviour (people who are short), not a bug, and I changed nothing in the mix or the ranges after seeing it. So "a balance below the overdraft limit" above is not a bug criterion; a bug is a persona that violates a rule the generator documents (for example a purchase approved past the limit, or an export that does not reconcile).
- **Laptop limits.** One persona at a time, each origin's result saved to disk as it is made (a stopped run resumes where it left off), `--quick` mode first (3 personas, 4 origins, 100 futures), the full run overnight.

### 2026-10-08: Week 4 step 1: the backtest runner (`eval/backtest.py`, `eval/backtest_real.py`)
- What it does: for one persona at a time it imports the 12 months into a throw-away database, builds the forecast "as of" each Monday origin with the same code the app uses (`build_forecast` with `as_of`), and reads the balances that really followed from the later rows. One JSON line per origin is written at once (resumable; the throw-away database is deleted before the next persona). Records hold the forecast (p10, p50, p90, the single-path estimate, each future's lowest balance, the shortfall probability, the median lowest balance) and what happened (daily balances, lowest, first day below the buffer, below zero), labelled `synthetic` or `real`, with archetype and held-out flag. No error or alert metric is computed in step 1: that is step 4, from these saved records, so the metrics can be changed without re-running forecasts.
- Each future's lowest balance is now kept in the simulation (`lowest_cents`, sorted). Without it only the buffer in force at run time could be checked; with it the probability of dipping below any buffer (and below $0 for the real account's overdraft episodes) can be computed afterwards, so the alert threshold can be chosen from saved records. The probability computed from the kept lows equals the stored one (tested).
- Rule zero, tested with mutations: deleting every row after the origin, or rewriting every later amount and balance, leaves the forecast byte-for-byte the same; recurring series are re-detected from the rows up to the origin; a decision dated the day after the origin is ignored and one dated the day before is used (the backtest itself makes none, so every series is "detected"). When I made the runner ignore the origin on purpose, four of these tests failed.
- Speed, measured: one forecast at 500 futures takes 0.1 to 0.2 seconds on these personas, a `--quick` run (3 personas, 4 origins, 100 futures) 2.5 seconds, so the full development run (20 personas by 40 origins) should take a few minutes. I did not rewrite the futures as array code: it would not change a run that short, and the current code keeps the per-payment random streams that scenarios rely on.
- Held-out personas run only with `--held-out`, and the runner refuses them otherwise. The origin count is 40 (corrected above). Results made with other settings (futures, seed, method) are refused rather than mixed.
- Real data: `eval/backtest_real.py` is run by me. It opens the database read-only (a test checks the file's bytes are unchanged), uses the account's own saved buffer, writes under `data/private/backtest/` in files named account-1, account-2, and prints only counts (accounts, origins, how many were followed by a balance below the buffer or below zero). I run it myself; it is never run on real data by anything else.

### 2026-10-08: Persona mix revised: four student archetypes (mix v2, ids `v2-<ARCH>-<nn>`)
**Why it was revised, stated exactly.** I revised the mix after researching Montreal student finances. The revision came after a `--quick` run that printed counts only, and before any forecast error, coverage or alert result was seen. What I had looked at before the revision: each generated history's lowest and closing balance (noted above), and the `--quick` run's counts, which included that 6 of its 12 origins were followed by a dip below the buffer and 5 by a dip below $0. No error, coverage or alert metric had been computed. From here the v2 mix, ranges, flags and held-out list below are final, under the same rule as before: a change needs a new entry and a new set of results.

**What changed.** The student-like archetypes were merged down to four, chosen by the cashflow shape one chequing account sees, not by label:

- **INTL-FAM** (international, reliable family funding; code `IFAM`) replaces half of LUMP. A family deposit arrives before each semester's tuition payment (this exercises the pass-through pairing), plus a steady monthly allowance and 4-6 recurring bill series (phone, internet, transit, insurance; rent and subscriptions are separate).
- **INTL-TIGHT** (international, stretched family; `ITIGHT`) replaces the other half of LUMP. Irregular and sometimes partial family transfers, part-time work, small buffer.
- **DOM-HOME** (domestic, living with parents; `DHOME`) replaces PTS. Little or no rent, wages, more discretionary spending and subscriptions.
- **DOM-RENT** (domestic, renting with roommates and working; `DRENT`) replaces AWAY and takes the student side of TIGHT. Shift wages that dip during exams; three of five are funded by loans or bursaries paid on set dates.
- Non-student archetypes stay with fewer personas: SAL 5 to 4, SEM 3 to 2, TIGHT 7 to 4 (the non-student tight-budget worker), FREE 3 to 2. The summer-and-December quiet months of AWAY become a per-persona setting (below).
- v0 personas A, B and C stay development-only demos.

**The mix: 30 personas, 10 held out** (same share as before):

| Archetype | n | Held out | Buffer | Opening balance |
|---|---|---|---|---|
| IFAM | 4 | 04 | $300 | $2,000-8,000 |
| ITIGHT | 5 | 04, 05 | $100 | $2,000-8,000 |
| DHOME | 4 | 04 | $150 | $300-1,500 |
| DRENT | 5 | 04, 05 | $150 | $300-2,000 |
| SAL | 4 | 04 | $300 | $1,500-4,000 |
| SEM | 2 | 02 | $300 | $1,200-3,500 |
| TIGHT | 4 | 04 | $100 | $100-500 |
| FREE | 2 | 02 | $200 | $500-2,000 |

SAL, SEM, TIGHT and FREE keep the parameter ranges recorded in the superseded entry. Held-out personas are never used to tune anything (code refuses their ids) and run once, at the end. Every student archetype has at least one held-out persona.

**Fixed per-persona assignments** (so each archetype has both cases, not left to a random draw):

| Persona | Tuition per semester | Paid from chequing | Other |
|---|---|---|---|
| IFAM-01 | $5,000-6,500 (the French/Belgian rate) | yes | one payment |
| IFAM-02 | $7,000-12,000 (partial scholarship) | yes | two installments, summer term |
| IFAM-03 | $14,500-21,500 (standard international) | **no** (the family pays the university) | no tuition rows and no tuition deposits |
| IFAM-04 (held out) | $22,000-30,000 | yes | two installments |
| ITIGHT-01 | $15,000-30,000 | yes | |
| ITIGHT-02 | $15,000-30,000 | **no** | no tuition rows and no tuition deposits |
| ITIGHT-03 | $15,000-30,000 | yes | summer term |
| ITIGHT-04 (held out) | $15,000-30,000 | yes | summer term |
| ITIGHT-05 (held out) | $15,000-30,000 | yes | |
| DHOME-01 | $0 (CEGEP) | n/a | no rent |
| DHOME-02 | $1,500-3,100 (university, Quebec rate) | yes | rent contribution $150-400, summer term |
| DHOME-03 | $1,500-3,100 | no | no rent, away in summer and December |
| DHOME-04 (held out) | $0 (CEGEP) | n/a | rent contribution $150-400 |
| DRENT-01 | $1,500-2,500 (Quebec rate) | yes | loans and bursaries, summer term |
| DRENT-02 | $1,500-2,500 | no | no aid, away in summer and December |
| DRENT-03 | $1,500-2,500 | yes | loans only |
| DRENT-04 (held out) | $4,800-7,500 (out of province) | yes | no aid, summer term |
| DRENT-05 (held out) | $4,800-7,500 | no | loans and bursaries, away in summer and December |

"Paid from chequing: yes" counts are the ones I asked for (IFAM 3 of 4, ITIGHT 4 of 5, DHOME 1 of 4, DRENT 3 of 5). Summer term (a third of the 18 student personas): IFAM-02, ITIGHT-03, ITIGHT-04, DHOME-02, DRENT-01, DRENT-04. Two semesters (Fall, Winter) for everyone, plus the summer term for those six. Tuition falls due around Jan 20 (Winter), May 20 (summer persona only) and Sep 12 (Fall), each date drawn within 5 days.

**Cashflow shapes** (all numbers are per persona, drawn once from its own seeded stream):

- **INTL-FAM.** The family deposit is **not** equal to tuition: it is 100-140% of the tuition due, arriving 1-30 days before the first payment. Some personas pay tuition in two installments (half each, the second 30-45 days after the first). Because of the 100-140% and the 1-30 days, many deposits fall outside the pass-through rule's window (85-115% of the deposit paid back out within 14 days), so the rule is tested by data that was not built to match it. When "paid from chequing" is no there is no tuition payment and no tuition deposit. Also a monthly allowance of $800-1,800, rent $700-1,400, 4-6 recurring bill series.
- **INTL-TIGHT.** Tuition $15,000-30,000 per semester when paid from chequing. Family transfers before each tuition payment cover 60-105% of it (often partial), plus irregular transfers of $300-2,000 every 15-45 days. Part-time work at $16-17 an hour: at most 24 hours a week during term, 25-40 in breaks (Dec 20 to Jan 4, and May to August unless the persona has a summer term); about 85% of gross reaches the account. Rent $500-800, 3-5 bill series.
- **DOM-HOME.** Wages at $16-20 an hour, 8-20 hours a week; purchases 1.3-1.9 a day; 2-4 bill series; 3-7 subscriptions.
- **DOM-RENT.** Shift wages at $16-21 an hour, 12-30 hours a week, falling to 50-70% of that in exam periods (Dec 6-20 and Apr 10-30); rent $450-900; 3-5 bill series. Aid, three personas, both patterns: **loans only** is one payment at the start of each semester ($2,500-4,500); **loans plus bursaries** is a larger payment at the start of the semester ($3,500-6,000, to help with tuition) and then smaller monthly payments for the rest of the term ($300-900). The deposit text looks like a bank transfer from "BMO Bank of Montreal" (the bank that handles Quebec student-loan payments), not a government name. **The monthly part of the loans-plus-bursaries pattern is likely but not fully confirmed**; I model it that way and say so.
- **Quiet months.** Three personas are away in July, August and December (everyday spending at 15%, no wages): DHOME-03, DRENT-02, DRENT-05.

**Subscriptions are separate from bills.** Phone, internet, transit and insurance are recurring bill series. Subscriptions (Netflix, Spotify and similar) are drawn on their own: the number of them (INTL-FAM 2-5, INTL-TIGHT 1-3, DOM-HOME 3-7, DOM-RENT 2-5) and the total monthly cost (INTL-FAM $20-80, INTL-TIGHT $10-40, DOM-HOME $30-110, DOM-RENT $20-80) are drawn independently, then the total is split across the chosen services. A persona with many subscriptions is not forced to pay more for them.

**What is anchored to published figures and what is not.**
- **Tuition ranges are anchored to published 2026-27 rates** (15 credits a semester, tuition only; ancillary fees come on top). The Quebec rate is $103.92 a credit (about $1,559 a semester): Concordia's undergraduate rates page (Fall 2026 and Winter 2027), the Universite de Montreal registrar's page and McGill's page agree; McGill calls its numbers estimates and I could not read the government's own page, which has no figures. Out of province: $324.35 a credit for students admitted before Fall 2024 and $432.85 after (about $4,865 and $6,493 a semester), per McGill, Concordia and the registrar's page of UdeM; UQAM's yearly estimate with fees is $10,541. International: Concordia $1,010 to $1,400 a credit by faculty ($15,150 to $21,000 a semester; Arts and Science $1,160), UdeM $1,025.76 a credit (about $15,400 a semester, about $15,800 after the stated 3% rise, not confirmed), UQAM's estimate $29,415 a year with fees (about $14,700 a semester). French and Belgian francophone students pay the out-of-province rate, which is $4,865 to $6,493 a semester.
- **Limitations in the tuition ranges.** (1) International tuition at McGill for science, engineering and management is above my $30,000 top (third-party figures put a BSc near $32,400 a semester); (2) those McGill international rates and UQAM's per-credit rates could not be confirmed from a primary source, I only have third-party figures or yearly estimates; (3) the French/Belgian rate is below the first range I proposed ($7,000), so INTL-FAM starts at $5,000; (4) management and engineering out-of-province supplements are not modelled.
- **Modeling assumptions, not research:** subscription counts and costs; the bill counts of the archetypes other than INTL-FAM; wage ranges (the $16.60 an hour I started from was not verified, so I draw $16-17); the 85% of gross that reaches the account; the share of tuition the family funds in INTL-TIGHT; allowance and aid amounts; exam-period dips; which personas are away.
- **Out of scope, listed as limitations:** student parents, and exchange students (different tuition rules, often no tuition paid by the student, short stays).

### 2026-10-08: Mix v2, additions made while writing the generator, and one finding (before any backtest)
- **Additions, not in the entry above.** A student whose tuition is paid from chequing and who has no aid needs the money to come from somewhere, or the account would fall by the whole tuition. So DHOME-02 and DRENT-04 (tuition from chequing, no aid) get a **family contribution** deposit of 80-110 percent of the tuition, 1-30 days before each payment (INTL-TIGHT's is 60-105 percent, INTL-FAM's 100-140 percent). The aid-funded DRENT personas are funded by their aid. Family deposits and tuition payments carry a shared `link_id` in the answer key, so the pass-through rule can later be scored against what really funded what, including the cases outside its window. Wage pay is paid on the biweekly Fridays (the pay for a period uses that period's hours, so exam dips and breaks show up in the pay due in them); the 85 percent net share, the family contribution shares and the 50-70 percent exam factor are modeling assumptions.
- **Finding, from looking at generated histories (data, not forecast results).** With the recorded ranges, some personas bleed money all year: v2-IFAM-01 closes the year at about -$5,700, v2-ITIGHT-03 at -$3,000, v2-ITIGHT-04 at -$4,100, v2-DRENT-04 at -$5,400, v2-DRENT-02 at -$2,500, v2-DHOME-02 and -03 at about -$1,000 to -$1,800. The reason is structural: rent, bills and everyday spending add up to more than the recorded income (for INTL-FAM the monthly allowance of $800-1,800 is below rent $700-1,400 plus bills plus spending), and scheduled payments post even past the overdraft limit by the generator's design. I have **not** changed any range. This matters for two reasons: a "reliable family funding" persona that is short every month is not what the archetype says, and personas already below zero at an origin make an alert test meaningless for those origins (which is why already-in-shortfall origins are reported separately). Whether to revise the ranges (for example an allowance that covers costs, drawn as 100-130 percent of them) is my decision, to be made before the first full backtest and recorded as a new entry.

### 2026-10-08: Two rules for the backtest metrics (how the records carry them)
- **Origins already in shortfall are reported separately.** A record says whether the account was already below its buffer at the origin (`in_shortfall_at_origin`: the balance at the origin is under the buffer). An alert about a shortfall that has already begun is not a warning ahead of time, so the alert metrics (caught, false alarms per 30 days, lead time) use only the other origins, and the already-in-shortfall origins get their own row with their own count. The summaries (synthetic and real) print the two counts apart.
- **History length and early recurring detection.** A record carries `history_days` (the span of rows before the origin) and `n_series_detected` (the recurring series found from those rows). Reports break errors and series counts down by history length ("8-12 weeks", "12-26 weeks", "26+ weeks"), so the weak early origins, where detection has seen few repeats, are visible rather than averaged away. The first origin has at least 8 weeks behind it by design. No text from the data goes into a record for this.
- I wrote these from the instruction "already-in-shortfall origins reported separately; history length before the first origin and early-origin recurring detection". If the intended rules were different, the fields are cheap to change before any result is computed.

### 2026-10-08: Week 4 step 2: the Time Machine back end (`lookahead/timemachine.py`)
- **What it does.** `replay(conn, account, dates)` gives, for each date in order, the forecast the app would have shown then and the alert state at that date, from only what was available then (rule zero): rows posted on or before the date, recurring series re-detected from them, decisions and planned items made by then. `precompute` saves one JSON line per date and resumes where it stopped; `available_dates` lists the days that can be replayed (eight weeks after the first row to the last row). The page that uses it is a later step.
- **Alerts in a replay.** Each replay keeps its own alert record in memory, so replaying never touches the live episodes, events or dismissals, and a read-only connection is enough (a test checks the file's bytes are unchanged). To resume, the record is rebuilt from the saved steps without running any forecast: `record_run` in `lookahead/alerts.py` was split in two, `record_snapshot` takes what the rule said and `record_run` is the old call on top of it. A resumed run equals one pass (tested).
- **Rule zero, tested with mutations.** The same dates replayed on a database that ends at the last date and on one that runs half a year longer give identical steps; rewriting every later amount and balance changes nothing; a decision or planned item dated the day after a date is ignored at that date and the same one dated the day before is used. When I made the replay ignore the date on purpose, 7 of the 16 tests failed.
- **Limits, written in the module.** Dismissals are not replayed (the replay shows the alert as if nothing had been dismissed); the safety buffer is the stored one for every date (the app keeps one buffer, not a history of it); the forecast does not use the categorizer, so nothing is retrained along the way (a model that does would have to be retrained inside the replay on rows up to each date). Dates must be in increasing order, and each needs eight weeks of history.
- **Cost, measured.** 40 weekly steps at 500 futures for one persona took 13 seconds and 0.17 MB; a snapshot holds the date the data runs to, the range, the single-path estimate, the alert state and its evidence, and no text from the data.

### 2026-10-08: Mix v2 revised: regular income tied to costs by a coverage ratio (before the first full backtest)
**Why.** I revised this after looking at generated balances only (the lowest and closing balance of each persona), before any forecast error, coverage or alert result existed, because several personas contradicted their own archetype in both directions. International students with "reliable family funding" went broke (v2-IFAM-01 closes the year near -$5,700), while the "stretched family" ones (v2-ITIGHT-01 and -02) closed at +$15,000 and +$10,600 and never fell below about +$3,300. Drawing income and costs independently cannot give the contrast the archetypes exist for. This replaces the independent income ranges (the IFAM allowance of $800-1,800, the ITIGHT family-transfer amounts and the wage hours of ITIGHT, DHOME and DRENT) in the entries above. Everything else stays.

**The rule.** For each student persona I draw a **coverage ratio** = regular income / (rent + bills + subscriptions + expected everyday spending), per month in a regular term month, and scale the regular income to match it:

| Archetype | Coverage ratio | What is scaled |
|---|---|---|
| IFAM | 1.05-1.30 | the monthly allowance |
| ITIGHT | 0.90-1.05 | wages first, then family transfers (see below) |
| DHOME | 1.00-1.30 | wage hours |
| DRENT | 0.95-1.15 | wage hours, after the aid |

- **Regular income** is the allowance, family transfers, wages and aid. **Tuition and the family deposits that fund it stay as they were and are outside the ratio** (they are not in the costs, and the deposits are not in the income).
- **Expected everyday spending** is computed from the persona's spending profile before any cutting back (purchases a day, average purchase including the long tail of larger ones, cash withdrawals); one-off larger purchases are not in the costs.
- **ITIGHT keeps the 24 hours a week cap in term.** Wages are drawn at 10 to 24 hours, then the target income is split in proportion; wages may not exceed the cap, and what wages cannot cover is made up by the family transfers (irregular, every 15-45 days, scaled so their monthly average makes up the rest).
- **Aid.** For a DRENT persona whose tuition is paid from chequing and funded by aid (DRENT-01, -03), the part of the aid that equals the tuition is tuition funding, outside the ratio; only the rest counts as regular income. For DRENT-05, whose tuition is not paid from chequing, all of the aid is regular income. This is how I read "tuition and the deposits that fund it stay outside the ratio". Wages are at least a quarter of the target, so a persona with a lot of aid still works.
- The ratio is defined on a regular term month. Exam dips, breaks and the months away change the real month, on purpose. The ratio is a property of the persona (a test recomputes it from the persona's sources and checks it is in the range).
- Because costs are now drawn before income, each persona's random stream is used in a different order: the same ids give new personas. Nothing was run on the old draws.
- **Not changed:** SAL and SEM. TIGHT and FREE are not changed either: I measured their current ratios and decide on them separately.

### 2026-10-08: The 30 personas after the coverage-ratio revision (generated balances only)
Seen before any forecast error, coverage or alert result. Made with `python -m eval.persona_report` (seed 42, generation only; nothing here runs a forecast). "Below" counts the weekly origins that start below the persona's buffer, which the metrics report separately.

| Persona | Set | Coverage | Buffer | Lowest | Closing | Origins starting below the buffer |
|---|---|---|---|---|---|---|
| v2-IFAM-01 | dev | 1.17 | $300.00 | $4350.00 | $10992.87 | 0 of 40 |
| v2-IFAM-02 | dev | 1.11 | $300.00 | $4948.48 | $15094.67 | 0 of 40 |
| v2-IFAM-03 | dev | 1.16 | $300.00 | $5103.12 | $9369.80 | 0 of 40 |
| v2-IFAM-04 | held-out | 1.27 | $300.00 | $1530.65 | $31662.78 | 0 of 40 |
| v2-ITIGHT-01 | dev | 0.96 | $100.00 | $2114.61 | $7323.07 | 0 of 40 |
| v2-ITIGHT-02 | dev | 1.05 | $100.00 | $5344.86 | $12377.13 | 0 of 40 |
| v2-ITIGHT-03 | dev | 0.98 | $100.00 | -$14706.30 | -$13992.21 | 13 of 40 |
| v2-ITIGHT-04 | held-out | 0.99 | $100.00 | -$3714.45 | -$2334.75 | 30 of 40 |
| v2-ITIGHT-05 | held-out | 1.03 | $100.00 | -$2222.28 | -$1979.89 | 0 of 40 |
| v2-DHOME-01 | dev | 1.22 | $150.00 | $55.02 | $3712.87 | 0 of 40 |
| v2-DHOME-02 | dev | 1.24 | $150.00 | $1035.42 | $3684.27 | 0 of 40 |
| v2-DHOME-03 | dev | 1.08 | $150.00 | $451.49 | $783.19 | 0 of 40 |
| v2-DHOME-04 | held-out | 1.04 | $150.00 | $60.19 | $1505.52 | 0 of 40 |
| v2-DRENT-01 | dev | 1.14 | $150.00 | $126.75 | $4266.45 | 1 of 40 |
| v2-DRENT-02 | dev | 1.12 | $150.00 | $786.96 | $1554.84 | 0 of 40 |
| v2-DRENT-03 | dev | 1.06 | $150.00 | -$497.02 | $2502.27 | 17 of 40 |
| v2-DRENT-04 | held-out | 0.98 | $150.00 | -$795.28 | -$510.59 | 18 of 40 |
| v2-DRENT-05 | held-out | 1.06 | $150.00 | -$767.90 | $1966.22 | 11 of 40 |
| v2-SAL-01 | dev | 1.98 | $300.00 | $2950.00 | $32180.04 | 0 of 40 |
| v2-SAL-02 | dev | 2.24 | $300.00 | $3000.00 | $34203.27 | 0 of 40 |
| v2-SAL-03 | dev | 1.55 | $300.00 | $3010.56 | $13757.93 | 0 of 40 |
| v2-SAL-04 | held-out | 2.29 | $300.00 | $2137.17 | $36847.99 | 0 of 40 |
| v2-SEM-01 | dev | 1.19 | $300.00 | $796.64 | $3459.58 | 0 of 40 |
| v2-SEM-02 | held-out | 1.65 | $300.00 | $596.81 | $16036.26 | 0 of 40 |
| v2-TIGHT-01 | dev | 0.99 | $100.00 | -$1475.00 | $1847.12 | 5 of 40 |
| v2-TIGHT-02 | dev | 0.97 | $100.00 | -$1020.80 | $277.69 | 4 of 40 |
| v2-TIGHT-03 | dev | 0.91 | $100.00 | -$1733.25 | $1266.76 | 13 of 40 |
| v2-TIGHT-04 | held-out | 0.89 | $100.00 | -$1202.14 | $611.92 | 14 of 40 |
| v2-FREE-01 | dev | 0.89 | $200.00 | -$764.44 | $688.26 | 0 of 40 |
| v2-FREE-02 | held-out | 0.84 | $200.00 | -$1838.24 | $1093.10 | 26 of 40 |

What I read from it, without changing anything: IFAM are all comfortable, as the archetype says. ITIGHT-01 and -02 are still comfortable (opening balances of $2,000-8,000 and, for -02, no tuition from chequing), while ITIGHT-03 falls to about -$14,700 (three semesters of partly funded tuition) and ITIGHT-04 is below its buffer at 30 of 40 origins. DRENT-03, -04 and -05 are below their buffers at 11 to 18 origins. SAL and SEM were left alone and have coverage ratios of 1.2 to 2.3. TIGHT is at 0.89-0.99 and FREE at 0.84-0.89 (FREE-02 is below its buffer at 26 of 40 origins). Open questions (ITIGHT opening balance and partial tuition funding, TIGHT and FREE) are not decided yet.

### 2026-10-09: Last revision of the generator before the backtest, with acceptance checks written before regenerating
**Why.** I made this revision after looking at generated balances only (lowest, closing, and the origins that start below the buffer), before any forecast error, coverage or alert result existed. The table in the previous entry showed two things I did not want: the stretched-family persona v2-ITIGHT-01 and -02 were still comfortable (opening balances of $2,000-8,000 and, for -02, no tuition from chequing), while v2-ITIGHT-03 fell to about -$14,700 because three semesters of partly funded tuition were never made up; and v2-FREE-02 was below its buffer at 26 of 40 origins.

**What changes (and nothing else).**
- **ITIGHT:** the opening balance is $300-2,000 (was $2,000-8,000). When tuition is paid from chequing and the family deposit before it covers less than the whole tuition, a **second family transfer** arrives 15-45 days after the tuition payment in about 70 percent of terms (each term is drawn at random with probability 0.7) and covers part of the gap: 50-100 percent of what was missing, to the $50. It is a separate series in the answer key (`<id>-tuition-topup`) with the same `link_id` as the tuition it helps pay.
- **FREE:** the deposit amounts are scaled so the coverage ratio is 0.95-1.15 (income over rent, bills and expected everyday spending, as for the students), keeping the shape of the old range (lowest deposit about a quarter of the average, highest about 1.75 times it). Costs are drawn before income for FREE too, so the same ids give new FREE personas.
- **TIGHT:** unchanged. Its coverage ratios are 0.89-0.99, within about 0.06 of 0.95-1.05, and the generator already halves everyday spending when the balance runs low, so these personas end up near zero, which is what the archetype says.
- **SAL and SEM:** unchanged (their histories must come out identical; this is checked).

**Acceptance checks, fixed now, before regenerating** (computed by `python -m eval.persona_report --check` from generated balances only; "origins below the buffer" is the number of the 40 weekly origins whose starting balance is under the persona's buffer):
1. **IFAM:** each persona is below its buffer at no more than 3 of the 40 origins.
2. **ITIGHT:** each persona dips near or below its buffer at some point in the year (its lowest balance is at most its buffer plus $300), and none runs away (its lowest balance is at least -$3,000).
3. **DHOME, DRENT, TIGHT and FREE:** no persona is below its buffer at more than 20 of the 40 origins.
4. **SAL and SEM:** each persona's lowest and closing balance are exactly what they were in the previous entry's table.

**What happens after regenerating.** The mix is frozen whatever comes out, unless an archetype contradicts its own description. If a check fails I say which and why, and nothing is adjusted further: a persona that fails is recorded here as a **known limitation**, not another round of changes. The origins, the split, the held-out list and everything else in the earlier entries stand.

## Tests

### 2026-10-01: Slow marker
- Decision: 22 tests are marked `slow`. I run `pytest -m "not slow"` routinely (about 46 s) and the full suite before commits (about 3 min).
- Lost: skipping slow tests by default; pytest-xdist.
