"""Development script: choose the big-deposit rule and check the pass-through band on synthetic
personas (never on real data).

A "big" row is one of at least FLOOR cents and at least TIMES x the typical (median) row of its
direction over the last 13 weeks (`lookahead.forecast.spending`). For money in, the question is
which deposits are set aside as "large deposits (not forecast)". Ground truth from the generator:

- planted large deposits: the family credit memos of persona C (money that is effectively
  income and arrives at times history cannot predict);
- ordinary deposits: everything else that is money in and in no series (small e-Transfers, and
  payroll where it is not a series).

Series payments are taken from the generator's own labels, so detection errors do not mix in.
The family memos are not counted as a series (they are irregular, so the detector finds none).

Run:  python -m eval.deposit_rule [--seeds 30]
This is development, not evaluation: the memo sizes in persona C were calibrated to one real
account, so the sweep chooses a rule on data that is partly circular. The held-out test is the
generator v1 personas (week 5).
"""

import argparse
from datetime import date, timedelta

from lookahead import one_time  # the cutoff constants live here (2026-11-02)
from lookahead.flows import Txn, flow_types
from lookahead.forecast import spending
from lookahead.generator.personas import PERSONAS
from lookahead.generator.pipeline import generate_persona

START, END = date(2026, 1, 1), date(2026, 9, 30)
FAMILY = "C-family"
FLOORS = (10000, 20000, 30000, 50000, 100000)
TIMES = (3, 5, 10, 20)
BANDS = ((90, 110), (85, 115), (80, 120))


def _sample(seed: int):  # type: ignore[no-untyped-def]
    for pid in "ABC":
        gen = generate_persona(PERSONAS[pid], START, END, seed)
        txns = [Txn(i, g.posted_date, g.amount_cents, g.description, g.sub_description)
                for i, g in enumerate(gen)]  # fmt: skip
        series = {i for i, g in enumerate(gen) if g.series_id and g.series_id != FAMILY}
        family = {i for i, g in enumerate(gen) if g.series_id == FAMILY and g.amount_cents > 0}
        links = {i: g.link_id for i, g in enumerate(gen) if g.link_id}
        yield pid, gen, txns, flow_types(txns), series, family, links


def _as_ofs() -> list[date]:
    first = START + timedelta(days=91)
    return [first + timedelta(days=14 * k) for k in range((END - first).days // 14 + 1)]


def _typical(txns, flows, series, as_of, mode: str) -> list[int]:  # type: ignore[no-untyped-def]
    """The rows whose median is the typical row: deposits only, deposits and spending, or
    (hybrid) the deposits when there are at least 5 of them and everything otherwise."""
    first = as_of - timedelta(days=spending.LARGE_WINDOW_DAYS - 1)
    deposits = [c for t, _f, c in spending._money_in(txns, flows, series, as_of)
                if t.posted_date >= first]  # fmt: skip
    out = [c for t, _f, c in spending._counted(txns, flows, series, as_of)
           if c > 0 and t.posted_date >= first]  # fmt: skip
    if mode == "deposits":
        return deposits
    if mode == "pooled":
        return deposits + out
    return deposits if len(deposits) >= spending.TYPICAL_MIN_ROWS else deposits + out


MODES = ("deposits", "pooled", "hybrid")


def sweep(seeds: int) -> None:
    samples = [list(_sample(s)) for s in range(seeds)]
    print(f"{seeds} seeds x 3 personas, a forecast date every 14 days from {_as_ofs()[0]}")
    print("typical row = median of: deposits only | deposits and spending | hybrid")
    print(
        f"{'floor $':>8} {'times':>5} | "
        + " | ".join(f"{m + ': caught, flagged':>26}" for m in MODES)
    )
    for floor in FLOORS:
        for times in TIMES:
            one_time.LARGE_MIN_CENTS, one_time.LARGE_TIMES_MEDIAN = floor, times
            tally = {m: [0, 0, 0, 0] for m in MODES}  # caught, large, flagged, ordinary
            for seed_rows in samples:
                for _pid, _gen, txns, flows, series, family, _ in seed_rows:
                    for as_of in _as_ofs():
                        rows = spending._money_in(txns, flows, series, as_of)
                        window_start = as_of - timedelta(days=spending.LARGE_WINDOW_DAYS - 1)
                        for mode in MODES:
                            cutoff = spending.large_cutoff(
                                _typical(txns, flows, series, as_of, mode)
                            )
                            t4 = tally[mode]
                            for t, _f, c in rows:
                                if t.posted_date < window_start:
                                    continue
                                if t.row_id in family:
                                    t4[1] += 1
                                    t4[0] += c >= cutoff
                                else:
                                    t4[3] += 1
                                    t4[2] += c >= cutoff
            cells = [
                f"{t[0]:>4}/{t[1]:<4} {t[0] / t[1]:>4.0%} {t[2]:>4}/{t[3]:<4}"
                for t in tally.values()
            ]
            print(f"{floor / 100:>8.0f} {times:>5} | " + " | ".join(f"{c:>26}" for c in cells))
    one_time.LARGE_MIN_CENTS, one_time.LARGE_TIMES_MEDIAN = 50000, 5


def pass_through_band(seeds: int) -> None:
    """How many of the generator's earmarked funding + payment pairs does each band find, and how
    many big deposits are paired that are not planted pairs?"""
    print("\nPass-through band (planted pairs: the funding memo and the payment linked to it)")
    for band in BANDS:
        found = planted = other = 0
        for s in range(seeds):
            for _pid, gen, txns, flows, _series, _family, links in _sample(s):
                by_link: dict[str, list[int]] = {}
                for i, lk in links.items():
                    by_link.setdefault(lk, []).append(i)
                window_start = END - timedelta(days=spending.LARGE_WINDOW_DAYS - 1)
                truth = [v for v in by_link.values()
                         if any(gen[i].amount_cents > 0 for i in v)
                         and any(gen[i].amount_cents < 0 for i in v)
                         and any(gen[i].series_id == FAMILY for i in v)
                         and all(gen[i].posted_date >= window_start for i in v)]  # fmt: skip
                _, big_in = spending._split_large(spending._money_in(txns, flows, set(), END), END)
                out_rows = spending._counted(txns, flows, set(), END)
                _, big_out = spending._split_large(out_rows, END)
                ids = {r[0].row_id for r in big_out}
                cand = [
                    r for r in out_rows
                    if r[2] > 0 and (r[0].row_id in ids or r[1].flow_type == "transfer"
                                     or r[1].reason == "bill payment")
                ]  # fmt: skip
                truth_deposits = {i for v in truth for i in v if gen[i].amount_cents > 0}
                planted += len(truth)
                for dep, _combo in spending._pass_through(big_in, cand, band):
                    if dep[0].row_id in truth_deposits:
                        found += 1
                    else:
                        other += 1
        print(
            f"  band {band[0]}-{band[1]} percent: planted pairs found {found}/{planted}; "
            f"other big deposits paired {other}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m eval.deposit_rule")
    parser.add_argument("--seeds", type=int, default=30)
    args = parser.parse_args()
    sweep(args.seeds)
    pass_through_band(args.seeds)


if __name__ == "__main__":
    main()
