# Plan

What I'm building, in what order, and the rules the code must keep. Reviews check the code against this file. When the plan changes, I update it in the same commit.

## Goal

A local-first cashflow planner: import bank CSVs, forecast the chequing balance, warn before it dips below a safety buffer, show the evidence behind every warning, and test what-ifs. It should be CV-ready with ML, NLP and RAG, and I only claim each one after it's built and measured.

## Feature freeze

Week 8 is in feature freeze: no new features until forecaster v2 is done. Fixes, tests, docs and the hosted demo are allowed.

## Weeks 1-7, as built

| Week | What I built | Status |
|---|---|---|
| 1 | Skeleton, synthetic generator, Scotiabank importer with dedupe and reconciliation, pseudonymizer, Transactions page | Done |
| 2 | Merchant resolution, flow types, 17 categories, hybrid categorizer, label review, holdout evaluation, opt-in LLM fallback | Done |
| 3 | Recurring payments, baseline forecast with simulated spending paths, shortfall alert, what-ifs: the working prototype | Done |
| 4 | Time Machine, rolling-origin backtests (synthetic, Berka, my account), Model Report, forecaster experiments (LightGBM and the later variants lost; the baseline stays), alert threshold, the held-out test run once, README numbers, history scrub, repo public | Done |
| 5 | Stable recurring-series ids, recurring alerts, "What changed since last time", scenario calculator, navigation rework, safe-to-spend, demo mode, categorizer v2 (cleaner and chain directory rejected; retrieval-augmented categorizer adopted, off by default) | Done |
| 6 | Budgets that forecast: limits proposed from history, pace and chance of going over from the simulated futures, budget alerts, one-click recategorizing | Done |
| 7 | Ask: offline question parser and answer engine, every number computed in code, rows shown behind each answer. The LLM planner missed its pre-registered silent-wrong cap on the test set, so Ask ships offline-only | Done |

## Week 8

1. Close-out: last Ask fixes on main, nothing expects an API key.
2. Faster tests: the full suite in parallel (pytest-xdist), after making every test safe to run in parallel; the merge gate uses it.
3. This plan, brought up to date.
4. Budgets N5 and the hosted demo: synthetic data only, no upload, LLM off; edits in the demo stay in the visitor's session.
5. Forecaster v2: a fresh held-out set locked first, all work on development data only, an adopt rule committed before any scored run.
6. CV and LinkedIn.

## After week 8

- Ask v2: a new test set, and the silent-wrong rule fixed before any v2 question is written.
- Deferred until after v1: Plaid sync, credit card accounts, a bilingual interface, an encrypted database with one-click install, a weekly digest, other banks.

## Rules the code must keep

Privacy
- Real data and anything derived from it (databases, trained models, caches holding merchant names) lives only in `data/private/`. Code that writes such files refuses other paths.
- Tests never read private files or the local settings file. They use temporary paths and synthetic data.
- Reports on real data: aggregate metrics may be printed in my terminal; never rows, dates, names or individual amounts, and never written into the repository. Only categorizer and merchant-matching accuracy on my labelled rows may be published, as percentages with n.
- Anything leaving `data/private/` is pseudonymized first.
- LLM features are off by default and opt-in, and get the least evidence they need (the categorizer gets a card purchase's merchant name only).
- The app opens databases read-only, and with real data it runs only when served on localhost. Demo mode (`LOOKAHEAD_DEMO=1`) is synthetic data only and can never open anything under `data/private`.

Evaluation
- Adopt rules are committed before any scored run. Every choice is made on development data. Held-out data is opened once, and nothing is tuned after it.
- Losers are reported, not hidden.
- Synthetic and real results are reported separately, always with n.
- I don't merge weak categories to raise a score.

Product
- Numbers are computed in code, never by an LLM. Model-generated code is never run.
- Every forecast feature on date t uses only data posted on or before t.
- The app never blocks on review: every row is categorized; low-confidence rows are only flagged.
- Money is integer cents.
- Forecast horizon: a fixed 30 days, starting the day after the data ends.
- Large one-offs are scheduled items, kept out of everyday spending; big money that keeps coming is drawn as random arrivals. Fees are modelled separately.
- Netted (fully reversed) rows are out of spending.
- Recurring detection upgrades card subscriptions and e-Transfer rent, and downgrades one-off bill payments.

Docs
- README numbers must match what the code and eval files produce.
- DECISIONS.md: first person, short, no personal financial details.
