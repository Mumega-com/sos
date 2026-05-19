"""
§16 A.6 Learning Loop tests — Sprint 004 A.6.

Unit tests: vector nudge, cold-start seed, outcome-event mapping.
Integration tests (requires DB): process_outcomes end-to-end with real DB rows.

Run all:   DATABASE_URL=... pytest tests/contracts/test_learning.py -v
Run unit:  pytest tests/contracts/test_learning.py -v -m "not db"
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock, call, patch

import pytest

from sos.contracts.learning import (
    _NDIM,
    _OUTCOME_EVENT,
    _VECTOR_ALPHA,
    _nudge_vector,
    _seed_vector_from_quest,
    process_outcomes,
)


# ── helpers ────────────────────────────────────────────────────────────────────


def _has_db() -> bool:
    return bool(os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL'))


def _uid() -> str:
    return f'test-learn-{uuid.uuid4().hex[:8]}'


db = pytest.mark.skipif(not _has_db(), reason='Mirror DB not configured')

_UNIFORM = [0.5] * _NDIM
_UNIT    = [1.0] * _NDIM
_ZERO    = [0.0] * _NDIM


# ── Unit: outcome→event mapping ───────────────────────────────────────────────


class TestOutcomeEventMap:
    def test_accepted_maps_to_task_completed(self) -> None:
        event_type, weight = _OUTCOME_EVENT['accepted']
        assert event_type == 'task_completed'
        assert weight > 0  # Glicko-2 win

    def test_rejected_maps_to_task_failed(self) -> None:
        event_type, weight = _OUTCOME_EVENT['rejected']
        assert event_type == 'task_failed'
        assert weight < 0  # Glicko-2 loss

    def test_abandoned_maps_to_task_abandoned(self) -> None:
        event_type, weight = _OUTCOME_EVENT['abandoned']
        assert event_type == 'task_abandoned'
        assert weight < 0  # also a mild loss

    def test_all_outcomes_covered(self) -> None:
        assert set(_OUTCOME_EVENT.keys()) == {'accepted', 'rejected', 'abandoned'}


# ── Unit: _nudge_vector ───────────────────────────────────────────────────────


class TestNudgeVector:
    def test_accepted_moves_toward_quest(self) -> None:
        citizen = [0.0] * _NDIM
        quest   = [1.0] * _NDIM
        result  = _nudge_vector(citizen, quest, 'accepted')
        assert result is not None
        # Each dim should move from 0.0 toward 1.0 by α * (1.0 - 0.0)
        expected = _VECTOR_ALPHA * 1.0
        for v in result:
            assert abs(v - expected) < 1e-9

    def test_rejected_moves_away_from_quest(self) -> None:
        citizen = [0.5] * _NDIM
        quest   = [0.0] * _NDIM
        result  = _nudge_vector(citizen, quest, 'rejected')
        assert result is not None
        # direction=-1: c + (-1)*α*(q-c) = 0.5 + (-1)*0.1*(0.0-0.5) = 0.5 + 0.05 = 0.55
        expected = 0.5 + (-1.0) * _VECTOR_ALPHA * (0.0 - 0.5)
        for v in result:
            assert abs(v - expected) < 1e-9

    def test_abandoned_returns_none(self) -> None:
        result = _nudge_vector(_UNIFORM, _UNIT, 'abandoned')
        assert result is None

    def test_result_clamped_to_zero_one(self) -> None:
        """Nudge on boundary values never escapes [0, 1]."""
        result_accepted = _nudge_vector(_UNIT, _UNIT, 'accepted')
        result_rejected = _nudge_vector(_ZERO, _ZERO, 'rejected')
        assert result_accepted is not None
        assert result_rejected is not None
        for v in result_accepted:
            assert 0.0 <= v <= 1.0
        for v in result_rejected:
            assert 0.0 <= v <= 1.0

    def test_accepted_at_equal_vectors_no_change(self) -> None:
        """If citizen already matches quest perfectly, accepted nudge = no change."""
        result = _nudge_vector(_UNIT, _UNIT, 'accepted')
        assert result is not None
        assert result == _UNIT

    def test_ndim_preserved(self) -> None:
        result = _nudge_vector(_UNIFORM, _UNIT, 'accepted')
        assert result is not None
        assert len(result) == _NDIM


# ── Unit: _seed_vector_from_quest ─────────────────────────────────────────────


class TestSeedVector:
    def test_seeds_at_half_quest_alignment(self) -> None:
        quest = [0.8] * _NDIM
        seeded = _seed_vector_from_quest(quest)
        assert len(seeded) == _NDIM
        for v in seeded:
            assert abs(v - 0.4) < 1e-9

    def test_zero_quest_seeds_zero(self) -> None:
        seeded = _seed_vector_from_quest(_ZERO)
        assert all(v == 0.0 for v in seeded)

    def test_unit_quest_seeds_half(self) -> None:
        seeded = _seed_vector_from_quest(_UNIT)
        assert all(abs(v - 0.5) < 1e-9 for v in seeded)

    def test_result_in_zero_one(self) -> None:
        quest = [0.9] * _NDIM
        seeded = _seed_vector_from_quest(quest)
        assert all(0.0 <= v <= 1.0 for v in seeded)


# ── Integration: process_outcomes ─────────────────────────────────────────────


@db
class TestProcessOutcomesDB:
    def test_no_pending_returns_zero(self) -> None:
        """When no pending outcomes exist, returns zeros without error."""
        # Patch DB fetch to return empty list
        with patch('sos.contracts.learning._fetch_pending_outcomes', return_value=[]):
            stats = process_outcomes()

        assert stats['processed'] == 0
        assert stats['errors'] == 0
        assert stats['vector_updates'] == 0

    def test_stats_keys_present(self) -> None:
        with patch('sos.contracts.learning._fetch_pending_outcomes', return_value=[]):
            stats = process_outcomes()

        expected = {'processed', 'skipped', 'errors', 'vector_updates'}
        assert set(stats.keys()) == expected

    def test_accepted_outcome_emits_task_completed(self) -> None:
        """Accepted outcome → task_completed action emitted via audit chain, recompute called."""
        fake_row = {
            'match_id': 9999,
            'quest_id': 'q-test',
            'candidate_id': 'c-test',
            'composite_score': 0.8,
            'outcome': 'accepted',
            'outcome_at': None,
            'tier': 'T2',
            'guild_scope': None,
        }

        with patch('sos.contracts.learning._fetch_pending_outcomes', return_value=[fake_row]):
            with patch('sos.contracts.learning._emit_via_audit_chain') as mock_emit:
                with patch('sos.contracts.reputation.recompute') as mock_recompute:
                    with patch('sos.contracts.learning._fetch_quest_vector', return_value=None):
                        with patch('sos.contracts.learning._fetch_citizen_vector', return_value=None):
                            with patch('sos.contracts.learning._mark_processed'):
                                stats = process_outcomes()

        assert stats['processed'] == 1
        # _emit_via_audit_chain(conn, holder_id, action, evidence_ref, guild_scope)
        assert mock_emit.called
        call_args = mock_emit.call_args
        assert call_args[0][2] == 'task_completed'   # action (index 2, after conn+holder_id)
        assert call_args[0][3] == 'match:9999'        # evidence_ref (index 3)
        mock_recompute.assert_called_once_with('c-test')

    def test_rejected_outcome_emits_task_failed(self) -> None:
        fake_row = {
            'match_id': 9998,
            'quest_id': 'q-test',
            'candidate_id': 'c-test',
            'composite_score': 0.4,
            'outcome': 'rejected',
            'outcome_at': None,
            'tier': 'T1',
            'guild_scope': None,
        }

        with patch('sos.contracts.learning._fetch_pending_outcomes', return_value=[fake_row]):
            with patch('sos.contracts.learning._emit_via_audit_chain') as mock_emit:
                with patch('sos.contracts.reputation.recompute'):
                    with patch('sos.contracts.learning._fetch_quest_vector', return_value=None):
                        with patch('sos.contracts.learning._fetch_citizen_vector', return_value=None):
                            with patch('sos.contracts.learning._mark_processed'):
                                stats = process_outcomes()

        assert stats['processed'] == 1
        call_args = mock_emit.call_args
        assert call_args[0][2] == 'task_failed'  # action (rejected → task_failed)

    def test_vector_nudge_called_when_both_vectors_present(self) -> None:
        """When quest + citizen vectors both exist, citizen_vectors is updated."""
        quest_vec   = [0.8] * _NDIM
        citizen_vec = [0.4] * _NDIM
        fake_row = {
            'match_id': 9997,
            'quest_id': 'q-test',
            'candidate_id': 'c-test',
            'composite_score': 0.7,
            'outcome': 'accepted',
            'outcome_at': None,
            'tier': 'T2',
            'guild_scope': None,
        }

        with patch('sos.contracts.learning._fetch_pending_outcomes', return_value=[fake_row]):
            with patch('sos.contracts.learning._emit_via_audit_chain'):
                with patch('sos.contracts.reputation.recompute'):
                    with patch('sos.contracts.learning._fetch_quest_vector', return_value=quest_vec):
                        with patch('sos.contracts.learning._fetch_citizen_vector', return_value=citizen_vec):
                            with patch('sos.contracts.learning._upsert_citizen_vector') as mock_upsert:
                                with patch('sos.contracts.learning._mark_processed'):
                                    stats = process_outcomes()

        assert stats['vector_updates'] == 1
        assert mock_upsert.called
        # Updated vector should have moved toward quest_vec
        updated = mock_upsert.call_args[0][2]   # 3rd positional arg: the vector
        for c, q, u in zip(citizen_vec, quest_vec, updated):
            expected = c + _VECTOR_ALPHA * (q - c)
            assert abs(u - expected) < 1e-9

    def test_abandoned_no_vector_nudge(self) -> None:
        """Abandoned outcome: no citizen vector update."""
        quest_vec   = [0.8] * _NDIM
        citizen_vec = [0.4] * _NDIM
        fake_row = {
            'match_id': 9996,
            'quest_id': 'q-test',
            'candidate_id': 'c-test',
            'composite_score': 0.5,
            'outcome': 'abandoned',
            'outcome_at': None,
            'tier': 'T1',
            'guild_scope': None,
        }

        with patch('sos.contracts.learning._fetch_pending_outcomes', return_value=[fake_row]):
            with patch('sos.contracts.learning._emit_via_audit_chain'):
                with patch('sos.contracts.reputation.recompute'):
                    with patch('sos.contracts.learning._fetch_quest_vector', return_value=quest_vec):
                        with patch('sos.contracts.learning._fetch_citizen_vector', return_value=citizen_vec):
                            with patch('sos.contracts.learning._upsert_citizen_vector') as mock_upsert:
                                with patch('sos.contracts.learning._mark_processed'):
                                    stats = process_outcomes()

        assert stats['vector_updates'] == 0
        mock_upsert.assert_not_called()

    def test_cold_start_accepted_seeds_citizen_vector(self) -> None:
        """No citizen vector + accepted outcome → seed from quest vector."""
        quest_vec = [0.6] * _NDIM
        fake_row = {
            'match_id': 9995,
            'quest_id': 'q-test',
            'candidate_id': 'c-new',
            'composite_score': 0.6,
            'outcome': 'accepted',
            'outcome_at': None,
            'tier': 'T2',
            'guild_scope': None,
        }

        with patch('sos.contracts.learning._fetch_pending_outcomes', return_value=[fake_row]):
            with patch('sos.contracts.learning._emit_via_audit_chain'):
                with patch('sos.contracts.reputation.recompute'):
                    with patch('sos.contracts.learning._fetch_quest_vector', return_value=quest_vec):
                        with patch('sos.contracts.learning._fetch_citizen_vector', return_value=None):
                            with patch('sos.contracts.learning._upsert_citizen_vector') as mock_upsert:
                                with patch('sos.contracts.learning._mark_processed'):
                                    stats = process_outcomes()

        assert stats['vector_updates'] == 1
        assert mock_upsert.called
        seeded = mock_upsert.call_args[0][2]
        for v in seeded:
            assert abs(v - 0.3) < 1e-9  # 0.5 × 0.6

    def test_error_in_row_increments_errors_continues(self) -> None:
        """Single row failure increments errors count; remaining rows still processed."""
        fake_rows = [
            {'match_id': 1, 'quest_id': 'q1', 'candidate_id': 'c1',
             'composite_score': 0.5, 'outcome': 'accepted',
             'outcome_at': None, 'tier': 'T1', 'guild_scope': None},
            {'match_id': 2, 'quest_id': 'q2', 'candidate_id': 'c2',
             'composite_score': 0.5, 'outcome': 'accepted',
             'outcome_at': None, 'tier': 'T1', 'guild_scope': None},
        ]

        call_count = 0

        def _boom(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError('simulated DB error on row 1')

        with patch('sos.contracts.learning._fetch_pending_outcomes', return_value=fake_rows):
            with patch('sos.contracts.learning._emit_via_audit_chain', side_effect=_boom):
                with patch('sos.contracts.reputation.recompute'):
                    with patch('sos.contracts.learning._fetch_quest_vector', return_value=None):
                        with patch('sos.contracts.learning._fetch_citizen_vector', return_value=None):
                            with patch('sos.contracts.learning._mark_processed'):
                                stats = process_outcomes()

        assert stats['errors'] == 1
        assert stats['processed'] == 1
