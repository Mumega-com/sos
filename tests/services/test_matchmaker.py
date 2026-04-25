"""
§16 Matchmaker service tests — Sprint 004 A.5.

Unit tests: matrix building logic, Hungarian assignment, priority mapping.
Integration tests (requires DB): run_tick with mocked Squad Service.

Run all:     DATABASE_URL=... pytest tests/services/test_matchmaker.py -v
Run unit:    pytest tests/services/test_matchmaker.py -v -m "not db"
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock, patch

import pytest

import sos.services.matchmaker as _mm_module
from sos.services.matchmaker import (
    _hungarian_assign,
    _tier_to_priority,
    _try_acquire_leader_lock,
    _db_url,
    run_tick,
)


# ── helpers ────────────────────────────────────────────────────────────────────


def _has_db() -> bool:
    return bool(os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL'))


def _uid() -> str:
    return f'test-mm-{uuid.uuid4().hex[:8]}'


db = pytest.mark.skipif(not _has_db(), reason='Mirror DB not configured')


def _quest(qid: str, tier: str = 'T1') -> dict:
    return {'id': qid, 'title': f'Quest {qid}', 'tier': tier,
            'guild_scope': None, 'required_capabilities': []}


# ── Unit: _tier_to_priority ───────────────────────────────────────────────────


class TestTierToPriority:
    def test_all_tiers_mapped(self) -> None:
        assert _tier_to_priority('T1') == 'low'
        assert _tier_to_priority('T2') == 'medium'
        assert _tier_to_priority('T3') == 'high'
        assert _tier_to_priority('T4') == 'critical'

    def test_unknown_tier_defaults_medium(self) -> None:
        assert _tier_to_priority('T5') == 'medium'
        assert _tier_to_priority('') == 'medium'


# ── Unit: _hungarian_assign ───────────────────────────────────────────────────


class TestHungarianAssign:
    def test_empty_quests_returns_empty(self) -> None:
        result = _hungarian_assign([], ['cand-1'], [[]])
        assert result == []

    def test_empty_candidates_returns_empty(self) -> None:
        result = _hungarian_assign([_quest('q1')], [], [[]])
        assert result == []

    def test_single_quest_single_candidate(self) -> None:
        quests = [_quest('q1')]
        candidates = ['cand-1']
        matrix = [[0.75]]
        result = _hungarian_assign(quests, candidates, matrix)
        assert len(result) == 1
        quest, cid, score = result[0]
        assert quest['id'] == 'q1'
        assert cid == 'cand-1'
        assert abs(score - 0.75) < 1e-9

    def test_zero_score_pair_skipped(self) -> None:
        """Pairs where composite_score == 0.0 (no eligible candidate) are dropped."""
        quests = [_quest('q1')]
        candidates = ['cand-1']
        matrix = [[0.0]]
        result = _hungarian_assign(quests, candidates, matrix)
        assert result == []

    def test_optimal_assignment_maximises_total(self) -> None:
        """
        2 quests × 2 candidates:
        matrix = [[0.9, 0.2],   # q1 prefers cand-1
                  [0.3, 0.8]]   # q2 prefers cand-2
        Optimal: q1→cand-1 (0.9) + q2→cand-2 (0.8) = 1.7
        Suboptimal: q1→cand-2 (0.2) + q2→cand-1 (0.3) = 0.5
        """
        quests = [_quest('q1'), _quest('q2')]
        candidates = ['cand-1', 'cand-2']
        matrix = [[0.9, 0.2], [0.3, 0.8]]
        result = _hungarian_assign(quests, candidates, matrix)

        assigned = {r[0]['id']: r[1] for r in result}
        assert assigned.get('q1') == 'cand-1'
        assert assigned.get('q2') == 'cand-2'

    def test_more_candidates_than_quests(self) -> None:
        """3 candidates, 2 quests → 2 assignments, 1 candidate unassigned."""
        quests = [_quest('q1'), _quest('q2')]
        candidates = ['cand-1', 'cand-2', 'cand-3']
        matrix = [
            [0.8, 0.3, 0.5],   # q1
            [0.2, 0.9, 0.4],   # q2
        ]
        result = _hungarian_assign(quests, candidates, matrix)
        assert len(result) == 2
        # q1 → cand-1 (0.8), q2 → cand-2 (0.9)
        assigned = {r[0]['id']: (r[1], r[2]) for r in result}
        assert assigned['q1'][0] == 'cand-1'
        assert assigned['q2'][0] == 'cand-2'

    def test_more_quests_than_candidates(self) -> None:
        """3 quests, 2 candidates → 2 assignments (one quest unmatched)."""
        quests = [_quest('q1'), _quest('q2'), _quest('q3')]
        candidates = ['cand-1', 'cand-2']
        matrix = [
            [0.9, 0.1],   # q1 strongly prefers cand-1
            [0.1, 0.9],   # q2 strongly prefers cand-2
            [0.5, 0.5],   # q3 indifferent
        ]
        result = _hungarian_assign(quests, candidates, matrix)
        assert len(result) == 2

    def test_returns_scores(self) -> None:
        """Returned composite_score matches matrix value."""
        quests = [_quest('q1')]
        candidates = ['cand-1']
        matrix = [[0.654]]
        result = _hungarian_assign(quests, candidates, matrix)
        assert len(result) == 1
        assert abs(result[0][2] - 0.654) < 1e-9


# ── Integration: run_tick with empty DB state ─────────────────────────────────


@db
class TestRunTickDB:
    def test_no_open_quests_returns_zero_assignments(self) -> None:
        """When there are no open quests, tick completes cleanly with 0 assignments."""
        # Patch DB fetch so we control the quest pool regardless of DB state
        with patch('sos.services.matchmaker._fetch_open_quests', return_value=[]):
            with patch('sos.services.matchmaker.DRY_RUN', True):
                stats = run_tick()

        assert stats['assignments'] == 0
        assert isinstance(stats['quests'], int)
        assert isinstance(stats['candidates'], int)

    def test_stats_keys_present(self) -> None:
        with patch('sos.services.matchmaker.DRY_RUN', True):
            stats = run_tick()

        expected = {'quests', 'candidates', 'assignments', 'dispatched', 'skipped',
                    'outcomes_processed', 'vector_updates', 'leader_acquired'}
        assert set(stats.keys()) == expected

    def test_dry_run_skips_squad_dispatch(self) -> None:
        """DRY_RUN=1 never calls _dispatch_to_squad."""
        # DRY_RUN is a module-level constant; patch the name directly, not env var
        with patch('sos.services.matchmaker._dispatch_to_squad') as mock_dispatch:
            with patch('sos.services.matchmaker.DRY_RUN', True):
                run_tick()

        # dry_run path should not invoke Squad dispatch even if assignments were made
        mock_dispatch.assert_not_called()


# ── TC-G50: leader-lock (Sprint 006 A.1) ─────────────────────────────────────

# Use a test-specific advisory lock slot to avoid competing with the live matchmaker
# timer service (fires every 30s on production slot (1001, 0)).
_TEST_LOCK_CLASSID = 9999
_TEST_LOCK_OBJID   = 9999


@db
class TestLeaderLock:
    """TC-G50a–e: PG session-level advisory leader-lock for dual-instance safety.

    All tests redirect the advisory lock to slot (9999, 9999) to avoid competing
    with the live matchmaker.timer service (fires every 30s on production slot (1001, 0)).
    The advisory lock semantics tested are identical regardless of slot.
    """

    def setup_method(self, _method) -> None:  # noqa: ANN001
        """Redirect to test slot before each test."""
        self._orig_classid = _mm_module._LEADER_CLASSID
        self._orig_objid   = _mm_module._LEADER_OBJID
        _mm_module._LEADER_CLASSID = _TEST_LOCK_CLASSID
        _mm_module._LEADER_OBJID   = _TEST_LOCK_OBJID

    def teardown_method(self, _method) -> None:  # noqa: ANN001
        """Restore production slot after each test."""
        _mm_module._LEADER_CLASSID = self._orig_classid
        _mm_module._LEADER_OBJID   = self._orig_objid

    def _conn(self):
        import psycopg2
        import psycopg2.extras
        return psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)

    def test_g50a_acquire_returns_true_when_free(self) -> None:
        """TC-G50a: _try_acquire_leader_lock returns True when no other session holds it."""
        conn = self._conn()
        try:
            assert _try_acquire_leader_lock(conn) is True
        finally:
            conn.close()

    def test_g50b_second_session_cannot_acquire(self) -> None:
        """TC-G50b: second connection cannot acquire while first holds the lock."""
        conn1 = self._conn()
        conn2 = self._conn()
        try:
            assert _try_acquire_leader_lock(conn1) is True
            assert _try_acquire_leader_lock(conn2) is False
        finally:
            conn1.close()
            conn2.close()

    def test_g50c_run_tick_observer_exits_early(self) -> None:
        """TC-G50c: run_tick returns leader_acquired=False and zero action stats when lock is taken."""
        # Hold the leader lock in an external connection before run_tick starts
        holder = self._conn()
        try:
            assert _try_acquire_leader_lock(holder) is True
            stats = run_tick()
            assert stats['leader_acquired'] is False
            assert stats['quests'] == 0
            assert stats['candidates'] == 0
            assert stats['assignments'] == 0
            assert stats['dispatched'] == 0
        finally:
            holder.close()

    def test_g50d_lock_released_after_tick(self) -> None:
        """TC-G50d: after run_tick completes, the lock is released and another session can acquire."""
        with patch('sos.services.matchmaker.DRY_RUN', True):
            stats = run_tick()
        assert stats['leader_acquired'] is True

        # After run_tick returns, lock_conn has been closed — another session can now acquire
        conn = self._conn()
        try:
            assert _try_acquire_leader_lock(conn) is True
        finally:
            conn.close()

    def test_g50e_leader_tick_sets_leader_acquired_true(self) -> None:
        """TC-G50e: run_tick returns leader_acquired=True when it wins the lock."""
        with patch('sos.services.matchmaker.DRY_RUN', True):
            stats = run_tick()

        assert stats['leader_acquired'] is True
        assert 'quests' in stats
        assert 'candidates' in stats
