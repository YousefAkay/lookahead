"""The fresh held-out set for forecaster v2, locked before any v2 code exists (DECISIONS.md,
2026-11-27). It is never used to tune anything and is opened once, by the v2 test run.

    python -m eval.fresh_set berka --data ../lookahead-runs/data/berka
    python -m eval.fresh_set personas
    python -m eval.fresh_set fingerprint

`berka` reruns the count-only filter of the 2026-10-17 split (months of history, non-mechanical
transactions a month, the longest quiet stretch: no balance, no forecast, no outcome), keeps the
accounts that pass the primary filter and are in none of the four lists already used (primary and
sensitivity, development and held-out), orders them by a hash of a new salt and takes the first
200. `personas` writes the 10 fresh personas (the held-out mix under new ids and a new parameter
seed, lookahead/generator/archetypes.py `build_fresh`), 12 and 24 months, into FOLDER, which is
git-ignored and refused by the guard (tools/heldout_guard.py). Both write their lists and
fingerprints to LIST_FILE, which is committed, and print only counts and fingerprints.
`fingerprint` checks the folder's files against LIST_FILE and prints how many match.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from eval.berka_gate import load, per_account
from eval.berka_split import PRIMARY_PER_MONTH, load_split, passes
from lookahead.generator.archetypes import (
    END,
    FRESH_IDS,
    FRESH_LONG_IDS,
    FRESH_PARAM_SEED,
    build_fresh,
)
from lookahead.generator.writer import write_persona

FOLDER = Path("data/forecast_lockbox")
LIST_FILE = Path(__file__).resolve().parents[1] / "docs" / "fresh_set.json"  # this checkout's
SALT = "berka-fresh-2026-11-27"
PER_SET = 200
TXN_SEED = 1127  # the transaction draws of the fresh personas (the development sets use 42)
USED = ("primary_development", "primary_held_out", "sensitivity_development",
        "sensitivity_held_out")  # fmt: skip


def digest(account_id: int) -> str:
    return hashlib.sha256(f"{SALT}:{int(account_id)}".encode()).hexdigest()


def list_hash(ids: list) -> str:
    return hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()


def choose(passing: list[int], used: set[int], n: int = PER_SET) -> list[int]:
    """The first `n` accounts by the new salt's hash, among those that pass and were never used."""
    fresh = [a for a in passing if a not in used]
    return sorted(sorted(fresh, key=digest)[:n])


def _read() -> dict:
    return json.loads(LIST_FILE.read_text(encoding="utf-8")) if LIST_FILE.exists() else {}


def _write(data: dict) -> None:
    LIST_FILE.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def berka(data_dir: Path) -> int:
    lists = _read()
    if "berka" in lists:
        print("the fresh Berka list is already locked; nothing done")
        return 1
    acc = per_account(load(data_dir))  # counts per account only
    passing = [int(a) for a in acc.index[passes(acc, PRIMARY_PER_MONTH)]]
    split = load_split()
    used = {int(a) for k in USED for a in split[k]}  # type: ignore[attr-defined]
    chosen = choose(passing, used)
    lists["berka"] = {
        "rule": f"primary filter of the 2026-10-17 split; not in any of {', '.join(USED)}; "
                f"ordered by sha256('{SALT}:<account_id>'); the first {PER_SET}",
        "primary_filter_accounts": len(passing),
        "used_primary_filter_accounts": len(used & set(passing)),
        "never_used": len([a for a in passing if a not in used]),
        "chosen": chosen,
        "sha256": list_hash(chosen),
    }  # fmt: skip
    _write(lists)
    b = lists["berka"]
    print(f"primary filter {b['primary_filter_accounts']}, already used "
          f"{b['used_primary_filter_accounts']}, never used {b['never_used']}, chosen "
          f"{len(chosen)}; list sha256 {b['sha256']}")  # fmt: skip
    return 0


def _file_hashes() -> dict[str, str]:
    out = {}
    for sub in ("12", "long"):
        folder = FOLDER / sub
        if folder.is_dir():
            for f in sorted(folder.iterdir()):
                out[f"{sub}/{f.name}"] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def personas() -> int:
    lists = _read()
    if "personas" in lists or any((FOLDER / s).exists() for s in ("12", "long")):
        print("the fresh personas are already written; nothing done")
        return 1
    for ids, sub in ((FRESH_IDS, "12"), (FRESH_LONG_IDS, "long")):
        for pid in ids:
            p = build_fresh(pid)
            write_persona(p.persona, FOLDER / sub, TXN_SEED, p.start, END)
    files = _file_hashes()
    lists["personas"] = {
        "ids_12_month": list(FRESH_IDS), "ids_24_month": list(FRESH_LONG_IDS),
        "param_seed": FRESH_PARAM_SEED, "transaction_seed": TXN_SEED,
        "files": files, "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode())
        .hexdigest(),
    }  # fmt: skip
    _write(lists)
    print(f"{len(FRESH_IDS)} + {len(FRESH_LONG_IDS)} personas, {len(files)} files written; "
          f"set sha256 {lists['personas']['sha256']}")  # fmt: skip
    return 0


def fingerprint() -> int:
    lists = _read()
    saved = lists.get("personas", {}).get("files", {})
    now = _file_hashes()
    same = sum(now.get(k) == v for k, v in saved.items())
    print(f"persona files: {same} of {len(saved)} match, {len(now)} in the folder")
    if "berka" in lists:
        b = lists["berka"]
        ok = list_hash(b["chosen"]) == b["sha256"]
        print(f"Berka list: {len(b['chosen'])} accounts, sha256 {'matches' if ok else 'DIFFERS'}")
    return 0 if same == len(saved) == len(now) else 1


def fresh_berka_accounts() -> set[int]:
    """The fresh Berka accounts: never development. The list is committed; if it is missing, no
    check can pass, so this raises instead of letting every account through."""
    if not LIST_FILE.exists():
        raise ValueError("the fresh held-out list (docs/fresh_set.json) is missing")
    return {int(a) for a in _read().get("berka", {}).get("chosen", [])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.fresh_set", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("berka")
    b.add_argument("--data", type=Path, required=True)
    sub.add_parser("personas")
    sub.add_parser("fingerprint")
    args = parser.parse_args(argv)
    if args.command == "berka":
        return berka(args.data)
    return personas() if args.command == "personas" else fingerprint()


if __name__ == "__main__":
    sys.exit(main())
