"""Detect malformed / duplicated DATABASE_URL definitions without printing secrets.

sos#213: passwords containing unencoded '@' make libpq and Python urlsplit
disagree on the host. Duplicate DATABASE_URL keys make last-wins a coin flip.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit


_DB_URL_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _strip_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def iter_env_assignments(env_text: str) -> list[tuple[int, str, str]]:
    """Return (line_number, key, value) for non-comment assignments."""
    out: list[tuple[int, str, str]] = []
    for index, line in enumerate(env_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _DB_URL_KEY_RE.match(line)
        if match is None:
            continue
        key = match.group(1)
        value = _strip_value(match.group(2))
        out.append((index, key, value))
    return out


def fingerprint_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def libpq_host_guess(url: str) -> str | None:
    """Approximate libpq host split (first '@' after scheme)."""
    if "://" not in url:
        return None
    rest = url.split("://", 1)[1]
    if "@" not in rest:
        return None
    _userinfo, hostpart = rest.split("@", 1)
    return hostpart.split("/")[0].split("?")[0]


def inspect_database_url_value(value: str) -> dict[str, Any]:
    """Inspect one URL value. Never includes the raw secret."""
    urlsplit_host: str | None
    try:
        urlsplit_host = urlsplit(value).hostname
    except ValueError:
        urlsplit_host = None
    return {
        "sha12": fingerprint_value(value),
        "at_count": value.count("@"),
        "urlsplit_host": urlsplit_host,
        "libpq_first_at_host": libpq_host_guess(value),
        "unencoded_at_in_userinfo": value.count("@") > 1,
    }


def find_database_url_defects(env_text: str) -> list[dict[str, Any]]:
    """Return defect records for DATABASE_URL-family keys in an env file body."""
    assignments = [
        (line, key, value)
        for line, key, value in iter_env_assignments(env_text)
        if key in {"DATABASE_URL", "SUPABASE_DATABASE_URL", "MIRROR_DATABASE_URL"}
    ]
    defects: list[dict[str, Any]] = []
    by_key: dict[str, list[tuple[int, str]]] = {}
    for line, key, value in assignments:
        by_key.setdefault(key, []).append((line, value))
        meta = inspect_database_url_value(value)
        if meta["unencoded_at_in_userinfo"]:
            defects.append(
                {
                    "kind": "unencoded_at_in_userinfo",
                    "key": key,
                    "line": line,
                    "sha12": meta["sha12"],
                    "urlsplit_host": meta["urlsplit_host"],
                    "libpq_first_at_host": meta["libpq_first_at_host"],
                    "hint": "percent-encode password '@' as %40 so libpq and urlsplit agree",
                }
            )
    for key, entries in by_key.items():
        if len(entries) < 2:
            continue
        defects.append(
            {
                "kind": "duplicate_key",
                "key": key,
                "lines": [line for line, _ in entries],
                "sha12s": [fingerprint_value(value) for _, value in entries],
                "hint": "keep one DATABASE_URL; name the other SUPABASE_DATABASE_URL (or similar)",
            }
        )
    return defects
