"""Shared helpers for the SOS bus token file.

This is a compatibility layer around ``sos/bus/tokens.json``. It keeps the
current file-backed authority model intact while centralizing the mechanics
that were drifting across provisioning scripts: raw token hashing, active-row
matching, and atomic JSON replacement.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

TokenRecord = dict[str, Any]


def normalize_subscriptions(raw: Any) -> list[str]:
    """Return canonical optional bus-channel subscriptions from a token row."""
    if not isinstance(raw, list):
        return []
    subscriptions: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = str(item).strip()
        if not value:
            continue
        if not value.startswith("sos:channel:"):
            value = f"sos:channel:{value}"
        if value not in seen:
            seen.add(value)
            subscriptions.append(value)
    return subscriptions


def hash_token(raw_token: str) -> str:
    """Return the canonical SHA-256 token hash."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def load_tokens(path: Path) -> list[TokenRecord]:
    """Load token records from *path*, returning an empty list for no file."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def write_tokens_atomic(path: Path, records: list[TokenRecord]) -> None:
    """Write token records as JSON using temp-file-and-rename discipline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        suffix=".tmp",
        delete=False,
    )
    try:
        json.dump(records, tmp, indent=2)
        tmp.write("\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        try:
            tmp.close()
        finally:
            try:
                os.unlink(tmp.name)
            except FileNotFoundError:
                pass
        raise


def find_active(records: list[TokenRecord], predicate: Callable[[TokenRecord], bool]) -> TokenRecord | None:
    """Return the first active record matching *predicate*."""
    for record in records:
        if record.get("active", True) and predicate(record):
            return record
    return None


def append_if_missing(
    path: Path,
    record: TokenRecord,
    predicate: Callable[[TokenRecord], bool],
) -> tuple[bool, TokenRecord]:
    """Append *record* unless an active matching record already exists.

    Returns ``(created, record)`` where ``record`` is either the existing active
    match or the newly appended row.
    """
    records = load_tokens(path)
    existing = find_active(records, predicate)
    if existing is not None:
        return False, existing
    records.append(record)
    write_tokens_atomic(path, records)
    return True, record


def append_record(path: Path, record: TokenRecord) -> None:
    """Append *record* to the token file."""
    records = load_tokens(path)
    records.append(record)
    write_tokens_atomic(path, records)


def update_matching(
    path: Path,
    predicate: Callable[[TokenRecord], bool],
    update: Callable[[TokenRecord], None],
) -> int:
    """Mutate all records matching *predicate* and return the number changed."""
    records = load_tokens(path)
    changed = 0
    for record in records:
        if predicate(record):
            update(record)
            changed += 1
    if changed:
        write_tokens_atomic(path, records)
    return changed
