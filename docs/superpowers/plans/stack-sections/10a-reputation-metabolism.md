# Section 10a — Reputation Metabolism (extension of §10)

**Author:** Loom
**Date:** 2026-04-25
**Version:** v0.1 (draft on `loom` branch — companion to §10 v1.0)
**Phase:** 5 (carries with §10 metabolic loop)
**Depends on:** Section 10 (engram metabolism — same primitives, different surface), Section 15 (reputation Glicko-2), Section 16 (matchmaking lambda_dna 16D)
**Gate:** Athena
**Owner:** Loom (spec) → Kasra (build)

---

## 0. TL;DR

§10 gives **engrams** a metabolism: decay, corroboration, reinforcement, sporulation. This document extends the same primitives to **reputation** and **lambda_dna** scoring.

Today the substrate updates Glicko-2 reputation immediately on each match outcome and lambda_dna position immediately on each completed quest. There is no inertia. A citizen who wins three matches in a tight window jumps reputation faster than the underlying signal warrants. This is the same shape as the brain dedupe loop (Sprint 005 mid-stream G35) — rapid state change without coherence accumulation.

§10a fixes this by giving reputation and lambda_dna the same mass/decay/reinforcement that §10 gives engrams.

---

## 1. Why extend the metabolism

§10 §1 frames forgetting as constitutional, not a bug. The same rule applies to scoring:

- A citizen's reputation should not jump on a single outlier outcome — it should *settle into* a new value as evidence accumulates.
- A citizen's lambda_dna position should not snap from one cluster to another on a single quest — it should *flow* toward the new region as repeated evidence reinforces the direction.
- A long-stable reputation that suddenly degrades on one bad outcome should not crash — it should *resist* until the new signal is corroborated.

This is the engineering principle: **state change should be proportional to signal, not just to event count.** Without inertia, a substrate is gameable by burst-pattern attacks: rapid wins inflate, rapid losses deflate, both faster than evidence justifies.

---

## 2. Three additions

### 2.1 Reputation inertia (mass term)

On `reputation_state` (§15):

```sql
ALTER TABLE reputation_state ADD COLUMN inertia_factor NUMERIC DEFAULT 1.0;
ALTER TABLE reputation_state ADD COLUMN last_significant_change_at TIMESTAMPTZ;
```

Glicko-2 update is wrapped in a gate:

```python
def update_reputation(citizen_id, outcome):
    current = fetch_state(citizen_id)
    proposed_mu, proposed_phi = glicko2_step(current, outcome)
    delta_mu = abs(proposed_mu - current.mu)

    # Coherence gate (matches CGL hard-gate from §10 mental model)
    if delta_mu < INERTIA_THRESHOLD * current.inertia_factor:
        # Update is below the noise floor — skip
        log_skip(citizen_id, reason="below_inertia_threshold", delta=delta_mu)
        return current

    # Update accepted — reduce inertia (further updates need stronger signal)
    new_inertia = current.inertia_factor * INERTIA_RECOVERY_RATE
    apply(citizen_id, proposed_mu, proposed_phi, new_inertia)
```

`INERTIA_THRESHOLD` and `INERTIA_RECOVERY_RATE` are env-configurable. Start: threshold = 0.05 × σ (5% of confidence interval), recovery_rate = 1.1 (each accepted update reduces inertia 10%, slowly recovering as state settles).

This implements the same hard-gate principle §10 uses on engram weights: **noise-floor updates are skipped, signal updates accumulate.**

### 2.2 Lambda_dna flow (decay + reinforcement)

On `citizen_vectors` (§16) and any future per-citizen lambda_dna columns:

```sql
ALTER TABLE citizen_vectors ADD COLUMN reinforcement_count INT DEFAULT 0;
ALTER TABLE citizen_vectors ADD COLUMN last_reinforced_at TIMESTAMPTZ;
ALTER TABLE citizen_vectors ADD COLUMN flow_inertia NUMERIC DEFAULT 1.0;
```

Updates use exponentially-decaying weighted average instead of replace-and-forget:

```python
def update_lambda_dna(citizen_id, new_evidence_vector, outcome_strength):
    current = fetch_vector(citizen_id)
    # Stronger evidence + more reinforcement → larger step toward new vector
    step_size = OUTCOME_BASE_STEP * outcome_strength / current.flow_inertia
    new_vector = (1 - step_size) * current.vector + step_size * new_evidence_vector
    new_vector = renormalize_to_unit_sphere(new_vector)
    apply(citizen_id, new_vector, current.reinforcement_count + 1, now())
```

Decay applies to dormant citizens via §10 Dreamer's nightly pass:

```python
# In dreamer.update_engram_quality(), add:
def update_lambda_dna_decay(citizen_id):
    days_since_reinforced = (now() - last_reinforced_at).days
    if days_since_reinforced > LAMBDA_DECAY_THRESHOLD_DAYS:
        flow_inertia += LAMBDA_DECAY_RATE
        # Higher inertia means the citizen's vector is "set"
        # — they need stronger evidence to move.
```

Result: an active citizen's vector flows naturally toward where their work positions them. A dormant citizen's vector hardens. A returning dormant citizen needs a few new wins to "warm up" before their position drifts again.

### 2.3 Reinforcement from corroboration (FRC verdict integration)

§15 reputation events (match_outcome, etc.) and §10's classifier corroboration share a primitive: **multiple independent signals confirming the same fact strengthen confidence.** §10a wires reputation to use this:

When `frc_verdicts` (Sprint 005 P0-2b) records a passing FRC veto for a citizen, treat it as a corroboration event for the citizen's lambda_dna position. Decrement `flow_inertia` (vector becomes more responsive — they've been vetted recently). Increment `reinforcement_count`.

This closes a loop: §10's engram corroboration model + §15's reputation events + §16's lambda_dna position become **one metabolic system**, not three independent scoring channels.

---

## 3. Not in §10a scope

- Renaming or refactoring Glicko-2. The Glicko-2 step itself is unchanged — §10a wraps it with the gate.
- Removing `reputation_state` and deriving it from lambda_dna (the "single source of truth" insight from Loom's 2026-04-25 reflection). That is a deeper architectural pass — Sprint 008 candidate.
- Profile primitive surfacing (§11) — Phase 6.

---

## 4. Build sequence

| # | Component | Owner | Effort | Gate |
|---|---|---|---|---|
| 10a.1 | Migration: `reputation_state` inertia columns | Kasra | 0.25d | G55 |
| 10a.2 | Migration: `citizen_vectors` flow + reinforcement columns | Kasra | 0.25d | G56 |
| 10a.3 | `update_reputation` gate logic + skip-below-noise-floor | Kasra | 0.5d | G57 |
| 10a.4 | `update_lambda_dna` decaying weighted average + reinforcement | Kasra | 0.5d | G58 |
| 10a.5 | Dreamer pass: lambda_dna decay for dormant citizens | Kasra | 0.25d | G59 |
| 10a.6 | FRC verdict → reinforcement bridge | Kasra | 0.25d | G60 |
| 10a.7 | Tests + telemetry: skip-rate counter, drift-rate metric | Kasra | 0.5d | none |

Total: ~2.5 engineer-day equivalents. Slots in alongside §10 Stage 5b
(memory metabolism) — same surface, same review session.

---

## 5. Acceptance criteria

1. A citizen with stable reputation receiving one outlier outcome has
   their reputation update *skipped* (verified via skip-rate counter).
2. A citizen receiving three outlier outcomes in a row has the third
   accepted (inertia recovers); reputation moves but slower than the
   raw Glicko-2 step would predict.
3. A dormant citizen (no reinforcement >30 days) has elevated
   `flow_inertia` (>1.5×) and requires stronger evidence to move
   their lambda_dna position.
4. FRC veto pass on a citizen reduces their `flow_inertia` by the
   configured factor; subsequent wins move their vector more readily.
5. No regression on §15 reputation tests or §16 matchmaking tests.

---

## 6. Open questions

- **Q1:** Inertia threshold = 0.05 × σ is a guess. Same status as §10's
  decay constants — needs empirical tuning. Likely two-week observation
  post-ship before locking the constant.
- **Q2:** Does inertia apply to *all* update paths, or only auto-update
  (matchmaker-triggered)? Recommend: yes to all. A human admin manually
  setting reputation should still go through the gate (with a flag to
  override if intentional). Athena calls.
- **Q3:** Single-source-of-truth refactor (reputation derives from
  lambda_dna) — defer to Sprint 008 design pass. §10a does not block
  that future move; both can coexist with §10a being the bridge.

---

## 7. Versioning

| Version | Date       | Change                                              |
|---------|------------|-----------------------------------------------------|
| v0.1    | 2026-04-25 | Initial draft on `loom` branch — Loom autonomous   |
|         |            | extension of §10 metabolic primitives to reputation|
|         |            | and lambda_dna scoring. Same engineering language  |
|         |            | as §10 (decay, inertia, reinforcement). Pending    |
|         |            | Athena gate to fold into Phase 5 or carry to       |
|         |            | Sprint 008.                                         |

---

## 8. Why this matters (one-paragraph rationale)

The brain dedupe loop (Sprint 005 mid-stream G35) and the silent
fail-open patterns we caught at live-flip (Kasra's three contract bugs)
share a shape: **the substrate updates state faster than evidence
justifies.** §10 already names this for engrams (forgetting is the
memory system, not its failure). §10a names it for the scoring layers.
A substrate without inertia is one where rapid bursts overwrite stable
patterns and the system is gameable by tempo. With inertia, the
substrate respects the underlying signal — slow when the world is
noisy, responsive when the world is consistent. That is the same
metabolic principle, applied one layer up.
