# Lookahead

A local-first cashflow planner. Lookahead reads bank CSV exports, forecasts the chequing balance 30 days ahead, warns before it is likely to dip below a safety buffer, shows the evidence behind every warning, and lets you test what-if scenarios. Everything runs on your own computer.

> Estimates for personal budgeting. Not financial advice.

## Status

Work in progress: Week 1 (data foundations).

## Results

Filled in with real measured numbers only. Synthetic and real results are reported separately.

| Component | Metric | Baseline | Model | Data (n) |
|---|---|---|---|---|
| — | — | — | — | — |

## Setup (Windows)

```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pytest
```
