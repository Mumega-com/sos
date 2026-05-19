"""Migrate tokens.json to hashed format.

Run ONCE. Backs up original first.
After migration, raw tokens are cleared and token_hash is set for every entry.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from sos.bus.token_store import load_tokens, write_tokens_atomic


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def main() -> None:
    tokens_path = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"
    backup_path = tokens_path.with_suffix(".json.backup")

    if not tokens_path.exists():
        print(f"ERROR: {tokens_path} not found")
        return

    # Backup
    shutil.copy2(tokens_path, backup_path)
    print(f"Backup written: {backup_path}")

    tokens: list[dict] = load_tokens(tokens_path)
    migrated = 0
    already_hashed = 0
    skipped = 0

    for t in tokens:
        raw = t.get("token", "")
        existing_hash = t.get("token_hash", "")

        if existing_hash and not raw:
            # Already migrated
            already_hashed += 1
            continue

        if raw:
            t["token_hash"] = hash_token(raw)
            t["token"] = ""  # Clear raw token
            migrated += 1
        else:
            # No raw token and no hash — leave as-is but note it
            skipped += 1

    write_tokens_atomic(tokens_path, tokens)
    print(f"Migrated:       {migrated}")
    print(f"Already hashed: {already_hashed}")
    print(f"Skipped (empty):{skipped}")
    print(f"Total entries:  {len(tokens)}")
    print("Done. Restart sos-mcp-sse to load the new hashes.")


if __name__ == "__main__":
    main()
