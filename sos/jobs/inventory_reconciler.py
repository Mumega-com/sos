"""
§14 Inventory reconciler — hourly background job (Sprint 003 Track C).

Walks inventory_grants where last_verified_at < now() - 24h and status='active'.
For each row, calls the per-kind verifier from VERIFIERS registry.
Updates status + last_verified_at + verify_attempt_count + last_error.
Hard-deletes orphaned/revoked rows older than 30 days (GC pass).

Run standalone:
    DATABASE_URL=... python3 -m sos.jobs.inventory_reconciler

Run via systemd timer: inventory-reconciler.timer (1h interval).

Loom's spec §6 + Athena G9: increment attempt_count on failure, reset on success.
Emit audit to stream='inventory' on status change.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow SOS imports when run standalone
_SOS = str(Path.home() / 'SOS')
if _SOS not in sys.path:
    sys.path.insert(0, _SOS)

import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)

_BATCH_SIZE = 1000
_STALE_HOURS = 24
_GC_DAYS = 30


def _db_url() -> str:
    url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('MIRROR_DATABASE_URL or DATABASE_URL not set')
    return url


def _connect():
    return psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)


def _emit_audit_sync(action: str, resource: str, payload: dict) -> None:
    try:
        import asyncio
        from sos.kernel.audit_chain import AuditChainEvent, emit_audit

        event = AuditChainEvent(
            stream_id='inventory',
            actor_id='system',
            actor_type='system',
            action=action,
            resource=resource,
            payload=payload,
        )
        asyncio.run(emit_audit(event))
    except Exception:  # noqa: BLE001
        log.debug('reconciler audit emission failed (non-fatal)', exc_info=True)


def reconcile_inventory() -> dict:
    """
    Main reconcile pass.

    Returns summary dict with counts for logging / monitoring.
    """
    from sos.contracts.inventory import VERIFIERS

    stats = {
        'checked': 0,
        'verified_ok': 0,
        'demoted': 0,
        'skipped_no_verifier': 0,
        'gc_deleted': 0,
        'errors': 0,
    }

    # ── Verification pass ──────────────────────────────────────────────────────
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT grant_id, holder_id, capability_kind, capability_ref, status
                      FROM inventory_grants
                     WHERE status = 'active'
                       AND last_verified_at < now() - interval '{_STALE_HOURS} hours'
                     ORDER BY last_verified_at ASC
                     LIMIT {_BATCH_SIZE}""",
            )
            rows = cur.fetchall()

    for row in rows:
        grant_id    = row['grant_id']
        kind        = row['capability_kind']
        ref         = row['capability_ref']
        prev_status = row['status']
        stats['checked'] += 1

        verifier = VERIFIERS.get(kind)
        if not verifier:
            stats['skipped_no_verifier'] += 1
            continue

        try:
            is_valid, hint = verifier(ref)
        except Exception as exc:  # noqa: BLE001
            # Verifier errored — increment attempt_count, keep status, don't update verified_at
            stats['errors'] += 1
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE inventory_grants
                              SET verify_attempt_count = verify_attempt_count + 1,
                                  last_error           = %s
                            WHERE grant_id = %s""",
                        (str(exc), grant_id),
                    )
                conn.commit()
            continue

        if is_valid:
            stats['verified_ok'] += 1
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE inventory_grants
                              SET last_verified_at      = now(),
                                  verify_attempt_count  = 0,
                                  last_error            = NULL,
                                  status                = 'active'
                            WHERE grant_id = %s""",
                        (grant_id,),
                    )
                conn.commit()
        else:
            stats['demoted'] += 1
            new_status = hint
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE inventory_grants
                              SET verify_attempt_count = verify_attempt_count + 1,
                                  last_error           = %s,
                                  status               = %s
                            WHERE grant_id = %s""",
                        (f'verifier returned invalid (hint={new_status})', new_status, grant_id),
                    )
                conn.commit()
            if new_status != prev_status:
                _emit_audit_sync(
                    'capability_demoted', f'inventory:{grant_id}',
                    payload={
                        'grant_id': grant_id,
                        'kind': kind,
                        'prev_status': prev_status,
                        'new_status': new_status,
                    },
                )

    # ── GC pass: hard-delete orphaned/revoked rows older than 30 days ──────────
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""DELETE FROM inventory_grants
                     WHERE status IN ('orphaned','revoked','expired')
                       AND updated_at < now() - interval '{_GC_DAYS} days'""",
            )
            stats['gc_deleted'] = cur.rowcount
        conn.commit()

    return stats


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    # Load env from SOS/.env.supabase if not already set
    if not os.getenv('DATABASE_URL') and not os.getenv('MIRROR_DATABASE_URL'):
        env_file = Path.home() / 'SOS' / '.env.supabase'
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    os.environ.setdefault(k.strip(), v.strip())

    log.info('inventory reconciler starting')
    try:
        stats = reconcile_inventory()
        log.info('reconcile complete: %s', json.dumps(stats))
        print(json.dumps(stats, indent=2))
    except Exception as exc:
        log.exception('reconciler failed: %s', exc)
        sys.exit(1)


if __name__ == '__main__':
    main()
