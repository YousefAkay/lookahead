"""Merchant resolution: decide which extracted names are the same merchant (DECISIONS.md).

Automatic (each one can be undone by your "split"):
- same text: equal after ignoring case, accents, punctuation and spacing
- accents: the bank's "?" stands for one accented letter ("Caf? Velisaro" = "Cafe Velisaro")
- alias list: a small built-in list of known bank spellings ("Uber Canada/Ubereats" = Uber Eats)
- cut prefix: a card name that fills the bank's 20-character field (so the bank may have cut
  it) and is the start of exactly one other merchant's name
- your merges: stored in the database (merchants + merchant_aliases tables)

Only proposed, waiting for you (never merged on their own):
- cut prefix, ambiguous: a cut name that starts two or more different merchants' names
- prefix: a name of 2+ words that starts a longer name at a word boundary, but is shorter
  than the cut length, so the bank did not necessarily cut it
- similar names: TF-IDF cosine of character 3-grams (fitted on the names being resolved, so
  chunks many names share count less) at or above `SIMILAR_AT`

Counts-only commands (names go only to a file in data/private/, never to the terminal):
    python -m lookahead.resolve report --db data/private/db/lookahead.db --write-proposals
    python -m lookahead.resolve apply --db data/private/db/lookahead.db
"""

import argparse
import csv
import io
import itertools
import math
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from lookahead.db import connect
from lookahead.merchants import extract_merchant, is_private_path

CUT = 20  # the bank's card merchant field: a name this long may have been cut
SIMILAR_AT = 0.4  # chosen on the dev half of the variant set (see DECISIONS.md)
DEFAULT_PROPOSALS_FILE = Path("data/private/eval/merchant_merge_proposals.tsv")

# Known bank spellings of well-known brands: general knowledge, nothing from real data.
ALIASES: dict[str, list[str]] = {
    "Uber Eats": ["Uber Canada/Ubereats", "Ubereats", "Uber Eats"],
    "Uber": ["Uber Canada/Ubertrip", "Ubertrip", "Uber Trip", "Uber"],
    "STM": ["Stm-Opus", "STM"],
    "Netflix": ["Netflix.Com", "Netflix"],
    "Spotify": ["Spotify"],
    "Amazon": ["Amazon.Ca", "Amazon.Com", "Amzn Mktp Ca", "Amzn Mktp Us"],
    "Steam": ["Steamgames.Com", "Steam Purchase", "Steam"],
    "DoorDash": ["Doordash", "Dd Doordash"],
    "Hydro-Québec": ["Hydro-Quebec", "Hydro Quebec"],
    "Vidéotron": ["Videotron"],
}

_PROPOSAL_ORDER = ("cut prefix, ambiguous", "prefix", "similar names")
_RULES = ("same text", "accents", "alias list", "cut prefix", "your merges")


def name_key(name: str) -> str:
    """Compare form: lowercase, no accents, punctuation as spaces ("Couche-Tard" -> "couche
    tard"). The bank's "?" is kept, since it stands for an unknown letter."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w?]+|_", " ", plain).split())


ALIAS_KEYS: dict[str, str] = {
    name_key(spelling): canonical
    for canonical, spellings in ALIASES.items()
    for spelling in spellings
}


def _grams(name: str) -> Counter[str]:
    """Character 3-grams inside each word, padded with spaces ("char_wb" in scikit-learn):
    "Tim Hortons" -> " ti", "tim", "im ", " ho", "hor", ..."""
    grams: Counter[str] = Counter()
    for word in name_key(name).split():
        padded = f" {word} "
        grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return grams


def tfidf_vectors(names: list[str]) -> list[dict[str, float]]:
    """TF-IDF vectors of character 3-grams, fitted on `names` itself.

    A 3-gram found in many names (a country, "restaurant") gets a low weight; a rare one a
    high weight. Same formula as scikit-learn's default: idf = ln((1 + n) / (1 + df)) + 1,
    weight = count x idf, then each vector is scaled to length 1 (so cosine = dot product).
    """
    counts = [_grams(n) for n in names]
    df = Counter(g for c in counts for g in c)  # in how many names each 3-gram appears
    idf = {g: math.log((1 + len(names)) / (1 + d)) + 1 for g, d in df.items()}
    vectors = []
    for c in counts:
        weights = {g: tf * idf[g] for g, tf in c.items()}
        norm = math.sqrt(sum(w * w for w in weights.values()))
        vectors.append({g: w / norm for g, w in weights.items()} if norm else {})
    return vectors


def _cosine(u: dict[str, float], v: dict[str, float]) -> float:
    if len(u) > len(v):
        u, v = v, u
    return round(sum(w * v.get(g, 0.0) for g, w in u.items()), 9)


def similarity(a: str, b: str, corpus: Iterable[str] = ()) -> float:
    """TF-IDF cosine of character 3-grams, weights fitted on `corpus` plus the two names:
    1.0 = same text, 0.0 = nothing in common."""
    names = list(dict.fromkeys([a, b, *corpus]))
    vectors = tfidf_vectors(names)
    return _cosine(vectors[0], vectors[names.index(b)])


def _same(a: str, b: str) -> bool:
    """Equal keys, where "?" on either side matches any one character."""
    return len(a) == len(b) and all(x == y or "?" in (x, y) for x, y in zip(a, b, strict=True))


def _starts(short: str, long: str) -> bool:
    return len(long) > len(short) and _same(short, long[: len(short)])


@dataclass(frozen=True)
class NameInfo:
    name: str
    rows: int = 1
    cut: bool = False  # a card name that fills the 20-character field


@dataclass
class UserDecisions:
    merges: dict[str, set[str]] = field(default_factory=dict)  # merchant name -> name keys
    splits: set[frozenset[str]] = field(default_factory=set)  # pairs of name keys


@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    reason: str
    score: float


@dataclass
class Group:
    name: str
    members: list[str]


@dataclass
class Resolution:
    groups: list[Group]
    names_before: int  # distinct names, ignoring case
    merged_by: Counter[str]  # names joined, per rule
    auto_pairs: list[Pair]  # what the rules joined, for your review
    proposals: list[Pair]  # waiting for your decision
    _group_of: dict[str, str]
    _rows: dict[str, int]

    def group_of(self, name: str) -> str:
        return self._group_of[name.casefold()]

    def rows_of(self, name: str) -> int:
        return self._rows[name.casefold()]


def name_infos(texts: Iterable[str]) -> list[NameInfo]:
    """Extract every raw text; one NameInfo per distinct name, with its row count."""
    rows: Counter[str] = Counter()
    cut: set[str] = set()
    for text in texts:
        got = extract_merchant(text)
        if got.merchant is None:
            continue
        rows[got.merchant] += 1
        if got.kind == "card" and len(got.merchant) == CUT:
            cut.add(got.merchant)
    return [NameInfo(name, n, name in cut) for name, n in rows.items()]


def _units(infos: Iterable[NameInfo]) -> list[NameInfo]:
    """One unit per name ignoring case; the spelling with the most rows represents it."""
    by_fold: dict[str, list[NameInfo]] = {}
    for info in infos:
        by_fold.setdefault(info.name.casefold(), []).append(info)
    units = []
    for spellings in by_fold.values():
        best = max(spellings, key=lambda i: i.rows)
        units.append(
            NameInfo(best.name, sum(i.rows for i in spellings), any(i.cut for i in spellings))
        )
    return sorted(units, key=lambda u: u.name.casefold())


def resolve(
    infos: Iterable[NameInfo],
    user: UserDecisions | None = None,
    similar_at: float = SIMILAR_AT,
    aliases: dict[str, str] | None = None,
) -> Resolution:
    """Group names into merchants with the automatic rules; list close pairs as proposals."""
    mine = user or UserDecisions()
    alias_keys = ALIAS_KEYS if aliases is None else aliases
    units = _units(infos)
    keys = [name_key(u.name) for u in units]
    parent = list(range(len(units)))
    members: dict[int, list[int]] = {i: [i] for i in range(len(units))}
    merged_by: Counter[str] = Counter()
    auto_pairs: list[Pair] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def kept_apart(ri: int, rj: int) -> bool:
        return any(
            frozenset((keys[x], keys[y])) in mine.splits for x in members[ri] for y in members[rj]
        )

    def join(i: int, j: int, reason: str) -> None:
        ri, rj = find(i), find(j)
        if ri == rj or kept_apart(ri, rj):
            return
        parent[rj] = ri
        members[ri] += members.pop(rj)
        merged_by[reason] += 1
        auto_pairs.append(Pair(units[i].name, units[j].name, reason, 1.0))

    def join_all(indexes: list[int], reason: str) -> None:
        for j in indexes[1:]:
            join(indexes[0], j, reason)

    user_name: dict[str, str] = {}
    for canonical, user_keys in mine.merges.items():
        join_all([i for i, k in enumerate(keys) if k in user_keys], "your merges")
        user_name.update(dict.fromkeys(user_keys, canonical))

    by_key: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        by_key.setdefault(k, []).append(i)
    for indexes in by_key.values():
        join_all(indexes, "same text")

    for i, k in enumerate(keys):
        if "?" in k:
            for j, other in enumerate(keys):
                if j != i and _same(k, other):
                    join(i, j, "accents")

    by_alias: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        if k in alias_keys:
            by_alias.setdefault(alias_keys[k], []).append(i)
    for indexes in by_alias.values():
        join_all(indexes, "alias list")

    ambiguous: list[Pair] = []
    for i, unit in enumerate(units):
        if not unit.cut:
            continue
        longer = [j for j, k in enumerate(keys) if _starts(keys[i], k) and find(j) != find(i)]
        roots = {find(j) for j in longer}
        if len(roots) == 1:
            join(i, longer[0], "cut prefix")
        else:
            ambiguous += [
                Pair(unit.name, units[j].name, "cut prefix, ambiguous", 1.0) for j in longer
            ]

    candidates = list(ambiguous)
    vectors = tfidf_vectors([u.name for u in units])  # fitted on this list of names
    for i, k in enumerate(keys):
        if len(k.split()) >= 2 and not units[i].cut:
            for j, other in enumerate(keys):
                if _starts(k + " ", other):
                    score = _cosine(vectors[i], vectors[j])
                    candidates.append(Pair(units[i].name, units[j].name, "prefix", score))
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            score = _cosine(vectors[i], vectors[j])
            if score >= similar_at:
                candidates.append(Pair(units[i].name, units[j].name, "similar names", score))

    index = {u.name: i for i, u in enumerate(units)}
    best: dict[frozenset[int], Pair] = {}
    for pair in candidates:
        ri, rj = find(index[pair.a]), find(index[pair.b])
        if ri == rj or kept_apart(ri, rj):
            continue
        roots = frozenset((ri, rj))
        old = best.get(roots)
        rank = (_PROPOSAL_ORDER.index(pair.reason), -pair.score)
        if old is None or rank < (_PROPOSAL_ORDER.index(old.reason), -old.score):
            best[roots] = pair
    proposals = sorted(
        best.values(), key=lambda p: (_PROPOSAL_ORDER.index(p.reason), -p.score, p.a, p.b)
    )

    groups: list[Group] = []
    group_of: dict[str, str] = {}
    for _, idx in sorted(members.items(), key=lambda kv: units[kv[0]].name.casefold()):
        member_units = [units[i] for i in idx]
        named = [user_name[keys[i]] for i in idx if keys[i] in user_name]
        aliased = [alias_keys[keys[i]] for i in idx if keys[i] in alias_keys]
        if named:
            display = named[0]
        elif aliased:
            display = aliased[0]
        else:
            display = max(
                member_units, key=lambda u: (u.rows, "?" not in u.name, not u.cut, len(u.name))
            ).name
        groups.append(Group(display, [u.name for u in member_units]))
        group_of.update({u.name.casefold(): display for u in member_units})

    return Resolution(
        groups=groups,
        names_before=len(units),
        merged_by=merged_by,
        auto_pairs=auto_pairs,
        proposals=proposals,
        _group_of=group_of,
        _rows={u.name.casefold(): u.rows for u in units},
    )


# --- Your decisions in the database ---


def _has_tables(conn: sqlite3.Connection) -> bool:
    found = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        " AND name IN ('merchants', 'merchant_aliases', 'merchant_splits')"
    ).fetchone()[0]
    return found == 3


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    sql = "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?"
    return bool(conn.execute(sql, (name,)).fetchone()[0])


def _from_tables(conn: sqlite3.Connection) -> UserDecisions:
    """Your decisions as of today, from the merchant tables."""
    if not _has_tables(conn):
        return UserDecisions()
    merges: dict[str, set[str]] = {}
    for canonical, key in conn.execute(
        "SELECT m.canonical_name, a.alias_key FROM merchant_aliases a"
        " JOIN merchants m USING (merchant_id) WHERE a.source = 'user'"
    ):
        merges.setdefault(canonical, set()).add(key)
    # A merchant left with one name (possible in databases written before splits removed
    # them) joins nothing, so it is not counted as a merge.
    merges = {name: keys for name, keys in merges.items() if len(keys) >= 2}
    splits = {frozenset(r) for r in conn.execute("SELECT key_a, key_b FROM merchant_splits")}
    return UserDecisions(merges, splits)


def replay(events: Iterable[tuple[str, str, str, str]]) -> UserDecisions:
    """Your decisions after (decision, key_a, key_b, name_a) events, in order. Same effect
    as `_merge` / `_split` on the tables, so replaying everything gives today's state."""
    merchant_of: dict[str, int] = {}
    members: dict[int, set[str]] = {}
    names: dict[int, str] = {}
    splits: set[frozenset[str]] = set()
    next_id = itertools.count(1)
    for decision, ka, kb, name_a in events:
        if ka == kb:
            continue
        ma, mb = merchant_of.get(ka), merchant_of.get(kb)
        if decision == "merge":
            splits.discard(frozenset((ka, kb)))
            if ma is None and mb is None:
                new = next(next_id)
                names[new], members[new] = name_a, {ka, kb}
                merchant_of[ka] = merchant_of[kb] = new
            elif ma is None:
                members[mb].add(ka)  # type: ignore[index]
                merchant_of[ka] = mb  # type: ignore[assignment]
            elif mb is None:
                members[ma].add(kb)
                merchant_of[kb] = ma
            elif ma != mb:  # two merchants become one
                for key in members.pop(mb):
                    members[ma].add(key)
                    merchant_of[key] = ma
                del names[mb]
        else:
            splits.add(frozenset((ka, kb)))
            if ma is not None and ma == mb:  # undo your earlier merge of these two
                members[ma].discard(kb)
                del merchant_of[kb]
                if len(members[ma]) < 2:  # a merchant of one name joins nothing
                    for key in members.pop(ma):
                        del merchant_of[key]
                    del names[ma]
    return UserDecisions({names[m]: keys for m, keys in members.items()}, splits)


def load_user_decisions(
    conn: sqlite3.Connection, as_of: date | None = None, use_log: bool = True
) -> UserDecisions:
    """Your merges and splits, as made by `as_of` (all of them if None).

    Read from the dated decision log. `use_log=False` reads today's state from the merchant
    tables instead (used to check that the two agree). An older database without the log
    can only give today's state; asking it for a date raises ValueError.
    """
    has_log = _table_exists(conn, "merchant_decisions") and bool(
        conn.execute("SELECT COUNT(*) FROM merchant_decisions").fetchone()[0]
    )
    if use_log and has_log:
        sql = "SELECT decision, key_a, key_b, name_a FROM merchant_decisions"
        params: tuple[str, ...] = ()
        if as_of is not None:
            sql += " WHERE decided_at <= ?"
            params = (as_of.isoformat(),)
        return replay(conn.execute(sql + " ORDER BY decided_at, decision_id", params))
    state = _from_tables(conn)
    if as_of is not None and (state.merges or state.splits):
        raise ValueError(
            "these decisions have no dates yet; run: python -m lookahead.db migrate --db <db>"
        )
    return state


def _merchant_id(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute(
        "SELECT merchant_id FROM merchant_aliases WHERE alias_key = ?", (key,)
    ).fetchone()
    return None if row is None else row[0]


def _add_alias(conn: sqlite3.Connection, key: str, merchant_id: int) -> None:
    conn.execute(
        "INSERT INTO merchant_aliases (alias_key, merchant_id, source) VALUES (?, ?, 'user')",
        (key, merchant_id),
    )


def _merge(conn: sqlite3.Connection, a: str, b: str) -> None:
    ka, kb = name_key(a), name_key(b)
    if ka == kb:
        return  # already the same name for the rules
    conn.execute("DELETE FROM merchant_splits WHERE key_a = ? AND key_b = ?", sorted((ka, kb)))
    ida, idb = _merchant_id(conn, ka), _merchant_id(conn, kb)
    if ida is None and idb is None:
        new = conn.execute(
            "INSERT INTO merchants (canonical_name, source) VALUES (?, 'user')", (a,)
        ).lastrowid
        assert new is not None
        _add_alias(conn, ka, new)
        _add_alias(conn, kb, new)
    elif ida is None:
        _add_alias(conn, ka, idb)  # type: ignore[arg-type]
    elif idb is None:
        _add_alias(conn, kb, ida)
    elif ida != idb:  # two merchants become one
        conn.execute(
            "UPDATE merchant_aliases SET merchant_id = ? WHERE merchant_id = ?", (ida, idb)
        )
        conn.execute("DELETE FROM merchants WHERE merchant_id = ?", (idb,))


def _split(conn: sqlite3.Connection, a: str, b: str) -> None:
    ka, kb = name_key(a), name_key(b)
    if ka == kb:
        return
    conn.execute(
        "INSERT OR IGNORE INTO merchant_splits (key_a, key_b) VALUES (?, ?)", sorted((ka, kb))
    )
    ida, idb = _merchant_id(conn, ka), _merchant_id(conn, kb)
    if ida is not None and ida == idb:  # undo your earlier merge of these two
        conn.execute("DELETE FROM merchant_aliases WHERE alias_key = ?", (kb,))
        left = conn.execute(
            "SELECT COUNT(*) FROM merchant_aliases WHERE merchant_id = ?", (ida,)
        ).fetchone()[0]
        if left < 2:  # a merchant of one name joins nothing: remove it
            conn.execute("DELETE FROM merchant_aliases WHERE merchant_id = ?", (ida,))
            conn.execute("DELETE FROM merchants WHERE merchant_id = ?", (ida,))


def apply_decisions(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[str, str, str]],
    decided_at: date | None = None,
) -> dict[str, int]:
    """Store (decision, name_a, name_b) rows: "merge" or "split"; anything else is skipped.
    Each one is also written to the dated decision log (`decided_at`, default today).
    All in one database transaction."""
    day = (decided_at or date.today()).isoformat()
    counts = {"merges": 0, "splits": 0, "skipped": 0}
    with conn:
        for decision, a, b in rows:
            choice = decision.strip().casefold()
            if choice not in ("merge", "split"):
                counts["skipped"] += 1
                continue
            (_merge if choice == "merge" else _split)(conn, a, b)
            counts["merges" if choice == "merge" else "splits"] += 1
            ka, kb = name_key(a), name_key(b)
            if ka != kb:
                conn.execute(
                    "INSERT INTO merchant_decisions (decided_at, decision, key_a, key_b, name_a)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (day, choice, ka, kb, a),
                )
    return counts


def _is_stored(conn: sqlite3.Connection, decision: str, a: str, b: str) -> bool:
    ka, kb = name_key(a), name_key(b)
    choice = decision.strip().casefold()
    if choice == "merge":
        if ka == kb:
            return True
        if not _has_tables(conn):
            return False
        ida = _merchant_id(conn, ka)
        return ida is not None and ida == _merchant_id(conn, kb)
    if choice == "split":
        return _has_tables(conn) and (
            conn.execute(
                "SELECT 1 FROM merchant_splits WHERE key_a = ? AND key_b = ?", sorted((ka, kb))
            ).fetchone()
            is not None
        )
    return True  # blank: nothing to store


# --- The review file ---

_HEADER = ["decision", "section", "reason", "score", "rows_a", "name_a", "rows_b", "name_b"]
_INSTRUCTIONS = """\
# Merchant merge proposals. PRIVATE: never commit, never paste.
# First column: "merge" (same merchant) or "split" (different merchants); blank = no decision.
# Section "auto": a rule joined them; write "split" to undo. "waiting": your call.
# Then store your decisions: python -m lookahead.resolve apply --db <database>
"""


def write_proposals(result: Resolution, path: Path) -> int:
    """Write auto merges and waiting pairs for review; returns the number of rows."""
    if not is_private_path(path):
        raise ValueError(f"refusing to write merchant names outside data/private/: {path}")
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter="\t", lineterminator="\n")
    writer.writerow(_HEADER)
    sections = [("auto", result.auto_pairs), ("waiting", result.proposals)]
    n = 0
    for section, pairs in sections:
        for p in pairs:
            writer.writerow(
                ["", section, p.reason, f"{p.score:.2f}",
                 result.rows_of(p.a), p.a, result.rows_of(p.b), p.b]
            )  # fmt: skip
            n += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_INSTRUCTIONS + buffer.getvalue(), encoding="utf-8")
    return n


def read_decisions(path: Path) -> list[tuple[str, str, str]]:
    """(decision, name_a, name_b) for every row of a review file, in file order."""
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    return [
        (r["decision"] or "", r["name_a"], r["name_b"])
        for r in csv.DictReader(lines, delimiter="\t")
    ]


# --- Commands ---


def _texts(conn: sqlite3.Connection, account: str) -> list[str]:
    sql = "SELECT sub_description FROM transactions WHERE account = ?"
    return [r[0] for r in conn.execute(sql, (account,))]


def _report(db_path: Path, account: str, proposals_file: Path | None) -> int:
    """Counts only. Names go to the private review file, never to the terminal."""
    if not db_path.exists():
        print(f"database not found: {db_path}")
        return 2
    if proposals_file is not None and not is_private_path(proposals_file):
        print("--write-proposals must point inside data/private/ (names are private)")
        return 2
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    if proposals_file is not None and proposals_file.exists():
        unstored = [d for d in read_decisions(proposals_file) if not _is_stored(conn, *d)]
        if unstored:
            conn.close()
            print(
                f"{proposals_file} has {len(unstored)} decisions that are not stored yet; "
                f"not overwriting it. Store them first: python -m lookahead.resolve apply "
                f"--db {db_path}"
            )
            return 2
    user = load_user_decisions(conn)
    texts = _texts(conn, account)
    conn.close()

    r = resolve(name_infos(texts), user)
    joined = sum(1 for g in r.groups if len(g.members) > 1)
    waiting = Counter(p.reason for p in r.proposals)
    print(f"Merchant resolution: account {account}")
    print(f"names before resolution: {r.names_before} (distinct, ignoring case)")
    print(f"merchant groups after resolution: {len(r.groups)}")
    print(f"groups that joined 2+ names: {joined}")
    print("names joined by: " + ", ".join(f"{rule} {r.merged_by[rule]}" for rule in _RULES))
    print(
        f"pairs waiting for your confirmation: {len(r.proposals)} ("
        + ", ".join(f"{reason} {waiting[reason]}" for reason in _PROPOSAL_ORDER)
        + ")"
    )
    merged_names = sum(len(keys) for keys in user.merges.values())
    print(
        f"your decisions stored: {len(user.merges)} merchants built from merges "
        f"({merged_names} names), {len(user.splits)} pairs kept apart"
    )
    if proposals_file is not None:
        n = write_proposals(r, proposals_file)
        print(f"wrote {n} pairs to review to {proposals_file} (not shown here)")
    return 0


def _apply(db_path: Path, decisions_file: Path) -> int:
    if not db_path.exists():
        print(f"database not found: {db_path}")
        return 2
    if not decisions_file.exists():
        print(f"decisions file not found: {decisions_file}")
        return 2
    rows = read_decisions(decisions_file)
    conn = connect(db_path)
    counts = apply_decisions(conn, rows)
    conn.close()
    print(f"read {len(rows)} rows")
    print(
        f"stored: merge decisions {counts['merges']}, split decisions {counts['splits']} "
        f"(no decision: {counts['skipped']})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.resolve")
    commands = parser.add_subparsers(dest="command", required=True)
    rep = commands.add_parser("report", help="counts-only merchant resolution report")
    rep.add_argument("--db", type=Path, required=True)
    rep.add_argument("--account", default="scotia-chequing")
    rep.add_argument(
        "--write-proposals",
        type=Path,
        nargs="?",
        const=DEFAULT_PROPOSALS_FILE,
        default=None,
        metavar="PATH",
        help=f"write pairs to review to a private file (default {DEFAULT_PROPOSALS_FILE})",
    )
    app = commands.add_parser("apply", help="store the decisions you wrote in the review file")
    app.add_argument("--db", type=Path, required=True)
    app.add_argument("--decisions", type=Path, default=None, metavar="PATH")
    args = parser.parse_args(argv)
    if args.command == "apply":
        return _apply(args.db, args.decisions or DEFAULT_PROPOSALS_FILE)
    return _report(args.db, args.account, args.write_proposals)


if __name__ == "__main__":
    sys.exit(main())
