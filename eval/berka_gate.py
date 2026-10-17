"""The Berka gate (experiment 2 of the 2026-10-17 plan): counts only, nothing is scored.

Berka (PKDD'99) is a public Czech bank dataset of 4,500 accounts, 1993 to 1998, in koruna. This
reads data/berka/trans.asc (downloaded by hand, ignored by git, never committed or redistributed)
and prints how sparse it is, so that the account filter, the buffer rule and the split can be
fixed from counts before any forecast is scored.

    python -m eval.berka_gate                  # prints the counts and writes the report below
    python -m eval.berka_gate --dir data/berka

What it prints: transactions per account and per month, months of history, the longest quiet
stretch, how many transactions are mechanical (interest, the monthly statement fee), how the
outflow is split between everyday-style spending (cash and card withdrawals), monthly payments
(standing-order style: household, insurance, loan) and the rest, how well the stored balance
agrees with the amounts, how many accounts pass each candidate filter, and, for the candidate
buffer rules, how often a balance below the buffer follows an origin (events, not forecasts).
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REPORT = Path("docs/results/berka_gate_2026-10-17.md")
MONTHLY_CODES = ("SIPO", "POJISTNE", "UVER")  # household, insurance, loan payment
FEE_CODES = ("SLUZBY", "SANKC. UROK")  # statement fee, penalty interest
MECHANICAL_CODES = ("UROK", "SLUZBY", "SANKC. UROK")  # interest credited, fees, penalty interest
HORIZON = 30
MIN_HISTORY_DAYS = 56
BUFFER_FRACTIONS = (0.25, 0.5, 1.0)  # of the median monthly outflow of the year before the origin
QUANTILES = (0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0)


def load(directory: Path) -> pd.DataFrame:
    """trans.asc as a frame: real dates, signed amounts in koruna, a class for every row."""
    t = pd.read_csv(
        directory / "trans.asc", sep=";", low_memory=False,
        dtype={"type": "string", "operation": "string", "k_symbol": "string", "bank": "string"},
    )  # fmt: skip
    t["day"] = pd.to_datetime(t["date"].astype(str), format="%y%m%d")
    t["k_symbol"] = t["k_symbol"].str.strip().replace("", pd.NA)
    t["signed"] = np.where(t["type"] == "PRIJEM", t["amount"], -t["amount"])
    t = t.sort_values(["account_id", "day", "trans_id"], kind="stable").reset_index(drop=True)
    out = t["signed"] < 0
    code = t["k_symbol"]
    is_everyday = t["operation"].isin(["VYBER", "VYBER KARTOU"]) | (t["type"] == "VYBER")
    t["flow"] = np.select(
        [~out, code.isin(MONTHLY_CODES), code.isin(FEE_CODES), is_everyday],
        ["in", "monthly", "fee", "everyday"], default="other_out",
    )  # fmt: skip
    t["mechanical"] = code.isin(MECHANICAL_CODES)
    return t


def closing_balances(t: pd.DataFrame) -> pd.DataFrame:
    """The closing balance of every account-day that has a posting.

    The file lists same-day rows in an order that is sometimes wrong (a withdrawal and its fee
    swapped), so the last row listed is not always the last posting; and the stored balances
    drift from the running total by a tenth of a koruna or two (interest rounding). A day's close
    is therefore the balance recorded that day that is nearest to the previous close plus the
    day's total; `matched` says it lies within one koruna of it."""
    days = (
        t.groupby(["account_id", "day"])
        .agg(total=("signed", "sum"), balances=("balance", list))
        .reset_index()
    )
    rows: list[tuple[int, pd.Timestamp, float, bool]] = []
    account, previous = None, 0.0
    for a, day, total, balances in days.itertuples(index=False):
        if a != account:
            account, previous = a, 0.0
        target = previous + total
        close = min(balances, key=lambda b: abs(b - target))
        rows.append((a, day, close, abs(close - target) <= 1.0))
        previous = close
    return pd.DataFrame(rows, columns=["account_id", "day", "close", "matched"])


def balance_agreement(t: pd.DataFrame, closes: pd.DataFrame) -> dict[str, float]:
    """How well the stored balances agree with the amounts."""
    prev = t.groupby("account_id")["balance"].shift(1)
    in_file_order = (np.abs(prev + t["signed"] - t["balance"]) < 0.01) | prev.isna()
    return {"rows_in_file_order": float(in_file_order.mean()),
            "days_matched": float(closes["matched"].mean()),
            "accounts_all_days": float(closes.groupby("account_id")["matched"].all().mean()),
            "days": float(len(closes))}  # fmt: skip


def per_account(t: pd.DataFrame) -> pd.DataFrame:
    """One row per account: history, counts, gaps, flow shares."""
    g = t.groupby("account_id")
    first, last = g["day"].min(), g["day"].max()
    rows = pd.DataFrame({"first": first, "last": last, "n": g.size()})
    rows["months"] = (rows["last"] - rows["first"]).dt.days / 30.44
    rows["n_real"] = t[~t["mechanical"]].groupby("account_id").size().reindex(rows.index).fillna(0)
    rows["per_month"] = rows["n"] / rows["months"].clip(lower=1)
    rows["real_per_month"] = rows["n_real"] / rows["months"].clip(lower=1)
    days = t[["account_id", "day"]].drop_duplicates()
    gap = days.groupby("account_id")["day"].diff().dt.days
    rows["longest_gap"] = gap.groupby(days["account_id"]).max().reindex(rows.index).fillna(0)
    real_days = t.loc[~t["mechanical"], ["account_id", "day"]].drop_duplicates()
    rgap = real_days.groupby("account_id")["day"].diff().dt.days
    rows["longest_real_gap"] = rgap.groupby(real_days["account_id"]).max().reindex(rows.index)
    out = t[t["signed"] < 0]
    value = out.pivot_table(index="account_id", columns="flow", values="amount", aggfunc="sum",
                            fill_value=0.0)  # fmt: skip
    count = out.pivot_table(index="account_id", columns="flow", values="amount", aggfunc="size",
                            fill_value=0)  # fmt: skip
    for flow in ("everyday", "monthly", "fee", "other_out"):
        v = value.get(flow, pd.Series(0.0, index=value.index)).reindex(rows.index).fillna(0)
        c = count.get(flow, pd.Series(0, index=count.index)).reindex(rows.index).fillna(0)
        total_v = value.sum(axis=1).reindex(rows.index).replace(0, np.nan)
        total_c = count.sum(axis=1).reindex(rows.index).replace(0, np.nan)
        rows[f"{flow}_value_share"] = v / total_v
        rows[f"{flow}_count_share"] = c / total_c
    return rows


def buffer_events(
    t: pd.DataFrame, closes: pd.DataFrame, accounts: list[int]
) -> dict[float, dict[str, int]]:
    """For each buffer fraction: the origins (weekly, 8 weeks of history, 30 days left, balance at
    or above the buffer) and how many were followed by a balance below it. Events, not forecasts.
    The buffer uses only the year before the origin: fraction x the median monthly outflow."""
    out = {f: {"origins": 0, "dips": 0, "accounts_with_3_dips": 0, "accounts": 0}
           for f in BUFFER_FRACTIONS}  # fmt: skip
    keep = set(accounts)
    closed = {a: g.set_index("day")["close"] for a, g in closes.groupby("account_id") if a in keep}
    for a_id, a in ((a, g) for a, g in t.groupby("account_id") if a in keep):
        closing = closed[a_id]
        full = pd.date_range(closing.index.min(), closing.index.max(), freq="D")
        bal = closing.reindex(full).ffill().to_numpy()
        spend = (-a[a["signed"] < 0].groupby("day")["amount"].sum()).reindex(full).fillna(0)
        month_out = spend.rolling(30, min_periods=30).sum().to_numpy() * -1
        month_out = np.nan_to_num(month_out, nan=0.0)
        dips_by_f = {f: 0 for f in BUFFER_FRACTIONS}
        for f in BUFFER_FRACTIONS:
            count = 0
            for i in range(MIN_HISTORY_DAYS, len(bal) - HORIZON, 7):
                lo = max(0, i - 365)
                past = month_out[lo : i + 1]
                past = past[past > 0]
                if len(past) < 30:
                    continue
                buffer = f * float(np.median(past))
                if bal[i] < buffer:
                    continue
                out[f]["origins"] += 1
                if bal[i + 1 : i + 1 + HORIZON].min() < buffer:
                    out[f]["dips"] += 1
                    count += 1
            dips_by_f[f] = count
            out[f]["accounts"] += 1
            out[f]["accounts_with_3_dips"] += count >= 3
    return out


def quantile_line(values: pd.Series, fmt: str = "{:.1f}") -> str:
    q = values.dropna().quantile(list(QUANTILES))
    return " / ".join(fmt.format(x) for x in q)


def build(directory: Path) -> str:
    t = load(directory)
    closes = closing_balances(t)
    agree = balance_agreement(t, closes)
    acc = per_account(t)
    first_day, last_day = t["day"].min().date(), t["day"].max().date()
    lines = [
        "# The Berka gate: counts only (nothing is scored)",
        "",
        "Made with `python -m eval.berka_gate` from `data/berka/trans.asc` (not committed). "
        "Method in DECISIONS.md (2026-10-17, experiment 2). Quantiles are "
        "min / 10% / 25% / median / 75% / 90% / max across accounts.",
        "",
        f"- {len(t):,} transactions, {len(acc):,} accounts, {first_day} to {last_day}; "
        f"amounts in koruna (CZK).",
        f"- Stored balance equals the previous balance plus the amount, rows in file order: "
        f"{agree['rows_in_file_order']:.1%} of rows. The file lists same-day rows in a wrong "
        f"order at times and the balances drift by 0.1 to 0.2 koruna (interest rounding), so a "
        f"day's close is the balance recorded that day nearest to the previous close plus the "
        f"day's total: {agree['days_matched']:.2%} of {int(agree['days']):,} account-days lie "
        f"within one koruna of it, {agree['accounts_all_days']:.1%} of accounts on every day.",
        f"- Mechanical rows (interest credited, the statement fee, penalty interest): "
        f"{t['mechanical'].mean():.1%} of all rows.",
        "",
        "## How sparse",
        "",
        "| | min / 10% / 25% / median / 75% / 90% / max |",
        "|---|---|",
        f"| months of history | {quantile_line(acc['months'])} |",
        f"| transactions in total | {quantile_line(acc['n'], '{:.0f}')} |",
        f"| transactions per month | {quantile_line(acc['per_month'])} |",
        f"| transactions per month, mechanical rows left out | "
        f"{quantile_line(acc['real_per_month'])} |",
        "| longest stretch without any posting (days) | "
        f"{quantile_line(acc['longest_gap'], '{:.0f}')} |",
        f"| longest stretch without a non-mechanical posting (days) | "
        f"{quantile_line(acc['longest_real_gap'], '{:.0f}')} |",
        "",
        "## What the outflow is made of",
        "",
        "Per account, the share of outflow by value and by count (everyday-style = cash and card "
        "withdrawals; monthly = household, insurance and loan payments; fees = statement fee and "
        "penalty interest; other = transfers out and the rest).",
        "",
        "| | by value: min / 10% / 25% / median / 75% / 90% / max | by count: median |",
        "|---|---|---|",
    ]
    for flow in ("everyday", "monthly", "fee", "other_out"):
        lines.append(
            f"| {flow} | {quantile_line(acc[f'{flow}_value_share'], '{:.2f}')} | "
            f"{acc[f'{flow}_count_share'].median():.2f} |"
        )
    out = t[t["signed"] < 0]
    total = out["amount"].sum()
    pooled = out.groupby("flow")["amount"].sum() / total
    lines += [
        "",
        "All accounts pooled, share of the value of all outflows: "
        + ", ".join(f"{k} {v:.1%}" for k, v in pooled.items())
        + ".",
        "",
    ]
    # filters
    lines += [
        "## Accounts passing each candidate filter",
        "",
        "History in months, and non-mechanical transactions per month (each row also needs "
        "the longest stretch without a non-mechanical posting to be at most 90 days).",
        "",
        "| | 12+ months | 24+ months | 36+ months | 48+ months |",
        "|---|---|---|---|---|",
    ]
    for per in (1, 2, 4, 8):
        cells = []
        for months in (12, 24, 36, 48):
            ok = ((acc["months"] >= months) & (acc["real_per_month"] >= per)
                  & (acc["longest_real_gap"] <= 90))  # fmt: skip
            cells.append(str(int(ok.sum())))
        lines.append(f"| {per}+ per month | " + " | ".join(cells) + " |")
    lines.append("")
    # buffer events on one candidate filter
    keep = acc[(acc["months"] >= 24) & (acc["real_per_month"] >= 2)
               & (acc["longest_real_gap"] <= 90)].index.tolist()  # fmt: skip
    lines += [
        f"## Dips after weekly origins, for the {len(keep)} accounts with 24+ months, "
        "2+ non-mechanical transactions a month and no quiet stretch over 90 days",
        "",
        "A buffer is a fraction of the account's median 30-day outflow over the year before "
        "the origin (past data only). Counts of events, not forecasts.",
        "",
        "| buffer | origins at or above it | followed by a dip below it | accounts with "
        "3+ such origins |",
        "|---|---|---|---|",
    ]
    events = buffer_events(t, closes, keep)
    for f, e in events.items():
        share = e["dips"] / e["origins"] if e["origins"] else 0.0
        lines.append(
            f"| {f:.0%} of a month's outflow | {e['origins']:,} | {e['dips']:,} "
            f"({share:.1%}) | {e['accounts_with_3_dips']} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.berka_gate",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dir", type=Path, default=Path("data/berka"))
    args = parser.parse_args(argv)
    if not (args.dir / "trans.asc").exists():
        print(f"No {args.dir / 'trans.asc'}: download the Berka files there first.")
        return 1
    text = build(args.dir)
    print(text)
    REPORT.write_text(text, encoding="utf-8")
    print(f"written: {REPORT} (as of {date.today()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
