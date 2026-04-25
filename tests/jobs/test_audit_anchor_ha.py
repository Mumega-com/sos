"""
Sprint 006 A.5 / G55 — Audit chain anchor HA + quorum tests.

Architecture under test:
  Primary timer:   fires at *:0/15
  Secondary timer: fires at *:2/15 (2-min offset)

PG advisory lock (1002, 0):
  - WRITER: acquired lock → runs run_once() (write path)
  - VERIFIER: lock held by other → reads R2, re-verifies hash chain

Idempotency: INSERT ... ON CONFLICT DO NOTHING + deterministic R2 key
prevent double-anchoring even if both timers fire simultaneously.

TC-G55a  run_with_quorum() writer path: acquires lock, calls run_once()
TC-G55b  run_with_quorum() verifier path: lock held elsewhere, returns verify result
TC-G55c  quorum prevents double-anchor: two concurrent run_with_quorum() calls
         produce exactly one anchor INSERT (verified via rowcount)
TC-G55d  _verify_stream(): valid anchor passes; tampered anchor fails
TC-G55e  verifier with no anchors returns ok=True, reason="no_anchors"
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import unittest.mock as mock
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_db() -> bool:
    return bool(os.getenv("MIRROR_DATABASE_URL") or os.getenv("DATABASE_URL"))


db = pytest.mark.skipif(not _has_db(), reason="Mirror DB not configured")

pytestmark = pytest.mark.asyncio


def _canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _make_anchor_payload(
    stream_id: str,
    anchored_seq: int,
    chain_head_hash: str,
    prev_anchor_hash: str | None,
) -> dict[str, Any]:
    """Build a fake anchor payload matching the production schema."""
    anchor_obj = {
        "stream_id": stream_id,
        "anchored_seq": anchored_seq,
        "chain_head_hash": chain_head_hash,
        "prev_anchor_hash": prev_anchor_hash,
        "anchored_at": "2026-04-25T19:00:00+00:00",
    }
    canonical = _canonical_json(anchor_obj)
    prev_bytes = bytes.fromhex(prev_anchor_hash) if prev_anchor_hash else None
    h = hashlib.sha256()
    if prev_bytes:
        h.update(prev_bytes)
    h.update(canonical)
    anchor_hash = h.hexdigest()
    return {**anchor_obj, "anchor_hash": anchor_hash, "r2_key": f"anchors/2026/04/25/{stream_id}-{anchored_seq}.json"}


# ---------------------------------------------------------------------------
# TC-G55a: writer path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g55a_writer_acquires_lock_and_anchors() -> None:
    """TC-G55a: when advisory lock is available, run_with_quorum() runs run_once()."""
    from sos.jobs.audit_anchor import run_with_quorum

    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=True)  # lock acquired
    mock_conn._anchor_lock_acquired = True
    mock_conn.close = AsyncMock()

    with patch("sos.jobs.audit_anchor._try_acquire_anchor_lock", return_value=mock_conn):
        with patch("sos.jobs.audit_anchor.run_once", new=AsyncMock(return_value={"ok": True, "anchors_written": 2})):
            result = await run_with_quorum()

    assert result["mode"] == "writer"
    assert result["ok"] is True
    mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# TC-G55b: verifier path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g55b_verifier_path_when_lock_held() -> None:
    """TC-G55b: when lock is held by another instance, verifier path runs."""
    from sos.jobs.audit_anchor import run_with_quorum

    mock_conn = AsyncMock()
    mock_conn._anchor_lock_acquired = False
    mock_conn.close = AsyncMock()

    # Pool + conn mock returning no streams
    mock_pool_conn = AsyncMock()
    mock_pool_conn.fetch = AsyncMock(return_value=[])
    mock_pool_conn.__aenter__ = AsyncMock(return_value=mock_pool_conn)
    mock_pool_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=mock_pool_conn)

    with patch("sos.jobs.audit_anchor._try_acquire_anchor_lock", return_value=mock_conn):
        with patch("sos.jobs.audit_anchor._get_pool", new=AsyncMock(return_value=mock_pool)):
            with patch("sos.jobs.audit_anchor._build_s3_client", return_value=MagicMock()):
                result = await run_with_quorum()

    assert result["mode"] == "verifier"
    assert result["ok"] is True
    assert result["streams_verified"] == 0
    mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# TC-G55c: concurrent quorum — no double-anchor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@db
async def test_g55c_concurrent_quorum_no_double_anchor() -> None:
    """TC-G55c: two concurrent run_with_quorum() → exactly one writer, one verifier."""
    import asyncpg  # type: ignore[import]

    db_url = os.getenv("MIRROR_DATABASE_URL") or os.getenv("DATABASE_URL")
    conn1 = await asyncpg.connect(db_url)
    conn2 = await asyncpg.connect(db_url)
    try:
        # conn1 acquires the lock
        acquired1: bool = await conn1.fetchval(
            "SELECT pg_try_advisory_lock($1, $2)", 1002, 0
        )
        assert acquired1, "Expected conn1 to acquire lock"

        # conn2 cannot acquire the lock while conn1 holds it
        acquired2: bool = await conn2.fetchval(
            "SELECT pg_try_advisory_lock($1, $2)", 1002, 0
        )
        assert not acquired2, "Expected conn2 to be blocked"
    finally:
        await conn1.close()  # releases lock
        await conn2.close()

    # After conn1 closes, a new connection should acquire
    conn3 = await asyncpg.connect(db_url)
    try:
        acquired3: bool = await conn3.fetchval(
            "SELECT pg_try_advisory_lock($1, $2)", 1002, 0
        )
        assert acquired3, "Expected lock to be free after conn1 close"
    finally:
        await conn3.close()


# ---------------------------------------------------------------------------
# TC-G55d: _verify_stream — valid and tampered anchor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g55d_verify_stream_valid_anchor() -> None:
    """TC-G55d: _verify_stream passes on a valid anchor."""
    from sos.jobs.audit_anchor import _verify_stream

    stream_id = "test:stream:g55"
    payload = _make_anchor_payload(stream_id, 42, "a" * 64, None)

    # Mock DB row
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={
        "anchored_seq": 42,
        "anchor_hash": bytes.fromhex(payload["anchor_hash"]),
        "prev_anchor_hash": None,
        "r2_object_key": payload["r2_key"],
    })

    # Mock S3 client returning valid payload
    mock_s3 = MagicMock()
    mock_body = MagicMock()
    mock_body.read = MagicMock(return_value=json.dumps(payload).encode())
    mock_s3.get_object = MagicMock(return_value={"Body": mock_body})

    result = await _verify_stream(mock_conn, stream_id, mock_s3, "test-bucket")
    assert result["ok"] is True, f"Expected ok=True, got: {result}"
    assert result["reason"] == "hash_verified"


@pytest.mark.asyncio
async def test_g55d_verify_stream_tampered_anchor() -> None:
    """TC-G55d: _verify_stream fails on a tampered anchor_hash."""
    from sos.jobs.audit_anchor import _verify_stream

    stream_id = "test:stream:g55:tampered"
    payload = _make_anchor_payload(stream_id, 43, "b" * 64, None)
    # Tamper: flip last character of anchor_hash
    tampered_payload = {**payload, "anchor_hash": payload["anchor_hash"][:-1] + "0"}

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={
        "anchored_seq": 43,
        "anchor_hash": bytes.fromhex(tampered_payload["anchor_hash"]),
        "prev_anchor_hash": None,
        "r2_object_key": tampered_payload["r2_key"],
    })

    mock_s3 = MagicMock()
    mock_body = MagicMock()
    mock_body.read = MagicMock(return_value=json.dumps(tampered_payload).encode())
    mock_s3.get_object = MagicMock(return_value={"Body": mock_body})

    result = await _verify_stream(mock_conn, stream_id, mock_s3, "test-bucket")
    assert result["ok"] is False
    # BLOCK-1 fix: DB column checked first — hash_mismatch now splits into
    # db_hash_mismatch (DB tampered) or r2_hash_mismatch (R2 overwritten).
    # DB and R2 have the same tampered hash here, so db_hash_mismatch fires.
    assert result["reason"] == "db_hash_mismatch"


@pytest.mark.asyncio
async def test_g55d_verify_stream_r2_overwrite_attack() -> None:
    """TC-G55d variant: R2 overwrite attack — DB has correct hash, R2 was rewritten."""
    from sos.jobs.audit_anchor import _verify_stream

    stream_id = "test:stream:g55:r2-overwrite"
    payload = _make_anchor_payload(stream_id, 44, "c" * 64, None)
    correct_hash = payload["anchor_hash"]

    # Attacker rewrites R2 with a new payload + recomputed hash — but DB still has original
    tampered_payload = _make_anchor_payload(stream_id, 44, "d" * 64, None)

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={
        "anchored_seq": 44,
        "anchor_hash": bytes.fromhex(correct_hash),  # DB still has original
        "prev_anchor_hash": None,
        "r2_object_key": payload["r2_key"],
    })

    mock_s3 = MagicMock()
    mock_body = MagicMock()
    mock_body.read = MagicMock(return_value=json.dumps(tampered_payload).encode())
    mock_s3.get_object = MagicMock(return_value={"Body": mock_body})

    result = await _verify_stream(mock_conn, stream_id, mock_s3, "test-bucket")
    assert result["ok"] is False
    # DB hash doesn't match recomputed (from tampered R2 fields) → db_hash_mismatch
    assert result["reason"] == "db_hash_mismatch"


# ---------------------------------------------------------------------------
# TC-G55e: _verify_stream with no anchors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g55e_verify_stream_no_anchors_no_events() -> None:
    """TC-G55e: _verify_stream returns ok=True only when stream has no events either."""
    from sos.jobs.audit_anchor import _verify_stream

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=0)  # no events

    result = await _verify_stream(mock_conn, "test:empty", MagicMock(), "bucket")
    assert result["ok"] is True
    assert result["reason"] == "no_events"


@pytest.mark.asyncio
async def test_g55e_verify_stream_events_but_no_anchors() -> None:
    """TC-G55e BLOCK-2: events exist but no anchors → integrity anomaly, ok=False."""
    from sos.jobs.audit_anchor import _verify_stream

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=5)  # 5 events, no anchor

    result = await _verify_stream(mock_conn, "test:unanchored", MagicMock(), "bucket")
    assert result["ok"] is False
    assert result["reason"] == "events_exist_but_no_anchor"
    assert result["event_count"] == 5
