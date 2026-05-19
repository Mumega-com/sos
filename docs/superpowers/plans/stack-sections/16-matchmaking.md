# Section 16 — Matchmaking (Routing By Resonance)

**Author:** Loom
**Date:** 2026-04-25
**Version:** v1.1 (G13 GREEN, Sprint 004)
**Phase:** 8 — substrate routes work to itself
**Depends on:** §1A (role registry), §2B.2 (audit chain), §10 (Dreamer / metabolic loop), §11 (citizen profile + lambda_dna 16D vector), §13 (guild), §14 (inventory), §15 (reputation — undergoing Glicko-2 reshape in this sprint)
**Gate:** Athena (G13)
**Owner:** Loom (spec) → Kasra (build) → Athena (schema gate)

---

## 0. TL;DR

Match each open quest to the top-K eligible citizens. **Eligibility is BOOLEAN** (composed from §13 guild + §14 inventory + §15-Glicko reputation thresholds). **Ranking is CONTINUOUS** (16D resonance cosine + reputation Glicko + freshness + workload, blended via multi-objective scalarization). **FRC coherence is a VETO, not a weight** — a match that violates `dS + k·d ln C = 0` beyond a tolerance ε is rejected, not just demoted. **Tier-gated σ-exploration** — T1 quests prefer high-σ citizens for fast Glicko-2 convergence; T3/T4 demand low-σ proven hands.

Routing happens via a **kernel matchmaking tick** (Hungarian assignment every ~30s over the open-quest × eligible-citizen pool) for global optimal allocation, plus a **request-driven greedy path** for immediate matching. Outcomes feed back into citizen vector evolution via `agent_dna.evolve()`.

This is Phase 8 — the substrate stops needing the principal to assign tasks by hand.

---

## 1. Principles (constitutional)

1. **Eligibility is hard, ranking is soft.** No amount of resonance can compensate for missing capability. No amount of reputation can compensate for guild scope mismatch. The kernel rule is non-negotiable.
2. **FRC coherence is a veto.** Coherence math is a constitutional constraint, not a feature weight. A pair that decreases coherence beyond tolerance is rejected even if it scores high on every other dimension.
3. **Resonance is RANK signal, never gate.** 16D cosine similarity orders the eligible pool. It cannot promote an ineligible citizen.
4. **Reputation uncertainty is private.** σ posteriors live in kernel; never exposed as a public leaderboard or "level." Tier-gated exploration is a routing policy, not a user-facing rank.
5. **Anti-gamification structural.** Citizens cannot directly write to their match score. λ_dna is FRC-derived. Reputation is audit-derived. σ requires stake-weighted observation (not pure count). The only way to score higher is to do honest work.
6. **Outcome-grounded learning.** match_history feeds back into citizen vector evolution. The substrate's beliefs about a citizen update from observed work, not declarations.
7. **Two routing surfaces, one ranker.** Request-driven (greedy, low-latency) AND tick-driven (Hungarian, global optimum). Same eligibility filter, same ranking math, different application — quest creators get instant feedback; the substrate gets allocation efficiency.

---

## 2. Components

### 2.1 quest_vectors (KEEP)
Per-quest 16D resonance signature. Auto-extracted via Vertex Flash Lite from quest description, with manual override via quest creator. Stored in Mirror.

### 2.2 match_history (KEEP)
Append-only log of every match decision (offered / accepted / completed / outcome quality). Feeds learning loop.

### 2.3 matchmaking contract module (KEEP)
`sos/contracts/matchmaking.py`. Pure Python (no service required for the contract itself). Composes §13/§14/§15 + reads quest_vectors + emits to match_history.

### 2.4 Kernel matchmaking tick (NEW)
Background service `sos/services/matchmaker.py`. Runs every ~30s. Reads open-quest × eligible-citizen pool. Computes pairwise scores. Runs Hungarian assignment for global optimum. Emits assignments to Squad Service.

### 2.5 Citizen vector evolution hook (KEEP, reuses existing infra)
`agent_dna.py` already has an `evolve()` method. match_history outcomes call it with the relevant skill axes nudged.

### CUT: Auction/bidding mechanism
Out of scope v1. Citizens don't bid on quests. The substrate decides eligibility + ranking; citizens accept or pass. Auction model risks gameability.

### CUT: Public leaderboards / matchmaking rating display
Out of scope. Matchmaker reads μ ± φ; citizens see their own reputation only. No public competition surface.

### CUT: ML-based learned ranker
Out of scope v1. Hand-tuned weights with explicit math beat any learned model at our scale (16D × ~10K × ~1K). Revisit at >100K labeled outcomes.

---

## 3. Data Model

### 3.0 quests

A new lightweight `quests` table in Mirror. Distinct from Squad Service's transient `squad_tasks` (which represent in-flight execution units). A quest is the persistent, postable, claim-eligible work unit that §16 routes; once claimed, a Squad Service task is spawned to execute it. The two are linked by `squad_task_id` once dispatch happens.

```sql
CREATE TABLE quests (
  id                TEXT PRIMARY KEY,                    -- slug, e.g. 'quest:gaf:lead-research-2026-04-25-abc'
  guild_scope       TEXT REFERENCES guilds(id) ON DELETE SET NULL,
  tier              TEXT NOT NULL CHECK (tier IN ('T1','T2','T3','T4')),
  title             TEXT NOT NULL,
  description       TEXT NOT NULL,
  required_capabilities JSONB,                            -- list of capability_kind+ref pairs from §14 inventory
  reputation_lcb_threshold NUMERIC(8,4) NOT NULL DEFAULT 0,
  status            TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','claimed','in_progress','completed','cancelled')),
  squad_task_id     TEXT,                                 -- nullable; set when dispatched to Squad Service
  created_by        TEXT NOT NULL,                        -- principal_id of poster
  created_at        TIMESTAMPTZ DEFAULT now(),
  claimed_at        TIMESTAMPTZ,
  completed_at      TIMESTAMPTZ
);

CREATE INDEX idx_quests_status_tier ON quests(status, tier);
CREATE INDEX idx_quests_guild       ON quests(guild_scope) WHERE status = 'open';
```

### 3.1 quest_vectors

Per Athena G13 BLOCKER 2: pgvector `vector(16)` for ANN performance + JSONB sidecar for readable named-dimension decomposition. The pgvector column is the source of truth for cosine; the JSONB sidecar is human-readable mirror.

```sql
-- Requires pgvector extension (already in Mirror per §1)
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE quest_vectors (
  quest_id        TEXT PRIMARY KEY REFERENCES quests(id) ON DELETE CASCADE,
  -- 16D resonance signature, mirrors profile.lambda_dna_* on citizen side.
  -- Order: [mu, phi, psi, chi, semantic_1..4, functional_1..4, coherence_1..4]
  embedding       vector(16) NOT NULL,
  -- Human-readable decomposition of the same vector for audit + debugging
  named_dims      JSONB NOT NULL,
  source          TEXT NOT NULL CHECK (source IN ('manual','auto-extracted','template')),
  template_id     TEXT,
  computed_at     TIMESTAMPTZ DEFAULT now(),
  recomputed_count INTEGER DEFAULT 0
);

-- HNSW index for fast cosine ANN
CREATE INDEX idx_quest_vectors_cosine ON quest_vectors
  USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_quest_vectors_source ON quest_vectors(source);
```

Citizen-side `lambda_dna` columns on Supabase profiles are similarly normalized into a `vector(16)` for matchmaker reads. Migration step: add `lambda_dna_embedding vector(16)` computed from existing scalar columns.

### 3.2 match_history

```sql
CREATE TABLE match_history (
  id                 BIGSERIAL PRIMARY KEY,
  quest_id           TEXT NOT NULL,                    -- soft ref; quests may complete or be archived
  candidate_id       TEXT NOT NULL,                    -- citizen profile_id
  resonance_score    NUMERIC(5,4) NOT NULL,            -- raw 16D cosine
  reputation_at_match NUMERIC(8,4) NOT NULL,           -- snapshot of μ at decision time
  sigma_at_match     NUMERIC(5,4) NOT NULL,            -- snapshot of σ for σ-exploration analysis
  coherence_factor   NUMERIC(5,4) NOT NULL,            -- FRC guard pass-through (0=vetoed, 1=clean)
  composite_score    NUMERIC(8,4) NOT NULL,            -- final score after multi-objective blend
  rank               INTEGER NOT NULL,                 -- where they ranked in the offer (1, 2, 3...)
  was_offered        BOOLEAN NOT NULL DEFAULT false,
  was_accepted       BOOLEAN,                          -- nullable; null = not yet decided
  was_completed      BOOLEAN,                          -- nullable; null = not yet decided
  outcome_quality    NUMERIC(5,4),                     -- nullable; verification score post-completion
  routing_path       TEXT NOT NULL CHECK (routing_path IN ('request','tick')),
  recorded_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_match_history_quest      ON match_history(quest_id, recorded_at DESC);
CREATE INDEX idx_match_history_candidate  ON match_history(candidate_id, recorded_at DESC);
CREATE INDEX idx_match_history_completion ON match_history(was_completed, outcome_quality)
  WHERE was_completed IS TRUE;
```

---

## 4. Contract Surface (`sos/contracts/matchmaking.py`)

```python
from pydantic import BaseModel
from typing import Literal

class Match(BaseModel):
    quest_id: str
    candidate_id: str
    composite_score: float            # final blended score
    resonance: float                  # raw 16D cosine
    reputation_lcb: float             # μ - k·φ (lower confidence bound — what we trust)
    reputation_ucb: float             # μ + k·φ (upper confidence bound — what's possible)
    sigma: float                      # uncertainty
    coherence_factor: float           # FRC veto pass: 0 (vetoed) or 1 (clean)
    capability_check: bool            # eligibility passed (always true; we'd reject otherwise)
    reason: str                       # explainability text for the user

# Reads — primary API
def find_matches(
    quest_id: str,
    k: int = 5,
    min_resonance: float = 0.4,
    explore_sigma_weight: float = None  # None = use tier-gated default
) -> list[Match]: ...

def can_claim(holder_id: str, quest_id: str) -> tuple[bool, str]:
    """Composed boolean check: guild scope + inventory + reputation tier."""

# Mutations — match outcome recording
def record_match_offered(quest_id: str, candidates: list[Match], routing_path: str) -> None: ...
def record_match_accepted(quest_id: str, candidate_id: str) -> None: ...
def record_match_outcome(
    quest_id: str,
    candidate_id: str,
    completed: bool,
    outcome_quality: float | None = None
) -> None: ...

# Quest-vector helpers
def get_quest_vector(quest_id: str) -> dict | None: ...
def auto_extract_quest_vector(quest_id: str, description: str) -> dict: ...  # Vertex Flash Lite call

# Kernel tick
def matchmaking_tick(max_quests: int = 100) -> dict:
    """Hungarian assignment over (open_quests × eligible_citizens) pool.
       Called every ~30s by sos.services.matchmaker systemd timer.
       Returns dict with stats: {quests_processed, matches_assigned, vetoed_pairs}."""
```

---

## 5. Algorithm

### 5.1 Stage 1 — Eligibility filter (kernel, boolean)

For each (quest, candidate) pair, all of the following must hold:

```python
def is_eligible(quest, candidate) -> bool:
    if not guild.assert_member(quest.guild_scope, candidate.id):
        return False
    if not inventory.assert_capabilities(candidate.id, quest.required_capabilities):
        return False
    rep = reputation.get_score(candidate.id, kind='overall', guild_scope=quest.guild_scope)
    if rep is None:
        return quest.tier in ('T1', 'T2')  # cold-start: T1/T2 only
    threshold = quest.reputation_lcb_threshold(quest.tier)
    return (rep.mu - rep.phi * 1.5) >= threshold  # lower confidence bound
```

This cuts ~10K candidates → ~50–200 typically.

### 5.2 Stage 2 — FRC coherence veto (v1: work-quality gate)

Per Athena G13 BLOCKER 1: the full ΔS + k·d ln C entropy-prediction math requires equations not yet derived for the (quest_vector, citizen_vector) pair. The A7 FRC overlay (shipped Sprint 003) computes κ alignment + W witness + four-failure-mode taxonomy on classifier output (a *ClassificationResult*), not on hypothetical pair entropy.

**v1 scope:** veto is grounded in *observed* FRC verdicts on the candidate's recent work, not predicted pair entropy.

```python
def coherence_check_v1(candidate_id: str, lookback_days: int = 30) -> tuple[bool, float]:
    """Reject candidate if their last K FRC verdicts include a 'failed' verdict
    in the lookback window. Compose with §15 reputation: an audit_violation
    in the lookback window also fails."""
    recent_verdicts = frc.get_recent_verdicts(candidate_id, lookback_days)
    if any(v.verdict == 'failed' for v in recent_verdicts):
        return False, 0.0
    if any(v.verdict == 'marginal' for v in recent_verdicts):
        return True, 0.7   # demoted but eligible
    return True, 1.0
```

Pairs failing the work-quality gate are removed from the pool. This composes with Sprint 003's A7 FRC overlay output (already kernel-derived signal) without adding new math.

**v2 scope (Sprint 005 candidate):** full pair-entropy veto — requires `predict_entropy_change(quest_v, citizen_v)` and `predict_ln_coherence_change(quest_v, citizen_v)` equations. Logged as G13b future gate. The current `coherence_factor` field on `match_history` is forward-compatible with both v1 and v2.

### 5.3 Stage 3 — Cosine resonance ranking

```python
def cosine_16d(quest_v, citizen_v) -> float:
    """Fast cosine over 16D vectors. ~3K multiply-adds per pair, sub-millisecond."""
    return np.dot(quest_v, citizen_v) / (np.linalg.norm(quest_v) * np.linalg.norm(citizen_v))
```

For 200 surviving candidates × 16D, full pairwise scoring is ~3K multiply-adds — sub-millisecond with NumPy or pgvector's `<=>` operator.

### 5.4 Stage 4 — Multi-objective scalarization

Per Athena G13 SOFT A: weights are hardcoded as v0 defaults, in spec, auditable. Coordinator can override via config; no hidden state.

```python
TIER_WEIGHTS = {
    'T1': {  # trivial / reversible / low-stakes
        'resonance':   0.30,   # less weight on perfect fit; T1 is also exploration territory
        'reputation':  0.10,   # cold-start tolerance — give new citizens a chance
        'freshness':   0.20,   # rotate quests so fresh work doesn't stale
        'workload':    0.10,   # mild penalty for over-allocated citizens
        'exploration': 0.30,   # high σ-exploration weight — info gain
    },
    'T2': {  # standard work
        'resonance':   0.40,
        'reputation':  0.25,
        'freshness':   0.15,
        'workload':    0.10,
        'exploration': 0.10,
    },
    'T3': {  # stakeholder-visible / multi-citizen / harder to reverse
        'resonance':   0.40,
        'reputation':  0.40,   # proven hands matter more
        'freshness':   0.10,
        'workload':    0.05,
        'exploration': 0.05,   # σ-exploration nearly off
    },
    'T4': {  # constitutional / irreversible / customer-binding
        'resonance':   0.30,
        'reputation':  0.55,   # reputation dominates
        'freshness':   0.05,
        'workload':    0.05,
        'exploration': 0.05,   # σ-exploration off
    },
}

def composite_score(
    cosine: float,
    rep_lcb: float,
    sigma: float,
    workload: float,
    freshness: float,
    coherence_factor: float,
    quest_tier: str,
) -> float:
    # Tier-gated weights — explicit, auditable, tunable
    weights = TIER_WEIGHTS[quest_tier]

    # σ-exploration term: T1/T2 prefer high σ (uncertainty = info gain).
    # T3/T4 prefer low σ (proven hands). Spec lock: σ never returned to citizens; this is kernel-internal.
    sigma_term = sigma if quest_tier in ('T1', 'T2') else (1.0 - sigma)

    return (
        weights['resonance']   * cosine
      + weights['reputation']  * rep_lcb
      + weights['freshness']   * freshness
      - weights['workload']    * workload
      + weights['exploration'] * sigma_term
    ) * coherence_factor       # FRC veto already filtered; this softly rewards higher coherence
```

Weights subject to revision once match_history has ~10K outcomes; until then, hand-tuned values above are the gate-approved baseline.

### 5.5 Stage 5 — Top-K and deterministic exploration (v1)

For request-driven matches, return top-K by composite_score. For tick-driven matches, run Hungarian assignment over the (quest, candidate) score matrix to find globally optimal assignment.

Per Athena G13 SOFT B: Thompson sampling deferred to v2 (needs match_history calibration data). v1 uses **deterministic exploration**: simpler, auditable, no hidden Beta posterior state.

For 1-of-K slots (e.g., k=5, reserve 1 slot for an under-exposed citizen):

```python
def select_explore_candidate(
    eligible_pool: list[Match],
    history: MatchHistory,
    lookback_days: int = 7,
    min_offers_threshold: int = 1,
) -> Match | None:
    """Reserve the K-th slot for the candidate with the fewest match offers
    in the lookback window. Tie-break by longest-since-offered. Returns
    None if every candidate already has min_offers_threshold offers."""
    candidates_with_counts = [
        (c, history.offers_count(c.candidate_id, lookback_days))
        for c in eligible_pool
    ]
    under_exposed = [c for c, n in candidates_with_counts if n < min_offers_threshold]
    if not under_exposed:
        return None
    # Tie-break: longest-since-last-offered
    under_exposed.sort(key=lambda c: history.last_offered_at(c.candidate_id))
    return under_exposed[0]

def assemble_top_k(
    eligible_pool: list[Match],
    k: int = 5,
    history: MatchHistory = None,
) -> list[Match]:
    eligible_pool.sort(key=lambda m: m.composite_score, reverse=True)
    top_minus_one = eligible_pool[:k-1]
    explore = select_explore_candidate(eligible_pool, history) if history else None
    if explore and explore not in top_minus_one:
        return top_minus_one + [explore]
    return eligible_pool[:k]
```

v2 (Sprint 005+, after ~10K outcomes): Thompson sampling over Beta posteriors per (citizen, skill-axis) — kernel-private state, perturbs composite_score on selection. Logged as future enhancement.

### 5.6 Stage 6 — Match offer → outcome recording

```python
def offer_match(quest_id, candidates: list[Match], routing_path: str):
    record_match_offered(quest_id, candidates, routing_path)
    notify_quest_owner(candidates)
    # Citizens accept or pass via Squad Service routes
    # outcome_quality recorded post-completion via verification
    # citizen vector .evolve() called from match_history trigger
```

---

## 6. RBAC Tier Mapping

`tier='role'` with `permitted_roles=['coordinator','quality_gate']`. Citizens see their own match offers + outcomes; they don't see other citizens' scores or rankings. Coordinators see across guild scope (composes with §13).

| View | Public | Self | Coordinator | Quality_gate |
|---|---|---|---|---|
| Get own match offers | — | ✓ | ✓ | ✓ |
| Get all match history for a quest | — | own only | ✓ | ✓ |
| Get composite_score breakdown for own match | — | ✓ | ✓ | ✓ |
| Get composite_score breakdown for any match | — | own only | ✓ | ✓ |
| Trigger matchmaking_tick manually | — | — | ✓ | ✓ |

---

## 7. Composition with Existing Primitives

**§13 guild:** `assert_member(quest.guild_scope, candidate)` — eligibility gate.
**§14 inventory:** `assert_capabilities(candidate, quest.required_capabilities)` — eligibility gate.
**§15 reputation (Glicko-2 reshape this sprint):** `get_score(candidate, kind, guild_scope)` returns `(μ, φ, σ)`. Eligibility uses LCB; ranking uses LCB + σ-exploration term.
**§11 profile:** `lambda_dna_*` columns (already on Supabase profiles + agent QNFT 488/16D) ARE the citizen vector. No new column required.
**§2B.2 audit chain:** every match offered/accepted/completed emits an `audit_event`. match_history is the operational log; audit chain is the immutable trail.
**§10 metabolic loop / Dreamer:** Glicko-2 RD inflation on inactivity + match_history outcomes feed Dreamer's reputation recompute.
**`agent_dna.evolve()`:** existing function in `mirror/agent_dna.py`. match_history calls it on completion to nudge citizen vectors.

---

## 8. Open Questions

1. **Quest tier definition.** v1 assumes T1/T2/T3/T4. Spec these in §3 (structured records) — or as a separate Sprint 004 item. Recommend: T1 = trivial (any agent), T2 = standard, T3 = stakeholder-visible, T4 = constitutional/irreversible.
2. **Cold-start on quest vectors.** Vertex Flash Lite auto-extraction may produce noisy vectors for thin descriptions. Recommend: require manual or template signature for T3/T4; auto-extracted is fine for T1/T2.
3. **Hungarian tick frequency.** v1 = 30s. May need to adapt — too slow → quest creators lose responsiveness; too fast → wasted compute when pool unchanged. Recommend: dynamic ticker that fires on pool-change events (new quest posted, citizen state change).
4. **Coherence tolerance ε.** Hardcoded 0.15 in spec. May need per-quest-tier values or per-guild tuning. Recommend: start at 0.15 globally, instrument violation rates, tune from data.
5. **Auto-decline / quest expiry.** What happens if no eligible citizens exist? v1 = surface to coordinator. Recommend: post-MVP, allow quest creator to relax constraints (lower reputation threshold, broader guild scope) with explicit consent.

---

## 9. Test Plan

| Test | Pass Condition |
|---|---|
| Eligibility hard gate | Citizen missing capability → returns 0 from is_eligible regardless of resonance |
| Reputation cold start | New citizen with no `reputation_state` row → only T1/T2 eligibility |
| FRC coherence veto | Pair with synthetic high-cosine but `coherence_violation > 0.15` → not in match list |
| Coherence factor pass-through | Pair with `coherence_violation = 0.05` → composite_score scaled by ~0.67 (1 - 0.05/0.15) |
| Cosine 16D math | Citizen vector identical to quest vector → cosine = 1.0; orthogonal → 0.0; opposed → -1.0 |
| Tier-gated σ-exploration | Same citizen rank, different quest tier (T1 vs T3) → different relative rank when high-σ candidates compete |
| Hungarian tick | 5 quests × 5 candidates with intentional optimal assignment → tick produces it |
| Greedy vs tick coverage | Random 100 quests through both paths → tick assigns globally optimal more often than greedy (measurable) |
| Match outcome learning | record_match_outcome with quality=1.0 → next call to evolve() shows citizen vector nudged toward quest vector |
| Audit chain integration | Each match action (offered/accepted/completed) → corresponding audit_events row + reputation_event |
| Cold-start cosine | Auto-extracted quest vector + new citizen lambda_dna → returns valid cosine; doesn't crash on default values |
| Anti-gamification: forced low-σ rotation | Same citizen offered for 5 consecutive quests → workload penalty + Thompson exploration push the offer away |

---

## 10. Migration / Rollout

1. Athena gates G13 (this spec).
2. Athena gates G14 (Glicko-2 §15 reshape — separate spec).
3. Athena schema migration: `quest_vectors` + `match_history` tables.
4. Kasra builds matchmaking.py contract module.
5. Kasra wires Hungarian tick service.
6. Kasra wires auto-extraction (Vertex Flash Lite call).
7. Backfill: existing quests get vectors auto-extracted; existing citizens already have lambda_dna.
8. Soft launch on T1/T2 quests only; observe match_history outcomes for ~1 week.
9. Promote to T3 once stability + outcome quality verified.

---

## 11. Versioning

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-04-25 | Initial draft post-research-convergence. Architecture: cosine 16D + FRC veto + Glicko-2 reputation + tier-gated σ-exploration + Hungarian tick. Sprint 004 Track A. |
| v1.1 | 2026-04-25 | Athena G13 patches applied: (1) quests table added as §3.0 (was missing FK target); (2) quest_vectors switched to pgvector vector(16) + JSONB sidecar for readability + HNSW cosine index (was decomposed NUMERIC columns conflicting with pgvector alternative); (3) FRC coherence veto v1 scoped to work-quality gate via Sprint 003 A7 verdicts (was hand-waved entropy prediction); (4) TIER_WEIGHTS spec-defined with v0 values per tier (was implicit); (5) deterministic exploration via offer-count + longest-since-offered (was Thompson sampling with undefined Beta posterior state). v2 entropy-veto + v2 Thompson sampling logged for Sprint 005+. G13 expected full GREEN post-patch. |
