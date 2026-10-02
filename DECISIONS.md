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

## Tests

### 2026-10-01: Slow marker
- Decision: 22 tests are marked `slow`. I run `pytest -m "not slow"` routinely (about 46 s) and the full suite before commits (about 3 min).
- Lost: skipping slow tests by default; pytest-xdist.
