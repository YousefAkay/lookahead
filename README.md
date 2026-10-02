# Lookahead

A local-first cashflow planner. Lookahead reads bank CSV exports, forecasts the chequing balance 30 days ahead, warns before it is likely to dip below a safety buffer, shows the evidence behind every warning, and lets you test what-if scenarios. Everything runs on your own computer.

> Estimates for personal budgeting. Not financial advice.

## Status

Work in progress. Done: data foundations (week 1) and the transaction categorizer (week 2). Next: recurring payments and the balance forecast.

## Results

Real measured numbers only. Synthetic and real results are reported separately.

### Transaction categorizer (real data, my own account)

I labelled two years of my own transactions (17 categories). Fixed rules decide the clear cases, 37% of rows (489 of 1,311: transfers, fees, cash, delivery platforms). Another 11% (148 rows) are charges that were fully reversed and need no category. The numbers below are for the rest, which a model has to decide.

The model is a hybrid: a merchant I have already labelled reuses its label; a new merchant goes to a logistic regression on character n-grams of the merchant name plus a French/English keyword list. It trains on 447 labelled rows from 82 merchants, plus synthetic rows.

I froze the test sets before labelling, made every choice on the development rows only, and scored the holdout once.

| Test set | What it measures | n | Accuracy | Macro-F1 |
|---|---|---|---|---|
| Test A | later rows, mostly merchants seen before | 114 rows, 35 merchants | 91.2% | 69.6% |
| Test B, new merchants | merchants never seen in any training data | 48 rows, 26 merchants | 72.9% | 49.8% |

- Test B has 107 rows from 27 merchants in total, at 87.9% accuracy. I do not use that number: 59 of those rows come from one chain that is also in my synthetic training data, so the model was pre-taught it (100% on those rows). The row above leaves that chain out.
- Every row gets a category; uncertain ones are flagged for an optional check. Rows that were not flagged were 98.1% correct on Test A (104 of 114 rows unflagged) and 94.4% correct on new merchants (18 of 48 rows unflagged).
- The sample is small. Most categories have fewer than 10 test rows, so the per-category numbers are rough. The labels follow what I actually buy at each merchant, which no outside model can know.

Optional, off by default: an LLM fallback for new merchants (Claude Haiku 4.5). It is sent the merchant name of a card purchase and nothing else: no amounts, dates or people's names.

| Test set | n | Accuracy | Macro-F1 |
|---|---|---|---|
| Test A, with the LLM fallback | 114 rows, 35 merchants | 96.5% | 87.8% |
| Test B, new merchants, with the LLM fallback | 48 rows, 26 merchants | 83.3% | 67.7% |

Cost: $0.014 for the 62 holdout merchant names, about $0.22 per 1,000 merchants; median 0.5 seconds per name.

Tried and not kept, all judged on development rows only: class weights, probability calibration, fold averaging, and sentence embeddings (multilingual MiniLM). Details are in [DECISIONS.md](DECISIONS.md).

## Setup (Windows)

```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pytest -m "not slow"
```
