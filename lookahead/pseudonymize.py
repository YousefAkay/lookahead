"""Pseudonym tokens for identifiers that must not leave data/private/ (see DECISIONS.md).

A token is an HMAC-SHA256 of the normalized value under a secret key, shortened:
    token("PERSON", "Nadia Haddad", key) -> "PERSON_3f9a1c2b7d04"
Stable (same value, same token), so matching still works; without the key it can be
neither reversed nor recomputed from a list of guesses.

The key lives in `.env` as PSEUDONYM_KEY (64 hex characters). Create it once with:
    python -m lookahead.pseudonymize init-key
Replacing or losing the key changes every token, so the command never overwrites one.
"""

import argparse
import hashlib
import hmac
import os
import re
import secrets
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from lookahead.config import load_env

KEY_NAME = "PSEUDONYM_KEY"
KEY_BYTES = 32
_TOKEN_HEX = 12  # 48 bits: collisions are negligible for a few thousand identifiers
_KIND = re.compile(r"[A-Z]+")


class PseudonymKeyError(RuntimeError):
    """The pseudonym key is missing or malformed. Messages never contain the key."""


def token(kind: str, value: str, key: bytes) -> str:
    """Stable token for `value`. Case and spacing are ignored ("NADIA  HADDAD" = "Nadia Haddad")."""
    if not _KIND.fullmatch(kind):
        raise ValueError(f"kind must be capital letters only, got {kind!r}")
    normalized = " ".join(value.split()).casefold()
    message = f"{kind}\x1f{normalized}".encode()  # the kind is mixed in: PERSON x != REF x
    digest = hmac.new(key, message, hashlib.sha256).hexdigest()
    return f"{kind}_{digest[:_TOKEN_HEX]}"


@dataclass(frozen=True)
class Pseudonymized:
    text: str
    rules: tuple[str, ...]  # which rules fired: "person", "reference", "number"


_MEMO = re.compile(r"(1/)?(.*?)(\s*)")  # optional "1/", the sender's name, trailing space
_MOBILE_BILL = re.compile(r"Mb-(.+) (\d{8})")  # "Mb-{payee} {8-digit reference}"
_LONG_DIGITS = re.compile(r"\d{7,}")


def _numbers(text: str, key: bytes, fired: list[str]) -> str:
    """Replace every run of 7+ digits in ORIGINAL text (never in text holding tokens)."""

    def replace(match: re.Match[str]) -> str:
        fired.append("number")
        return token("NUM", match.group(), key)

    return _LONG_DIGITS.sub(replace, text)


def pseudonymize_sub_description(
    description: str, sub_description: str, key: bytes
) -> Pseudonymized:
    """Replace identifiers in one row's Sub-description; everything else is kept exactly.

    Rules: credit memo sender name -> PERSON token (keeps "1/" and trailing spaces);
    mobile-banking bill reference -> REF token (payee kept); any other run of 7+ digits
    -> NUM token. Apply to raw text once: tokens themselves contain digits.
    """
    fired: list[str] = []
    if description == "credit memo":
        match = _MEMO.fullmatch(sub_description)
        assert match is not None  # the pattern matches any text
        prefix, name, space = match.groups()
        if name:
            fired.append("person")
            return Pseudonymized(f"{prefix or ''}{token('PERSON', name, key)}{space}", tuple(fired))
    bill = _MOBILE_BILL.fullmatch(sub_description)
    if bill:
        payee, reference = bill.groups()
        payee = _numbers(payee, key, fired)
        fired.append("reference")
        return Pseudonymized(f"Mb-{payee} {token('REF', reference, key)}", tuple(fired))
    return Pseudonymized(_numbers(sub_description, key, fired), tuple(fired))


def pseudonym_key(env_path: Path = Path(".env"), environ: Mapping[str, str] | None = None) -> bytes:
    """The secret key: from the environment variable if set, else from the .env file."""
    environ = os.environ if environ is None else environ
    text = environ.get(KEY_NAME) or load_env(env_path).get(KEY_NAME, "")
    if not text:
        raise PseudonymKeyError(
            f"{KEY_NAME} is not set. Create one with: python -m lookahead.pseudonymize init-key"
        )
    try:
        key = bytes.fromhex(text)
    except ValueError:
        raise PseudonymKeyError(f"{KEY_NAME} is not valid hexadecimal") from None
    if len(key) != KEY_BYTES:
        raise PseudonymKeyError(f"{KEY_NAME} must be {KEY_BYTES * 2} hex characters")
    return key


def init_key(env_path: Path) -> bool:
    """Write a new random key into .env unless one is already set. True if written."""
    if load_env(env_path).get(KEY_NAME):
        return False
    new_line = f"{KEY_NAME}={secrets.token_hex(KEY_BYTES)}"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    # Replace an empty "PSEUDONYM_KEY=" line if present, else append.
    replaced = [new_line if ln.split("=", 1)[0].strip() == KEY_NAME else ln for ln in lines]
    if new_line not in replaced:
        replaced.append(new_line)
    env_path.write_text("\n".join(replaced) + "\n", encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lookahead.pseudonymize")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init-key", help=f"create {KEY_NAME} in .env if it is not set")
    init.add_argument("--env", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)

    if init_key(args.env):
        print(f"wrote a new {KEY_NAME} to {args.env} (keep it; replacing it changes every token)")
        return 0
    print(f"{KEY_NAME} is already set in {args.env}; not replacing it")
    return 1


if __name__ == "__main__":
    sys.exit(main())
