"""
§16 Matchmaking contract tests — Sprint 004 A.3.

Unit tests: stage implementations, TIER_WEIGHTS, cosine math, freshness/workload/exploration scores.
Integration tests (requires DB): rank_candidates, record_assignment, record_outcome.

Run all:     DATABASE_URL=... pytest tests/contracts/test_matchmaking.py -v
Run unit:    pytest tests/contracts/test_matchmaking.py -v -m "not db"
"""
from __future__ import annotations

import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from sos.contracts.matchmaking import (
    TIER_WEIGHTS,
    TIER_REP_THRESHOLDS,
    CandidateScore,
    MatchResult,
    _stage3_cosine,
    _stage4_freshness_score,
    _stage4_workload_score,
    _stage4_exploration_score,
    _stage4_composite,
    coherence_check_v1,
    rank_candidates,
    record_assignment,
    record_outcome,
)


# ── helpers ────────────────────────────────────────────────────────────────────


def _has_db() -> bool:
    return bool(os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL'))


def _uid() -> str:
    return f'test-match-{uuid.uuid4().hex[:8]}'


db = pytest.mark.skipif(not _has_db(), reason='Mirror DB not configured')


# ── Unit: TIER_WEIGHTS ────────────────────────────────────────────────────────


class TestTierWeights:
    def test_all_tiers_present(self) -> None:
        for tier in ('T1', 'T2', 'T3', 'T4'):
            assert tier in TIER_WEIGHTS

    def test_all_dimensions_present(self) -> None:
        dims = {'resonance', 'reputation', 'freshness', 'workload', 'exploration'}
        for tier, weights in TIER_WEIGHTS.items():
            assert set(weights.keys()) == dims, f'{tier} missing dims'

    def test_weights_sum_to_one(self) -> None:
        """Each tier's weights must sum to exactly 1.0."""
        for tier, weights in TIER_WEIGHTS.items():
            total = sum(weights.values())
            assert abs(total - 1.0) < 1e-9, f'{tier} weights sum={total}'

    def test_t1_exact_values(self) -> None:
        w = TIER_WEIGHTS['T1']
        assert w['resonance']   == 0.30
        assert w['reputation']  == 0.10
        assert w['freshness']   == 0.20
        assert w['workload']    == 0.10
        assert w['exploration'] == 0.30

    def test_t2_exact_values(self) -> None:
        w = TIER_WEIGHTS['T2']
        assert w['resonance']   == 0.40
        assert w['reputation']  == 0.25
        assert w['freshness']   == 0.15
        assert w['workload']    == 0.10
        assert w['exploration'] == 0.10

    def test_t3_exact_values(self) -> None:
        w = TIER_WEIGHTS['T3']
        assert w['resonance']   == 0.40
        assert w['reputation']  == 0.40
        assert w['freshness']   == 0.10
        assert w['workload']    == 0.05
        assert w['exploration'] == 0.05

    def test_t4_exact_values(self) -> None:
        w = TIER_WEIGHTS['T4']
        assert w['resonance']   == 0.30
        assert w['reputation']  == 0.55
        assert w['freshness']   == 0.05
        assert w['workload']    == 0.05
        assert w['exploration'] == 0.05

    def test_t4_reputation_dominates(self) -> None:
        """T4 reputation weight is highest — proven performance gate."""
        w = TIER_WEIGHTS['T4']
        assert w['reputation'] == max(w.values())

    def test_t1_exploration_equals_resonance(self) -> None:
        """T1 emphasises exploration equally with resonance."""
        w = TIER_WEIGHTS['T1']
        assert w['exploration'] == w['resonance']


# ── Unit: Stage 3 cosine ──────────────────────────────────────────────────────


class TestStage3Cosine:
    def test_identical_vectors_score_one(self) -> None:
        v = [1.0] * 16
        # cos=1.0 → (1+1)/2 = 1.0
        assert abs(_stage3_cosine(v, v) - 1.0) < 1e-9

    def test_opposite_vectors_score_zero(self) -> None:
        v = [1.0] * 16
        neg = [-1.0] * 16
        # cos=-1.0 → (−1+1)/2 = 0.0
        assert abs(_stage3_cosine(v, neg) - 0.0) < 1e-9

    def test_orthogonal_vectors_score_half(self) -> None:
        a = [1.0, 0.0] + [0.0] * 14
        b = [0.0, 1.0] + [0.0] * 14
        # cos=0.0 → 0.5
        assert abs(_stage3_cosine(a, b) - 0.5) < 1e-9

    def test_none_citizen_returns_neutral(self) -> None:
        assert _stage3_cosine(None, [1.0] * 16) == 0.5

    def test_none_quest_returns_neutral(self) -> None:
        assert _stage3_cosine([1.0] * 16, None) == 0.5

    def test_both_none_returns_neutral(self) -> None:
        assert _stage3_cosine(None, None) == 0.5

    def test_zero_norm_citizen_returns_zero(self) -> None:
        assert _stage3_cosine([0.0] * 16, [1.0] * 16) == 0.0

    def test_output_in_zero_one(self) -> None:
        import random
        rng = random.Random(42)
        for _ in range(20):
            a = [rng.gauss(0, 1) for _ in range(16)]
            b = [rng.gauss(0, 1) for _ in range(16)]
            s = _stage3_cosine(a, b)
            assert 0.0 <= s <= 1.0


# ── Unit: Stage 4 dimension scores ───────────────────────────────────────────


class TestFreshnessScore:
    def test_never_assigned_is_one(self) -> None:
        assert _stage4_freshness_score(None) == 1.0

    def test_14_days_ago_is_half(self) -> None:
        t = datetime.now(timezone.utc) - timedelta(days=14)
        assert abs(_stage4_freshness_score(t) - 0.5) < 0.02

    def test_very_recent_near_one(self) -> None:
        t = datetime.now(timezone.utc) - timedelta(seconds=10)
        assert _stage4_freshness_score(t) > 0.99

    def test_old_assignment_near_zero(self) -> None:
        t = datetime.now(timezone.utc) - timedelta(days=365)
        assert _stage4_freshness_score(t) < 0.01

    def test_naive_dt_treated_as_utc(self) -> None:
        t = datetime.now() - timedelta(days=7)
        score = _stage4_freshness_score(t)
        assert 0.0 < score < 1.0


class TestWorkloadScore:
    def test_zero_active_is_one(self) -> None:
        assert _stage4_workload_score(0) == 1.0

    def test_cap_is_zero(self) -> None:
        assert _stage4_workload_score(10) == 0.0

    def test_above_cap_is_zero(self) -> None:
        assert _stage4_workload_score(15) == 0.0

    def test_half_cap_is_half(self) -> None:
        assert abs(_stage4_workload_score(5) - 0.5) < 1e-9

    def test_decreases_monotonically(self) -> None:
        scores = [_stage4_workload_score(i) for i in range(11)]
        assert all(scores[i] >= scores[i+1] for i in range(len(scores)-1))


class TestExplorationScore:
    def test_never_offered_is_one(self) -> None:
        assert _stage4_exploration_score(0) == 1.0

    def test_one_offer_is_half(self) -> None:
        assert abs(_stage4_exploration_score(1) - 0.5) < 1e-9

    def test_decreases_with_offers(self) -> None:
        scores = [_stage4_exploration_score(i) for i in range(10)]
        assert all(scores[i] > scores[i+1] for i in range(len(scores)-1))

    def test_always_positive(self) -> None:
        for n in range(100):
            assert _stage4_exploration_score(n) > 0.0


class TestStage4Composite:
    def test_perfect_scores_is_one(self) -> None:
        w = TIER_WEIGHTS['T3']
        score = _stage4_composite(w, 1.0, 1.0, 1.0, 1.0, 1.0)
        assert abs(score - 1.0) < 1e-9

    def test_zero_scores_is_zero(self) -> None:
        w = TIER_WEIGHTS['T3']
        score = _stage4_composite(w, 0.0, 0.0, 0.0, 0.0, 0.0)
        assert abs(score - 0.0) < 1e-9

    def test_weighted_sum_correct(self) -> None:
        """T3: 0.40·res + 0.40·rep + 0.10·fresh + 0.05·work + 0.05·expl."""
        w = TIER_WEIGHTS['T3']
        score = _stage4_composite(w, 0.8, 0.6, 1.0, 0.5, 0.3)
        expected = 0.40*0.8 + 0.40*0.6 + 0.10*1.0 + 0.05*0.5 + 0.05*0.3
        assert abs(score - expected) < 1e-9

    def test_output_in_zero_one(self) -> None:
        """With all inputs in [0,1] and weights summing to 1, output is in [0,1] (±ε float)."""
        for tier in ('T1', 'T2', 'T3', 'T4'):
            w = TIER_WEIGHTS[tier]
            for val in (0.0, 0.3, 0.7, 1.0):
                s = _stage4_composite(w, val, val, val, val, val)
                assert -1e-9 <= s <= 1.0 + 1e-9


# ── Unit: Stage 2 coherence_check_v1 ─────────────────────────────────────────


class TestCoherenceCheckV1:
    def test_no_verdicts_returns_one(self) -> None:
        with patch('sos.contracts.matchmaking.frc.get_recent_verdicts', return_value=[]):
            assert coherence_check_v1('citizen-x') == 1.0

    def test_aligned_returns_one(self) -> None:
        verdicts = [{'engram_id': 'e1', 'verdict': 'aligned', 'recorded_at': None}]
        with patch('sos.contracts.matchmaking.frc.get_recent_verdicts', return_value=verdicts):
            assert coherence_check_v1('citizen-x') == 1.0

    def test_degraded_returns_0_7(self) -> None:
        verdicts = [{'engram_id': 'e1', 'verdict': 'degraded', 'recorded_at': None}]
        with patch('sos.contracts.matchmaking.frc.get_recent_verdicts', return_value=verdicts):
            assert abs(coherence_check_v1('citizen-x') - 0.7) < 1e-9

    def test_failed_returns_zero(self) -> None:
        verdicts = [{'engram_id': 'e1', 'verdict': 'failed', 'recorded_at': None}]
        with patch('sos.contracts.matchmaking.frc.get_recent_verdicts', return_value=verdicts):
            assert coherence_check_v1('citizen-x') == 0.0

    def test_uses_most_recent_verdict(self) -> None:
        """Most recent (index 0, newest-first) is used, not oldest."""
        verdicts = [
            {'engram_id': 'e1', 'verdict': 'failed',  'recorded_at': None},  # most recent
            {'engram_id': 'e2', 'verdict': 'aligned', 'recorded_at': None},  # older
        ]
        with patch('sos.contracts.matchmaking.frc.get_recent_verdicts', return_value=verdicts):
            assert coherence_check_v1('citizen-x') == 0.0


# ── Unit: CandidateScore model ────────────────────────────────────────────────


class TestCandidateScoreModel:
    def test_valid(self) -> None:
        cs = CandidateScore(
            candidate_id='kasra', quest_id='q1', tier='T2',
            stage1_passed=True, stage1_reason='',
            frc_score=1.0, resonance_score=0.7, reputation_score=0.6,
            freshness_score=0.9, workload_score=0.8, exploration_score=1.0,
            composite_score=0.75,
        )
        assert cs.candidate_id == 'kasra'
        assert cs.stage1_passed is True

    def test_frozen(self) -> None:
        cs = CandidateScore(
            candidate_id='kasra', quest_id='q1', tier='T1',
            stage1_passed=False, stage1_reason='not a member',
            frc_score=0.0, resonance_score=0.0, reputation_score=0.0,
            freshness_score=0.0, workload_score=0.0, exploration_score=0.0,
            composite_score=0.0,
        )
        with pytest.raises(ValidationError):
            cs.composite_score = 1.0  # type: ignore[misc]


# ── Unit: TIER_REP_THRESHOLDS ordering ───────────────────────────────────────


class TestTierRepThresholds:
    def test_all_tiers_present(self) -> None:
        for tier in ('T1', 'T2', 'T3', 'T4'):
            assert tier in TIER_REP_THRESHOLDS

    def test_thresholds_increase_by_tier(self) -> None:
        """T1 ≤ T2 ≤ T3 ≤ T4 — higher tiers require stronger reputation."""
        t = TIER_REP_THRESHOLDS
        assert t['T1'] <= t['T2'] <= t['T3'] <= t['T4']

    def test_t3_requires_positive_lcb(self) -> None:
        """T3 threshold ≥ 0 — proven hands required."""
        assert TIER_REP_THRESHOLDS['T3'] >= 0.0

    def test_t1_is_cold_start_friendly(self) -> None:
        """T1 threshold is very low — cold-start citizens can participate."""
        assert TIER_REP_THRESHOLDS['T1'] < 0.0


# ── Integration: DB-backed ────────────────────────────────────────────────────


@db
class TestRankCandidatesDB:
    # Each test class instance gets a unique creator so rate-limit trigger doesn't
    # accumulate across test runs. IDs are tracked for cleanup in teardown.
    _creator: str = ''
    _inserted_qids: list

    def setup_method(self) -> None:
        self._creator = f'test-{uuid.uuid4().hex[:12]}'
        self._inserted_qids = []

    def teardown_method(self) -> None:
        if not self._inserted_qids:
            return
        import psycopg2, psycopg2.extras
        from sos.contracts.matchmaking import _connect
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                'DELETE FROM match_history WHERE quest_id = ANY(%s)',
                (self._inserted_qids,),
            )
            cur.execute(
                'DELETE FROM quests WHERE id = ANY(%s)',
                (self._inserted_qids,),
            )
        conn.commit()
        conn.close()

    def _insert_quest(self, conn, tier: str = 'T1') -> str:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO quests (title, description, tier, created_by)
                   VALUES ('Test Quest', 'desc', %s, %s)
                   RETURNING id""",
                (tier, self._creator),
            )
            qid = cur.fetchone()['id']
        conn.commit()
        self._inserted_qids.append(qid)
        return qid

    def test_empty_candidate_pool(self) -> None:
        import psycopg2, psycopg2.extras
        from sos.contracts.matchmaking import _connect
        conn = _connect()
        qid = self._insert_quest(conn)
        conn.close()

        result = rank_candidates(qid, [])
        assert result.quest_id == qid
        assert result.ranked == []
        assert result.ineligible == []
        assert result.explore_candidate_id is None

    def test_quest_not_found_raises(self) -> None:
        with pytest.raises(ValueError, match='not found'):
            rank_candidates('nonexistent-quest-id', ['cand-1'])

    def test_result_structure(self) -> None:
        import psycopg2, psycopg2.extras
        from sos.contracts.matchmaking import _connect
        conn = _connect()
        qid = self._insert_quest(conn, tier='T1')
        conn.close()

        # Candidate with no guild membership, no rep, no capabilities → ineligible or eligible
        # T1 threshold is very low (−4.0 LCB) and no required_capabilities → might pass stage 1
        # This test only verifies structural correctness
        result = rank_candidates(qid, [_uid()])
        assert isinstance(result, MatchResult)
        assert isinstance(result.ranked, list)
        assert isinstance(result.ineligible, list)

    def test_record_assignment_and_outcome(self) -> None:
        import psycopg2, psycopg2.extras
        from sos.contracts.matchmaking import _connect
        conn = _connect()
        qid = self._insert_quest(conn)
        conn.close()

        candidate_id = _uid()
        match_id = record_assignment(qid, candidate_id, 0.75)
        assert isinstance(match_id, int)
        assert match_id > 0

        record_outcome(match_id, 'accepted')

    def test_record_outcome_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match='invalid outcome'):
            record_outcome(1, 'unknown_outcome')
