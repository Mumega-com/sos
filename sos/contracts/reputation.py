"""
§15 Reputation — Glicko-2 Bayesian Trust Rating (Sprint 004 A.2 reshape).

Gate: Athena G14 APPROVED (pending)
Prior gate: Athena G10 APPROVED v1.2
Migrations:
  022_reputation.sql  — reputation_events, original reputation_scores TABLE, trigger
  029_reputation_state.sql — reputation_state TABLE, reputation_scores VIEW (derived)

Constitutional constraints (unchanged from G10):
  1. audit chain is the ONLY source of reputation_events. App code NEVER writes
     to reputation_events directly — REVOKE INSERT enforces this at DB level.
     The SECURITY DEFINER trigger audit_to_reputation() is the sole writer.
  2. reputation_state is written ONLY by recompute_reputation_scores() (Dreamer hook).
     App role REVOKED INSERT on reputation_state at DB level.
  3. σ (volatility) is KERNEL-PRIVATE. Never returned to non-kernel callers.
     get_score() and get_state_public() mask σ. Only get_state_raw() exposes it —
     callers are responsible for kernel-tier auth check before calling get_state_raw().
  4. No XP/levels/quest vocabulary. Scores are honest Glicko-2 LCB.

Glicko-2 model (Glickman 2012, https://www.glicko.net/glicko/glicko2.pdf):
  Each citizen has a posterior (μ, φ, σ) per (holder_id, kind, guild_scope).
  μ  — rating (Glicko-2 scale, initial 0.0)
  φ  — rating deviation (uncertainty); initial 350/173.7178 ≈ 2.014732
  σ  — volatility (how erratic performance is); initial 0.06

  Display score (LCB, k=1.5):  value = μ − 1.5·φ  (penalises high uncertainty)
  Cold-start UCB (k=1.5):      upper = μ + 1.5·φ  (computed by matchmaking, not stored)

  Each reputation event is modelled as a "game" against a neutral reference citizen
  (μ_ref=0, φ_ref=0). Outcome s=1.0 for positive weight, s=0.0 for negative weight.

Contract surface:
  - ReputationScore (immutable, backward-compat snapshot — from reputation_scores VIEW)
  - ReputationState  (immutable, kernel-private — includes σ)
  - get_score()       — reads reputation_scores VIEW (backward-compat, masks σ)
  - get_state_raw()   — reads reputation_state TABLE (kernel-private, exposes σ)
  - get_recent_events() — reads reputation_events (unchanged)
  - can_claim()       — eligibility check for §16 matchmaking
  - recompute_reputation_scores() — Dreamer hook (ONLY write path to reputation_state)

DB: psycopg2 (sync) against MIRROR_DATABASE_URL or DATABASE_URL.

ON CONFLICT NOTE (Athena G10 RESHAPE 1 — preserved for G14):
  Two partial unique indexes, not one UNIQUE:
    idx_rep_state_unique_global  WHERE guild_scope IS NULL  → (holder_id, kind)
    idx_rep_state_unique_scoped  WHERE guild_scope IS NOT NULL → (holder_id, kind, guild_scope)
  _upsert_state() has TWO separate paths — never combined. Same PG NULL semantics.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from typing import Literal

import psycopg2
import psycopg2.extras
from pydantic import BaseModel, ConfigDict

log = logging.getLogger(__name__)

# ── Glicko-2 Constants ────────────────────────────────────────────────────────

_MU_0    = 0.0         # initial rating on Glicko-2 scale
_PHI_0   = 2.014732    # initial RD = 350 / 173.7178
_SIGMA_0 = 0.06        # initial volatility (Glickman recommendation)
_TAU     = 0.5         # system constant τ; constrains volatility swing; range [0.3, 1.2]
_K_LCB   = 1.5         # confidence bound multiplier for display score (LCB = μ - k·φ)
_K_UCB   = 1.5         # confidence bound multiplier for cold-start UCB (UCB = μ + k·φ)

# Score-kind → event-type sets that contribute to each dimension (unchanged from G10)
_KIND_EVENTS: dict[str, set[str]] = {
    'reliability': {'task_completed', 'task_failed', 'task_abandoned'},
    'quality':     {'verification_passed', 'verification_failed'},
    'compliance':  {'audit_clean', 'audit_violation'},
    'overall':     {
        'task_completed', 'task_failed', 'task_abandoned',
        'verification_passed', 'verification_failed',
        'audit_clean', 'audit_violation',
        'peer_endorsed', 'peer_flagged',
    },
}

# ── Types ──────────────────────────────────────────────────────────────────────

ScoreKind = Literal['overall', 'reliability', 'quality', 'compliance']


class ReputationScore(BaseModel):
    """
    Immutable snapshot from the reputation_scores VIEW.

    value = μ - 1.5·φ (LCB). decay_factor is 0.0 post-G14 (not applicable in Glicko-2).
    Callers MUST NOT depend on decay_factor ≠ 0 after migration 029.
    """

    model_config = ConfigDict(frozen=True)

    holder_id: str
    score_kind: ScoreKind
    guild_scope: str | None
    value: float
    sample_size: int
    decay_factor: float      # 0.0 post-G14; kept for backward compat
    computed_at: datetime


class ReputationState(BaseModel):
    """
    Kernel-private Glicko-2 state. σ NEVER leaves kernel tier.
    Call-site is responsible for ensuring caller has kernel-level trust (T4).
    """

    model_config = ConfigDict(frozen=True)

    holder_id: str
    kind: ScoreKind
    guild_scope: str | None
    mu: float
    phi: float
    sigma: float             # volatility — kernel-private
    sample_size: int
    last_updated: datetime

    @property
    def lcb(self) -> float:
        """Lower confidence bound: μ - k·φ (displayed score)."""
        return self.mu - _K_LCB * self.phi

    @property
    def ucb(self) -> float:
        """Upper confidence bound: μ + k·φ (cold-start matchmaking)."""
        return self.mu + _K_UCB * self.phi


class ReputationEvent(BaseModel):
    """Immutable snapshot of one reputation_events row (read-only from contract)."""

    model_config = ConfigDict(frozen=True)

    id: int
    holder_id: str
    event_type: str
    weight: float
    guild_scope: str | None
    evidence_ref: str
    recorded_at: datetime


# ── DB connection ──────────────────────────────────────────────────────────────


def _db_url() -> str:
    url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError(
            'MIRROR_DATABASE_URL or DATABASE_URL is not set — '
            'reputation contract cannot connect to Mirror'
        )
    return url


def _connect():
    return psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)


# ── Glicko-2 equations (Glickman 2012) ────────────────────────────────────────


def _g(phi: float) -> float:
    """g-function: g(φ) = 1 / sqrt(1 + 3φ²/π²)."""
    return 1.0 / math.sqrt(1.0 + 3.0 * phi**2 / math.pi**2)


def _E(mu: float, mu_j: float, phi_j: float) -> float:
    """Expected outcome E(s|μ, μ_j, φ_j) = 1 / (1 + exp(-g(φ_j)·(μ - μ_j)))."""
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _new_sigma(
    sigma: float,
    phi: float,
    v: float,
    delta: float,
    tau: float = _TAU,
) -> float:
    """
    Glicko-2 volatility update — §4 Step 5 of Glickman 2012.

    Uses Illinois (regula-falsi) root-finding on f(x) = 0 where
    f(x) encodes the τ-bounded constraint on volatility change.
    τ must be in [0.3, 1.2] per Glickman recommendation.
    Convergence tolerance: 1e-6 (well within double precision for these magnitudes).
    """
    a = math.log(sigma**2)
    eps = 1e-6

    def _f(x: float) -> float:
        ex = math.exp(x)
        numerator = ex * (delta**2 - phi**2 - v - ex)
        denominator = 2.0 * (phi**2 + v + ex) ** 2
        return numerator / denominator - (x - a) / tau**2

    # Initial bracket [A, B] such that f(A)·f(B) < 0
    A = a
    if delta**2 > phi**2 + v:
        B = math.log(delta**2 - phi**2 - v)
    else:
        k = 1
        while _f(a - k * tau) < 0:
            k += 1
        B = a - k * tau

    fA, fB = _f(A), _f(B)

    # Illinois algorithm (avoids slow convergence of bisection)
    for _ in range(100):
        C = A + (A - B) * fA / (fB - fA)
        fC = _f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA /= 2.0
        B, fB = C, fC
        if abs(B - A) < eps:
            break

    return math.exp(B / 2.0)


def _glicko2_update(
    mu: float,
    phi: float,
    sigma: float,
    events: list[dict],
    tau: float = _TAU,
) -> tuple[float, float, float]:
    """
    Glicko-2 closed-form batch update (Glickman 2012 §4).

    Each reputation event is a "game" against a neutral reference citizen
    (μ_ref=0, φ_ref=0 → g=1.0, E=sigmoid(μ)).

    Outcome mapping:
      s = 1.0  for positive-weight events  (citizen performed)
      s = 0.0  for negative-weight events  (citizen underperformed)

    If no events in period: inflate uncertainty (φ grows by σ), state otherwise stable.

    Returns (μ', φ', σ') — the updated posterior.
    """
    if not events:
        # No games in rating period — inflate RD only (§4 Step 6 degenerate case)
        phi_star = math.sqrt(phi**2 + sigma**2)
        return (mu, phi_star, sigma)

    # Reference opponent constants
    mu_ref, phi_ref = 0.0, 0.0
    g_ref = _g(phi_ref)  # = 1.0

    # Per-event expected outcomes and outcomes
    pairs: list[tuple[float, float]] = []  # (s, E)
    for ev in events:
        weight = float(ev['weight'])
        s = 1.0 if weight > 0 else 0.0
        e = _E(mu, mu_ref, phi_ref)   # = 1/(1 + exp(-μ))
        pairs.append((s, e))

    # §4 Step 3: v (estimated variance of performance)
    v = 1.0 / sum(g_ref**2 * e * (1.0 - e) for _, e in pairs)

    # §4 Step 4: Δ (estimated improvement over expected)
    delta = v * sum(g_ref * (s - e) for s, e in pairs)

    # §4 Step 5: σ' (new volatility via Illinois root-finding)
    sigma_new = _new_sigma(sigma, phi, v, delta, tau)

    # §4 Step 6: φ* (pre-rating-period uncertainty inflation)
    phi_star = math.sqrt(phi**2 + sigma_new**2)

    # §4 Step 7: φ', μ' (posterior update)
    phi_new = 1.0 / math.sqrt(1.0 / phi_star**2 + 1.0 / v)
    mu_new  = mu + phi_new**2 * sum(g_ref * (s - e) for s, e in pairs)

    return (mu_new, phi_new, sigma_new)


# ── Reads ──────────────────────────────────────────────────────────────────────


def get_score(
    holder_id: str,
    kind: ScoreKind = 'overall',
    guild_scope: str | None = None,
) -> ReputationScore | None:
    """
    Return the latest derived score for (holder_id, kind, guild_scope).

    Reads from reputation_scores VIEW (value = μ - 1.5·φ LCB).
    Returns None for cold-start citizens — matchmaking treats None as below threshold.
    σ is masked; use get_state_raw() with kernel-tier auth if σ is needed.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            if guild_scope is None:
                cur.execute(
                    """SELECT holder_id, score_kind, guild_scope, value, sample_size,
                              decay_factor, computed_at
                         FROM reputation_scores
                        WHERE holder_id = %s AND score_kind = %s AND guild_scope IS NULL
                        ORDER BY computed_at DESC LIMIT 1""",
                    (holder_id, kind),
                )
            else:
                cur.execute(
                    """SELECT holder_id, score_kind, guild_scope, value, sample_size,
                              decay_factor, computed_at
                         FROM reputation_scores
                        WHERE holder_id = %s AND score_kind = %s AND guild_scope = %s
                        ORDER BY computed_at DESC LIMIT 1""",
                    (holder_id, kind, guild_scope),
                )
            row = cur.fetchone()
    if not row:
        return None
    return ReputationScore(
        holder_id=row['holder_id'],
        score_kind=row['score_kind'],
        guild_scope=row['guild_scope'],
        value=float(row['value']),
        sample_size=row['sample_size'],
        decay_factor=float(row['decay_factor']),
        computed_at=row['computed_at'],
    )


def get_state_raw(
    holder_id: str,
    kind: ScoreKind = 'overall',
    guild_scope: str | None = None,
) -> ReputationState | None:
    """
    Return the raw Glicko-2 state (μ, φ, σ) for a citizen. KERNEL-PRIVATE.

    Call-site MUST enforce kernel-tier (T4) auth before calling this.
    σ is never returned to citizen-facing API surfaces.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            if guild_scope is None:
                cur.execute(
                    """SELECT holder_id, kind, guild_scope, mu, phi, sigma,
                              sample_size, last_updated
                         FROM reputation_state
                        WHERE holder_id = %s AND kind = %s AND guild_scope IS NULL
                        LIMIT 1""",
                    (holder_id, kind),
                )
            else:
                cur.execute(
                    """SELECT holder_id, kind, guild_scope, mu, phi, sigma,
                              sample_size, last_updated
                         FROM reputation_state
                        WHERE holder_id = %s AND kind = %s AND guild_scope = %s
                        LIMIT 1""",
                    (holder_id, kind, guild_scope),
                )
            row = cur.fetchone()
    if not row:
        return None
    return ReputationState(
        holder_id=row['holder_id'],
        kind=row['kind'],
        guild_scope=row['guild_scope'],
        mu=float(row['mu']),
        phi=float(row['phi']),
        sigma=float(row['sigma']),
        sample_size=row['sample_size'],
        last_updated=row['last_updated'],
    )


def get_recent_events(
    holder_id: str,
    limit: int = 50,
    guild_scope: str | None = None,
) -> list[ReputationEvent]:
    """
    Return the most recent reputation_events for holder_id.
    Caller is responsible for RBAC — this function performs no auth check.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            if guild_scope is None:
                cur.execute(
                    """SELECT id, holder_id, event_type, weight, guild_scope,
                              evidence_ref, recorded_at
                         FROM reputation_events
                        WHERE holder_id = %s
                        ORDER BY recorded_at DESC LIMIT %s""",
                    (holder_id, limit),
                )
            else:
                cur.execute(
                    """SELECT id, holder_id, event_type, weight, guild_scope,
                              evidence_ref, recorded_at
                         FROM reputation_events
                        WHERE holder_id = %s AND guild_scope = %s
                        ORDER BY recorded_at DESC LIMIT %s""",
                    (holder_id, guild_scope, limit),
                )
            rows = cur.fetchall()
    return [
        ReputationEvent(
            id=r['id'],
            holder_id=r['holder_id'],
            event_type=r['event_type'],
            weight=float(r['weight']),
            guild_scope=r['guild_scope'],
            evidence_ref=r['evidence_ref'],
            recorded_at=r['recorded_at'],
        )
        for r in rows
    ]


def can_claim(
    holder_id: str,
    quest_id: str,
    *,
    min_overall: float = 0.0,
    guild_scope: str | None = None,
    cooling_off_days: int = 7,
) -> tuple[bool, str]:
    """
    Eligibility check for §16 matchmaking.

    Returns (eligible, reason). reason is empty string when eligible=True.
    Three veto conditions at v1:
      1. recent audit_violation within cooling-off window in scope
      2. overall LCB score below threshold (None treated as below threshold)
      3. no qualifying events in guild scope (if guild_scope provided)

    quest_id is not yet used at v1 — placeholder for Sprint 004 quest spec lookup.
    min_overall compares against LCB value (μ - 1.5·φ from reputation_scores VIEW).
    """
    # Condition 1: recent audit_violation in scope
    with _connect() as conn:
        with conn.cursor() as cur:
            if guild_scope is None:
                cur.execute(
                    """SELECT 1 FROM reputation_events
                        WHERE holder_id = %s
                          AND event_type = 'audit_violation'
                          AND recorded_at >= now() - (%s || ' days')::interval
                        LIMIT 1""",
                    (holder_id, str(cooling_off_days)),
                )
            else:
                cur.execute(
                    """SELECT 1 FROM reputation_events
                        WHERE holder_id = %s
                          AND event_type = 'audit_violation'
                          AND guild_scope = %s
                          AND recorded_at >= now() - (%s || ' days')::interval
                        LIMIT 1""",
                    (holder_id, guild_scope, str(cooling_off_days)),
                )
            if cur.fetchone():
                return (False, 'recent audit_violation in scope; cooling-off period')

    # Condition 2: overall LCB below threshold
    score = get_score(holder_id, 'overall', guild_scope)
    if score is None or score.value < min_overall:
        return (False, 'reputation below threshold for this quest tier')

    # Condition 3: no qualifying events in guild (if scoped)
    if guild_scope is not None and score.sample_size == 0:
        return (False, 'no qualifying events in this guild')

    return (True, '')


def recompute(holder_id: str) -> None:
    """
    Force a synchronous Glicko-2 recompute for one holder.
    Coordinator/admin gate — call-site is responsible for auth check.
    """
    recompute_reputation_scores(holder_id=holder_id)


# ── Internal: reputation_state write path ─────────────────────────────────────


def _fetch_state(conn, holder_id: str, kind: str, guild_scope: str | None) -> dict:
    """
    Fetch current Glicko-2 state for (holder_id, kind, guild_scope).
    Returns default cold-start values if no row exists yet.
    """
    with conn.cursor() as cur:
        if guild_scope is None:
            cur.execute(
                """SELECT mu, phi, sigma, sample_size FROM reputation_state
                    WHERE holder_id = %s AND kind = %s AND guild_scope IS NULL
                    LIMIT 1""",
                (holder_id, kind),
            )
        else:
            cur.execute(
                """SELECT mu, phi, sigma, sample_size FROM reputation_state
                    WHERE holder_id = %s AND kind = %s AND guild_scope = %s
                    LIMIT 1""",
                (holder_id, kind, guild_scope),
            )
        row = cur.fetchone()
    if row:
        return {
            'mu': float(row['mu']),
            'phi': float(row['phi']),
            'sigma': float(row['sigma']),
            'sample_size': row['sample_size'],
        }
    return {'mu': _MU_0, 'phi': _PHI_0, 'sigma': _SIGMA_0, 'sample_size': 0}


def _upsert_state(
    cur,
    holder_id: str,
    kind: str,
    guild_scope: str | None,
    mu: float,
    phi: float,
    sigma: float,
    sample_size: int,
) -> None:
    """
    Upsert a reputation_state row.

    CRITICAL — two separate ON CONFLICT paths per Athena G14 (preserving G10 RESHAPE 1):
    PG NULL != NULL prevents a single UNIQUE constraint from enforcing uniqueness
    on NULL guild_scope rows. Two partial indexes (same pattern as G10) solve this.
    """
    if guild_scope is None:
        cur.execute(
            """INSERT INTO reputation_state
                   (holder_id, kind, guild_scope, mu, phi, sigma, sample_size, last_updated)
               VALUES (%s, %s, NULL, %s, %s, %s, %s, now())
               ON CONFLICT (holder_id, kind) WHERE guild_scope IS NULL
               DO UPDATE SET
                   mu           = EXCLUDED.mu,
                   phi          = EXCLUDED.phi,
                   sigma        = EXCLUDED.sigma,
                   sample_size  = EXCLUDED.sample_size,
                   last_updated = now()""",
            (holder_id, kind, mu, phi, sigma, sample_size),
        )
    else:
        cur.execute(
            """INSERT INTO reputation_state
                   (holder_id, kind, guild_scope, mu, phi, sigma, sample_size, last_updated)
               VALUES (%s, %s, %s, %s, %s, %s, %s, now())
               ON CONFLICT (holder_id, kind, guild_scope) WHERE guild_scope IS NOT NULL
               DO UPDATE SET
                   mu           = EXCLUDED.mu,
                   phi          = EXCLUDED.phi,
                   sigma        = EXCLUDED.sigma,
                   sample_size  = EXCLUDED.sample_size,
                   last_updated = now()""",
            (holder_id, kind, guild_scope, mu, phi, sigma, sample_size),
        )


def _active_holders_last_7_days(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT holder_id FROM reputation_events
                WHERE recorded_at >= now() - interval '7 days'""",
        )
        return [r['holder_id'] for r in cur.fetchall()]


def _guilds_with_events_for_holder(conn, holder_id: str) -> list[str | None]:
    """Return distinct guild_scope values (including None for global) for a holder."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT guild_scope FROM reputation_events
                WHERE holder_id = %s""",
            (holder_id,),
        )
        return [r['guild_scope'] for r in cur.fetchall()]


def _fetch_events_for_holder(conn, holder_id: str, guild_scope: str | None) -> list[dict]:
    with conn.cursor() as cur:
        if guild_scope is None:
            cur.execute(
                """SELECT event_type, weight, recorded_at FROM reputation_events
                    WHERE holder_id = %s AND guild_scope IS NULL""",
                (holder_id,),
            )
        else:
            cur.execute(
                """SELECT event_type, weight, recorded_at FROM reputation_events
                    WHERE holder_id = %s AND guild_scope = %s""",
                (holder_id, guild_scope),
            )
        return [dict(r) for r in cur.fetchall()]


# ── Dreamer recompute hook (the ONLY write path to reputation_state) ──────────


def recompute_reputation_scores(
    holder_id: str | None = None,
    *,
    tau: float = _TAU,
) -> dict[str, int]:
    """
    Dreamer hook — update reputation_state from reputation_events via Glicko-2.

    If holder_id given: recompute only that citizen.
    Else: recompute all citizens with at least one event in the last 7 days.

    Algorithm per (holder_id, kind, guild_scope):
      1. Fetch all events in scope matching the kind's event-type set.
      2. Load existing (μ, φ, σ) from reputation_state (or cold-start defaults).
      3. Apply Glicko-2 closed-form batch update (Glickman 2012 §4).
      4. Upsert updated (μ', φ', σ', sample_size) into reputation_state.
      5. reputation_scores VIEW self-updates (derived from reputation_state, no write needed).

    τ (tau) controls volatility constraint; valid range [0.3, 1.2] per Glickman.
    Returns summary: {'holders': N, 'scores_written': M}
    """
    if not (0.3 <= tau <= 1.2):
        raise ValueError(f'tau={tau} out of Glickman recommended range [0.3, 1.2]')

    stats: dict[str, int] = {'holders': 0, 'scores_written': 0}

    with _connect() as conn:
        if holder_id is not None:
            holders = [holder_id]
        else:
            holders = _active_holders_last_7_days(conn)

        stats['holders'] = len(holders)

        for h in holders:
            guild_scopes = _guilds_with_events_for_holder(conn, h)

            for scope in guild_scopes:
                all_events = _fetch_events_for_holder(conn, h, scope)
                if not all_events:
                    continue

                with conn.cursor() as cur:
                    for kind in ('reliability', 'quality', 'compliance', 'overall'):
                        kind_types = _KIND_EVENTS[kind]
                        kind_events = [e for e in all_events if e['event_type'] in kind_types]

                        state = _fetch_state(conn, h, kind, scope)
                        mu, phi, sigma = state['mu'], state['phi'], state['sigma']

                        mu_new, phi_new, sigma_new = _glicko2_update(
                            mu, phi, sigma, kind_events, tau=tau
                        )

                        _upsert_state(
                            cur, h, kind, scope,   # type: ignore[arg-type]
                            mu_new, phi_new, sigma_new,
                            sample_size=len(kind_events),
                        )
                        stats['scores_written'] += 1

                conn.commit()

    return stats
