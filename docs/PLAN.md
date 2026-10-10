# Plan

What I'm building, in what order, and the rules the code must keep. Reviews check the code against this file. When the plan changes, I update it in the same commit.

## Goal

A local-first cashflow planner: import bank CSVs, forecast the chequing balance, warn before it dips below a safety buffer, show the evidence behind every warning, and test what-ifs. It should be CV-ready with ML, NLP and RAG, and I only claim each one after it's built and measured.

## Weeks

| Week | Scope | Status |
|---|---|---|
| 1 | Skeleton, synthetic generator v0 (3 personas, frozen until week 5), Scotiabank importer with dedupe and reconciliation, pseudonymizer, Transactions page | Done |
| 2 | Merchant extraction and resolution, flow typing, 17 categories, hybrid categorizer, label review, holdout evaluation, app pages (To check, corrections, Retrain, opt-in LLM fallback) | Done |
| 3 | 3a recurring payments; 3b baseline forecast with block-bootstrap paths; 3c shortfall alert; 3d what-ifs. This is the working prototype | In progress (3a) |
| 4 | Time Machine, baseline backtest, Model Report, README. CV-ready demo; then the repo goes public | Next |
| 5 | Generator v1 (100+ weighted-archetype personas, some with a monthly credit card payment), LightGBM direct-horizon forecaster, time-series CV, conformal intervals | |
| 6 | Full backtest on synthetic, Berka and my real data, reported separately | |
| 7 | Ask: questions answered from the data (local model by default, Claude opt-in with minimal pseudonymized evidence) | |
| Later | Credit card accounts (first after the demo), Plaid sync (opt-in, CSV stays the default), receipts, DistilBERT | |

## Rules the code must keep

Privacy
- Real data and anything derived from it (databases, trained models, caches holding merchant names) lives only in `data/private/`. Code that writes such files refuses other paths.
- Tests never read private files. They use temporary paths and synthetic data.
- Reports on real data: aggregate metrics over many forecasts may be printed in my terminal and shared in prompts; never rows, dates, names or individual transaction amounts, and never written into the repository (rule of 2026-10-12, replacing "counts only").
- Anything leaving `data/private/` is pseudonymized first.
- The LLM fallback is off by default, opt-in, and is sent the merchant name of a card purchase only: no amounts, dates or people's names. Claude Haiku 4.5 pinned, temperature 0, cached.
- The app listens on localhost only and opens databases read-only.

Evaluation
- Test sets are frozen before labelling. Every choice is made on development rows. The holdout is scored once.
- Experiments are declared in DECISIONS.md before they run. Losers are reported, not hidden.
- Synthetic and real results are reported separately, always with n.
- I don't merge weak categories to raise a score.

Product
- The app never blocks on review: every row is categorized; low-confidence rows are only flagged.
- Money is integer cents.
- Forecast horizon: next expected income or 30 days, whichever is sooner.
- Large one-offs are scheduled items, kept out of everyday spending. Fees are modelled separately, outside the everyday-spending bootstrap.
- Netted (fully reversed) rows are out of spending.
- Recurring detection upgrades card subscriptions and e-Transfer rent, and downgrades one-off bill payments.
- Generator v0 stays frozen until week 5.

Docs
- README numbers must match what the code and eval files produce.
- DECISIONS.md: first person, short, no personal financial details.
