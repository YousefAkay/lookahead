"""Experiment 4: online combination with "balance stays" (synthetic development personas and Berka
development accounts).

The rule is in DECISIONS.md (2026-10-17, "Experiment 4", commit e510b1b), with my readings of
2026-11-02. It reads the default forecaster's saved futures (eval.futures_store) and the outcome
fields of the experiment 3 records, and simulates nothing.

* The combination (recentre). With s the balance at the origin, x_f(h) future f on day h and m(h)
  the forecast's median that day, the combined future is x_f(h) - (1 - w)(m(h) - s). Every future
  slides by the same amount each day, so the band width is unchanged and the combined median is
  w m(h) + (1 - w) s. w = 1 is today's forecast, w = 0 is "balance stays".
* The weights come from the grid 0, 0.1, ..., 1; ties go to the larger weight.
* The loss of a weight at one origin is the mean over days 1 to 30 of the exact CRPS of the
  combined futures.
* Three variants: (i) w = 0.5 everywhere; (ii) one global weight, learned from the other accounts
  (synthetic: the personas of the other archetypes of the same set; Berka: every other account);
  (iii) a weight per account, learned from its own past origins.
* Rule zero: a weight for origin t learns only from origins t' <= t - 35 days, whose 30 days were
  all posted before t.

The median m(h) is the forecast's p50 (the simulator's nearest-rank percentile, as saved in the
records). Held-out personas and held-out Berka accounts are refused.

    python -m eval.combination eligible --set 12 --root ../lookahead-runs   # counts only
    python -m eval.combination quick --root ../lookahead-runs               # timing, counts only
    python -m eval.combination run --root ../lookahead-runs                 # both synthetic sets
    python -m eval.combination run --root ../lookahead-runs --berka ii      # and Berka, (ii) only

`run` writes docs/results/combination_2026-11-02.md (and a .json beside it).
"""

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from eval.anchor import bootstrap_gain, cov_gap, rel
from eval.baselines import crps_samples
from eval.berka_split import assert_development_only as berka_development_only
from eval.berka_split import development_accounts
from eval.futures_store import check_file, load, nearest_rank
from eval.futures_store import read_records as read_record_list
from eval.metrics import alert_metrics, forecast_metrics
from eval.thresholds import THRESHOLDS
from eval.thresholds_raw import choose, curve
from lookahead.generator.archetypes import assert_development_only, development_ids

GRID = tuple(k / 10 for k in range(11))
FIXED = 0.5
LAG = timedelta(days=35)
MIN_OWN = 8
MIN_OTHER_ACCOUNTS = 5
MIN_OTHER_ORIGINS = 30
MIN_DIPS = 5
CASH_FILE = Path("docs/berka_cash_series_accounts.json")
VARIANTS = {"i": "(i) fixed 50/50", "ii": "(ii) one global weight", "iii": "(iii) per-account"}
LEARNED = ("ii", "iii")
STORE = {"12": Path("data/backtest/futures/12"), "long": Path("data/backtest/futures/long"),
         "berka": Path("data/berka/runs/futures")}  # fmt: skip
RECORDS = {"12": Path("data/backtest/exp3/12"), "long": Path("data/backtest/exp3/long"),
           "berka": Path("data/berka/runs/exp3")}  # fmt: skip
REPORT = Path("docs/results/combination_2026-11-02.md")
PLAN = "2026-10-17 experiment 4 (e510b1b), readings of 2026-11-02"

Record = dict[str, Any]


# --- The combination ---


def shift(start_cents: int, median: np.ndarray, w: float) -> np.ndarray:
    """What is taken off every future on days 1 .. 30: (1 - w)(m(h) - s)."""
    return (1 - w) * (np.asarray(median, dtype=float) - start_cents)


def combined(paths: np.ndarray, start_cents: int, median: np.ndarray, w: float) -> np.ndarray:
    """The combined futures (futures x days), in cents, not rounded."""
    return np.asarray(paths, dtype=float) - shift(start_cents, median, w)


def losses(
    paths: np.ndarray, start_cents: int, median: np.ndarray, realized: np.ndarray
) -> np.ndarray:  # fmt: skip
    """The loss of each weight of the grid at one origin: the mean over the 30 days of the exact
    CRPS of the combined futures."""
    return np.array([
        float(crps_samples(combined(paths, start_cents, median, w), realized).mean()) for w in GRID
    ])  # fmt: skip


def best_weight(mean_losses: np.ndarray) -> float:
    """The weight of the grid with the lowest loss; a tie goes to the larger weight."""
    best = len(GRID) - 1
    for i in range(len(GRID) - 2, -1, -1):
        if mean_losses[i] < mean_losses[best]:
            best = i
    return GRID[best]


def view(
    paths: np.ndarray, start_cents: int, buffer_cents: int, median: np.ndarray,
    realized: np.ndarray, w: float,
) -> Record:  # fmt: skip
    """The numbers of one origin as the combination with weight `w` makes them. The band and the
    lowest balances are read from the combined futures; w = 1 gives the default exactly."""
    x = combined(paths, start_cents, median, w)
    lows = x.min(axis=1)

    def cents(values: np.ndarray) -> list[int]:
        return np.rint(values).astype(int).tolist()

    return {
        "w": w,
        "p10": cents(nearest_rank(x, 0.1)),
        "p50": cents(nearest_rank(x, 0.5)),
        "p90": cents(nearest_rank(x, 0.9)),
        "shortfall_probability": float((lows < buffer_cents).mean()),
        "median_lowest_cents": int(np.rint(nearest_rank(lows, 0.5))),
        "crps": crps_samples(x, np.asarray(realized, dtype=float)).round(1).tolist(),
        "moved": bool(w < 1 and np.any(np.asarray(median) != start_cents)),
    }


# --- Reading the saved futures ---

# The fields of an experiment 3 record that say what happened (and who and when); the forecast's
# own numbers come from the saved futures.
OUTCOME_FIELDS = (
    "persona", "archetype", "origin", "days", "start_balance_cents", "buffer_cents", "point",
    "realized", "realized_lowest_cents", "realized_first_below_buffer", "in_shortfall_at_origin",
    "crps_stays", "everyday", "history_days",
)  # fmt: skip


def read_records(path: Path) -> dict[str, Record]:
    """An experiment 3 records file, by origin date."""
    rows = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x]
    return {r["origin"]: r for r in rows}


def _load(name: str, store: Path, records: Path) -> list[Record]:
    """One account's origins, oldest first: its saved futures with the outcome fields of its
    experiment 3 records. Called only after the held-out check."""
    data = load(store / f"{name}.npz")
    if str(data["account"]) != name:
        raise ValueError(f"{store / name}.npz holds another account")
    by_origin = read_records(records / f"{name}.jsonl")
    out = []
    for i, origin in enumerate(data["origin"].tolist()):
        rec = by_origin[origin]
        if rec.get("held_out"):
            raise ValueError("a held-out record is not for tuning or checking")
        if (rec["start_balance_cents"], rec["buffer_cents"]) != (
            int(data["start_cents"][i]), int(data["buffer_cents"][i])
        ):  # fmt: skip
            raise ValueError(f"{name} {origin}: the store and the records disagree")
        out.append({**{k: rec[k] for k in OUTCOME_FIELDS}, "paths": data["paths"][i],
                    "account": name})  # fmt: skip
    return sorted(out, key=lambda r: r["origin"])


def load_persona(pid: str, store: Path, records: Path) -> list[Record]:
    """One development persona. A held-out persona is refused before any file is opened."""
    assert_development_only([pid])
    return _load(pid, store, records)


def load_account(account: int, store: Path, records: Path) -> list[Record]:
    """One Berka development account. A held-out account is refused before any file is opened."""
    berka_development_only([account])
    return _load(str(account), store, records)


def load_set(name: str, root: Path, limit: int | None = None) -> list[Record]:
    """Every development persona of a synthetic set ("12" or "long"), or every Berka primary
    development account ("berka"), oldest origin first within each."""
    store, records = root / STORE[name], root / RECORDS[name]
    if name == "berka":
        ids = development_accounts("primary")[:limit]
        return [r for a in ids for r in load_account(a, store, records)]
    pids = development_ids(long=name == "long")[:limit]
    return [r for pid in pids for r in load_persona(pid, store, records)]


# --- Learning the weights (rule zero: origins whose 30 days are over) ---


def _dates(rows: list[Record]) -> np.ndarray:
    return np.array([date.fromisoformat(r["origin"]) for r in rows], dtype="datetime64[D]")


def eligibility(rows: list[Record], leave_out: str) -> dict[str, list[bool]]:
    """Which origins get a learned weight, from the origin dates alone (nothing is scored).
    (iii): at least MIN_OWN of the account's own origins are resolved. (ii): the resolved
    origins of the accounts outside the left-out group (the archetype on synthetic, the account
    on Berka) number at least MIN_OTHER_ORIGINS, from at least MIN_OTHER_ACCOUNTS accounts."""
    d = _dates(rows)
    acct = np.array([r["account"] for r in rows])
    group = np.array([r[leave_out] for r in rows])
    own, glob = [], []
    for i in range(len(rows)):
        past = d <= d[i] - np.timedelta64(LAG.days, "D")
        own.append(int((past & (acct == acct[i])).sum()) >= MIN_OWN)
        other = past & (group != group[i])
        glob.append(
            int(other.sum()) >= MIN_OTHER_ORIGINS
            and len(set(acct[other].tolist())) >= MIN_OTHER_ACCOUNTS
        )
    return {"ii": glob, "iii": own}


def prepare(rows: list[Record]) -> None:
    """Add each origin's median and the loss of every weight of the grid."""
    for r in rows:
        r["median"] = nearest_rank(r["paths"], 0.5)
        r["loss"] = losses(r["paths"], r["start_balance_cents"], r["median"],
                           np.asarray(r["realized"], dtype=float))  # fmt: skip


def weights(rows: list[Record], leave_out: str) -> dict[str, list[float]]:
    """The weight of every origin under each variant. Learned weights use only resolved origins
    (t' <= t - 35 days); an origin that does not meet the minimums keeps w = 1."""
    ok = eligibility(rows, leave_out)
    d = _dates(rows)
    acct = np.array([r["account"] for r in rows])
    group = np.array([r[leave_out] for r in rows])
    loss = np.array([r["loss"] for r in rows])
    if np.any(loss[:, -1] <= 0):
        raise ValueError("an origin has loss(1) = 0, so loss(w) / loss(1) is undefined: stop")
    ratio = loss / loss[:, -1:]
    out: dict[str, list[float]] = {"i": [FIXED] * len(rows), "ii": [], "iii": []}
    for i in range(len(rows)):
        past = d <= d[i] - np.timedelta64(LAG.days, "D")
        mine = past & (acct == acct[i])
        other = past & (group != group[i])
        out["iii"].append(best_weight(loss[mine].mean(axis=0)) if ok["iii"][i] else 1.0)
        out["ii"].append(best_weight(ratio[other].mean(axis=0)) if ok["ii"][i] else 1.0)
    return out


def views(rows: list[Record], w: dict[str, list[float]]) -> dict[str | None, list[Record]]:
    """The records the measures read: the default (key None) and each variant."""
    out: dict[str | None, list[Record]] = {None: [], **{k: [] for k in VARIANTS}}
    for i, r in enumerate(rows):
        base = {k: r[k] for k in OUTCOME_FIELDS}
        for key in out:
            wk = 1.0 if key is None else w[key][i]
            v = view(r["paths"], r["start_balance_cents"], r["buffer_cents"], r["median"],
                     np.asarray(r["realized"]), wk)  # fmt: skip
            point = np.asarray(r["point"]) - shift(r["start_balance_cents"], r["median"], wk)
            out[key].append({**base, **v, "point": np.rint(point).astype(int).tolist(),
                             "account": r["account"]})  # fmt: skip
    return out


# --- The measures of the rule ---


def ordinary(rows: list[Record]) -> list[Record]:
    return [r for r in rows if not r["in_shortfall_at_origin"]]


def stats(rows: list[Record]) -> Record | None:
    """The dip measures, on the origins not already below the line."""
    o = ordinary(rows)
    if not o:
        return None
    m = forecast_metrics(o)
    return {"n": len(o), "accounts": len({r["persona"] for r in o}),
            "err30": m["error"][30]["mean_p50"], "bias": m["lowest"]["bias"],
            "cov30": m["coverage"][30], "covall": m["coverage"]["all"],
            "width30": m["width"][30], "brier": m["brier"]["brier"]}  # fmt: skip


def crps_mean(rows: list[Record]) -> float:
    """CRPS in dollars, mean over origins and the 30 days (every origin)."""
    return float(np.mean([np.mean(r["crps"]) for r in rows])) / 100


def crps_stays(rows: list[Record]) -> float:
    return float(np.mean([np.mean(r["crps_stays"]) for r in rows])) / 100


def events(rows: list[Record]) -> int:
    return sum(r["realized_first_below_buffer"] is not None for r in ordinary(rows))


def brier_stays(rows: list[Record]) -> float:
    """ "Balance stays" never dips from an ordinary origin: its Brier score is the dip rate."""
    return events(rows) / len(ordinary(rows))


def by(rows: list[Record], key: str) -> dict[str, list[Record]]:
    out: dict[str, list[Record]] = {}
    for r in rows:
        out.setdefault(str(r[key]), []).append(r)
    return dict(sorted(out.items()))


UNMOVED_FIELDS = ("p10", "p50", "p90", "point", "shortfall_probability", "median_lowest_cents",
                  "crps")  # fmt: skip


def condition_1(default: list[Record], variant: list[Record], store: tuple[int, int]) -> Record:
    """Origins with w = 1 equal the default to the digit, and the saved futures reproduce the
    experiment 3 records exactly (`store` = origins checked, origins equal)."""
    same = [(d, v) for d, v in zip(default, variant, strict=True) if v["w"] == 1.0]
    differing = sum(any(d[f] != v[f] for f in UNMOVED_FIELDS) for d, v in same)
    checked, equal = store
    return {"pass": differing == 0 and checked > 0 and equal == checked, "n": len(same),
            "differing": differing, "store_checked": checked, "store_equal": equal}  # fmt: skip


def dips_caught(default: list[Record], variant: list[Record]) -> Record:
    """Condition 6: dips caught on ahead at each method's own threshold (70 percent precision and
    the false-alert cap). A method with no such threshold catches 0 dips."""
    out: Record = {}
    for name, rows in (("default", default), ("variant", variant)):
        pick = choose(curve(rows, THRESHOLDS))
        m = alert_metrics(rows, pick["threshold"], median_rule=False) if pick else None
        out[name] = {"threshold": pick["threshold"] if pick else None,
                     "caught_on": m["caught_on"] if m else 0}  # fmt: skip
    out["episodes"] = alert_metrics(default, THRESHOLDS[0], median_rule=False)["shortfall_episodes"]
    both_none = out["default"]["threshold"] is None and out["variant"]["threshold"] is None
    out["note"] = "neither method has a threshold meeting both conditions" if both_none else ""
    out["pass"] = out["variant"]["caught_on"] >= out["default"]["caught_on"]
    return out


def synthetic_conditions(
    v: dict[str | None, list[Record]], key: str, store: tuple[int, int]
) -> dict[str, Record]:  # fmt: skip
    """Conditions 1 to 7 and 11 on one synthetic set, for one variant."""
    d_rows, v_rows = v[None], v[key]
    arch_d, arch_v = by(d_rows, "archetype"), by(v_rows, "archetype")
    out: dict[str, Record] = {"1": condition_1(d_rows, v_rows, store)}
    table = {a: (stats(arch_d[a]), stats(arch_v[a])) for a in arch_d}
    # 2: no harm
    bad_a = [
        a
        for a, (d, s) in table.items()
        if s["err30"] > d["err30"] * 1.01 or abs(s["bias"]) > abs(d["bias"]) * 1.01
    ]
    pers_d, pers_v = by(d_rows, "persona"), by(v_rows, "persona")
    bad_p = []
    for p in pers_d:
        d, s = stats(pers_d[p]), stats(pers_v[p])
        if d and s and (s["err30"] > d["err30"] * 1.05 or abs(s["bias"]) > abs(d["bias"]) * 1.05):
            bad_p.append(p)
    out["2"] = {"pass": not bad_a and not bad_p, "archetypes_worse": bad_a,
                "personas_worse": bad_p, "n_archetypes": len(table),
                "n_personas": len(pers_d)}  # fmt: skip
    # 3: a real gain
    rel_e, rel_b, net, shares = [], [], [], {}
    for a, (d, s) in table.items():
        de, db = rel(s["err30"], d["err30"]), rel(abs(s["bias"]), abs(d["bias"]))
        rel_e.append(de)
        rel_b.append(db)
        o = ordinary(arch_v[a])
        shares[a] = sum(r["w"] < 1 for r in o) / len(o)
        if de < -0.01 and db <= 0.01 and shares[a] >= 0.5:
            net.append(a)
    out["3"] = {"pass": len(net) >= 2 and float(np.mean(rel_e)) < 0 and float(np.mean(rel_b)) < 0,
                "net_better": net, "mean_rel_err30": float(np.mean(rel_e)),
                "mean_rel_abs_bias": float(np.mean(rel_b)), "share_w_below_1": shares,
                "rel_err30": dict(zip(table, rel_e, strict=True)),
                "rel_abs_bias": dict(zip(table, rel_b, strict=True))}  # fmt: skip
    # 4: honest band
    cov_bad = [a for a, (d, s) in table.items()
               if cov_gap(s["cov30"]) > cov_gap(d["cov30"]) + 0.02
               or cov_gap(s["covall"]) > cov_gap(d["covall"]) + 0.02]  # fmt: skip
    wide = [a for a, (d, s) in table.items() if s["width30"] > d["width30"] * 1.10]
    out["4"] = {"pass": not cov_bad and not wide, "coverage_worse": cov_bad, "wider": wide}
    # 5: CRPS skill against balance stays, mean over archetypes (every origin, mean over 30 days)
    skill_d = float(np.mean([1 - crps_mean(arch_d[a]) / crps_stays(arch_d[a]) for a in arch_d]))
    skill_v = float(np.mean([1 - crps_mean(arch_v[a]) / crps_stays(arch_v[a]) for a in arch_v]))
    out["5"] = {"pass": skill_v >= skill_d, "default": skill_d, "variant": skill_v}
    # 6: dips caught
    out["6"] = dips_caught(d_rows, v_rows)
    # 7: Brier skill over archetypes with MIN_DIPS or more dips
    dippy = [a for a in arch_d if events(arch_d[a]) >= MIN_DIPS]
    if dippy:
        bs_d = float(np.mean([1 - table[a][0]["brier"] / brier_stays(arch_d[a]) for a in dippy]))
        bs_v = float(np.mean([1 - table[a][1]["brier"] / brier_stays(arch_d[a]) for a in dippy]))
        out["7"] = {"pass": bs_v >= bs_d, "default": bs_d, "variant": bs_v, "archetypes": dippy,
                    "dips": sum(events(arch_d[a]) for a in dippy)}  # fmt: skip
    else:
        out["7"] = {"pass": None, "archetypes": [], "dips": 0}  # not measurable: a stop
    # 11: the mean over archetypes of the CRPS (every origin, mean over 30 days) falls
    c_d = float(np.mean([crps_mean(arch_d[a]) for a in arch_d]))
    c_v = float(np.mean([crps_mean(arch_v[a]) for a in arch_v]))
    out["11"] = {"pass": c_v < c_d, "default": c_d, "variant": c_v}
    return out


def account_crps30(rows: list[Record]) -> dict[str, float]:
    """The day-30 CRPS of each account, in dollars, mean over its origins."""
    return {a: float(np.mean([r["crps"][29] for r in g])) / 100 for a, g in by(rows, "account").items()}  # fmt: skip  # noqa: E501


def berka_conditions(
    v: dict[str | None, list[Record]], key: str, store: tuple[int, int], cash: set[str]
) -> dict[str, Record]:  # fmt: skip
    """Conditions 1, 6 to 11 on the Berka primary development accounts, for one variant."""
    d_rows, v_rows = v[None], v[key]
    out: dict[str, Record] = {
        "1": condition_1(d_rows, v_rows, store),
        "6": dips_caught(d_rows, v_rows),
    }
    d, s = stats(d_rows), stats(v_rows)
    out["7"] = {"pass": rel(s["brier"], d["brier"]) <= 0.01, "default": d["brier"],
                "variant": s["brier"], "n": d["n"]}  # fmt: skip
    cd, cv = account_crps30(d_rows), account_crps30(v_rows)
    accts = sorted(cd)
    g, lo, hi = bootstrap_gain(np.array([cd[a] for a in accts]), np.array([cv[a] for a in accts]))
    out["8"] = {"pass": g >= 0.01 and lo > 0, "gain": g, "low": lo, "high": hi,
                "accounts": len(accts), "default": float(np.mean(list(cd.values())))}  # fmt: skip
    out["9"] = {"pass": rel(s["err30"], d["err30"]) <= 0.01
                and cov_gap(s["cov30"]) <= cov_gap(d["cov30"]) + 0.02
                and cov_gap(s["covall"]) <= cov_gap(d["covall"]) + 0.02,
                "default": d, "variant": s}  # fmt: skip
    zero_d = [r for r in d_rows if r["everyday"] == 0]
    zero_v = [r for r in v_rows if r["everyday"] == 0]
    zd, zv = stats(zero_d), stats(zero_v)
    zero_ok = bool(zd and zv) and (
        rel(zv["err30"], zd["err30"]) <= 0.01 and rel(abs(zv["bias"]), abs(zd["bias"])) <= 0.01
        and cov_gap(zv["cov30"]) <= cov_gap(zd["cov30"]) + 0.02
        and cov_gap(zv["covall"]) <= cov_gap(zd["covall"]) + 0.02
    )  # fmt: skip
    rest = [a for a in accts if a not in cash]
    gr = bootstrap_gain(np.array([cd[a] for a in rest]), np.array([cv[a] for a in rest]))
    out["10"] = {"pass": zero_ok and (gr[0] > 0) == (g > 0), "zero_default": zd,
                 "zero_variant": zv, "zero_accounts": len({r["account"] for r in zero_d}),
                 "without_cash_gain": gr[0], "without_cash_accounts": len(rest)}  # fmt: skip
    out["11"] = {"pass": float(np.mean(list(cv.values()))) < float(np.mean(list(cd.values()))),
                 "default": float(np.mean(list(cd.values()))),
                 "variant": float(np.mean(list(cv.values())))}  # fmt: skip
    return out


SYNTHETIC_CONDITIONS = ("1", "2", "3", "4", "5", "6", "7", "11")
BERKA_CONDITIONS = ("1", "6", "7", "8", "9", "10", "11")


def passes(conditions: dict[str, Record]) -> bool:
    return all(c["pass"] is True for c in conditions.values())


# --- Running a set ---


def store_check(name: str, root: Path) -> tuple[int, int]:
    """The futures-store gate for one set: (origins checked, origins equal) against the
    experiment 3 records (condition 1, second part)."""
    checked = equal = 0
    for path in sorted((root / STORE[name]).glob("*.npz")):
        if name == "berka":
            berka_development_only([int(path.stem)])
        else:
            assert_development_only([path.stem])
        c, e = check_file(path, read_record_list(root / RECORDS[name] / f"{path.stem}.jsonl"))
        checked += c
        equal += e
    return checked, equal


def eligible_counts(rows: list[Record], leave_out: str) -> dict[str, Record]:
    ok = eligibility(rows, leave_out)
    return {k: {"eligible": sum(ok[k]), "origins": len(rows),
                "accounts": len({r["account"] for r, e in zip(rows, ok[k], strict=True) if e})}
            for k in LEARNED}  # fmt: skip


def run_set(name: str, root: Path) -> Record:
    rows = load_set(name, root)
    leave_out = "account" if name == "berka" else "archetype"
    counts = eligible_counts(rows, leave_out)
    prepare(rows)
    w = weights(rows, leave_out)
    v = views(rows, w)
    learned = {k: {a: [x for x, r in zip(w[k], rows, strict=True) if r["archetype"] == a]
                   for a in sorted({r["archetype"] for r in rows})} for k in LEARNED}  # fmt: skip
    return {"name": name, "rows": rows, "weights": w, "views": v, "eligible": counts,
            "learned": learned, "store": store_check(name, root)}  # fmt: skip


# --- The report ---


def _pct(x: float) -> str:
    return f"{x:+.1%}"


def _mark(p: bool | None) -> str:
    return "not measurable" if p is None else ("pass" if p else "FAIL")


def describe(c: str, r: Record) -> str:
    """One line of evidence for a condition."""
    if c == "1":
        return (f"origins at w = 1: {r['n']:,}, differing from the default: {r['differing']}; "
                f"saved futures equal to the experiment 3 records: {r['store_equal']:,} of "
                f"{r['store_checked']:,}")  # fmt: skip
    if c == "2":
        return (f"archetypes worse by more than 1%: {', '.join(r['archetypes_worse']) or 'none'} "
                f"(of {r['n_archetypes']}); personas worse by more than 5%: "
                f"{', '.join(r['personas_worse']) or 'none'} (of {r['n_personas']})")  # fmt: skip
    if c == "3":
        return (f"net better: {', '.join(r['net_better']) or 'none'} ({len(r['net_better'])}; 2 "
                f"needed); mean relative change over archetypes: 30-day error "
                f"{_pct(r['mean_rel_err30'])}, absolute bias {_pct(r['mean_rel_abs_bias'])} "
                f"(both must be below 0)")  # fmt: skip
    if c == "4":
        return (f"coverage further from 80% by more than 2 points: "
                f"{', '.join(r['coverage_worse']) or 'none'}; 30-day band wider by more than 10%: "
                f"{', '.join(r['wider']) or 'none'}")  # fmt: skip
    if c == "5":
        return f"mean CRPS skill against balance stays: default {r['default']:+.4f}, variant {r['variant']:+.4f}"  # noqa: E501
    if c == "6":
        d, v = r["default"], r["variant"]
        note = f"; {r['note']}" if r["note"] else ""
        return (f"dips caught on ahead: default {d['caught_on']} (threshold {d['threshold']}), "
                f"variant {v['caught_on']} (threshold {v['threshold']}); shortfall episodes "
                f"{r['episodes']}{note}")  # fmt: skip
    if c == "7" and "archetypes" in r:
        if r["pass"] is None:
            return "no archetype has 5 or more dips"
        return (f"mean Brier skill against balance stays over {len(r['archetypes'])} archetypes "
                f"with 5+ dips ({r['dips']} dips): default {r['default']:+.4f}, variant "
                f"{r['variant']:+.4f}")  # fmt: skip
    if c == "7":
        return (
            f"Brier of the dip chance: default {r['default']:.4f}, variant {r['variant']:.4f} "
            f"({_pct(rel(r['variant'], r['default']))}; limit +1%), n = {r['n']:,} origins"
        )
    if c == "8":
        return (
            f"day-30 CRPS, accounts weighted equally (n = {r['accounts']}): default "
            f"${r['default']:,.2f}; relative gain {_pct(r['gain'])}, 95% bootstrap "
            f"[{_pct(r['low'])}, {_pct(r['high'])}]; needs +1.0% and a lower end above 0"
        )
    if c == "9":
        d, v = r["default"], r["variant"]
        return (f"30-day error ${d['err30']:,.0f} → ${v['err30']:,.0f} "
                f"({_pct(rel(v['err30'], d['err30']))}); coverage at 30 days {d['cov30']:.1%} → "
                f"{v['cov30']:.1%}, all days {d['covall']:.1%} → {v['covall']:.1%}")  # fmt: skip
    if c == "10":
        zd, zv = r["zero_default"], r["zero_variant"]
        z = (f"zero-estimate stratum ({zd['n']:,} ordinary origins, {r['zero_accounts']} accounts):"
             f" error ${zd['err30']:,.0f} → ${zv['err30']:,.0f}, bias ${zd['bias']:,.0f} → "
             f"${zv['bias']:,.0f}, coverage {zd['cov30']:.1%} → {zv['cov30']:.1%} (30 days), "
             f"{zd['covall']:.1%} → {zv['covall']:.1%} (all days)") if zd and zv else "zero-estimate stratum: too few origins"  # fmt: skip  # noqa: E501
        return (f"{z}; without the mostly-cash accounts ({r['without_cash_accounts']} accounts) "
                f"the day-30 CRPS gain is {_pct(r['without_cash_gain'])}")  # fmt: skip
    if c == "11":
        return f"mean CRPS: default ${r['default']:,.2f}, variant ${r['variant']:,.2f} (must be strictly lower)"  # noqa: E501
    return ""


LABELS = {
    "1": "1 unchanged where it should be",
    "2": "2 no harm",
    "3": "3 a real gain",
    "4": "4 honest band",
    "5": "5 CRPS not just flatter",
    "6": "6 dips caught must not fall",
    "7": "7 dip Brier skill must not fall",
    "8": "8 Berka margin",
    "9": "9 Berka other measures",
    "10": "10 vetoes",
    "11": "11 synthetic and Berka agree",
}


def report(results: dict[str, Record], verdicts: dict[str, dict[str, dict[str, Record]]]) -> str:
    lines = ["# Experiment 4: online combination with \"balance stays\"", "",
             f"Made with `python -m eval.combination run`. Rule: {PLAN}. Nothing here changes a "
             "default and nothing is adopted. **Variants counted: 3** ((i) fixed 50/50, (ii) one "
             "global weight, (iii) per-account weights); scaling the futures and replacing a share "
             "of them were not tried. Condition 12 (my account can only veto) is not judged here. "
             "The synthetic sets are never pooled.", ""]  # fmt: skip
    for name, res in results.items():
        title = {
            "12": "12-month set",
            "long": "24-month set",
            "berka": "Berka primary development",
        }[name]  # noqa: E501
        rows = res["rows"]
        lines += [f"## {title}", "",
                  f"Origins: {len(rows):,}, of which {len(ordinary(rows)):,} not already below the "
                  f"line; accounts: {len({r['account'] for r in rows})}.", "",
                  "**Origins that get a learned weight** (counted from the dates before scoring): "
                  + "; ".join(f"{VARIANTS[k]} {c['eligible']:,} of {c['origins']:,} "
                              f"({c['eligible'] / c['origins']:.1%}), {c['accounts']} accounts"
                              for k, c in res["eligible"].items()) + ".", ""]  # fmt: skip
        if name != "berka":
            lines += [
                "**Learned weights by archetype** (mean w over origins; share of origins "
                "with w < 1):",
                "",
                "| archetype | (ii) | (iii) |",
                "|---|---|---|",
            ]
            for a in res["learned"]["ii"]:
                cells = [
                    f"{np.mean(res['learned'][k][a]):.2f} "
                    f"({np.mean(np.array(res['learned'][k][a]) < 1):.0%})"
                    for k in LEARNED
                ]
                lines.append(f"| {a} | {cells[0]} | {cells[1]} |")
            lines.append("")
        for key, conds in verdicts[name].items():
            lines += [f"### {VARIANTS[key]}: {'passes' if passes(conds) else 'does not pass'}", ""]
            lines += [
                f"- **{LABELS[c]}: {_mark(r['pass'])}.** {describe(c, r)}."
                for c, r in conds.items()
            ]
            lines.append("")
    columns = [f"{n} {k}" for n in verdicts for k in verdicts[n]]
    lines += ["## Verdict by condition (judged, not decided: the decision is mine)", "",
              "| condition | " + " | ".join(columns) + " |",
              "|---|" + "---|" * len(columns)]  # fmt: skip
    for c in LABELS:
        cells = [_mark(verdicts[n][k][c]["pass"]) if c in verdicts[n][k] else "-"
                 for n in verdicts for k in verdicts[n]]  # fmt: skip
        lines.append(f"| {LABELS[c]} | " + " | ".join(cells) + " |")
    lines.append("| 12 my account can only veto | " + " | ".join(
        "not judged" for n in verdicts for _ in verdicts[n]) + " |")  # fmt: skip
    return "\n".join(lines) + "\n"


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.generic):
        return x.item()
    return x


# --- Commands ---


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.combination", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("command", choices=["eligible", "quick", "run"])
    parser.add_argument("--set", choices=["12", "long", "berka"], default="12")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="the folder that holds data/ (the saved futures and the records)",
    )
    parser.add_argument("--berka", nargs="*", choices=list(VARIANTS), default=[],
                        help="also score these variants on Berka")  # fmt: skip
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    if args.command == "eligible":
        rows = load_set(args.set, args.root)
        counts = eligible_counts(rows, "account" if args.set == "berka" else "archetype")
        for k, c in counts.items():
            print(
                f"{args.set} {VARIANTS[k]}: {c['eligible']} of {c['origins']} origins get a "
                f"learned weight ({c['eligible'] / c['origins']:.1%}), {c['accounts']} accounts"
            )
        if args.set == "berka" and any(c["eligible"] < c["origins"] / 2 for c in counts.values()):
            print("STOP: fewer than half of the Berka origins get a learned weight")
            return 3
        return 0
    if args.command == "quick":
        for name in ("12", "long"):
            start = time.time()
            rows = load_set(name, args.root, limit=4)
            prepare(rows)
            w = weights(rows, "archetype")
            views(rows, w)
            print(f"quick {name}: {len(rows)} origins of 4 personas loaded, weighed and viewed in "
                  f"{time.time() - start:.1f} s; w < 1 at "
                  + ", ".join(f"{VARIANTS[k]} {sum(x < 1 for x in w[k])}" for k in w))  # fmt: skip
        return 0
    results: dict[str, Record] = {}
    verdicts: dict[str, dict[str, dict[str, Record]]] = {}
    for name in ("12", "long"):
        start = time.time()
        res = run_set(name, args.root)
        results[name] = res
        verdicts[name] = {k: synthetic_conditions(res["views"], k, res["store"]) for k in VARIANTS}
        print(f"{name}: scored in {time.time() - start:.0f} s", flush=True)
    if args.berka:
        rows = load_set("berka", args.root)
        counts = eligible_counts(rows, "account")
        if any(c["eligible"] < c["origins"] / 2 for c in counts.values()):
            print(
                "STOP: fewer than half of the Berka origins get a learned weight; Berka not scored"
            )
            return 3
        res = run_set("berka", args.root)
        results["berka"] = res
        cash = {str(a) for a in json.loads(CASH_FILE.read_text("utf-8"))["accounts"]}
        verdicts["berka"] = {
            k: berka_conditions(res["views"], k, res["store"], cash) for k in args.berka
        }  # noqa: E501
    text = report(results, verdicts)
    REPORT.write_text(text, encoding="utf-8")
    REPORT.with_suffix(".json").write_text(
        json.dumps(
            _jsonable(
                {
                    "plan": PLAN,
                    "verdicts": verdicts,
                    "eligible": {n: r["eligible"] for n, r in results.items()},
                }
            ),
            indent=1,
        ),
        "utf-8",
    )
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
