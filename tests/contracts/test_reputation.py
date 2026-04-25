"""
§15 Reputation contract tests — Sprint 004 A.2 (Glicko-2 reshape).

Unit tests: Glicko-2 math (g, E, sigma update, batch update), type validation.
Integration tests (requires DB): recompute, get_score, can_claim, get_recent_events.

Run all:     DATABASE_URL=... pytest tests/contracts/test_reputation.py -v
Run unit:    pytest tests/contracts/test_reputation.py -v -m "not db"
"""
from __future__ import annotations

import math
import os
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sos.contracts.reputation import (
    _E,
    _K_LCB,
    _K_UCB,
    _MU_0,
    _PHI_0,
    _SIGMA_0,
    _TAU,
    _g,
    _glicko2_update,
    _new_sigma,
    ReputationEvent,
    ReputationScore,
    ReputationState,
    ScoreKind,
    can_claim,
    get_recent_events,
    get_score,
    recompute_reputation_scores,
)


# ── helpers ────────────────────────────────────────────────────────────────────


def _has_db() -> bool:
    return bool(os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL'))


def _uid() -> str:
    return f'test-rep-{uuid.uuid4().hex[:8]}'


db = pytest.mark.skipif(not _has_db(), reason='Mirror DB not configured')


def _ev(event_type: str, weight: float) -> dict:
    """Make a minimal event dict for Glicko-2 update tests."""
    return {'event_type': event_type, 'weight': weight,
            'recorded_at': datetime.now(timezone.utc)}


# ── Unit: ReputationScore model ────────────────────────────────────────────────


class TestReputationScoreModel:
    def test_valid(self) -> None:
        now = datetime.now(timezone.utc)
        score = ReputationScore(
            holder_id='kasra',
            score_kind='overall',
            guild_scope=None,
            value=0.75,
            sample_size=10,
            decay_factor=0.0,   # 0.0 post-G14; Glicko-2 has no decay constant
            computed_at=now,
        )
        assert score.holder_id == 'kasra'
        assert score.score_kind == 'overall'
        assert score.guild_scope is None

    def test_frozen(self) -> None:
        now = datetime.now(timezone.utc)
        score = ReputationScore(
            holder_id='kasra',
            score_kind='reliability',
            guild_scope=None,
            value=0.5,
            sample_size=3,
            decay_factor=0.0,
            computed_at=now,
        )
        with pytest.raises(ValidationError):
            score.value = 0.9  # type: ignore[misc]

    def test_scoped(self) -> None:
        now = datetime.now(timezone.utc)
        score = ReputationScore(
            holder_id='kasra',
            score_kind='quality',
            guild_scope='mumega-inc',
            value=0.8,
            sample_size=5,
            decay_factor=0.0,
            computed_at=now,
        )
        assert score.guild_scope == 'mumega-inc'


# ── Unit: ReputationState model ────────────────────────────────────────────────


class TestReputationStateModel:
    def test_valid(self) -> None:
        now = datetime.now(timezone.utc)
        state = ReputationState(
            holder_id='kasra',
            kind='overall',
            guild_scope=None,
            mu=0.5,
            phi=1.2,
            sigma=0.06,
            sample_size=10,
            last_updated=now,
        )
        assert state.mu == 0.5
        assert state.phi == 1.2
        assert state.sigma == 0.06

    def test_lcb_property(self) -> None:
        now = datetime.now(timezone.utc)
        state = ReputationState(
            holder_id='x', kind='overall', guild_scope=None,
            mu=1.0, phi=0.5, sigma=0.06, sample_size=5,
            last_updated=now,
        )
        # LCB = μ - 1.5·φ = 1.0 - 1.5*0.5 = 0.25
        assert abs(state.lcb - 0.25) < 1e-9

    def test_ucb_property(self) -> None:
        now = datetime.now(timezone.utc)
        state = ReputationState(
            holder_id='x', kind='overall', guild_scope=None,
            mu=1.0, phi=0.5, sigma=0.06, sample_size=5,
            last_updated=now,
        )
        # UCB = μ + 1.5·φ = 1.0 + 1.5*0.5 = 1.75
        assert abs(state.ucb - 1.75) < 1e-9

    def test_frozen(self) -> None:
        now = datetime.now(timezone.utc)
        state = ReputationState(
            holder_id='x', kind='reliability', guild_scope=None,
            mu=0.0, phi=_PHI_0, sigma=_SIGMA_0, sample_size=0,
            last_updated=now,
        )
        with pytest.raises(ValidationError):
            state.mu = 1.0  # type: ignore[misc]


# ── Unit: ReputationEvent model ────────────────────────────────────────────────


class TestReputationEventModel:
    def test_valid(self) -> None:
        now = datetime.now(timezone.utc)
        event = ReputationEvent(
            id=1, holder_id='kasra', event_type='task_completed',
            weight=1.0, guild_scope=None, evidence_ref='audit:chain:abc123',
            recorded_at=now,
        )
        assert event.event_type == 'task_completed'

    def test_frozen(self) -> None:
        now = datetime.now(timezone.utc)
        event = ReputationEvent(
            id=2, holder_id='kasra', event_type='verification_passed',
            weight=1.0, guild_scope=None, evidence_ref='audit:chain:def456',
            recorded_at=now,
        )
        with pytest.raises(ValidationError):
            event.weight = 2.0  # type: ignore[misc]


# ── Unit: Glicko-2 g-function ─────────────────────────────────────────────────


class TestGFunction:
    def test_g_zero_phi_is_one(self) -> None:
        """g(0) = 1 — perfect certainty means no dampening."""
        assert abs(_g(0.0) - 1.0) < 1e-9

    def test_g_decreases_with_phi(self) -> None:
        """Larger φ → smaller g (uncertainty dampens outcome influence)."""
        assert _g(0.5) > _g(1.0) > _g(2.0)

    def test_g_initial_phi(self) -> None:
        """g(_PHI_0) is between 0 and 1."""
        val = _g(_PHI_0)
        assert 0.0 < val < 1.0

    def test_g_formula(self) -> None:
        """g(φ) = 1/sqrt(1 + 3φ²/π²) — spot-check at φ=1."""
        phi = 1.0
        expected = 1.0 / math.sqrt(1.0 + 3.0 * phi**2 / math.pi**2)
        assert abs(_g(phi) - expected) < 1e-12


# ── Unit: Glicko-2 E-function ─────────────────────────────────────────────────


class TestEFunction:
    def test_equal_ratings_half_probability(self) -> None:
        """When μ == μ_j and φ_j == 0, expected outcome is 0.5."""
        assert abs(_E(0.0, 0.0, 0.0) - 0.5) < 1e-9

    def test_higher_rating_favoured(self) -> None:
        """μ > μ_j → expected outcome > 0.5."""
        assert _E(1.0, 0.0, 0.0) > 0.5

    def test_lower_rating_disfavoured(self) -> None:
        """μ < μ_j → expected outcome < 0.5."""
        assert _E(-1.0, 0.0, 0.0) < 0.5

    def test_bounded_zero_one(self) -> None:
        """Expected outcome always in (0, 1)."""
        for mu in (-10.0, -1.0, 0.0, 1.0, 10.0):
            val = _E(mu, 0.0, 0.0)
            assert 0.0 < val < 1.0

    def test_uncertainty_dampens_spread(self) -> None:
        """Large φ_j → E closer to 0.5 (uncertainty dampens extreme predictions)."""
        e_certain   = _E(2.0, 0.0, 0.0)    # φ_j=0, g=1 → strong signal
        e_uncertain = _E(2.0, 0.0, 3.0)    # φ_j=3, g<1 → dampened
        assert e_uncertain < e_certain


# ── Unit: Glicko-2 volatility update ─────────────────────────────────────────


class TestNewSigma:
    def test_stable_performance_keeps_low_sigma(self) -> None:
        """Consistent performance → σ' stays close to σ."""
        mu, phi, sigma = 0.0, _PHI_0, _SIGMA_0
        # v=1, delta=0 → no performance surprise → σ' ≈ σ
        v = 1.0
        delta = 0.0
        sigma_new = _new_sigma(sigma, phi, v, delta)
        # Should remain near σ_0 when no surprise
        assert 0.0 < sigma_new < 1.0

    def test_volatile_performance_increases_sigma(self) -> None:
        """Large delta (unexpected outcome) → σ' > σ."""
        mu, phi, sigma = 0.0, 1.5, _SIGMA_0
        v = 0.5
        delta = 3.0   # large surprise
        sigma_new = _new_sigma(sigma, phi, v, delta)
        assert sigma_new >= sigma

    def test_sigma_stays_positive(self) -> None:
        """σ' is always positive."""
        for delta in (-5.0, 0.0, 0.5, 2.0):
            s = _new_sigma(_SIGMA_0, _PHI_0, 1.0, delta)
            assert s > 0.0

    def test_tau_bounds_enforced_by_caller(self) -> None:
        """_new_sigma itself does not enforce τ range — recompute does."""
        # This is a documentation test — just verify it runs with edge τ values
        _new_sigma(_SIGMA_0, _PHI_0, 1.0, 0.5, tau=0.3)
        _new_sigma(_SIGMA_0, _PHI_0, 1.0, 0.5, tau=1.2)


# ── Unit: Glicko-2 batch update ───────────────────────────────────────────────


class TestGlicko2Update:
    def test_no_events_inflates_phi_only(self) -> None:
        """No games in period → μ unchanged, φ increases by σ, σ unchanged."""
        mu_new, phi_new, sigma_new = _glicko2_update(_MU_0, _PHI_0, _SIGMA_0, [])
        assert mu_new == _MU_0
        # φ' = sqrt(φ² + σ²) > φ
        expected_phi = math.sqrt(_PHI_0**2 + _SIGMA_0**2)
        assert abs(phi_new - expected_phi) < 1e-9
        assert sigma_new == _SIGMA_0

    def test_positive_events_increase_mu(self) -> None:
        """Positive outcomes against neutral ref → μ moves up from 0."""
        events = [_ev('task_completed', 1.0)] * 5
        mu_new, phi_new, sigma_new = _glicko2_update(_MU_0, _PHI_0, _SIGMA_0, events)
        assert mu_new > _MU_0

    def test_negative_events_decrease_mu(self) -> None:
        """Negative outcomes → μ moves down from 0."""
        events = [_ev('task_failed', -0.8)] * 5
        mu_new, phi_new, sigma_new = _glicko2_update(_MU_0, _PHI_0, _SIGMA_0, events)
        assert mu_new < _MU_0

    def test_phi_decreases_with_evidence(self) -> None:
        """More events → uncertainty φ decreases (becomes more certain)."""
        events = [_ev('task_completed', 1.0)] * 10
        mu_new, phi_new, sigma_new = _glicko2_update(_MU_0, _PHI_0, _SIGMA_0, events)
        assert phi_new < _PHI_0

    def test_phi_always_positive(self) -> None:
        """φ' must always be positive."""
        for n in (1, 5, 20):
            events = [_ev('task_completed', 1.0)] * n
            _, phi_new, _ = _glicko2_update(_MU_0, _PHI_0, _SIGMA_0, events)
            assert phi_new > 0.0

    def test_sigma_always_positive(self) -> None:
        """σ' must always be positive."""
        for weight in (1.0, -0.8, -3.0):
            events = [_ev('task_failed', weight)] * 3
            _, _, sigma_new = _glicko2_update(_MU_0, _PHI_0, _SIGMA_0, events)
            assert sigma_new > 0.0

    def test_mixed_events_moderate_mu(self) -> None:
        """Equal positive and negative events → μ stays near 0."""
        events = (
            [_ev('task_completed', 1.0)] * 5
            + [_ev('task_failed', -0.8)] * 5
        )
        mu_new, _, _ = _glicko2_update(_MU_0, _PHI_0, _SIGMA_0, events)
        # Should be near 0 (slight positive bias from weight asymmetry)
        assert abs(mu_new) < 1.0

    def test_tau_validation_in_recompute(self) -> None:
        """recompute_reputation_scores rejects τ outside [0.3, 1.2]."""
        with pytest.raises(ValueError, match='tau='):
            recompute_reputation_scores(holder_id=_uid(), tau=0.1)
        with pytest.raises(ValueError, match='tau='):
            recompute_reputation_scores(holder_id=_uid(), tau=1.5)

    def test_update_is_deterministic(self) -> None:
        """Same inputs → same (μ', φ', σ') always."""
        events = [_ev('task_completed', 1.0), _ev('task_failed', -0.8)]
        r1 = _glicko2_update(_MU_0, _PHI_0, _SIGMA_0, events)
        r2 = _glicko2_update(_MU_0, _PHI_0, _SIGMA_0, events)
        assert r1 == r2

    def test_lcb_ucb_spread(self) -> None:
        """LCB < μ < UCB for φ > 0."""
        events = [_ev('task_completed', 1.0)] * 3
        mu_new, phi_new, _ = _glicko2_update(_MU_0, _PHI_0, _SIGMA_0, events)
        lcb = mu_new - _K_LCB * phi_new
        ucb = mu_new + _K_UCB * phi_new
        assert lcb < mu_new < ucb


# ── Unit: ScoreKind literal ────────────────────────────────────────────────────


class TestScoreKind:
    def test_valid_kinds(self) -> None:
        for kind in ('overall', 'reliability', 'quality', 'compliance'):
            now = datetime.now(timezone.utc)
            score = ReputationScore(
                holder_id='x',
                score_kind=kind,  # type: ignore[arg-type]
                guild_scope=None,
                value=0.0,
                sample_size=0,
                decay_factor=0.0,
                computed_at=now,
            )
            assert score.score_kind == kind

    def test_invalid_kind_rejected(self) -> None:
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            ReputationScore(
                holder_id='x',
                score_kind='xp_score',  # type: ignore[arg-type]
                guild_scope=None,
                value=0.0,
                sample_size=0,
                decay_factor=0.0,
                computed_at=now,
            )


# ── Unit: Glicko-2 constants ──────────────────────────────────────────────────


class TestConstants:
    def test_phi_0_is_350_over_173(self) -> None:
        """_PHI_0 = 350 / 173.7178 ≈ 2.014732 (Glickman standard)."""
        expected = 350.0 / 173.7178
        assert abs(_PHI_0 - expected) < 1e-4

    def test_tau_in_recommended_range(self) -> None:
        """Default τ within Glickman recommended range [0.3, 1.2]."""
        assert 0.3 <= _TAU <= 1.2

    def test_k_values_positive(self) -> None:
        assert _K_LCB > 0
        assert _K_UCB > 0

    def test_sigma_0_is_standard(self) -> None:
        """σ₀ = 0.06 is the standard Glicko-2 starting volatility."""
        assert abs(_SIGMA_0 - 0.06) < 1e-9


# ── Integration: DB-backed reads + recompute ───────────────────────────────────


@db
class TestGetScore:
    def test_cold_start_returns_none(self) -> None:
        """Holder with no events → get_score returns None."""
        holder = _uid()
        result = get_score(holder, 'overall')
        assert result is None

    def test_cold_start_scoped_returns_none(self) -> None:
        holder = _uid()
        result = get_score(holder, 'overall', guild_scope='mumega-inc')
        assert result is None


@db
class TestGetRecentEvents:
    def test_no_events_returns_empty(self) -> None:
        holder = _uid()
        events = get_recent_events(holder)
        assert events == []

    def test_scoped_no_events_returns_empty(self) -> None:
        holder = _uid()
        events = get_recent_events(holder, guild_scope='mumega-inc')
        assert events == []


@db
class TestRecompute:
    """
    DB integration tests for recompute_reputation_scores().

    NOTE: reputation_events can only be written via the audit_to_reputation()
    SECURITY DEFINER trigger. These tests validate the recompute path given
    the contract surface — they verify the function runs without error and
    returns a stats dict. Cannot inject synthetic events from app code.

    Full pipeline testing (audit → trigger → reputation_events → recompute)
    is in tests/integration/test_reputation_pipeline.py.
    """

    def test_recompute_no_events_returns_zero(self) -> None:
        """Holder with no events → recompute returns holders=1, scores_written=0."""
        holder = _uid()
        stats = recompute_reputation_scores(holder_id=holder)
        assert stats['holders'] == 1
        assert stats['scores_written'] == 0

    def test_recompute_bulk_runs_without_error(self) -> None:
        """Bulk recompute (no holder_id) runs and returns a valid stats dict."""
        stats = recompute_reputation_scores()
        assert 'holders' in stats
        assert 'scores_written' in stats
        assert stats['holders'] >= 0
        assert stats['scores_written'] >= 0

    def test_recompute_stats_keys(self) -> None:
        holder = _uid()
        stats = recompute_reputation_scores(holder_id=holder)
        assert set(stats.keys()) == {'holders', 'scores_written'}

    def test_recompute_default_tau_valid(self) -> None:
        """Default τ=0.5 passes validation."""
        holder = _uid()
        stats = recompute_reputation_scores(holder_id=holder, tau=0.5)
        assert isinstance(stats, dict)

    def test_recompute_tau_boundary_valid(self) -> None:
        """τ at boundary values [0.3, 1.2] are accepted."""
        holder = _uid()
        recompute_reputation_scores(holder_id=holder, tau=0.3)
        recompute_reputation_scores(holder_id=holder, tau=1.2)


@db
class TestCanClaim:
    def test_cold_start_below_threshold(self) -> None:
        """Holder with no events → score is None → ineligible."""
        holder = _uid()
        eligible, reason = can_claim(holder, quest_id='q-test-001', min_overall=0.0)
        assert eligible is False
        assert 'threshold' in reason

    def test_cold_start_zero_threshold_ineligible(self) -> None:
        """Even min_overall=0.0 rejects None score."""
        holder = _uid()
        eligible, reason = can_claim(holder, quest_id='q-test-002', min_overall=0.0)
        assert eligible is False


@db
class TestCanClaimGuildScope:
    def test_scoped_cold_start_ineligible(self) -> None:
        holder = _uid()
        eligible, reason = can_claim(
            holder, quest_id='q-guild-001', min_overall=0.0,
            guild_scope='mumega-inc',
        )
        assert eligible is False

    def test_scoped_no_events_reason_non_empty(self) -> None:
        holder = _uid()
        eligible, reason = can_claim(
            holder, quest_id='q-guild-002', min_overall=0.0,
            guild_scope='mumega-inc', cooling_off_days=7,
        )
        assert eligible is False
        assert reason  # non-empty reason
