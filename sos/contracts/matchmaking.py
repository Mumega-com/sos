"""
§16 Matchmaking — Five-Stage Citizen-to-Quest Assignment (Sprint 004 A.3).

Gate: Athena G15 (pending)
Depends on: G10 (reputation), G14 (Glicko-2), G13 (matchmaking spec v1.1)
Migrations: 030_matchmaking_schema.sql (quests, quest_vectors, citizen_vectors, match_history)

Five stages (deterministic, ordered):
  Stage 1 — Eligibility filter
            guild membership + inventory capabilities + Glicko-2 LCB ≥ tier threshold
  Stage 2 — FRC veto (coherence_check_v1)
            recent FRC verdicts from frc.get_recent_verdicts(); failed→0.0, degraded→0.7, aligned→1.0
  Stage 3 — Cosine 16D resonance
            citizen_vector vs quest_vector; neutral 0.5 on cold-start (no vector)
  Stage 4 — Multi-objective scalarization
            composite = Σ weight_dim * score_dim per TIER_WEIGHTS (exact per G13 spec v1.1)
  Stage 5 — Deterministic σ-exploration
            select_explore_candidate: fewest offers, longest-since-last-offered (no RNG)

TIER_WEIGHTS — EXACT per G13 spec v1.1 (no deviation without re-gate):
  T1: resonance=0.30, reputation=0.10, freshness=0.20, workload=0.10, exploration=0.30
  T2: resonance=0.40, reputation=0.25, freshness=0.15, workload=0.10, exploration=0.10
  T3: resonance=0.40, reputation=0.40, freshness=0.10, workload=0.05, exploration=0.05
  T4: resonance=0.30, reputation=0.55, freshness=0.05, workload=0.05, exploration=0.05

DB: psycopg2 (sync) against MIRROR_DATABASE_URL or DATABASE_URL.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras
from pydantic import BaseModel, ConfigDict

from sos.contracts import guild, inventory
from sos.contracts.reputation import ScoreKind, get_state_raw
from sos.services.intake import frc

log = logging.getLogger(__name__)

# ── TIER_WEIGHTS — locked per G13 spec v1.1; no deviation without re-gate ─────

TIER_WEIGHTS: dict[str, dict[str, float]] = {
    'T1': {'resonance': 0.30, 'reputation': 0.10, 'freshness': 0.20, 'workload': 0.10, 'exploration': 0.30},
    'T2': {'resonance': 0.40, 'reputation': 0.25, 'freshness': 0.15, 'workload': 0.10, 'exploration': 0.10},
    'T3': {'resonance': 0.40, 'reputation': 0.40, 'freshness': 0.10, 'workload': 0.05, 'exploration': 0.05},
    'T4': {'resonance': 0.30, 'reputation': 0.55, 'freshness': 0.05, 'workload': 0.05, 'exploration': 0.05},
}

# Minimum Glicko-2 LCB (μ - 1.5·φ) required per tier to pass eligibility
TIER_REP_THRESHOLDS: dict[str, float] = {
    'T1': -4.0,   # cold-start friendly — almost any LCB qualifies
    'T2': -2.0,   # modest history required
    'T3':  0.0,   # net-positive proven performance required
    'T4':  1.5,   # strong positive track record required
}

# FRC coherence_check_v1 lookback window (days)
_FRC_LOOKBACK_DAYS = 30

# Freshness normalization half-life (days since last assignment → score)
_FRESHNESS_HALF_LIFE_DAYS = 14.0

# Workload normalization cap (assignments above this get score=0.0)
_WORKLOAD_CAP = 10


# ── Types ──────────────────────────────────────────────────────────────────────


class CandidateScore(BaseModel):
    """Composite score for one candidate against one quest."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    quest_id: str
    tier: str
    stage1_passed: bool
    stage1_reason: str             # empty when passed
    frc_score: float               # Stage 2: 0.0 / 0.7 / 1.0
    resonance_score: float         # Stage 3: cosine 16D (0.0–1.0)
    reputation_score: float        # Stage 4 dim: normalized Glicko-2 LCB
    freshness_score: float         # Stage 4 dim: days-since-last-assignment decay
    workload_score: float          # Stage 4 dim: inverse active-assignment count
    exploration_score: float       # Stage 4 dim: offer-count inverse
    composite_score: float         # Stage 4: weighted sum


class MatchResult(BaseModel):
    """Final ranked output of rank_candidates()."""

    model_config = ConfigDict(frozen=True)

    quest_id: str
    tier: str
    ranked: list[CandidateScore]            # eligible, descending composite_score
    ineligible: list[CandidateScore]        # failed Stage 1
    explore_candidate_id: str | None        # Stage 5 deterministic pick (may be in ranked)


# ── DB connection ──────────────────────────────────────────────────────────────


def _db_url() -> str:
    url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError(
            'MIRROR_DATABASE_URL or DATABASE_URL is not set — '
            'matchmaking contract cannot connect to Mirror'
        )
    return url


def _connect():
    return psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)


# ── Quest + vector fetches ────────────────────────────────────────────────────


def _fetch_quest(conn, quest_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, title, tier, guild_scope, required_capabilities, status
                 FROM quests WHERE id = %s""",
            (quest_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def _fetch_quest_vector(conn, quest_id: str) -> list[float] | None:
    with conn.cursor() as cur:
        cur.execute(
            'SELECT vector FROM quest_vectors WHERE quest_id = %s',
            (quest_id,),
        )
        row = cur.fetchone()
    return list(row['vector']) if row else None


def _fetch_citizen_vector(conn, holder_id: str) -> list[float] | None:
    with conn.cursor() as cur:
        cur.execute(
            'SELECT vector FROM citizen_vectors WHERE holder_id = %s',
            (holder_id,),
        )
        row = cur.fetchone()
    return list(row['vector']) if row else None


def _fetch_active_assignment_count(conn, candidate_id: str) -> int:
    """Count open (outcome=NULL) match_history rows for a candidate."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) AS cnt FROM match_history
                WHERE candidate_id = %s AND outcome IS NULL""",
            (candidate_id,),
        )
        row = cur.fetchone()
    return int(row['cnt']) if row else 0


def _fetch_last_offered(conn, candidate_id: str, quest_id: str) -> dict:
    """Return offer_count and most recent assigned_at for (candidate, quest)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) AS offer_count, MAX(assigned_at) AS last_offered
                 FROM match_history
                WHERE candidate_id = %s AND quest_id = %s""",
            (candidate_id, quest_id),
        )
        row = cur.fetchone()
    return {
        'offer_count': int(row['offer_count']) if row else 0,
        'last_offered': row['last_offered'] if row else None,
    }


# ── Stage implementations ─────────────────────────────────────────────────────


def _stage1_eligibility(
    candidate_id: str,
    quest: dict,
) -> tuple[bool, str]:
    """
    Stage 1: guild membership + inventory capabilities + reputation LCB ≥ tier threshold.

    Returns (passed, reason). reason is empty when passed.
    """
    guild_scope: str | None = quest.get('guild_scope')
    tier: str = quest['tier']
    required_caps: list[dict] = quest.get('required_capabilities') or []

    # Guild membership (if quest is guild-scoped)
    if guild_scope:
        if not guild.assert_member(guild_scope, candidate_id):
            return (False, f'not a member of guild {guild_scope!r}')

    # Inventory capabilities
    for cap in required_caps:
        kind = cap.get('kind', '')
        ref  = cap.get('ref', '')
        action = cap.get('action', 'use')
        if not inventory.assert_capability(candidate_id, kind, ref, action):
            return (False, f'missing capability {kind}:{ref}:{action}')

    # Reputation Glicko-2 LCB ≥ tier threshold
    threshold = TIER_REP_THRESHOLDS.get(tier, 0.0)
    state = get_state_raw(candidate_id, 'overall', guild_scope)
    lcb = state.lcb if state else -float('inf')
    if lcb < threshold:
        return (False, f'reputation LCB {lcb:.3f} < tier {tier} threshold {threshold}')

    return (True, '')


def coherence_check_v1(candidate_id: str, lookback_days: int = _FRC_LOOKBACK_DAYS) -> float:
    """
    Stage 2: FRC veto — returns coherence multiplier [0.0, 1.0].

    Calls frc.get_recent_verdicts() (mirror_engrams proxy).
    Uses the most recent verdict (newest-first ordering guaranteed by get_recent_verdicts):
      'failed'   → 0.0  (full veto — not assignable)
      'degraded' → 0.7  (partial penalty)
      'aligned'  → 1.0  (no penalty)
      no data    → 1.0  (cold-start: no veto; benefit of the doubt)
    """
    verdicts = frc.get_recent_verdicts(candidate_id, lookback_days)
    if not verdicts:
        return 1.0
    recent = verdicts[0]['verdict']
    if recent == 'failed':
        return 0.0
    if recent == 'degraded':
        return 0.7
    return 1.0


def _stage3_cosine(
    citizen_vec: list[float] | None,
    quest_vec: list[float] | None,
) -> float:
    """
    Stage 3: cosine similarity between 16D citizen and quest vectors.

    Returns 0.5 (neutral) when either vector is absent (cold-start friendly).
    Returns 0.0 on zero-norm vectors to avoid division by zero.
    """
    if citizen_vec is None or quest_vec is None:
        return 0.5   # cold-start neutral
    if len(citizen_vec) != 16 or len(quest_vec) != 16:
        log.warning('cosine: expected 16D vectors, got %d and %d', len(citizen_vec), len(quest_vec))
        return 0.5

    dot = sum(a * b for a, b in zip(citizen_vec, quest_vec))
    norm_c = math.sqrt(sum(x * x for x in citizen_vec))
    norm_q = math.sqrt(sum(x * x for x in quest_vec))
    if norm_c == 0.0 or norm_q == 0.0:
        return 0.0
    raw = dot / (norm_c * norm_q)
    # Cosine is in [-1, 1]; shift to [0, 1] for uniform dimension range
    return (raw + 1.0) / 2.0


def _stage4_reputation_score(candidate_id: str, tier: str, guild_scope: str | None) -> float:
    """
    Normalize Glicko-2 LCB to [0, 1] for Stage 4 scalarization.

    Normalization: sigmoid(lcb / 3.0) maps practical LCB range to (0, 1).
    LCB=0 → 0.5, LCB=3 → 0.73, LCB=-3 → 0.27.
    Multiplied by tier-gated UCB for T1/T2 (cold-start boost, §16 spec).
    """
    state = get_state_raw(candidate_id, 'overall', guild_scope)
    if state is None:
        # T1/T2: cold-start gets neutral 0.5; T3/T4: no state → 0.0
        return 0.5 if tier in ('T1', 'T2') else 0.0
    if tier in ('T1', 'T2'):
        # Use UCB for cold-start-friendly tiers
        score_val = state.ucb
    else:
        # Use LCB for proven-performance tiers
        score_val = state.lcb
    # Sigmoid normalization
    return 1.0 / (1.0 + math.exp(-score_val / 3.0))


def _stage4_freshness_score(last_assignment: datetime | None) -> float:
    """
    Freshness: time since last assignment decays exponentially.

    last_assignment=None (never assigned) → 1.0 (fully fresh).
    14-day half-life: 14 days ago → 0.5, 28 days ago → 0.25.
    """
    if last_assignment is None:
        return 1.0
    now = datetime.now(timezone.utc)
    assigned = last_assignment if last_assignment.tzinfo else last_assignment.replace(tzinfo=timezone.utc)
    days_ago = (now - assigned).total_seconds() / 86400.0
    return math.exp(-days_ago * math.log(2) / _FRESHNESS_HALF_LIFE_DAYS)


def _stage4_workload_score(active_count: int) -> float:
    """
    Workload: fewer active assignments → higher score.

    active_count=0 → 1.0, cap=10 → 0.0. Linear interpolation.
    """
    if active_count >= _WORKLOAD_CAP:
        return 0.0
    return 1.0 - (active_count / _WORKLOAD_CAP)


def _stage4_exploration_score(offer_count: int) -> float:
    """
    Exploration: fewer prior offers for this quest → higher exploration score.

    offer_count=0 → 1.0. Decays as 1/(1+offer_count).
    Used in Stage 5 selection; included as a dimension so T1/T2 weight it heavily.
    """
    return 1.0 / (1.0 + offer_count)


def _stage4_composite(
    weights: dict[str, float],
    resonance: float,
    reputation: float,
    freshness: float,
    workload: float,
    exploration: float,
) -> float:
    """
    Stage 4: weighted sum of five normalized dimensions.
    Weights must sum to 1.0 (enforced by TIER_WEIGHTS definition).
    """
    return (
        weights['resonance']   * resonance
        + weights['reputation']  * reputation
        + weights['freshness']   * freshness
        + weights['workload']    * workload
        + weights['exploration'] * exploration
    )


def _stage5_explore_candidate(
    eligible: list[CandidateScore],
    conn,
    quest_id: str,
) -> str | None:
    """
    Stage 5: deterministic exploration candidate.

    Selection rule (no RNG):
      1. Among eligible candidates, find those with fewest total quest offers.
      2. Among ties, pick the one longest-since-last-offered (or never offered).
      3. If all have equal history, return the highest composite_score candidate
         (falls back to exploitation — no forced random pick).

    Returns candidate_id or None if no eligible candidates.
    """
    if not eligible:
        return None

    # Fetch offer stats for all eligible candidates for this quest
    stats: list[dict] = []
    for cs in eligible:
        row = _fetch_last_offered(conn, cs.candidate_id, quest_id)
        stats.append({
            'candidate_id': cs.candidate_id,
            'offer_count': row['offer_count'],
            'last_offered': row['last_offered'],
            'composite_score': cs.composite_score,
        })

    # Sort by: offer_count ASC, last_offered ASC (None sorts first = never offered first)
    def _sort_key(s: dict) -> tuple:
        last = s['last_offered']
        if last is None:
            # Never offered — highest priority for exploration
            ts = 0.0
        else:
            lo = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            ts = lo.timestamp()
        return (s['offer_count'], ts)

    stats.sort(key=_sort_key)
    return stats[0]['candidate_id']


# ── Public API ────────────────────────────────────────────────────────────────


def rank_candidates(
    quest_id: str,
    candidate_ids: list[str],
) -> MatchResult:
    """
    Run all five matchmaking stages for a quest against a candidate pool.

    Args:
        quest_id:       ID of the quest row in `quests` table.
        candidate_ids:  Profile/agent IDs to evaluate.

    Returns:
        MatchResult with ranked eligible + ineligible + explore_candidate.

    Raises:
        ValueError if quest not found or has non-open status.

    Stage summary:
      1. Eligibility filter — removes ineligible candidates
      2. FRC veto — multiplies composite by coherence_check_v1 score
      3. Cosine 16D — resonance dimension from citizen/quest vectors
      4. Scalarization — TIER_WEIGHTS weighted sum
      5. Deterministic exploration — select_explore_candidate
    """
    with _connect() as conn:
        quest = _fetch_quest(conn, quest_id)
        if quest is None:
            raise ValueError(f'quest {quest_id!r} not found')
        if quest['status'] != 'open':
            raise ValueError(f'quest {quest_id!r} has status {quest["status"]!r} (expected open)')

        tier: str = quest['tier']
        guild_scope: str | None = quest.get('guild_scope')
        weights = TIER_WEIGHTS[tier]

        quest_vec = _fetch_quest_vector(conn, quest_id)

        eligible_scores: list[CandidateScore] = []
        ineligible_scores: list[CandidateScore] = []

        for candidate_id in candidate_ids:
            # ── Stage 1: Eligibility ──────────────────────────────────────────
            passed, reason = _stage1_eligibility(candidate_id, quest)

            if not passed:
                ineligible_scores.append(CandidateScore(
                    candidate_id=candidate_id,
                    quest_id=quest_id,
                    tier=tier,
                    stage1_passed=False,
                    stage1_reason=reason,
                    frc_score=0.0,
                    resonance_score=0.0,
                    reputation_score=0.0,
                    freshness_score=0.0,
                    workload_score=0.0,
                    exploration_score=0.0,
                    composite_score=0.0,
                ))
                continue

            # ── Stage 2: FRC veto ─────────────────────────────────────────────
            frc_score = coherence_check_v1(candidate_id)
            if frc_score == 0.0:
                # Full veto — treat as ineligible
                ineligible_scores.append(CandidateScore(
                    candidate_id=candidate_id,
                    quest_id=quest_id,
                    tier=tier,
                    stage1_passed=True,
                    stage1_reason='',
                    frc_score=0.0,
                    resonance_score=0.0,
                    reputation_score=0.0,
                    freshness_score=0.0,
                    workload_score=0.0,
                    exploration_score=0.0,
                    composite_score=0.0,
                ))
                continue

            # ── Stage 3: Cosine 16D resonance ────────────────────────────────
            citizen_vec = _fetch_citizen_vector(conn, candidate_id)
            resonance = _stage3_cosine(citizen_vec, quest_vec)

            # ── Stage 4: Remaining dimensions ────────────────────────────────
            rep_score = _stage4_reputation_score(candidate_id, tier, guild_scope)

            active_count = _fetch_active_assignment_count(conn, candidate_id)
            workload_score = _stage4_workload_score(active_count)

            offer_info = _fetch_last_offered(conn, candidate_id, quest_id)
            freshness_score = _stage4_freshness_score(offer_info['last_offered'])
            exploration_score = _stage4_exploration_score(offer_info['offer_count'])

            raw_composite = _stage4_composite(
                weights,
                resonance=resonance,
                reputation=rep_score,
                freshness=freshness_score,
                workload=workload_score,
                exploration=exploration_score,
            )
            # Apply FRC multiplier (degraded→0.7 penalty)
            composite = raw_composite * frc_score

            eligible_scores.append(CandidateScore(
                candidate_id=candidate_id,
                quest_id=quest_id,
                tier=tier,
                stage1_passed=True,
                stage1_reason='',
                frc_score=frc_score,
                resonance_score=resonance,
                reputation_score=rep_score,
                freshness_score=freshness_score,
                workload_score=workload_score,
                exploration_score=exploration_score,
                composite_score=composite,
            ))

        # Sort eligible descending by composite_score
        eligible_scores.sort(key=lambda c: c.composite_score, reverse=True)

        # ── Stage 5: Deterministic exploration ───────────────────────────────
        explore_id = _stage5_explore_candidate(eligible_scores, conn, quest_id)

    return MatchResult(
        quest_id=quest_id,
        tier=tier,
        ranked=eligible_scores,
        ineligible=ineligible_scores,
        explore_candidate_id=explore_id,
    )


def record_assignment(
    quest_id: str,
    candidate_id: str,
    composite_score: float,
) -> int:
    """
    Write a match_history row when a candidate is formally assigned.

    Returns the new match_history.id.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO match_history (quest_id, candidate_id, composite_score, offer_count)
                   VALUES (%s, %s, %s,
                     (SELECT COUNT(*) FROM match_history
                       WHERE quest_id = %s AND candidate_id = %s) + 1
                   )
                   RETURNING id""",
                (quest_id, candidate_id, composite_score, quest_id, candidate_id),
            )
            row = cur.fetchone()
        conn.commit()
    return int(row['id'])


def record_outcome(
    match_id: int,
    outcome: str,
) -> None:
    """
    Record outcome on a match_history row.
    outcome must be 'accepted', 'rejected', or 'abandoned'.
    """
    if outcome not in ('accepted', 'rejected', 'abandoned'):
        raise ValueError(f'invalid outcome {outcome!r}')
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE match_history
                      SET outcome = %s, outcome_at = now()
                    WHERE id = %s""",
                (outcome, match_id),
            )
        conn.commit()
