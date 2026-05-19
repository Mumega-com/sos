"""sos.kernel.audit_chain — unified hash-chained audit stream (Burst 2B-2).

This module implements the tamper-evident PG-backed audit layer that
supersedes Sprint 001 K9/K10 (immutable lock + hash-chained timestamps).

Public API
----------
emit_audit(event) → str
    Persist one AuditChainEvent to ``audit_events``.  Returns the UUID.

    Constraints enforced (Athena G4):
    - seq = nextval('audit_seq_<stream_id>') via the DB function —
      never an app-side counter.
    - len(canonical_json(payload)) ≤ 8192 bytes; if larger, payload is
      replaced with {summary, hash_of_full} and payload_redacted=True.
    - stream_id='dispatcher' requires Ed25519 signature; helper raises
      AuditSigningRequired if the signing key is not available.
    - All other streams: signature set if AUDIT_SIGNING_KEY is in env,
      otherwise null.

    hash = SHA-256(prev_hash_bytes || canonical_json(event_without_hash))
    prev_hash = hash of the previous event in the same stream (NULL for genesis).

Design notes
------------
- Per-stream chains (not one global chain) so high-throughput streams don't
  serialize through a single lock.  audit_next_seq() uses a PG advisory lock
  keyed on abs(hashtext(stream_id)) — atomic, no application-level locking.
- The audit pool (asyncpg) is a module-level singleton initialised lazily on
  the first emit_audit call.  Services that want to supply their own pool can
  call set_pool() before the first emission.
- The WORM anchor job lives in sos/jobs/audit_anchor.py.
- verify_chain lives in sos/scripts/verify_chain.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("sos.kernel.audit_chain")

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AuditSigningRequired(RuntimeError):
    """Raised when stream_id='dispatcher' is emitted without a signing key."""


# ---------------------------------------------------------------------------
# Event shape
# ---------------------------------------------------------------------------

@dataclass
class AuditChainEvent:
    """Input shape for emit_audit.

    All fields required except payload, payload_summary, and signature_key.
    """
    stream_id: str        # 'kernel' | 'mirror' | 'squad' | 'dispatcher' | 'plugin:<name>'
    actor_id: str         # principal_id or 'system'
    actor_type: str       # 'agent' | 'human' | 'system'
    action: str           # verb: 'created', 'updated', 'granted', 'denied', ...
    resource: str         # e.g. 'engram:abc123', 'role_assignment:xyz'
    payload: dict[str, Any] | None = None
    # Override canonical payload summary used when payload is redacted
    payload_summary: str | None = None


# ---------------------------------------------------------------------------
# Module-level pool singleton
# ---------------------------------------------------------------------------

_pool: Any = None  # asyncpg.Pool


def set_pool(pool: Any) -> None:
    """Allow callers to inject a pre-created asyncpg pool."""
    global _pool
    _pool = pool


async def _get_pool() -> Any:
    global _pool
    if _pool is None:
        import asyncpg
        db_url = os.getenv("MIRROR_DATABASE_URL") or os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/postgres",
        )
        _pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
    return _pool


# ---------------------------------------------------------------------------
# Signing key helpers
# ---------------------------------------------------------------------------

def _load_signing_key() -> Any | None:
    """Load Ed25519 private key from AUDIT_SIGNING_KEY env var (base64).

    Returns None if the env var is absent.  Only the 'dispatcher' stream
    raises when the key is absent — other streams treat it as optional.
    """
    raw = os.getenv("AUDIT_SIGNING_KEY", "")
    if not raw:
        return None
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key_bytes = base64.b64decode(raw)
    return Ed25519PrivateKey.from_private_bytes(key_bytes)


def _sign(key: Any, data: bytes) -> bytes:
    return key.sign(data)


# ---------------------------------------------------------------------------
# Canonical JSON helper
# ---------------------------------------------------------------------------

def _canonical_json(obj: Any) -> bytes:
    """Sorted-key, no-whitespace JSON, UTF-8 encoded."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Payload size enforcement
# ---------------------------------------------------------------------------

_PAYLOAD_MAX_BYTES = 8192


def _enforce_payload_size(
    payload: dict[str, Any] | None,
    summary: str | None,
) -> tuple[dict[str, Any] | None, bool]:
    """Return (final_payload, was_redacted).

    If payload > 8KB, replace with {summary, hash_of_full}.
    """
    if payload is None:
        return None, False
    raw = _canonical_json(payload)
    if len(raw) <= _PAYLOAD_MAX_BYTES:
        return payload, False
    # Redact: replace with summary + hash of original
    full_hash = hashlib.sha256(raw).hexdigest()
    redacted = {
        "summary": summary or f"payload redacted — original {len(raw)} bytes",
        "hash_of_full": full_hash,
    }
    return redacted, True


# ---------------------------------------------------------------------------
# Core emit function
# ---------------------------------------------------------------------------

async def emit_audit(event: AuditChainEvent) -> str:
    """Persist one hash-chained audit event.  Returns the UUID string.

    This is the single emission point for all services.  Call it after
    the action completes (not before — seq allocation is the lock point).

    Thread / task safety: safe for concurrent asyncio tasks; advisory lock
    in audit_next_seq() serialises per-stream seq allocation at the DB level.
    """
    pool = await _get_pool()

    # 1. Enforce payload size
    payload, payload_redacted = _enforce_payload_size(
        event.payload, event.payload_summary
    )

    # 2. Resolve signing key
    signing_key = _load_signing_key()
    if event.stream_id == "dispatcher" and signing_key is None:
        raise AuditSigningRequired(
            "stream_id='dispatcher' requires AUDIT_SIGNING_KEY — "
            "dispatcher events cross a trust boundary and must be signed."
        )

    ts_now = datetime.now(timezone.utc)
    event_id = str(uuid.uuid4())

    async with pool.acquire() as conn:
        async with conn.transaction():
            # 3. Allocate seq atomically via PG advisory lock function
            seq: int = await conn.fetchval(
                "SELECT audit_next_seq($1)", event.stream_id
            )

            # 4. Fetch prev_hash for this stream
            prev_row = await conn.fetchrow(
                """
                SELECT hash FROM audit_events
                WHERE stream_id = $1 AND seq = $2
                """,
                event.stream_id,
                seq - 1,
            )
            prev_hash: bytes | None = prev_row["hash"] if prev_row else None

            # 5. Build canonical representation (without hash/signature fields)
            canonical_obj = {
                "id": event_id,
                "stream_id": event.stream_id,
                "seq": seq,
                "ts": ts_now.isoformat(),
                "actor_id": event.actor_id,
                "actor_type": event.actor_type,
                "action": event.action,
                "resource": event.resource,
                "payload": payload,
                "payload_redacted": payload_redacted,
                "prev_hash": prev_hash.hex() if prev_hash else None,
            }
            canonical_bytes = _canonical_json(canonical_obj)

            # 6. Compute hash: SHA-256(prev_hash_bytes || canonical_bytes)
            h = hashlib.sha256()
            if prev_hash:
                h.update(prev_hash)
            h.update(canonical_bytes)
            event_hash: bytes = h.digest()

            # 7. Sign hash (mandatory for dispatcher, optional otherwise)
            signature: bytes | None = None
            if signing_key is not None:
                signature = _sign(signing_key, event_hash)

            # 8. Insert
            await conn.execute(
                """
                INSERT INTO audit_events (
                    id, stream_id, seq, ts,
                    actor_id, actor_type, action, resource,
                    payload, payload_redacted,
                    prev_hash, hash, signature
                ) VALUES (
                    $1, $2, $3, $4,
                    $5, $6, $7, $8,
                    $9, $10,
                    $11, $12, $13
                )
                """,
                event_id, event.stream_id, seq, ts_now,
                event.actor_id, event.actor_type, event.action, event.resource,
                json.dumps(payload) if payload is not None else None,
                payload_redacted,
                prev_hash, event_hash, signature,
            )

            # 9. Update genesis hash on first event in stream
            if seq == 1:
                await conn.execute(
                    """
                    UPDATE audit_stream_seqs
                    SET genesis_hash = $1
                    WHERE stream_id = $2 AND genesis_hash IS NULL
                    """,
                    event_hash,
                    event.stream_id,
                )

    logger.debug(
        "audit_chain: stream=%s seq=%d actor=%s action=%s resource=%s",
        event.stream_id, seq, event.actor_id, event.action, event.resource,
    )
    return event_id


# ---------------------------------------------------------------------------
# Chain verification utility (also available as CLI via verify_chain.py)
# ---------------------------------------------------------------------------

async def verify_chain(
    stream_id: str,
    from_seq: int = 1,
    to_seq: int | None = None,
) -> dict[str, Any]:
    """Recompute SHA-256 chain for stream_id[from_seq..to_seq].

    Returns:
        {"ok": True, "checked": N}  on success
        {"ok": False, "broken_at_seq": N, "reason": str}  on failure
    """
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT seq, prev_hash, hash, payload, payload_redacted,
                   id, stream_id, ts, actor_id, actor_type, action, resource
            FROM audit_events
            WHERE stream_id = $1
              AND seq >= $2
              AND ($3::BIGINT IS NULL OR seq <= $3)
            ORDER BY seq ASC
            """,
            stream_id, from_seq, to_seq,
        )

    if not rows:
        return {"ok": True, "checked": 0}

    prev_hash: bytes | None = None
    for row in rows:
        seq = row["seq"]

        # Reconstruct canonical object
        payload_val = json.loads(row["payload"]) if row["payload"] else None
        # Normalise types that asyncpg returns as non-JSON-serialisable objects
        ts_val = row["ts"]
        if hasattr(ts_val, "isoformat"):
            ts_str = ts_val.isoformat()
        else:
            ts_str = str(ts_val)
        canonical_obj = {
            "id": str(row["id"]),      # UUID → str
            "stream_id": row["stream_id"],
            "seq": seq,
            "ts": ts_str,
            "actor_id": row["actor_id"],
            "actor_type": row["actor_type"],
            "action": row["action"],
            "resource": row["resource"],
            "payload": payload_val,
            "payload_redacted": row["payload_redacted"],
            "prev_hash": prev_hash.hex() if prev_hash else None,
        }
        canonical_bytes = _canonical_json(canonical_obj)

        h = hashlib.sha256()
        if prev_hash:
            h.update(prev_hash)
        h.update(canonical_bytes)
        expected_hash = h.digest()

        stored_hash: bytes = bytes(row["hash"])
        if stored_hash != expected_hash:
            return {
                "ok": False,
                "broken_at_seq": seq,
                "reason": f"hash mismatch: stored={stored_hash.hex()[:16]}… expected={expected_hash.hex()[:16]}…",
            }

        # Verify prev_hash pointer
        stored_prev: bytes | None = bytes(row["prev_hash"]) if row["prev_hash"] else None
        if stored_prev != prev_hash:
            return {
                "ok": False,
                "broken_at_seq": seq,
                "reason": "prev_hash pointer mismatch — possible row insertion or reordering",
            }

        prev_hash = stored_hash

    return {"ok": True, "checked": len(rows)}


__all__ = [
    "AuditChainEvent",
    "AuditSigningRequired",
    "emit_audit",
    "verify_chain",
    "set_pool",
]
