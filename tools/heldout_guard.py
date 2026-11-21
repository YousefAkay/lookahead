"""The guard on Ask's held-out folder (spec docs/specs/2026-11-20-ask.md, step 9).

A local hook (outside the repository) calls `decide` before every file or command access of the
coding session and of every agent, with the access as one of these kinds:
- "read" or "write": one file, by path;
- "search": a file search, by the folder it searches (no folder means the current folder);
- "pattern": the file-name pattern of a search, relative to the folder searched;
- "command": a shell command, by its text.
Anything else that names the folder is "other" and is refused.

What it stops: accidental reads of the folder, and the routes below. It refuses the folder's name
in any spelling (quotes and backslashes removed first, any case, any separator between its two
words), any wildcard or variable over data/, a `cd` into data, a recursive command or a search
whose targets are not all code folders (no target means the current folder, so it is refused
too), a search pattern that climbs out of its folder, a `cd` to a wildcard or variable, and
`ln` or `mv` of data. A command that runs an interpreter (python, runpy) is checked with the
module's file name left in. The allowed check commands are matched on the text exactly as typed,
with only plain path characters (no `;`, braces, `$`, quotes or other expansions) and one
`cd <folder> &&` in front.

What it cannot stop: a name or a command deliberately disguised (built from pieces, globs over
another folder, a variable), or what a script does. That is the same limit as the deny rule on
data/private. So the printout of questions and plans no longer prints: `disagreements` writes
its text into the blocked folder and prints only a line count (eval/ask_heldout.py), so running
the module by any route shows no content; Yousef opens the file himself.

Who may do what:
- the three writers (heldout-questions, heldout-gold-a, heldout-gold-b) read and write only their
  own files, directly in the folder, and run only their own check commands;
- the coding session may run only `python -m eval.ask_heldout fingerprint`;
- everyone else, the reviewer included, nothing;
- `disagreements` (which writes questions and plans into the folder) is refused for every
  caller: only Yousef runs it, himself.
"""

import posixpath
import re
from dataclasses import dataclass

FOLDER = "data/ask_heldout"
QUESTION_FILES = ("questions_agent.jsonl", "questions_yousef.txt")
WRITERS: dict[str, dict[str, tuple[str, ...]]] = {
    "heldout-questions": {"read": ("questions_agent.jsonl",),
                          "write": ("questions_agent.jsonl",),
                          "command": ("check-questions", "fingerprint")},
    "heldout-gold-a": {"read": (*QUESTION_FILES, "gold_a.jsonl"), "write": ("gold_a.jsonl",),
                       "command": ("check-gold --which a", "fingerprint")},
    "heldout-gold-b": {"read": (*QUESTION_FILES, "gold_b.jsonl"), "write": ("gold_b.jsonl",),
                       "command": ("check-gold --which b", "fingerprint")},
}  # fmt: skip
SESSION_COMMANDS = ("fingerprint",)  # the coding session (no agent name)

# The folder's name: "ask" then "held", with anything but letters and digits between them.
NAMES = re.compile(r"ask[\W_]*held", re.IGNORECASE)
# The module and its test are code, not the folder: they may be edited, listed and committed.
CODE_FILES = re.compile(r"eval[/\\]ask_heldout\.py|tests[/\\]test_ask_heldout\.py", re.IGNORECASE)
# Running the module by name; a command that runs an interpreter keeps the file name in.
MODULE = re.compile(r"eval\.ask_?heldout", re.IGNORECASE)
INTERPRETER = re.compile(r"python[\d.]*\b|runpy|\bexec\b|\bsource\b", re.IGNORECASE)
# On the text as typed: plain path characters only, so nothing can ride in front of the command.
MODULE_COMMAND = re.compile(
    r"^(?:cd [\w./~-]+ && )?[\w./~-]*python3? -m eval\.ask_heldout (?P<sub>[a-z]+(?:-[a-z]+)?"
    r"(?: --which [ab])?)$"
)
WILDCARD = re.compile(r"data[\\/]+\S*[*?\[{$]", re.IGNORECASE)
CD_DATA = re.compile(r"\b(cd|pushd)\s+((\S*[\\/])?data\b|\S*[*?\[{$])", re.IGNORECASE)
MOVE_DATA = re.compile(r"\b(ln|mv)\b[^|;&]*(^|[\s/])data(/|\s|$)", re.IGNORECASE)
# Commands that walk folders. For the first group the first plain word is the search text.
WALK_WITH_TEXT = ("grep", "egrep", "fgrep", "rg", "ag", "ack", "fd")
WALK = ("find", "tree", "tar", "rsync", "zip", "du")
RECURSIVE_FLAG = re.compile(
    r"^(-\w*[rR]\w*|--recursive|--dereference-recursive|--directories=recurse|-d)$"
)
TEXT_OPTIONS = ("-e", "-f", "--regexp", "--file")
# Options whose next word is a value, not a folder (context lines, globs, types, counts).
VALUE_OPTIONS = ("-A", "-B", "-C", "-m", "-g", "-t", "-T", "-M", "--glob", "--type",
                 "--type-not", "--max-count", "--context", "--max-depth", "--include",
                 "--exclude")  # fmt: skip
CODE_DIRS = ("lookahead", "app", "tests", "eval", "tools", "docs", ".streamlit")
# A repository folder (the main one or a worktree) in front of a code folder: the first folder
# named lookahead... in the path, so one nested deeper (say under data/) does not count.
REPO_PREFIX = re.compile(r"^(?:.*?/)?lookahead[\w\-]*/")
TOP_FILE = re.compile(r"^[\w\-]+\.(md|toml|txt|cfg|ini|py)$")
SEPARATORS = re.compile(r"[|;&()\n`]+")
PREFIX_WORDS = ("sudo", "env", "nice", "time", "command", "exec", "nohup")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    reaches: bool  # the access names the folder or could reach it


def plain(text: str) -> str:
    """Quotes and backslashes removed, so a name split by quotes reads as the name it spells."""
    return re.sub(r"['\"\\`]", "", text)


def code_path(path: str) -> bool:
    """A path inside a code folder (lookahead/, app/, tests/, ...), relative or under a
    repository folder, that does not climb back out with `..`."""
    p = posixpath.normpath(path.replace("\\", "/"))
    if p.startswith("~") or "$" in p:
        return False
    rest = REPO_PREFIX.sub("", p, count=1) if p.startswith(("/", "..")) else p
    if rest.startswith(("/", "..")):
        return False
    parts = rest.split("/")
    if len(parts) == 1 and TOP_FILE.match(parts[0]):  # README.md, DECISIONS.md, pyproject.toml
        return True
    return parts[0] in CODE_DIRS and ".." not in parts


def _walk_targets(words: list[str]) -> list[str] | None:
    """The folders a folder-walking command reaches, or None when the command does not walk
    folders. A walk with no folder named walks the current one, returned as "."."""
    if not words:
        return None
    name = words[0].rsplit("/", 1)[-1]
    if name == "xargs":  # its targets come from its input, so they are unknown
        return None if _walk_targets(words[1:]) is None else ["."]
    options = [w for w in words[1:] if w.startswith("-")]
    plain_words = [w for w in words[1:] if not w.startswith("-")]
    if name in WALK_WITH_TEXT:
        if name in ("grep", "egrep", "fgrep") and not any(RECURSIVE_FLAG.match(o) for o in options):
            return None
        targets, skip, text_given = [], False, False
        for w in words[1:]:
            if skip:  # the value of an option: not a folder
                skip = False
            elif w in TEXT_OPTIONS:
                skip = text_given = True
            elif w in VALUE_OPTIONS:
                skip = True
            elif w.startswith(("--regexp=", "--file=")):
                text_given = True
            elif not w.startswith("-"):
                targets.append(w)
        if not text_given:
            targets = targets[1:]  # the first plain word is the search text
        return targets or ["."]
    if name == "find":
        targets = []
        for w in words[1:]:
            if w.startswith(("-", "(", "!")):
                break
            targets.append(w)
        return targets or ["."]
    if name in WALK:
        return plain_words or ["."]
    if name == "ls" and any(re.match(r"^-\w*R", o) for o in options):
        return plain_words or ["."]
    if name == "cp" and any(re.match(r"^-\w*[rR]", o) for o in options):
        return plain_words
    return None


def walks_outside_code(command: str) -> bool:
    """True when some part of the command walks a folder that is not a code folder."""
    for part in SEPARATORS.split(command):
        words = part.split()
        while words and (re.match(r"^\w+=", words[0]) or words[0] in PREFIX_WORDS):
            words = words[1:]
        targets = _walk_targets(words)
        if targets is not None and not all(code_path(t) for t in targets):
            return True
    return False


def reaches(kind: str, text: str) -> bool:
    """Does the access name the folder, or could it reach it?"""
    raw = plain(text)
    runs_code = kind == "command" and INTERPRETER.search(raw)
    t = raw if runs_code else CODE_FILES.sub(" ", raw)
    if NAMES.search(t) or WILDCARD.search(t):
        return True
    if kind == "command":
        return bool(MODULE.search(raw) or CD_DATA.search(t) or MOVE_DATA.search(t)
                    or walks_outside_code(t))  # fmt: skip
    if kind == "search":
        return not code_path(raw.strip() or ".")
    if kind == "pattern":
        p = t.strip().replace("\\", "/")
        return p.startswith(("/", "~")) or ".." in p.split("/") or "$" in p
    return False


def decide(kind: str, text: str, agent: str | None) -> Decision:
    """May this access go ahead? `text` is the path (read, write), the searched folder (search),
    the search pattern (pattern) or the command (command); `agent` is the agent's name, None for
    the coding session."""
    if not reaches(kind, text):
        return Decision(True, "does not reach the held-out folder", False)
    t = plain(text).strip()
    if kind == "command":
        m = MODULE_COMMAND.match(text.strip())  # as typed, not with quotes removed
        if m is not None:
            sub = m.group("sub")
            if agent in WRITERS:
                allowed = WRITERS[agent]["command"]
            else:
                allowed = SESSION_COMMANDS if agent is None else ()
            if sub in allowed:
                return Decision(True, f"the check command {sub}", True)
            return Decision(False, f"this caller may not run the held-out command {sub!r}", True)
        return Decision(False, "this command could reach the held-out folder", True)
    rights = WRITERS.get(agent or "")
    if rights is None or kind not in ("read", "write"):
        return Decision(False, "the held-out folder is blocked for this caller", True)
    path = posixpath.normpath(t.replace("\\", "/"))
    name = path.rsplit("/", 1)[-1]
    if not path.endswith(f"{FOLDER}/{name}"):
        return Decision(False, "not a file directly in the held-out folder", True)
    if name in rights[kind]:
        return Decision(True, f"{agent} may {kind} {name}", True)
    return Decision(False, f"{agent} may not {kind} {name}", True)
