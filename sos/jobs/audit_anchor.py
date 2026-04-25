"""sos.jobs.audit_anchor — WORM anchor job for the hash-chained audit stream (Burst 2B-2).

Runs every 15 minutes (via systemd timer or APScheduler).  For each active
audit stream it:

1. Finds the last anchored seq in ``audit_anchors``.
2. Reads the latest event in that stream from ``audit_events``.
3. If new events exist since the last anchor, writes an anchor record:
   - anchor_hash  = SHA-256( prev_anchor_hash_bytes || canonical_json(anchor_sans_hash) )
   - Uploads a JSON anchor file to Cloudflare R2 with Object Lock
     (COMPLIANCE mode, 7-year retention), named:
       anchors/{yyyy}/{mm}/{dd}/{stream_id}-{last_seq}.json
4. Inserts the anchor row into ``audit_anchors``.

R2 environment variables
------------------------
CLOUDFLARE_ACCOUNT_ID  — used to build the R2 endpoint URL
R2_ACCESS_KEY_ID       — R2 HMAC key id
R2_SECRET_ACCESS_KEY   — R2 HMAC secret
AUDIT_R2_BUCKET        — bucket name (required; no default — raise on unset)
                         Must point to a bucket created with Object Lock enabled.
                         (ADV-G51-WARN-4: fallback default removed — fail loud if unset)

Design notes
------------
- boto3 S3 calls are synchronous; we run them in asyncio's default
  thread-pool executor so the event loop stays responsive.
- The anchor chain is per-stream: prev_anchor_hash links the most recent
  anchor for that stream only (not across streams).
- Running multiple instances concurrently is safe: the INSERT ... ON CONFLICT
  DO NOTHING guard in step 4 ensures idempotency.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("sos.jobs.audit_anchor")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RETENTION_YEARS = 7

# Quorum / leader-election (Sprint 006 A.5 / G55)
_ANCHOR_LOCK_CLASSID: int = 1002  # audit-anchor namespace (separate from matchmaker 1003, G23 quest-xact 1001)
_ANCHOR_LOCK_OBJID: int = 0       # writer sentinel

# ---------------------------------------------------------------------------
# R2 / S3 client helpers
# ---------------------------------------------------------------------------


def _build_s3_client() -> Any:
    """Return a boto3 S3 client pointed at Cloudflare R2."""
    import boto3

    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or os.environ.get("CF_ACCOUNT_ID")
    if not account_id:
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID / CF_ACCOUNT_ID not set")

    endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def _r2_object_key(stream_id: str, last_seq: int, ts: datetime) -> str:
    """Return the canonical R2 object key for an anchor."""
    # Sanitise stream_id: replace ':' and other chars safe for path components
    safe_stream = stream_id.replace(":", "_").replace("/", "_")
    return f"anchors/{ts.year:04d}/{ts.month:02d}/{ts.day:02d}/{safe_stream}-{last_seq}.json"


def _verify_bucket_object_lock(s3_client: Any, bucket: str) -> None:
    """Assert bucket has Object Lock COMPLIANCE mode enabled.

    Called at anchor-service startup (ADV-G51-WARN-1).  Fails loud rather than
    silently writing to a bucket without WORM protection.

    Note: GetBucketObjectLockConfiguration is a HEAD-equivalent — read-only,
    no data access.  A scoped write-only R2 token that lacks this permission
    will cause a loud startup failure rather than silently anchoring to an
    unprotected bucket.
    """
    try:
        resp = s3_client.get_bucket_object_lock_configuration(Bucket=bucket)
        lock_cfg = resp.get("ObjectLockConfiguration", {})
        enabled = lock_cfg.get("ObjectLockEnabled", "") == "Enabled"
        rule = lock_cfg.get("Rule", {})
        mode = rule.get("DefaultRetention", {}).get("Mode", "")
        if not enabled or mode != "COMPLIANCE":
            raise RuntimeError(
                f"R2 bucket {bucket!r} does not have Object Lock COMPLIANCE mode enabled "
                f"(ObjectLockEnabled={lock_cfg.get('ObjectLockEnabled')!r}, Mode={mode!r}). "
                "WORM property is not enforced — refusing to anchor. "
                "Fix: recreate the bucket with Object Lock enabled at creation."
            )
    except s3_client.exceptions.ClientError as exc:  # type: ignore[attr-defined]
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("ObjectLockConfigurationNotFoundError", "NoSuchBucket"):
            raise RuntimeError(
                f"R2 bucket {bucket!r}: Object Lock not configured ({error_code}). "
                "Anchor service refusing to start without WORM guarantee."
            ) from exc
        # Other client errors (permission denied, etc.) — re-raise loud
        raise RuntimeError(
            f"R2 bucket {bucket!r}: Object Lock configuration check failed: {exc}"
        ) from exc


def _put_r2_object(s3_client: Any, bucket: str, key: str, body: bytes) -> None:
    """Upload body to R2 under the bucket's COMPLIANCE Object Lock retention rule.

    Cloudflare R2 enforces WORM via bucket-level Object Lock rules (set at creation).
    Per-object S3 Object Lock headers (ObjectLockMode, ObjectLockRetainUntilDate) are
    not used — CF R2 returns NotImplemented for them.  The bucket's COMPLIANCE rule
    covers all objects written under the anchors/ prefix automatically.

    The `AUDIT_R2_OBJECT_LOCK` env var and `retain` parameter have been removed
    (ADV-G51-WARN-2).  The bucket-level rule enforces WORM unconditionally; an env
    var escape hatch would be a lie (the bucket refuses deletes regardless of env state).
    """
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
    )


# ---------------------------------------------------------------------------
# Canonical JSON (mirrors audit_chain._canonical_json)
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_pool: Any = None


async def _get_pool() -> Any:
    global _pool
    if _pool is None:
        import asyncpg
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            raise RuntimeError(
                "DATABASE_URL is required for audit_anchor — no default allowed "
                "(insecure fallback removed per adversarial gate G55 BLOCK-4)"
            )
        _pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)
    return _pool


# ---------------------------------------------------------------------------
# Core anchor logic
# ---------------------------------------------------------------------------


async def _anchor_stream(
    conn: Any,
    stream_id: str,
    s3_client: Any,
    bucket: str,
) -> bool:
    """Attempt to anchor one stream.  Returns True if a new anchor was written."""

    # BLOCK-5 fix: REPEATABLE READ transaction holds the stream's head position
    # for the duration of both reads and the DB INSERT, preventing a race where
    # new events arrive between reads and the anchor references a stale seq.
    #
    # BLOCK-7 fix: DB INSERT happens inside the transaction (before R2 upload).
    # R2 upload happens after commit. If the process crashes after DB commit but
    # before R2 upload, the DB row exists with the correct r2_object_key and
    # anchor_hash; the verifier reports r2_fetch_failed (observable signal) until
    # a repair pass re-uploads the object. With WORM compliance mode on the R2
    # bucket, crash-after-R2-before-DB would have created an unoverwritable
    # orphan object — this order eliminates that scenario.
    loop = asyncio.get_event_loop()
    db_inserted = False
    r2_key: str = ""
    r2_body: bytes = b""

    async with conn.transaction(isolation="repeatable_read"):
        # 1. Latest event in this stream
        latest_event = await conn.fetchrow(
            """
            SELECT seq, hash FROM audit_events
            WHERE stream_id = $1
            ORDER BY seq DESC LIMIT 1
            """,
            stream_id,
        )
        if not latest_event:
            return False  # no events yet

        latest_seq: int = latest_event["seq"]
        chain_head_hash: bytes = bytes(latest_event["hash"])

        # 2. Last anchor for this stream
        last_anchor = await conn.fetchrow(
            """
            SELECT anchored_seq, anchor_hash FROM audit_anchors
            WHERE stream_id = $1
            ORDER BY anchored_seq DESC LIMIT 1
            """,
            stream_id,
        )

        if last_anchor and last_anchor["anchored_seq"] >= latest_seq:
            return False  # already anchored up to the current head

        prev_anchor_hash: bytes | None = (
            bytes(last_anchor["anchor_hash"]) if last_anchor else None
        )

        # 3. Build anchor object
        anchored_at = datetime.now(timezone.utc)
        anchor_obj: dict[str, Any] = {
            "stream_id": stream_id,
            "anchored_seq": latest_seq,
            "chain_head_hash": chain_head_hash.hex(),
            "prev_anchor_hash": prev_anchor_hash.hex() if prev_anchor_hash else None,
            "anchored_at": anchored_at.isoformat(),
        }
        anchor_canonical = _canonical_json(anchor_obj)

        # 4. Compute anchor_hash = SHA-256(prev_anchor_hash_bytes || anchor_canonical)
        h = hashlib.sha256()
        if prev_anchor_hash:
            h.update(prev_anchor_hash)
        h.update(anchor_canonical)
        anchor_hash: bytes = h.digest()

        # 5. R2 object key (computed before INSERT so it's in the DB row)
        r2_key = _r2_object_key(stream_id, latest_seq, anchored_at)

        # 6. Build the full R2 payload (includes the anchor_hash)
        r2_payload = {**anchor_obj, "anchor_hash": anchor_hash.hex(), "r2_key": r2_key}
        r2_body = _canonical_json(r2_payload)

        # 7. DB INSERT first (idempotent: ON CONFLICT DO NOTHING)
        insert_status: str = await conn.execute(
            """
            INSERT INTO audit_anchors
                (stream_id, anchored_seq, chain_head_hash, prev_anchor_hash, anchor_hash, r2_object_key, anchored_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (stream_id, anchored_seq) DO NOTHING
            """,
            stream_id,
            latest_seq,
            chain_head_hash,
            prev_anchor_hash,
            anchor_hash,
            r2_key,
            anchored_at,
        )
        db_inserted = insert_status == "INSERT 0 1"

    if not db_inserted:
        return False  # concurrent writer already anchored this seq

    # 8. R2 upload after DB commit (outside transaction).
    # On failure: DB row exists, chain is intact. Verifier reports r2_fetch_failed
    # until a repair pass re-uploads the object. No unrecoverable WORM orphan.
    try:
        await loop.run_in_executor(
            None,
            lambda: _put_r2_object(s3_client, bucket, r2_key, r2_body),
        )
        logger.info(
            "audit_anchor: uploaded stream=%s seq=%d key=%s",
            stream_id, latest_seq, r2_key,
        )
    except Exception as exc:
        logger.error(
            "audit_anchor: R2 upload failed stream=%s seq=%d: %s — "
            "anchor committed to DB, chain intact; R2 repair needed",
            stream_id, latest_seq, exc,
        )
        # Return True: the anchor IS in DB. Verifier will signal r2_fetch_failed.
        return True

    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_once() -> dict[str, Any]:
    """Walk all active streams and anchor each.

    Returns a summary dict suitable for logging / health checks.
    """
    pool = await _get_pool()

    # ADV-G51-WARN-4: no fallback default — fail loud if AUDIT_R2_BUCKET is unset
    bucket = os.environ.get("AUDIT_R2_BUCKET")
    if not bucket:
        raise RuntimeError(
            "AUDIT_R2_BUCKET is required — no default allowed. "
            "Set to a bucket created with Object Lock enabled at creation."
        )

    # Build S3 client — fail loud if env missing
    try:
        s3_client = await asyncio.get_event_loop().run_in_executor(None, _build_s3_client)
    except Exception as exc:
        logger.error("audit_anchor: cannot build R2 client: %s", exc)
        return {"ok": False, "error": str(exc), "anchored_streams": 0}

    # ADV-G51-WARN-1: verify bucket has Object Lock COMPLIANCE mode before writing
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: _verify_bucket_object_lock(s3_client, bucket)
        )
    except RuntimeError as exc:
        logger.error("audit_anchor: bucket Object Lock verification failed: %s", exc)
        return {"ok": False, "error": str(exc), "anchored_streams": 0}

    async with pool.acquire() as conn:
        # All streams that have at least one event
        rows = await conn.fetch(
            "SELECT stream_id FROM audit_stream_seqs ORDER BY stream_id"
        )
        streams = [row["stream_id"] for row in rows]

    results: dict[str, bool] = {}
    for stream_id in streams:
        async with pool.acquire() as conn:
            anchored = await _anchor_stream(conn, stream_id, s3_client, bucket)
            results[stream_id] = anchored

    total = len(results)
    wrote = sum(1 for v in results.values() if v)
    logger.info(
        "audit_anchor: run complete — %d streams checked, %d new anchors written",
        total, wrote,
    )
    return {
        "ok": True,
        "streams_checked": total,
        "anchors_written": wrote,
        "detail": results,
    }


# ---------------------------------------------------------------------------
# Quorum: advisory lock + verifier path (Sprint 006 A.5 / G55)
# ---------------------------------------------------------------------------


async def _try_acquire_anchor_lock() -> "asyncpg.Connection":  # type: ignore[name-defined]
    """Open a dedicated asyncpg connection and try to acquire the writer lock.

    Returns the connection.  Caller checks the returned ``acquired`` bool
    (stored as an attribute set below).  Caller MUST close the connection
    when done — closing it releases the session-level advisory lock.
    """
    import asyncpg  # type: ignore[import]

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL is required for audit_anchor advisory lock — no default allowed "
            "(insecure fallback removed per adversarial gate G55 BLOCK-4)"
        )
    conn = await asyncpg.connect(db_url)
    acquired: bool = await conn.fetchval(
        "SELECT pg_try_advisory_lock($1, $2)",
        _ANCHOR_LOCK_CLASSID,
        _ANCHOR_LOCK_OBJID,
    )
    conn._anchor_lock_acquired = acquired  # type: ignore[attr-defined]
    return conn


async def _verify_stream(
    conn: Any,
    stream_id: str,
    s3_client: Any,
    bucket: str,
) -> dict[str, Any]:
    """Re-verify the most recent anchor for a stream.

    Fetches the R2 object, recomputes SHA-256(prev_hash || canonical_anchor),
    and compares with the stored ``anchor_hash``.

    Returns {"stream_id": ..., "ok": bool, "reason": str, ...}.
    """
    anchor_row = await conn.fetchrow(
        """
        SELECT anchored_seq, anchor_hash, prev_anchor_hash, r2_object_key
        FROM audit_anchors
        WHERE stream_id = $1
        ORDER BY anchored_seq DESC
        LIMIT 1
        """,
        stream_id,
    )
    if not anchor_row:
        # BLOCK-2 fix: ok=True only if the stream also has no events.
        # "no anchors" with existing events is an integrity anomaly.
        # WARN-4 fix: query audit_events (committed events), not audit_stream_seqs
        # (which tracks registered streams, not event presence).
        event_count: int = await conn.fetchval(
            "SELECT COUNT(*) FROM audit_events WHERE stream_id = $1",
            stream_id,
        )
        if event_count:
            logger.error(
                "audit_anchor: INTEGRITY ANOMALY stream=%s has %d event(s) but no anchors",
                stream_id, event_count,
            )
            return {
                "stream_id": stream_id,
                "ok": False,
                "reason": "events_exist_but_no_anchor",
                "event_count": event_count,
            }
        return {"stream_id": stream_id, "ok": True, "reason": "no_events"}

    r2_key: str = anchor_row["r2_object_key"]
    loop = asyncio.get_event_loop()

    # Fetch R2 object
    try:
        r2_obj = await loop.run_in_executor(
            None,
            lambda: s3_client.get_object(Bucket=bucket, Key=r2_key),
        )
        r2_bytes: bytes = await loop.run_in_executor(None, r2_obj["Body"].read)
    except Exception as exc:
        logger.error("audit_anchor: verify R2 fetch failed stream=%s key=%s: %s", stream_id, r2_key, exc)
        return {"stream_id": stream_id, "ok": False, "reason": f"r2_fetch_failed: {exc}"}

    try:
        r2_data: dict[str, Any] = json.loads(r2_bytes)
    except Exception as exc:
        return {"stream_id": stream_id, "ok": False, "reason": f"r2_parse_failed: {exc}"}

    stored_hash_hex: str | None = r2_data.get("anchor_hash")
    if not stored_hash_hex:
        return {"stream_id": stream_id, "ok": False, "reason": "anchor_hash_missing_in_r2"}

    # Recompute: SHA-256(prev_anchor_hash_bytes || canonical_json(anchor_sans_hash_and_key))
    anchor_obj = {k: v for k, v in r2_data.items() if k not in ("anchor_hash", "r2_key")}
    anchor_canonical = _canonical_json(anchor_obj)

    prev_hash_bytes: bytes | None = None
    if r2_data.get("prev_anchor_hash"):
        try:
            prev_hash_bytes = bytes.fromhex(r2_data["prev_anchor_hash"])
        except ValueError:
            return {"stream_id": stream_id, "ok": False, "reason": "prev_anchor_hash_not_hex"}

    h = hashlib.sha256()
    if prev_hash_bytes:
        h.update(prev_hash_bytes)
    h.update(anchor_canonical)
    expected_hex = h.hexdigest()

    # BLOCK-1 fix: cross-reference against DB anchor_hash (authoritative) AND R2.
    # R2 is the WORM copy; DB is the source of truth. Both must agree.
    db_hash_hex: str = anchor_row["anchor_hash"].hex() if isinstance(anchor_row["anchor_hash"], (bytes, bytearray)) else anchor_row["anchor_hash"]

    if expected_hex != db_hash_hex:
        logger.error(
            "audit_anchor: DB CHAIN INTEGRITY FAILURE stream=%s seq=%d expected=%s db_stored=%s",
            stream_id, anchor_row["anchored_seq"], expected_hex, db_hash_hex,
        )
        return {
            "stream_id": stream_id,
            "ok": False,
            "reason": "db_hash_mismatch",
            "seq": anchor_row["anchored_seq"],
            "expected": expected_hex,
            "db_stored": db_hash_hex,
        }

    if expected_hex != stored_hash_hex:
        logger.error(
            "audit_anchor: R2 CHAIN INTEGRITY FAILURE stream=%s seq=%d expected=%s r2_stored=%s",
            stream_id, anchor_row["anchored_seq"], expected_hex, stored_hash_hex,
        )
        return {
            "stream_id": stream_id,
            "ok": False,
            "reason": "r2_hash_mismatch",
            "seq": anchor_row["anchored_seq"],
            "expected": expected_hex,
            "r2_stored": stored_hash_hex,
        }

    # BLOCK-6 fix: verify the chain link — current anchor's prev_anchor_hash must
    # match the anchor_hash of the previous anchor row. Without this, an attacker
    # can insert a forged top-of-chain anchor with any prev_anchor_hash and pass
    # the per-anchor hash check. One-depth check per cycle is sufficient.
    current_prev_raw = anchor_row["prev_anchor_hash"]
    if current_prev_raw is not None:
        current_prev_hex = (
            current_prev_raw.hex()
            if isinstance(current_prev_raw, (bytes, bytearray))
            else current_prev_raw
        )
        prev_anchor = await conn.fetchrow(
            """
            SELECT anchor_hash FROM audit_anchors
            WHERE stream_id = $1 AND anchored_seq < $2
            ORDER BY anchored_seq DESC LIMIT 1
            """,
            stream_id,
            anchor_row["anchored_seq"],
        )
        if prev_anchor is None:
            logger.error(
                "audit_anchor: CHAIN LINK BROKEN stream=%s seq=%d has prev_anchor_hash but no prior row",
                stream_id, anchor_row["anchored_seq"],
            )
            return {
                "stream_id": stream_id,
                "ok": False,
                "reason": "prev_anchor_missing",
                "seq": anchor_row["anchored_seq"],
            }
        prev_db_hash_hex = (
            prev_anchor["anchor_hash"].hex()
            if isinstance(prev_anchor["anchor_hash"], (bytes, bytearray))
            else prev_anchor["anchor_hash"]
        )
        if prev_db_hash_hex != current_prev_hex:
            logger.error(
                "audit_anchor: CHAIN LINK BROKEN stream=%s seq=%d prev_anchor_hash=%s but prior row hash=%s",
                stream_id, anchor_row["anchored_seq"], current_prev_hex, prev_db_hash_hex,
            )
            return {
                "stream_id": stream_id,
                "ok": False,
                "reason": "chain_link_broken",
                "seq": anchor_row["anchored_seq"],
                "expected_prev": current_prev_hex,
                "actual_prev": prev_db_hash_hex,
            }

    return {
        "stream_id": stream_id,
        "ok": True,
        "reason": "hash_verified",
        "seq": anchor_row["anchored_seq"],
    }


async def run_with_quorum() -> dict[str, Any]:
    """Run with PG advisory lock quorum: one writer, one verifier.

    Acquires session-level advisory lock (1002, 0):
    - WRITER (lock acquired): runs the existing anchor-write logic.
    - VERIFIER (lock not acquired): re-reads last anchor per stream from R2
      and verifies the hash chain.

    If the primary instance is dead, the secondary acquires the lock on its
    next timer fire and becomes the writer for that cycle.
    """
    lock_conn = await _try_acquire_anchor_lock()
    try:
        writer: bool = lock_conn._anchor_lock_acquired  # type: ignore[attr-defined]
        mode = "writer" if writer else "verifier"
        logger.info("audit_anchor: quorum mode=%s", mode)

        if writer:
            result = await run_once()
            result["mode"] = "writer"
            return result

        # Verifier path
        # ADV-G51-WARN-4: no fallback default
        bucket = os.environ.get("AUDIT_R2_BUCKET")
        if not bucket:
            raise RuntimeError("AUDIT_R2_BUCKET is required — no default allowed")
        try:
            s3_client = await asyncio.get_event_loop().run_in_executor(None, _build_s3_client)
        except Exception as exc:
            logger.error("audit_anchor: verifier cannot build R2 client: %s", exc)
            return {"ok": False, "mode": "verifier", "error": str(exc)}

        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT stream_id FROM audit_stream_seqs ORDER BY stream_id"
            )
            streams = [row["stream_id"] for row in rows]

        instance_id = os.environ.get("AUDIT_ANCHOR_INSTANCE_ID", "audit-anchor-secondary")
        verify_results: dict[str, Any] = {}
        all_ok = True
        for stream_id in streams:
            async with pool.acquire() as conn:
                vr = await _verify_stream(conn, stream_id, s3_client, bucket)
            verify_results[stream_id] = vr
            if not vr["ok"]:
                all_ok = False
                # BLOCK-1: drift must be loud — emit explicit bus signal per failing stream
                try:
                    from sos.observability.sprint_telemetry import emit_verifier_drift_detected
                    emit_verifier_drift_detected(
                        stream_id=stream_id,
                        reason=vr.get("reason", "unknown"),
                        seq=vr.get("anchored_seq"),
                        instance_id=instance_id,
                    )
                except Exception as _emit_exc:
                    logger.error("audit_anchor: emit_verifier_drift_detected failed: %s", _emit_exc)
                logger.warning(
                    "audit_anchor: DRIFT DETECTED stream=%s reason=%s seq=%s",
                    stream_id, vr.get("reason"), vr.get("anchored_seq"),
                )

        logger.info(
            "audit_anchor: verify complete — %d streams, all_ok=%s",
            len(streams), all_ok,
        )
        return {
            "ok": all_ok,
            "mode": "verifier",
            "streams_verified": len(streams),
            "detail": verify_results,
        }
    finally:
        await lock_conn.close()  # closing releases the session-level advisory lock


def main() -> None:
    """CLI entry point: run anchor job once and exit.

    Default: quorum mode (PG advisory lock, one writer + one verifier).
    Pass --no-quorum to use the legacy single-instance run_once() directly
    (useful for manual maintenance or first-time setup).
    """
    import argparse

    parser = argparse.ArgumentParser(description="SOS audit WORM anchor job")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run in a loop every --interval seconds instead of once",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=900,
        metavar="SECONDS",
        help="Loop interval in seconds (default: 900 = 15 min)",
    )
    parser.add_argument(
        "--no-quorum",
        action="store_true",
        dest="no_quorum",
        help="Bypass quorum lock — run run_once() directly (maintenance/test)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # BLOCK-3 fix: --no-quorum is a production security control bypass.
    # Require explicit AUDIT_MAINTENANCE_MODE=1 to prevent accidental/malicious use
    # via systemd overrides or CI scripts.
    if args.no_quorum and os.environ.get("AUDIT_MAINTENANCE_MODE") != "1":
        parser.error("--no-quorum requires AUDIT_MAINTENANCE_MODE=1 (set env var to enable)")

    _runner = run_once if args.no_quorum else run_with_quorum

    async def _run() -> None:
        if args.loop:
            logger.info("audit_anchor: starting loop, interval=%ds, quorum=%s",
                        args.interval, not args.no_quorum)
            while True:
                result = await _runner()
                logger.info("audit_anchor: result=%s", result)
                await asyncio.sleep(args.interval)
        else:
            result = await _runner()
            print(json.dumps(result, indent=2))
            # BLOCK-1: verifier drift must produce non-zero exit so systemd alerts.
            # Systemd's OnFailure= + journald capture this; silent ok=False exit 0 = invisible.
            if result.get("mode") == "verifier" and not result.get("ok"):
                import sys
                sys.exit(1)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
