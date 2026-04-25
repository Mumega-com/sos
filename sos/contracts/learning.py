"""
§16 A.6 — Learning Loop (Sprint 004 A.6).

Closes the feedback cycle between quest outcomes and citizen reputation/vectors.

Each outcome in match_history (where outcome IS NOT NULL, reputation_processed_at IS NULL)
feeds:
  1. Glicko-2 reputation update — emits a reputation event, then triggers a targeted
     recompute_reputation_scores(holder_id) so the Dreamer picks up the change next cycle.
  2. Citizen vector nudge — moves the citizen's 16D alignment vector toward (accepted)
     or away from (rejected) the quest's 16D alignment vector at rate α=0.1.

Outcome → reputation event mapping:
  accepted  → task_completed,  weight=+1.0  (s=1.0, Glicko-2 win)
  rejected  → task_failed,     weight=-1.0  (s=0.0, Glicko-2 loss)
  abandoned → task_abandoned,  weight=-0.5  (s=0.0, mild loss — incomplete)

Citizen vector learning (α=0.1, [0,1] clamp per dimension):
  accepted:  v += α * (quest_v - v)   [move toward quest alignment]
  rejected:  v -= α * (quest_v - v)   [move away from quest alignment]
  abandoned: no update

Cold-start handling:
  - No quest vector: Glicko-2 update still proceeds; no vector nudge.
  - No citizen vector: Glicko-2 update proceeds; citizen_vectors initialized from quest_v * 0.5
    on accepted outcome only (first positive signal seeds the vector).

Public API:
  process_outcomes(batch_size=50) → {'processed', 'skipped', 'errors', 'vector_updates'}

Called by matchmaker.run_tick() at the start of each 30-second tick so each tick
operates on the most current reputation state before assigning new quests.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_VECTOR_ALPHA = 0.1    # learning rate for citizen vector nudge

# outcome → (reputation event_type, weight for Glicko-2)
_OUTCOME_EVENT: dict[str, tuple[str, float]] = {
    'accepted':  ('task_completed', 1.0),
    'rejected':  ('task_failed',   -1.0),
    'abandoned': ('task_abandoned', -0.5),
}

_NDIM = 16  # canonical dimension count (must match DIMENSION_NAMES in quest_vectors.py)


# ── DB helpers ────────────────────────────────────────────────────────────────


def _db_url() -> str:
    url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('MIRROR_DATABASE_URL or DATABASE_URL is not set')
    return url


def _connect():
    return psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)


def _fetch_pending_outcomes(conn, batch_size: int) -> list[dict]:
    """
    Fetch match_history rows with resolved outcomes not yet processed.

    Ordered by outcome_at ASC — oldest unprocessed outcomes first.
    Also joins quest tier and guild_scope for context.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT
                   mh.id            AS match_id,
                   mh.quest_id,
                   mh.candidate_id,
                   mh.composite_score,
                   mh.outcome,
                   mh.outcome_at,
                   q.tier,
                   q.guild_scope
               FROM match_history mh
               JOIN quests q ON q.id = mh.quest_id
              WHERE mh.outcome IS NOT NULL
                AND mh.reputation_processed_at IS NULL
              ORDER BY mh.outcome_at ASC
              LIMIT %s""",
            (batch_size,),
        )
        return [dict(r) for r in cur.fetchall()]


def _fetch_quest_vector(conn, quest_id: str) -> list[float] | None:
    """Return stored 16D vector for quest, or None if not yet extracted."""
    with conn.cursor() as cur:
        cur.execute(
            'SELECT vector FROM quest_vectors WHERE quest_id = %s',
            (quest_id,),
        )
        row = cur.fetchone()
    return list(row['vector']) if row else None


def _fetch_citizen_vector(conn, holder_id: str) -> list[float] | None:
    """Return stored 16D vector for citizen, or None if not yet registered."""
    with conn.cursor() as cur:
        cur.execute(
            'SELECT vector FROM citizen_vectors WHERE holder_id = %s',
            (holder_id,),
        )
        row = cur.fetchone()
    return list(row['vector']) if row else None


def _upsert_citizen_vector(conn, holder_id: str, vector: list[float]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO citizen_vectors (holder_id, vector, updated_at)
               VALUES (%s, %s, now())
               ON CONFLICT (holder_id) DO UPDATE SET
                   vector     = EXCLUDED.vector,
                   updated_at = now()""",
            (holder_id, vector),
        )
    conn.commit()


def _emit_audit_sync(
    conn,
    stream_id: str,
    actor_id: str,
    actor_type: str,
    action: str,
    resource: str,
    payload: dict | None,
) -> str:
    """
    Sync (psycopg2) audit chain emission.

    Produces the same hash chain as the async emit_audit() in audit_chain.py.
    Safe to call from sync matchmaker context — no asyncio required.

    Inserts into audit_events; the SECURITY DEFINER audit_to_reputation() trigger
    fires on that INSERT and writes the corresponding row to reputation_events.
    This is the constitutional path: audit chain is the ONLY reputation source.

    Returns the audit_events UUID.
    """
    import hashlib
    import json
    import uuid as _uuid
    from datetime import datetime, timezone

    event_id = str(_uuid.uuid4())
    ts_now = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        # Allocate seq atomically via DB function (advisory xact lock inside)
        cur.execute('SELECT audit_next_seq(%s)', (stream_id,))
        seq: int = cur.fetchone()['audit_next_seq']

        # Fetch prev_hash for hash chain
        cur.execute(
            'SELECT hash FROM audit_events WHERE stream_id = %s AND seq = %s',
            (stream_id, seq - 1),
        )
        prev_row = cur.fetchone()
        prev_hash: bytes | None = bytes(prev_row['hash']) if prev_row else None

        # Enforce payload size cap (8 KB) — matches audit_chain.py _enforce_payload_size
        payload_redacted = False
        if payload is not None:
            raw = json.dumps(
                payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
            ).encode('utf-8')
            if len(raw) > 8192:
                full_hash = hashlib.sha256(raw).hexdigest()
                payload = {
                    'summary': f'payload redacted — original {len(raw)} bytes',
                    'hash_of_full': full_hash,
                }
                payload_redacted = True

        # Canonical JSON — sorted keys, no whitespace, UTF-8 (must match audit_chain.py)
        canonical_obj = {
            'action': action,
            'actor_id': actor_id,
            'actor_type': actor_type,
            'id': event_id,
            'payload': payload,
            'payload_redacted': payload_redacted,
            'prev_hash': prev_hash.hex() if prev_hash else None,
            'resource': resource,
            'seq': seq,
            'stream_id': stream_id,
            'ts': ts_now.isoformat(),
        }
        canonical_bytes = json.dumps(
            canonical_obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
        ).encode('utf-8')

        # SHA-256(prev_hash_bytes || canonical_bytes)
        h = hashlib.sha256()
        if prev_hash:
            h.update(prev_hash)
        h.update(canonical_bytes)
        event_hash: bytes = h.digest()

        # signature=NULL — Sprint 005 P0-1b: distribute AUDIT_SIGNING_KEY to matchmaker
        cur.execute(
            """INSERT INTO audit_events (
                   id, stream_id, seq, ts,
                   actor_id, actor_type, action, resource,
                   payload, payload_redacted,
                   prev_hash, hash, signature
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                event_id, stream_id, seq, ts_now,
                actor_id, actor_type, action, resource,
                json.dumps(payload) if payload is not None else None,
                payload_redacted,
                prev_hash, event_hash, None,
            ),
        )
    conn.commit()
    return event_id


def _emit_via_audit_chain(
    conn,
    holder_id: str,
    action: str,
    evidence_ref: str,
    guild_scope: str | None,
) -> None:
    """
    Emit a match outcome through the audit chain (F-02 constitutional fix).

    Routes through audit_events (stream_id='kernel') so the SECURITY DEFINER
    audit_to_reputation() trigger creates the reputation_events row.
    Replaces the former direct INSERT into reputation_events.

    Resource encoding for guild inference by trigger:
      guild_scope set  → 'guild:{guild_scope}:match:{evidence_ref}'
      guild_scope None → 'match:{evidence_ref}'
    """
    if guild_scope:
        resource = f'guild:{guild_scope}:match:{evidence_ref}'
    else:
        resource = f'match:{evidence_ref}'

    _emit_audit_sync(
        conn,
        stream_id='kernel',
        actor_id=holder_id,
        actor_type='agent',
        action=action,
        resource=resource,
        payload={'evidence_ref': evidence_ref, 'guild_scope': guild_scope},
    )


def _mark_processed(conn, match_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            'UPDATE match_history SET reputation_processed_at = now() WHERE id = %s',
            (match_id,),
        )
    conn.commit()


# ── Vector nudge ──────────────────────────────────────────────────────────────


def _nudge_vector(
    citizen_vec: list[float],
    quest_vec: list[float],
    outcome: str,
    alpha: float = _VECTOR_ALPHA,
) -> list[float] | None:
    """
    Move citizen_vec toward (accepted) or away from (rejected) quest_vec.

    Returns the updated vector clamped to [0.0, 1.0] per dimension.
    Returns None for 'abandoned' (no update).
    """
    if outcome == 'abandoned':
        return None

    direction = 1.0 if outcome == 'accepted' else -1.0
    updated = [
        max(0.0, min(1.0, c + direction * alpha * (q - c)))
        for c, q in zip(citizen_vec, quest_vec)
    ]
    return updated


def _seed_vector_from_quest(quest_vec: list[float]) -> list[float]:
    """
    Cold-start: initialize citizen vector at half the quest alignment.

    A new citizen has no history, so we seed them at 0.5× the quest signal
    rather than copying the quest fully — avoids anchoring on a single outcome.
    """
    return [v * 0.5 for v in quest_vec]


# ── Public API ────────────────────────────────────────────────────────────────


def process_outcomes(batch_size: int = 50) -> dict[str, int]:
    """
    Process a batch of resolved match_history outcomes.

    For each pending outcome:
      1. Emit a reputation event (task_completed/task_failed/task_abandoned).
      2. Trigger targeted Glicko-2 recompute for the candidate.
      3. Nudge citizen_vectors (accepted→toward, rejected→away, abandoned→skip).
      4. Mark the match_history row as processed.

    Returns: {'processed', 'skipped', 'errors', 'vector_updates'}
    """
    from sos.contracts.reputation import recompute

    stats: dict[str, int] = {
        'processed': 0,
        'skipped': 0,
        'errors': 0,
        'vector_updates': 0,
    }

    with _connect() as conn:
        pending = _fetch_pending_outcomes(conn, batch_size)

    if not pending:
        log.debug('learning_loop: no pending outcomes')
        return stats

    log.info('learning_loop: processing %d pending outcomes', len(pending))

    for row in pending:
        match_id    = row['match_id']
        quest_id    = row['quest_id']
        candidate_id = row['candidate_id']
        outcome     = row['outcome']
        guild_scope = row['guild_scope']

        event_type, _weight = _OUTCOME_EVENT.get(outcome, ('task_completed', 1.0))
        evidence_ref = f'match:{match_id}'

        try:
            with _connect() as conn:
                # Step 1: emit via audit chain (→ trigger → reputation_events)
                _emit_via_audit_chain(
                    conn, candidate_id, event_type, evidence_ref, guild_scope,
                )

                # Step 2: targeted Glicko-2 recompute for this candidate
                # (sync call; Dreamer will pick up on its next cycle anyway, but
                #  we update immediately so the next matchmaker tick sees fresh state)
                recompute(candidate_id)

                # Step 3: citizen vector nudge
                quest_vec   = _fetch_quest_vector(conn, quest_id)
                citizen_vec = _fetch_citizen_vector(conn, candidate_id)

                if quest_vec is not None:
                    if citizen_vec is not None:
                        updated_vec = _nudge_vector(citizen_vec, quest_vec, outcome)
                        if updated_vec is not None:
                            _upsert_citizen_vector(conn, candidate_id, updated_vec)
                            stats['vector_updates'] += 1
                    elif outcome == 'accepted':
                        # Cold-start: seed citizen vector from quest
                        seeded = _seed_vector_from_quest(quest_vec)
                        _upsert_citizen_vector(conn, candidate_id, seeded)
                        stats['vector_updates'] += 1
                        log.info(
                            'learning_loop: seeded citizen_vector for %s from quest %s',
                            candidate_id, quest_id,
                        )

                # Step 4: mark processed
                _mark_processed(conn, match_id)

            stats['processed'] += 1
            log.debug(
                'learning_loop: processed match=%d candidate=%s outcome=%s',
                match_id, candidate_id, outcome,
            )

        except Exception as exc:
            log.warning(
                'learning_loop: error processing match=%d: %s',
                match_id, exc,
            )
            stats['errors'] += 1

    log.info(
        'learning_loop done: processed=%d skipped=%d errors=%d vector_updates=%d',
        stats['processed'], stats['skipped'], stats['errors'], stats['vector_updates'],
    )
    return stats
