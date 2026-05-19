#!/usr/bin/env python3
"""sync-tokens-to-kv — push active tokens from tokens.json to Cloudflare KV.

Dispatcher (CF Worker) reads from KV namespace `SOS_TOKENS` keyed by
`token:<sha256(raw)>` → JSON AuthContext. Source of truth stays on the VPS
at sos/bus/tokens.json. This script makes CF a read-through cache.

Safe to run on cron + on file-change. Idempotent. Dry-run supported.

Usage:
  python3 scripts/sync-tokens-to-kv.py              # push to CF KV
  python3 scripts/sync-tokens-to-kv.py --dry-run    # preview without writing
  python3 scripts/sync-tokens-to-kv.py --prune      # also remove KV keys
                                                    # that aren't in tokens.json

Requires:
  - wrangler CLI authenticated (or CF_API_TOKEN + CF_ACCOUNT_ID in env)
  - wrangler.toml at workers/sos-dispatcher/ with SOS_TOKENS binding already bound
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


SOS_ROOT = Path(__file__).resolve().parent.parent
TOKENS_PATH = SOS_ROOT / "sos" / "bus" / "tokens.json"
WRANGLER_DIR = SOS_ROOT / "workers" / "sos-dispatcher"
KV_BINDING = "SOS_TOKENS"


def _active_entries() -> list[dict]:
    entries = json.loads(TOKENS_PATH.read_text())
    return [e for e in entries if e.get("active")]


def _hash_for_entry(entry: dict) -> str | None:
    """Return the lookup hash. Prefer sha256 token_hash; skip bcrypt-only entries."""
    sha = entry.get("token_hash", "")
    if sha and not sha.startswith(("$2a$", "$2b$", "$2y$")):
        return sha
    return None


def _auth_context(entry: dict) -> dict:
    return {
        "tenant_id": entry.get("project") or None,
        "agent": entry.get("agent") or "",
        "scope": entry.get("scope") or "agent",
        "plan": entry.get("plan") or None,
        "role": entry.get("role", "admin"),
    }


def _wrangler_put(key: str, value: str) -> bool:
    cmd = [
        "wrangler", "kv", "key", "put",
        "--remote",
        "--binding", KV_BINDING,
        key, value,
    ]
    try:
        subprocess.run(cmd, cwd=str(WRANGLER_DIR), check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"  ERROR: wrangler put {key} failed: {exc.stderr.strip()[:200]}", file=sys.stderr)
        return False


def _wrangler_list_keys() -> list[str]:
    cmd = ["wrangler", "kv", "key", "list", "--remote", "--binding", KV_BINDING]
    try:
        result = subprocess.run(cmd, cwd=str(WRANGLER_DIR), check=True, capture_output=True, text=True)
        keys = json.loads(result.stdout)
        return [k["name"] for k in keys if k["name"].startswith("token:")]
    except Exception as exc:
        print(f"  ERROR: wrangler list failed: {exc}", file=sys.stderr)
        return []


def _wrangler_delete(key: str) -> bool:
    cmd = ["wrangler", "kv", "key", "delete", "--remote", "--binding", KV_BINDING, key]
    try:
        subprocess.run(cmd, cwd=str(WRANGLER_DIR), check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"  ERROR: wrangler delete {key} failed: {exc.stderr.strip()[:200]}", file=sys.stderr)
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync tokens.json → Cloudflare KV")
    ap.add_argument("--dry-run", action="store_true", help="Preview without writing")
    ap.add_argument("--prune", action="store_true", help="Delete KV keys not in tokens.json (be careful)")
    args = ap.parse_args()

    if not TOKENS_PATH.exists():
        sys.exit(f"tokens.json not found at {TOKENS_PATH}")
    if not WRANGLER_DIR.exists():
        sys.exit(f"wrangler dir not found at {WRANGLER_DIR}")

    active = _active_entries()
    kv_pairs: dict[str, str] = {}
    skipped = 0

    for entry in active:
        h = _hash_for_entry(entry)
        if not h:
            skipped += 1
            continue
        kv_pairs[f"token:{h}"] = json.dumps(_auth_context(entry))

    print(f"tokens.json: {len(active)} active entries, {len(kv_pairs)} sha256-backed, {skipped} skipped (bcrypt-only)")

    if args.dry_run:
        for key, value in list(kv_pairs.items())[:5]:
            print(f"  would put {key[:20]}… → {value[:80]}…")
        if len(kv_pairs) > 5:
            print(f"  (+ {len(kv_pairs) - 5} more)")
        if args.prune:
            existing = _wrangler_list_keys()
            to_delete = [k for k in existing if k not in kv_pairs]
            print(f"  would delete {len(to_delete)} KV keys")
        return

    # Real writes
    print(f"Pushing {len(kv_pairs)} keys to KV...")
    success = 0
    for key, value in kv_pairs.items():
        if _wrangler_put(key, value):
            success += 1
    print(f"Pushed {success}/{len(kv_pairs)}.")

    if args.prune:
        existing = _wrangler_list_keys()
        to_delete = [k for k in existing if k not in kv_pairs]
        print(f"Pruning {len(to_delete)} stale KV keys...")
        for key in to_delete:
            _wrangler_delete(key)


if __name__ == "__main__":
    main()
