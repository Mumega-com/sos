"""
§16 Matchmaker — Hungarian Assignment Tick (Sprint 004 A.5 / Sprint 006 A.1).

Runs as a systemd one-shot service on a 30-second timer.
Each tick:
  1. Acquires a PG session-level advisory leader lock (Sprint 006 A.1 / G50).
     If another instance holds the lock, this process exits immediately as an
     observer (zero writes).  The lock is released automatically when the
     process exits — including kill -9 (PG drops the session lock on disconnect).
  2. Fetches all open quests from Mirror DB.
  3. Builds candidate pool from active citizens (recent reputation_state or citizen_vectors).
  4. Runs rank_candidates() per quest (five-stage pipeline from A.3).
  5. Builds composite_score matrix (quests × candidates).
  6. Applies scipy.optimize.linear_sum_assignment on negated matrix (max→min transform).
  7. Records each assignment in match_history via matchmaking.record_assignment().
  8. Emits assignment tasks to Squad Service (:8060) via POST /tasks + /claim.

Candidate pool strategy (v1):
  Active citizens = union of:
    - holders in reputation_state updated within last 7 days
    - holders in citizen_vectors (any entry = ever registered)
  Deduped. Pool capped at MAX_POOL_SIZE to keep matrix tractable.

Cost discipline:
  rank_candidates() is synchronous and DB-only (no LLM calls in pipeline).
  quest_vectors.extract() is NOT called here — A.4 extraction is a separate job.
  If a quest has no vector yet, Stage 3 returns neutral 0.5 (cold-start path).

Env vars:
  SQUAD_SERVICE_URL        — default http://localhost:8060
  SOS_SYSTEM_TOKEN         — auth token for Squad Service API calls
  MIRROR_DATABASE_URL or DATABASE_URL — Mirror DB
  MAX_POOL_SIZE            — candidate pool cap (default 200)
  MATCHMAKER_LIVE          — set to '1' to enable Squad Service dispatch; default is dry-run
  MATCHMAKER_INSTANCE_ID   — optional label for this instance in log lines (default 'default')
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import sys
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import requests

from sos.contracts.matchmaking import (
    MatchResult,
    rank_candidates,
    record_assignment,
)

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

SQUAD_SERVICE_URL    = os.environ.get('SQUAD_SERVICE_URL', 'http://localhost:8060')
SOS_SYSTEM_TOKEN     = os.environ.get('SOS_SYSTEM_TOKEN', 'sk-sos-system')
MAX_POOL_SIZE        = int(os.environ.get('MAX_POOL_SIZE', '200'))
# F-05: quest-flood DoS protection — cap quests per tick and bound matrix build time
MAX_QUESTS_PER_TICK  = int(os.environ.get('MAX_QUESTS_PER_TICK', '100'))
MATRIX_TIMEOUT_S     = int(os.environ.get('MATCHMAKER_MATRIX_TIMEOUT', '25'))
_LIVE                = os.environ.get('MATCHMAKER_LIVE', '') == '1'
DRY_RUN              = not _LIVE
# Sprint 006 A.1 (G50): dual-instance leader election via PG advisory lock
INSTANCE_ID          = os.environ.get('MATCHMAKER_INSTANCE_ID', 'default')
_LEADER_CLASSID      = 1001   # G23 matchmaker advisory lock namespace
_LEADER_OBJID        = 0      # leader sentinel (objid=0 reserved for leader election)

_HEADERS = {
    'Authorization': f'Bearer {SOS_SYSTEM_TOKEN}',
    'Content-Type': 'application/json',
}


# ── DB helpers ────────────────────────────────────────────────────────────────


def _db_url() -> str:
    url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('MIRROR_DATABASE_URL or DATABASE_URL is not set')
    return url


def _connect():
    return psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)


def _try_acquire_leader_lock(conn) -> bool:
    """Try to acquire the matchmaker leader advisory lock (session-level, non-blocking).

    Uses pg_try_advisory_lock(classid, objid) — 2-arg form to avoid namespace
    collision with other advisory locks elsewhere in the system.

    classid=1001 : matchmaker namespace (established in G23).
    objid=0      : leader sentinel — one instance may hold this at a time.

    Session-level semantics: the lock is held for the lifetime of *conn*, not
    just a transaction.  It is automatically released when the connection is
    closed or dropped — including kill -9 (PG drops the backend session and
    releases all its session-level advisory locks).

    Returns True if this instance won the lock, False if another holds it.
    """
    with conn.cursor() as cur:
        cur.execute(
            'SELECT pg_try_advisory_lock(%s, %s)',
            (_LEADER_CLASSID, _LEADER_OBJID),
        )
        row = cur.fetchone()
        # RealDictCursor returns a dict-like row; key is the function name
        return bool(row['pg_try_advisory_lock'])


def _fetch_open_quests(conn) -> list[dict]:
    """
    Return open quests from Mirror DB, capped at MAX_QUESTS_PER_TICK.

    F-05: LIMIT prevents a quest-flood DoS where a bad actor creates thousands
    of open quests causing _build_matrix() to run O(N×M) rank_candidates() calls
    per tick. Default cap: 100 quests. Env: MAX_QUESTS_PER_TICK.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, title, tier, guild_scope, required_capabilities
                 FROM quests
                WHERE status = 'open'
                ORDER BY created_at ASC
                LIMIT %s""",
            (MAX_QUESTS_PER_TICK,),
        )
        return [dict(r) for r in cur.fetchall()]


def _fetch_candidate_pool(conn) -> list[str]:
    """
    Build active citizen pool from reputation_state + citizen_vectors.
    Capped at MAX_POOL_SIZE. Returns deduplicated list of holder_ids.
    """
    with conn.cursor() as cur:
        # Union: recent reputation holders + anyone with a citizen vector
        cur.execute(
            """SELECT DISTINCT holder_id FROM (
                SELECT holder_id FROM reputation_state
                 WHERE last_updated >= now() - interval '7 days'
                 UNION
                SELECT holder_id FROM citizen_vectors
               ) AS pool
               LIMIT %s""",
            (MAX_POOL_SIZE,),
        )
        return [r['holder_id'] for r in cur.fetchall()]


# ── Hungarian assignment ──────────────────────────────────────────────────────


def _build_matrix(
    quests: list[dict],
    candidates: list[str],
) -> list[list[float]]:
    """
    Run rank_candidates() for each quest and build composite_score matrix.

    matrix[i][j] = composite_score of candidate j for quest i.
    Ineligible or vetoed candidates get 0.0 (map correctly to high cost after negation).
    """
    matrix: list[list[float]] = []

    for quest in quests:
        result: MatchResult = rank_candidates(quest['id'], candidates)

        # Build a lookup from candidate_id → composite_score
        score_map: dict[str, float] = {}
        for cs in result.ranked:
            score_map[cs.candidate_id] = cs.composite_score
        # Ineligible stay 0.0

        row = [score_map.get(cid, 0.0) for cid in candidates]
        matrix.append(row)

        log.debug(
            'quest=%s tier=%s eligible=%d ineligible=%d explore=%s',
            quest['id'], quest['tier'],
            len(result.ranked), len(result.ineligible),
            result.explore_candidate_id,
        )

    return matrix


def _hungarian_assign(
    quests: list[dict],
    candidates: list[str],
    matrix: list[list[float]],
) -> list[tuple[dict, str, float]]:
    """
    Apply scipy.optimize.linear_sum_assignment on negated matrix.

    Returns list of (quest, candidate_id, composite_score) tuples for assigned pairs.
    Pairs where composite_score == 0.0 (all ineligible) are dropped — no assignment.
    """
    import numpy as np
    from scipy.optimize import linear_sum_assignment  # type: ignore[import]

    if not quests or not candidates:
        return []

    cost = np.array(matrix, dtype=float)
    row_idx, col_idx = linear_sum_assignment(-cost)   # negate → minimise → maximise scores

    assignments = []
    for r, c in zip(row_idx, col_idx):
        score = matrix[r][c]
        if score <= 0.0:
            # No eligible candidate for this quest — skip
            log.info('quest=%s: no eligible candidate in pool, skipping', quests[r]['id'])
            continue
        assignments.append((quests[r], candidates[c], score))

    return assignments


# ── Squad Service dispatch ────────────────────────────────────────────────────


def _dispatch_to_squad(quest: dict, candidate_id: str, composite_score: float) -> bool:
    """
    Create a task in Squad Service for the quest assignment, then claim it.

    POST /tasks  → creates task with quest metadata
    POST /tasks/{id}/claim  → atomically claims for candidate

    Returns True on success, False on any HTTP error (fail-open; log and continue).
    """
    import uuid as _uuid
    task_id = str(_uuid.uuid4())
    # Squad Service requires squad_id; use 'dev' as the default dispatch target.
    # Guild-scoped routing to specific squads is a Sprint 005 feature.
    squad_id = 'dev'
    task_payload = {
        'id': task_id,
        'squad_id': squad_id,
        'title': quest.get('title', quest['id']),
        'labels': [f'quest:{quest["id"]}', f'tier:{quest["tier"]}'],
        'priority': _tier_to_priority(quest['tier']),
        'assignee': candidate_id,
        'inputs': {
            'quest_id': quest['id'],
            'tier': quest['tier'],
            'composite_score': composite_score,
            'source': 'matchmaker',
        },
    }

    try:
        r = requests.post(
            f'{SQUAD_SERVICE_URL}/tasks',
            json=task_payload,
            headers=_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        # task_id already set above; response may confirm it
        resp_id = r.json().get('id') or r.json().get('task_id')
        if resp_id:
            task_id = resp_id
    except Exception as exc:
        log.warning('matchmaker: failed to create Squad task for quest=%s: %s', quest['id'], exc)
        return False

    # Claim it immediately for the matched candidate
    try:
        r = requests.post(
            f'{SQUAD_SERVICE_URL}/tasks/{task_id}/claim',
            json={'assignee': candidate_id, 'attempt': 0},
            headers=_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
    except Exception as exc:
        log.warning('matchmaker: failed to claim task=%s for candidate=%s: %s', task_id, candidate_id, exc)
        return False

    return True


def _tier_to_priority(tier: str) -> str:
    # Squad Service priority enum: low / medium / high / critical
    return {'T1': 'low', 'T2': 'medium', 'T3': 'high', 'T4': 'critical'}.get(tier, 'medium')


# ── Main tick ─────────────────────────────────────────────────────────────────


def run_tick() -> dict:
    """
    Execute one matchmaker assignment tick.

    Sprint 006 A.1 (G50) — leader election:
      Acquire a session-level PG advisory lock before any reads or writes.
      If another instance already holds the lock, return immediately as an
      observer (leader_acquired=False, all counts zero).  The lock is held
      for the full duration of the tick and released in the finally block
      (connection close), including on kill -9 (PG auto-releases on disconnect).

    Phase 0: Process any pending match_history outcomes via the A.6 learning loop
             so this tick operates on the freshest reputation state.
    Phase 1: Fetch open quests + active candidate pool.
    Phase 2: Build composite_score matrix + Hungarian assignment.
    Phase 3: Record assignments in match_history + dispatch to Squad Service.

    Returns summary: {quests, candidates, assignments, dispatched, skipped,
                      outcomes_processed, vector_updates, leader_acquired}.
    """
    from sos.contracts.learning import process_outcomes

    stats: dict = {
        'quests': 0, 'candidates': 0, 'assignments': 0,
        'dispatched': 0, 'skipped': 0,
        'outcomes_processed': 0, 'vector_updates': 0,
        'leader_acquired': False,
    }

    # ── Leader election (G50) ────────────────────────────────────────────────
    # Separate connection so the session lock outlives individual transactions.
    lock_conn = _connect()
    try:
        if not _try_acquire_leader_lock(lock_conn):
            log.info(
                'matchmaker[%s]: observer — leader lock held by another instance, exiting',
                INSTANCE_ID,
            )
            return stats
        stats['leader_acquired'] = True
        log.debug('matchmaker[%s]: leader lock acquired', INSTANCE_ID)

        # Phase 0: learning loop — process pending outcomes before assigning new quests
        try:
            lo_stats = process_outcomes()
            stats['outcomes_processed'] = lo_stats['processed']
            stats['vector_updates']     = lo_stats['vector_updates']
        except Exception as exc:
            log.warning('matchmaker: learning loop error (non-fatal): %s', exc)

        with _connect() as conn:
            quests = _fetch_open_quests(conn)
            candidates = _fetch_candidate_pool(conn)

        stats['quests'] = len(quests)
        stats['candidates'] = len(candidates)

        if not quests:
            log.info('matchmaker: no open quests — tick complete')
            return stats
        if not candidates:
            log.info('matchmaker: empty candidate pool — tick complete')
            return stats

        log.info('matchmaker: %d quests × %d candidates', len(quests), len(candidates))

        # Build score matrix with deadline (F-05: prevents quest-flood DoS).
        # rank_candidates() opens its own DB connections — thread-safe to run in executor.
        # Do NOT use `with executor:` — its __exit__ calls shutdown(wait=True), which
        # blocks until the thread finishes and defeats the purpose of the timeout.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_build_matrix, quests, candidates)
        try:
            matrix = future.result(timeout=MATRIX_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            log.warning(
                'matchmaker: matrix build exceeded %ds deadline — skipping tick '
                '(quests=%d candidates=%d)',
                MATRIX_TIMEOUT_S, len(quests), len(candidates),
            )
            stats['skipped'] = len(quests)
            executor.shutdown(wait=False)
            return stats
        executor.shutdown(wait=False)

        assignments = _hungarian_assign(quests, candidates, matrix)
        stats['assignments'] = len(assignments)

        for quest, candidate_id, composite_score in assignments:
            quest_id = quest['id']

            # Record in match_history
            match_id = record_assignment(quest_id, candidate_id, composite_score)

            if DRY_RUN:
                log.info(
                    'DRY_RUN: quest=%s → candidate=%s score=%.4f match_id=%d',
                    quest_id, candidate_id, composite_score, match_id,
                )
                stats['dispatched'] += 1
                continue

            # Dispatch to Squad Service
            ok = _dispatch_to_squad(quest, candidate_id, composite_score)
            if ok:
                stats['dispatched'] += 1
                log.info(
                    'matchmaker: assigned quest=%s → %s (score=%.4f)',
                    quest_id, candidate_id, composite_score,
                )
            else:
                stats['skipped'] += 1

        log.info(
            'matchmaker tick done: quests=%d candidates=%d assigned=%d dispatched=%d skipped=%d',
            stats['quests'], stats['candidates'],
            stats['assignments'], stats['dispatched'], stats['skipped'],
        )
        return stats

    finally:
        # Close the lock connection — releases the session-level advisory lock.
        # Guard against double-close: set to None after closing.
        if lock_conn:
            lock_conn.close()
            lock_conn = None


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    if DRY_RUN:
        log.info('MATCHMAKER_LIVE not set — Squad Service calls suppressed (dry-run)')

    try:
        stats = run_tick()
        log.info('tick stats: %s', stats)
        sys.exit(0)
    except Exception as exc:
        log.exception('matchmaker tick failed: %s', exc)
        sys.exit(1)


if __name__ == '__main__':
    main()
