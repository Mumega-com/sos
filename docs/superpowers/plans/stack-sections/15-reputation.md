# Section 15 — Reputation (Computed, Time-Decayed Trust Score)

**Author:** Loom
**Date:** 2026-04-25
**Version:** v1.0 (draft)
**Phase:** 8 — substrate primitives
**Depends on:** §1A (role registry), §2B.2 (audit chain), §10 (Dreamer / metabolic loop), §11 (citizen profile), §13 (guild), §14 (inventory)
**Gate:** Athena
**Owner:** Loom (spec) → Kasra (build) → Athena (schema gate + trigger function)

---

## 0. TL;DR

Reputation is a **scored, time-decayed measure of trustworthiness** computed from audit-chain evidence. It is not a feeling, not a vote, not stars-out-of-five. It is the substrate's belief about a citizen's reliability, derived from their actual work record, recomputed periodically by Dreamer, and used as input to matchmaking eligibility (§16, Sprint 004).

**Two tables:** `reputation_events` (append-only source, written by trigger from audit_events on a strict whitelist of action types) + `reputation_scores` (Dreamer-materialized snapshots with decay applied).

**Three constitutional locks:**
1. Audit chain is the only source. No direct writes to reputation_events from application code.
2. Reputation_scores is a materialized table, not a view. Dreamer controls timing.
3. No XP/levels/quest vocabulary. Scores are honest: completion rate, verification pass rate, audit cleanliness.

---

## 1. Principles (constitutional)

1. **Earned, not granted.** Reputation cannot be set manually. It accrues from audit events. There is no admin override; only event corrections (which themselves audit-log).
2. **Decayed.** Recent events count more than old ones. Reputation answers "trustworthy now," not "trustworthy ever." Dreamer applies exponential decay in the recompute pass.
3. **Honest dimensions.** Three score kinds at v1: `reliability` (do they finish?), `quality` (does the work pass verification?), `compliance` (do they stay clean of audit violations?). An overall score is a weighted blend, but raw dimensions remain queryable.
4. **Guild-scoped.** Reputation can be global or guild-scoped. A founder of Mumega Inc. is not automatically a known quantity in AgentLink. Same citizen, different reputation contexts.
5. **No direct application writes.** Application code emits audit events. The trigger function `audit_to_reputation()` is the only writer to `reputation_events`. Application reads `get_score()`; never writes.
6. **Anti-gamification structural.** Kernel does not expose XP totals, levels, badges, or "achievements." It exposes raw rates. Inkwell hosts may render them as anything; kernel keeps the receipts honest.

---

## 2. Components

### 2.1 reputation_events (KEEP)
Append-only event log. One row per qualifying audit event. Triggered automatically.

### 2.2 reputation_scores (KEEP)
Materialized snapshot. Dreamer-recomputed (period configurable, default 1 hour). Per (holder_id, score_kind, guild_scope) tuple.

### 2.3 audit_to_reputation trigger (KEEP)
PG trigger function. AFTER INSERT ON audit_events. Filters action against whitelist. Inserts into reputation_events with the action's predefined weight.

### 2.4 Dreamer recompute hook (KEEP)
Existing Dreamer (§10) gets a new task: `recompute_reputation_scores()`. Walks reputation_events, applies exponential decay, recomputes scores per (holder, kind, guild) tuple.

### CUT: Manual peer reviews / star ratings
Out of scope v1. Subjective reputation is a different beast (and a gameable one). Stick to objective audit-derived scores until the event taxonomy is rich enough.

### CUT: Reputation transfer between citizens
Out of scope v1. Reputation is non-fungible. No "lend my rep to my apprentice" mechanism.

### CUT: Reputation slashing for whole-guild violations
Out of scope v1. A guild violation slashes the guild's reputation, not its members'. Members can be individually penalized via their own audit_violation events.

---

## 3. Data Model

### 3.1 reputation_events

```sql
CREATE TABLE reputation_events (
  id              BIGSERIAL PRIMARY KEY,
  holder_id       TEXT NOT NULL,                        -- profile_id of the citizen receiving the rep impact
  event_type      TEXT NOT NULL CHECK (event_type IN (
                    'task_completed','task_failed','task_abandoned',
                    'verification_passed','verification_failed',
                    'audit_clean','audit_violation',
                    'peer_endorsed','peer_flagged'
                  )),
  weight          NUMERIC(6,3) NOT NULL,                -- positive or negative; magnitude per event_type
  guild_scope     TEXT REFERENCES guilds(id) ON DELETE SET NULL,  -- nullable; null = global
  evidence_ref    TEXT NOT NULL,                        -- audit_events.id (for traceability)
  recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_rep_events_holder       ON reputation_events(holder_id, recorded_at DESC);
CREATE INDEX idx_rep_events_guild        ON reputation_events(guild_scope, recorded_at DESC) WHERE guild_scope IS NOT NULL;
CREATE INDEX idx_rep_events_type         ON reputation_events(event_type, recorded_at DESC);
```

### 3.2 reputation_scores

```sql
CREATE TABLE reputation_scores (
  id                BIGSERIAL PRIMARY KEY,
  holder_id         TEXT NOT NULL,
  score_kind        TEXT NOT NULL CHECK (score_kind IN ('overall','reliability','quality','compliance')),
  guild_scope       TEXT REFERENCES guilds(id) ON DELETE SET NULL,
  value             NUMERIC(8,4) NOT NULL,              -- final computed score (range typically -100..100, but unbounded)
  sample_size       INTEGER NOT NULL,                   -- count of events feeding this score
  decay_factor      NUMERIC(5,4) NOT NULL,              -- the half-life decay constant used
  computed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per Athena G10 RESHAPE 1: PG NULL != NULL for unique constraints, so a single UNIQUE on
-- (holder_id, score_kind, guild_scope) does NOT prevent duplicate global rows. Two partial
-- unique indexes — one for NULL, one for NOT NULL — are the correct enforcement.
CREATE UNIQUE INDEX idx_rep_scores_unique_global
  ON reputation_scores(holder_id, score_kind) WHERE guild_scope IS NULL;
CREATE UNIQUE INDEX idx_rep_scores_unique_scoped
  ON reputation_scores(holder_id, score_kind, guild_scope) WHERE guild_scope IS NOT NULL;

CREATE INDEX idx_rep_scores_holder       ON reputation_scores(holder_id, score_kind);
CREATE INDEX idx_rep_scores_guild        ON reputation_scores(guild_scope, score_kind) WHERE guild_scope IS NOT NULL;
```

### 3.3 Trigger function

```sql
-- Maps audit event types to reputation event_types + weights.
-- Weights extracted as CONSTANT declarations per Athena: tuning later = 9 line edits, no logic change.
-- Weight tuning by Athena (more conservative on penalties to avoid scoring volatility):
-- Per Athena G10 RESHAPE 2: SECURITY DEFINER required.
-- App role has been REVOKED INSERT on reputation_events. Without SECURITY DEFINER the trigger
-- runs as the invoking app role and throws permission_denied — failing both the audit insert
-- AND the reputation insert. SECURITY DEFINER escalates to function owner (table owner) just
-- for this trigger's writes. Owner must hold INSERT on reputation_events.
CREATE OR REPLACE FUNCTION audit_to_reputation() RETURNS TRIGGER
  SECURITY DEFINER
  SET search_path = public, pg_temp                    -- per PG security best-practice for SECURITY DEFINER
AS $$
DECLARE
  -- Weights — single source of truth. Tune here without touching CASE logic.
  w_task_completed       CONSTANT NUMERIC :=  1.0;
  w_task_failed          CONSTANT NUMERIC := -0.8;
  w_task_abandoned       CONSTANT NUMERIC := -0.3;
  w_verification_passed  CONSTANT NUMERIC :=  1.5;
  w_verification_failed  CONSTANT NUMERIC := -1.2;
  w_audit_clean          CONSTANT NUMERIC :=  0.2;
  w_audit_violation      CONSTANT NUMERIC := -3.0;
  w_peer_endorsed        CONSTANT NUMERIC :=  0.5;
  w_peer_flagged         CONSTANT NUMERIC := -1.0;

  v_event_type TEXT;
  v_weight NUMERIC;
  v_guild TEXT;
BEGIN
  -- Whitelist check + weight assignment
  CASE NEW.action
    WHEN 'task_completed'      THEN v_event_type := 'task_completed';      v_weight := w_task_completed;
    WHEN 'task_failed'         THEN v_event_type := 'task_failed';         v_weight := w_task_failed;
    WHEN 'task_abandoned'      THEN v_event_type := 'task_abandoned';      v_weight := w_task_abandoned;
    WHEN 'verification_passed' THEN v_event_type := 'verification_passed'; v_weight := w_verification_passed;
    WHEN 'verification_failed' THEN v_event_type := 'verification_failed'; v_weight := w_verification_failed;
    WHEN 'audit_clean'         THEN v_event_type := 'audit_clean';         v_weight := w_audit_clean;
    WHEN 'audit_violation'     THEN v_event_type := 'audit_violation';     v_weight := w_audit_violation;
    WHEN 'peer_endorsed'       THEN v_event_type := 'peer_endorsed';       v_weight := w_peer_endorsed;
    WHEN 'peer_flagged'        THEN v_event_type := 'peer_flagged';        v_weight := w_peer_flagged;
    ELSE RETURN NEW;  -- not a reputation-relevant event; skip
  END CASE;

  -- Infer guild_scope from resource pattern.
  -- CONTRACT: emit_audit() must format resource as 'guild:{slug}:{resource_type}:{id}' OR
  --   non-guild-scoped as anything that doesn't match the prefix. Drift in this format
  --   silently returns NULL guild_scope (rep event becomes global). Test coverage required.
  v_guild := substring(NEW.resource from '^guild:([a-z0-9-]+):');

  INSERT INTO reputation_events (holder_id, event_type, weight, guild_scope, evidence_ref, recorded_at)
  VALUES (NEW.actor_id, v_event_type, v_weight, v_guild, NEW.id::TEXT, NEW.ts);

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_to_reputation
  AFTER INSERT ON audit_events
  FOR EACH ROW
  EXECUTE FUNCTION audit_to_reputation();
```

### 3.4 Dreamer recompute hook

```python
async def recompute_reputation_scores(holder_id: str | None = None):
    """
    Walk reputation_events, apply exponential decay, materialize reputation_scores.

    If holder_id given: recompute only that citizen.
    Else: recompute all citizens with at least one event in the last 7 days.

    Decay: weight_decayed = weight * exp(-Δt / half_life_days)
    Default half_life = 30 days.
    """
    targets = [holder_id] if holder_id else _active_holders_last_7_days()
    for h in targets:
        for kind in ('reliability','quality','compliance','overall'):
            for guild in _guilds_with_events_for_holder(h):
                value, n = _decayed_sum(h, kind, guild, half_life_days=30)
                pg.execute("""
                    INSERT INTO reputation_scores (holder_id, score_kind, guild_scope, value, sample_size, decay_factor, computed_at)
                    VALUES ($1, $2, $3, $4, $5, $6, now())
                    ON CONFLICT (holder_id, score_kind, guild_scope) DO UPDATE SET
                      value = EXCLUDED.value,
                      sample_size = EXCLUDED.sample_size,
                      decay_factor = EXCLUDED.decay_factor,
                      computed_at = now()
                """, h, kind, guild, value, n, 30.0)
```

Score-kind composition:
- `reliability` = sum of decayed weights from {task_completed, task_failed, task_abandoned}
- `quality` = sum from {verification_passed, verification_failed}
- `compliance` = sum from {audit_clean, audit_violation}
- `overall` = weighted blend (0.4·reliability + 0.4·quality + 0.2·compliance) + 0.5·peer_endorsed - 0.5·peer_flagged

---

## 4. Contract Surface (`sos/contracts/reputation.py`)

```python
from pydantic import BaseModel
from datetime import datetime
from typing import Literal

class ReputationScore(BaseModel):
    holder_id: str
    score_kind: Literal['overall','reliability','quality','compliance']
    guild_scope: str | None
    value: float
    sample_size: int
    computed_at: datetime

# Reads
def get_score(
    holder_id: str,
    kind: str = 'overall',
    guild_scope: str | None = None
) -> ReputationScore | None: ...

def get_recent_events(
    holder_id: str,
    limit: int = 50,
    guild_scope: str | None = None
) -> list[dict]: ...

# Eligibility — used by §16 matchmaking
def can_claim(
    holder_id: str,
    quest_id: str
) -> tuple[bool, str]:
    """
    Returns (eligible, reason). reason is human-readable when False:
    'reputation below threshold for this quest tier'
    'recent audit_violation in scope; cooling-off period'
    'no qualifying events in this guild'
    """

# Forced recompute (admin/coordinator)
def recompute(holder_id: str) -> None: ...

# NO write API. Reputation accrues from audit events only.
# (Test code may use _test_seed_event() in test fixtures; production has no entry point.)
```

---

## 5. RBAC Tier Mapping

`tier='role'` with `permitted_roles=['coordinator','quality_gate','self']`.

| View | Public | Self | Same-guild member | Coordinator | Quality_gate (Athena) |
|---|---|---|---|---|---|
| Get own score | — | ✓ | — | ✓ | ✓ |
| Get any score | — | own only | — | ✓ | ✓ |
| List recent events for self | — | ✓ | — | ✓ | ✓ |
| can_claim eligibility check | — | ✓ | — | ✓ | ✓ |

Reputation scores are sensitive — leak risk creates incentive to game them. Tight visibility by design. Customers / partners / public **never** see raw reputation. They may see derived signals via Inkwell hosts ("verified contributor", "high-reliability builder") with no underlying numbers.

---

## 6. Composition with Existing Primitives

**§2B.2 audit chain:** the trigger feeds reputation_events. Audit chain is the source of truth; reputation derives.

**§10 Dreamer:** recompute_reputation_scores() is registered as a periodic Dreamer task. Reuse the existing event-trigger-on-hot-store-threshold + nightly batch infrastructure.

**§13 guild:** reputation_events.guild_scope nullable FK to guilds. Allows guild-scoped queries. Guild dissolution sets scope to null (preserves history; just degrades to global).

**§14 inventory:** doesn't directly modify reputation. But inventory is read by matchmaking; reputation is also read by matchmaking; both gate eligibility independently.

**§16 matchmaking (Sprint 004):** `can_claim(holder_id, quest_id)` reads reputation as eligibility input. A quest spec includes minimum reputation thresholds; matchmaking filters candidates accordingly.

---

## 7. Decay Model

Default: exponential decay with 30-day half-life.

```
weight_decayed(event) = event.weight * exp(- (now - event.recorded_at) / (30 * 86400) * ln(2))
```

A `task_completed` event from 30 days ago contributes 0.5 of its original weight. From 90 days ago, 0.125. From 1 year ago, ~0.0001 (effectively zero).

Per-event-type half-life can be configured later (e.g., audit_violations decay slower than task_completed). v1 uses one universal half-life. Sprint 004+.

---

## 8. Open Questions

1. **Cold-start citizens.** A new citizen has no events → no reputation_scores row → `get_score` returns None. Should that be treated as zero, or as "unknown"? Recommend: return None; matchmaking treats None as "below threshold" for reputation-required quests, "neutral" for entry-level quests.
2. **Bot vs human weight.** Should an AI agent's task_completed count the same as a human's? Recommend: yes at v1 — both produce work output; let weights stay equal. Revisit if data shows divergent quality.
3. **Reputation transfer on guild merge.** If two guilds merge (hypothetical Sprint 004+ feature), do reputation_events.guild_scope rewrite? Recommend: no; events keep their original scope; queries can union both via parent_guild_id chain.
4. **Audit event back-dating.** If an old audit event surfaces (e.g., a deferred verification result), should it backfill reputation? Recommend: yes via trigger; Dreamer recompute picks up the new row in next cycle. But back-dated events use their original `ts` for decay calc, not `now()`.

---

## 9. Test Plan

| Test | Pass Condition |
|---|---|
| Trigger fires on whitelisted action | INSERT INTO audit_events with action='task_completed' → reputation_events row created with weight=1.0 |
| Trigger skips non-whitelisted action | INSERT with action='created' or 'viewed' → no reputation_events row |
| Guild scope inferred | audit_events.resource='guild:mumega-inc:task:abc' → reputation_events.guild_scope='mumega-inc' |
| Global scope when no guild | resource='task:standalone' → reputation_events.guild_scope=NULL |
| Dreamer recompute | Insert 10 events for holder X over last 7 days → recompute_reputation_scores('X') → 4 score rows (one per kind) materialized |
| Decay applied | Two events of same weight, one 60d old, one today → 60d-old event contributes 0.25× its weight (60d = 2 half-lives) |
| Get score returns latest | Two recompute_scores calls → get_score returns the more recent computed_at |
| Tier-gated read | Citizen calls get_score for another citizen → returns nothing (None) unless caller is coordinator/quality_gate |
| can_claim positive | Citizen with overall ≥ threshold → can_claim returns (True, '') |
| can_claim negative — low score | Citizen with overall < threshold → can_claim returns (False, 'reputation below threshold for this quest tier') |
| can_claim negative — recent violation | audit_violation in last 7d for scope → can_claim returns (False, 'recent audit_violation in scope; cooling-off period') |
| No write path | `INSERT INTO reputation_events ...` from app code is forbidden via REVOKE INSERT on app role; only trigger function (run as table owner) can write |

---

## 10. What This Unlocks

After §15 ships:

- **Substrate-routed work.** §16 matchmaking has the input it needs to filter eligible citizens.
- **Honest credibility.** "Verified high-reliability builder for Mumega Inc." is a kernel-derived signal, not a marketing claim.
- **Self-correcting workforce.** A citizen whose verification rate drops triggers automatic eligibility downgrades. They keep working but on lower-stakes quests until they rebuild rep.
- **Trust audits for partners.** When AgentLink wants to vet a Mumega citizen for a joint engagement, they can request a reputation snapshot (with citizen's consent). Honest receipts.
- **No popularity contests.** Reputation isn't peer voting; it's audit-derived. Liked-by-others doesn't matter; works-and-passes-verification does.

---

## 11. Versioning

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-04-25 | Initial draft. Strict event-type whitelist per Athena G10 pre-question. Sprint 003 Track C. |
| v1.1 | 2026-04-25 | Athena pre-question follow-up: weights extracted as CONSTANT NUMERIC declarations at top of trigger function (9-line edit surface for future tuning). Athena's weight tuning adopted (more conservative penalties). |
| v1.2 | 2026-04-25 | Athena G10 reshapes applied: (1) UNIQUE NULL semantics — replaced single UNIQUE with two partial unique indexes (idx_rep_scores_unique_global WHERE guild_scope IS NULL, idx_rep_scores_unique_scoped WHERE NOT NULL). (2) audit_to_reputation() declared SECURITY DEFINER + SET search_path; required because app role is REVOKED INSERT on reputation_events. (3) Resource naming contract documented as test-required: emit_audit must format `guild:{slug}:{type}:{id}`. G10 APPROVED post-reshape. |
