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
AUDIT_R2_BUCKET        — bucket name (default: sos-audit-worm)
AUDIT_R2_OBJECT_LOCK   — set to "false" to skip Object Lock (dev/test)
                         (default: "true" in production)

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


def _put_r2_object(s3_client: Any, bucket: str, key: str, body: bytes, retain: bool) -> None:
    """Upload body to R2 with optional Object Lock retention.

    Cloudflare R2 uses bucket-level lock rules (not per-object S3 headers).
    When retain=True, we rely on the bucket's COMPLIANCE retention rule
    (set via CF API: 7-year rule on anchors/ prefix). Per-object
    ObjectLockMode/ObjectLockRetainUntilDate headers are NOT sent — CF R2
    returns NotImplemented for them. The bucket rule enforces retention
    automatically on any object written under the anchors/ prefix.
    """
    put_kwargs: dict[str, Any] = {
        "Bucket": bucket,
        "Key": key,
        "Body": body,
        "ContentType": "application/json",
    }
    # Note: AWS S3 per-object lock headers (ObjectLockMode, ObjectLockRetainUntilDate)
    # are NOT used — CF R2 enforces retention via bucket-level rules only.
    s3_client.put_object(**put_kwargs)


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
        db_url = os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/postgres",
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
    use_object_lock: bool,
) -> bool:
    """Attempt to anchor one stream.  Returns True if a new anchor was written."""

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

    # 5. R2 object key
    r2_key = _r2_object_key(stream_id, latest_seq, anchored_at)

    # 6. Build the full R2 payload (includes the anchor_hash)
    r2_payload = {**anchor_obj, "anchor_hash": anchor_hash.hex(), "r2_key": r2_key}
    r2_body = _canonical_json(r2_payload)

    # 7. Upload to R2 in thread pool (boto3 is synchronous)
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: _put_r2_object(s3_client, bucket, r2_key, r2_body, use_object_lock),
        )
        logger.info(
            "audit_anchor: uploaded stream=%s seq=%d key=%s lock=%s",
            stream_id, latest_seq, r2_key, use_object_lock,
        )
    except Exception as exc:
        logger.error(
            "audit_anchor: R2 upload failed stream=%s seq=%d: %s",
            stream_id, latest_seq, exc,
        )
        # Do NOT write DB row if R2 failed — keeps chain honest
        return False

    # 8. Insert anchor row (idempotent: ON CONFLICT DO NOTHING)
    await conn.execute(
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
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_once() -> dict[str, Any]:
    """Walk all active streams and anchor each.

    Returns a summary dict suitable for logging / health checks.
    """
    pool = await _get_pool()

    bucket = os.getenv("AUDIT_R2_BUCKET", "sos-audit-worm")
    use_object_lock = os.getenv("AUDIT_R2_OBJECT_LOCK", "true").lower() != "false"

    # Build S3 client lazily — fail loud if env missing
    try:
        s3_client = await asyncio.get_event_loop().run_in_executor(None, _build_s3_client)
    except Exception as exc:
        logger.error("audit_anchor: cannot build R2 client: %s", exc)
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
            anchored = await _anchor_stream(conn, stream_id, s3_client, bucket, use_object_lock)
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


def main() -> None:
    """CLI entry point: run anchor job once and exit."""
    import argparse

    parser = argparse.ArgumentParser(description="SOS audit WORM anchor job")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run in a loop every 15 minutes instead of once",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=900,
        metavar="SECONDS",
        help="Loop interval in seconds (default: 900 = 15 min)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    async def _run() -> None:
        if args.loop:
            logger.info("audit_anchor: starting loop, interval=%ds", args.interval)
            while True:
                result = await run_once()
                logger.info("audit_anchor: result=%s", result)
                await asyncio.sleep(args.interval)
        else:
            result = await run_once()
            print(json.dumps(result, indent=2))

    asyncio.run(_run())


if __name__ == "__main__":
    main()
