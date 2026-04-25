# E2E Test Plan — Sprint 004 Substrate
**Author:** Athena (Quality Gate)  
**Date:** 2026-04-25  
**Status:** DRAFT — awaiting Kasra implementation  
**Directive:** Hadi via Loom — enterprise-grade E2E validation before matchmaker goes live

---

## 0. Purpose

Validate the full Sprint 004 §16 matchmaking substrate end-to-end on a controlled synthetic dataset before:
- Flipping `MATCHMAKER_DRY_RUN=0` in production
- Opening Sprint 005

The test covers the complete Phase 8 cycle: quest creation → candidate scoring → Hungarian assignment → outcome injection → learning loop → second tick operating on updated state → audit chain reconstruction.

A parallel adversarial subagent (dispatched by Loom) independently probes for gameability/exploitation. Their findings + this E2E result jointly govern the go-live decision.

---

## 1. Scope

| # | Area | Depth |
|---|------|-------|
| 1 | Stage 1 eligibility | guild / capability / reputation LCB per tier |
| 2 | Stage 2 FRC veto | failed→exclude, degraded→0.7, cold-start→1.0 |
| 3 | Stage 3 cosine | rank order vs manual calculation |
| 4 | Stage 4 scalarization | composite scores vs TIER_WEIGHTS literal calculation |
| 5 | Stage 5 exploration | fewest-offers pick + tie-break |
| 6 | Hungarian assignment | globally optimal vs greedy baseline |
| 7 | Outcome injection | 8 outcomes (mix of accepted/rejected/abandoned) |
| 8 | Learning loop | event emit, Glicko-2 math, vector nudge, idempotency |
| 9 | Second tick | operates on updated state |
| 10 | Audit chain | full cycle reconstruction |
| 11 | Performance budget | p50/p99 latency, memory delta, CPU peak |

---

## 2. Fixtures

### 2.1 Guilds (2)

```sql
INSERT INTO guilds (id, name, tier_floor) VALUES
  ('guild:alpha', 'Alpha Guild', 'T1'),
  ('guild:beta',  'Beta Guild',  'T2');
```

### 2.2 Citizen Pool (N=10)

| ID | μ | φ | LCB | Guild | Capabilities | Vector | Notes |
|----|---|---|-----|-------|--------------|--------|-------|
| `cit:01` | 0.0 | 2.014732 | -3.02 | none | none | none | Cold-start — no state, no vector |
| `cit:02` | 0.0 | 2.014732 | -3.02 | alpha | none | yes | T1-eligible, alpha member |
| `cit:03` | 1.5 | 1.2 | -0.30 | alpha,beta | cap:audit | yes | T2-eligible |
| `cit:04` | 2.0 | 0.8 | 0.80 | alpha,beta | cap:audit,cap:review | yes | T3-eligible |
| `cit:05` | 3.5 | 0.6 | 2.60 | alpha,beta | cap:audit,cap:review,cap:sign | yes | T4-eligible, reputation dominant |
| `cit:06` | 1.0 | 1.5 | -1.25 | alpha | cap:audit | yes | FRC-failed (synthetic verdict) |
| `cit:07` | 1.0 | 1.5 | -1.25 | alpha | cap:audit | yes | FRC-degraded (synthetic verdict) |
| `cit:08` | 2.0 | 0.8 | 0.80 | none | cap:audit,cap:review | yes | Not member of guild:beta (Stage 1 fail for guild-scoped quests) |
| `cit:09` | 0.5 | 2.0 | -2.50 | alpha,beta | none | yes | Missing capabilities (Stage 1 fail for capped quests) |
| `cit:10` | 1.0 | 1.2 | -0.80 | alpha,beta | cap:audit | yes | T2-eligible, has 2 prior offers (exploration score lower) |

LCB formula: `μ - 1.5·φ`

**reputation_state inserts (per citizen, kind='overall', guild_scope=NULL):**

```python
# cit:01 — no row (cold-start)
# cit:02
(holder_id='cit:02', kind='overall', guild_scope=None, mu=0.0,  phi=2.014732, sigma=0.06, sample_size=0)
# cit:03
(holder_id='cit:03', kind='overall', guild_scope=None, mu=1.5,  phi=1.2,      sigma=0.05, sample_size=8)
# cit:04
(holder_id='cit:04', kind='overall', guild_scope=None, mu=2.0,  phi=0.8,      sigma=0.04, sample_size=15)
# cit:05
(holder_id='cit:05', kind='overall', guild_scope=None, mu=3.5,  phi=0.6,      sigma=0.03, sample_size=24)
# cit:06
(holder_id='cit:06', kind='overall', guild_scope=None, mu=1.0,  phi=1.5,      sigma=0.05, sample_size=6)
# cit:07
(holder_id='cit:07', kind='overall', guild_scope=None, mu=1.0,  phi=1.5,      sigma=0.05, sample_size=6)
# cit:08
(holder_id='cit:08', kind='overall', guild_scope=None, mu=2.0,  phi=0.8,      sigma=0.04, sample_size=15)
# cit:09
(holder_id='cit:09', kind='overall', guild_scope=None, mu=0.5,  phi=2.0,      sigma=0.06, sample_size=3)
# cit:10
(holder_id='cit:10', kind='overall', guild_scope=None, mu=1.0,  phi=1.2,      sigma=0.05, sample_size=5)
```

**guild_members inserts:**
```sql
-- cit:02: alpha only
INSERT INTO guild_members (guild_id, member_id, rank) VALUES ('guild:alpha', 'cit:02', 'member');
-- cit:03, cit:04, cit:05, cit:06, cit:07, cit:09, cit:10: both guilds
-- cit:08: no guild_members rows (will fail guild-scoped quests for guild:beta)
```

**inventory_grants inserts (kind='capability'):**
```sql
-- cap:audit  → cit:02(no), cit:03(yes), cit:04(yes), cit:05(yes), cit:06(yes), cit:07(yes), cit:10(yes)
-- cap:review → cit:04(yes), cit:05(yes), cit:08(yes)
-- cap:sign   → cit:05(yes)
```

**Synthetic FRC verdicts — mirror_engrams rows for cit:06 and cit:07:**
```sql
-- cit:06: failed verdict (confidence=0.30 in classifier_run_log)
INSERT INTO mirror_engrams (owner_id, content, timestamp, classifier_run_log)
VALUES ('cit:06', 'synthetic', now() - interval '5 days',
        '[{"pass_number":1,"confidence":0.30,"parse_error":false}]');

-- cit:07: degraded verdict (confidence=0.60)
INSERT INTO mirror_engrams (owner_id, content, timestamp, classifier_run_log)
VALUES ('cit:07', 'synthetic', now() - interval '5 days',
        '[{"pass_number":1,"confidence":0.60,"parse_error":false}]');
```

**citizen_vectors (16D, representative values):**

| Citizen | Vector pattern | Notes |
|---------|---------------|-------|
| cit:01 | none | Cold-start |
| cit:02 | `[0.2]*8 + [0.8]*8` | Low first-8, high last-8 |
| cit:03 | `[0.5]*16` | Neutral uniform |
| cit:04 | `[0.7, 0.8, 0.6, 0.7, 0.8, 0.6, 0.7, 0.8, 0.3, 0.2, 0.4, 0.3, 0.2, 0.4, 0.3, 0.2]` | High dims 0-7 |
| cit:05 | `[0.9]*8 + [0.1]*8` | Strongly aligned to first-8 quests |
| cit:06 | `[0.5]*16` | Neutral (FRC-failed, will be vetoed) |
| cit:07 | `[0.5]*16` | Neutral (FRC-degraded, 0.7 multiplier) |
| cit:08 | `[0.7]*16` | High alignment (excluded by guild for guild:beta quests) |
| cit:09 | `[0.4]*16` | Moderate (excluded by missing caps) |
| cit:10 | `[0.6]*16` | Moderate, already offered twice |

### 2.3 Quest Pool (N=20)

5 quests per tier. Fixture strategy: vary guild_scope and required_capabilities to exercise each Stage 1 failure path.

| ID | Tier | Guild | Required Caps | Quest vector | Notes |
|----|------|-------|---------------|--------------|-------|
| `q:t1-global-01` | T1 | none | none | `[0.8]*8+[0.2]*8` | Global, no caps — most citizens eligible |
| `q:t1-global-02` | T1 | none | none | `[0.2]*8+[0.8]*8` | Opposite alignment to q:t1-global-01 |
| `q:t1-alpha-01`  | T1 | alpha | none | `[0.5]*16` | Guild-scoped: only alpha members |
| `q:t1-alpha-02`  | T1 | alpha | [cap:audit] | `[0.6]*16` | Guild + capability |
| `q:t1-global-03` | T1 | none | [cap:audit] | `[0.7]*16` | Caps required: excludes cit:01,02,08(no-cap),09 |
| `q:t2-global-01` | T2 | none | none | `[0.9]*8+[0.1]*8` | LCB ≥ -2.0; excludes cit:01,02 (LCB=-3.02) |
| `q:t2-global-02` | T2 | none | [cap:audit] | `[0.5]*16` | T2 + caps |
| `q:t2-beta-01`   | T2 | beta | none | `[0.6]*8+[0.4]*8` | Beta guild; excludes cit:02,cit:08 |
| `q:t2-beta-02`   | T2 | beta | [cap:audit] | `[0.4]*16` | Beta + caps |
| `q:t2-global-03` | T2 | none | [cap:review] | `[0.8]*16` | cap:review: only cit:04,05,08 have it |
| `q:t3-global-01` | T3 | none | none | `[0.8]*8+[0.2]*8` | LCB ≥ 0.0; only cit:04,05,08 qualify |
| `q:t3-global-02` | T3 | none | [cap:audit] | `[0.7]*16` | T3 + cap:audit |
| `q:t3-alpha-01`  | T3 | alpha | none | `[0.6]*16` | T3 alpha: cit:04,05 (cit:08 not in alpha) |
| `q:t3-alpha-02`  | T3 | alpha | [cap:audit,cap:review] | `[0.9]*8+[0.1]*8` | Both caps + alpha guild |
| `q:t3-global-03` | T3 | none | [cap:review] | `[0.5]*16` | T3 + cap:review |
| `q:t4-global-01` | T4 | none | none | `[0.9]*8+[0.1]*8` | LCB ≥ 1.5; only cit:05 qualifies |
| `q:t4-global-02` | T4 | none | [cap:sign] | `[0.8]*16` | T4 + cap:sign: only cit:05 |
| `q:t4-alpha-01`  | T4 | alpha | [cap:sign] | `[0.7]*16` | T4 + alpha + cap:sign: only cit:05 |
| `q:t4-global-03` | T4 | none | none | `[0.1]*8+[0.9]*8` | T4, opposite alignment to cit:05 — tests reputation dominance at T4 |
| `q:t4-global-04` | T4 | none | none | `[0.5]*16` | T4 neutral — reputation score dominates (weight=0.55) |

**Synthetic prior offers for cit:10 (exploration test):**
```sql
-- 2 prior offers for cit:10 on q:t1-global-01
INSERT INTO match_history (quest_id, candidate_id, composite_score, offer_count, outcome, outcome_at)
VALUES
  ('q:t1-global-01', 'cit:10', 0.5, 1, 'abandoned', now() - interval '10 days'),
  ('q:t1-global-01', 'cit:10', 0.5, 2, 'rejected',  now() - interval '5 days');
```

---

## 3. Test Cases

### TC-01: Stage 1 Eligibility

**Precondition:** Fixtures loaded (§2). Run `rank_candidates('q:t1-alpha-02', all_citizens)`.

**Pass criteria:**

| Citizen | Expected Stage 1 result | Reason |
|---------|------------------------|--------|
| cit:01  | FAIL | LCB=-∞ (no state) < T1=-4.0 |
| cit:02  | FAIL | missing cap:audit |
| cit:03  | PASS | alpha member + cap:audit + LCB=-0.30 > -4.0 |
| cit:04  | PASS | all conditions met |
| cit:05  | PASS | all conditions met |
| cit:06  | PASS (Stage 1); FAIL Stage 2 | alpha + cap:audit — Stage 2 vetoes |
| cit:07  | PASS (Stage 1); degraded Stage 2 | same |
| cit:08  | FAIL | not alpha member |
| cit:09  | FAIL | missing cap:audit |
| cit:10  | PASS | alpha + cap:audit + LCB=-0.80 > -4.0 |

For `q:t3-alpha-02` (T3, alpha, [cap:audit, cap:review]):

| Citizen | Expected |
|---------|----------|
| cit:04  | PASS (LCB=0.80 ≥ 0.0, alpha, both caps) |
| cit:05  | PASS |
| cit:08  | FAIL (not alpha member) |
| all others | FAIL (LCB too low or missing caps) |

**Verification:** Assert `ineligible` list contains expected citizens with matching `stage1_reason` substring.

---

### TC-02: Stage 2 FRC Veto

**Precondition:** Fixtures loaded. Run `rank_candidates('q:t1-global-01', ['cit:06','cit:07','cit:02','cit:03'])`.

**Pass criteria:**

| Citizen | Expected | Verification |
|---------|----------|-------------|
| cit:06  | In `ineligible` (frc_score=0.0) | `stage1_passed=True`, `frc_score=0.0` |
| cit:07  | In `ranked` with `composite_score = raw_composite × 0.7` | frc_score=0.7 |
| cit:02  | In `ranked` with `frc_score=1.0` | No verdict → cold-start 1.0 |
| cit:03  | In `ranked` with `frc_score=1.0` | Same |

**Manual verification for cit:07:** Compute `raw_composite` using T1 TIER_WEIGHTS and assert `composite_score == raw_composite * 0.7` (tolerance 1e-6).

---

### TC-03: Stage 3 Cosine — Rank Order

**Precondition:** Run `rank_candidates('q:t1-global-01', eligible_citizens)` where `q:t1-global-01` has vector `[0.8]*8+[0.2]*8`.

**Manual cosine calculation (reference):**

For each citizen vector `v_c` against quest vector `v_q = [0.8]*8 + [0.2]*8`:

- cit:02: `v=[0.2]*8+[0.8]*8` → dot = 8×(0.2×0.8) + 8×(0.8×0.2) = 1.28+1.28 = 2.56; norm_c=√(8×0.04+8×0.64)=√5.44≈2.332; norm_q=√(8×0.64+8×0.04)=√5.44≈2.332; raw=2.56/5.44≈0.471; shifted=(0.471+1)/2≈0.735
- cit:05: `v=[0.9]*8+[0.1]*8` → dot=8×(0.9×0.8)+8×(0.1×0.2)=5.76+0.16=5.92; norm_c=√(8×0.81+8×0.01)=√6.56≈2.561; norm_q≈2.332; raw=5.92/(2.561×2.332)≈0.991; shifted≈0.996
- cit:04: `v=high dims 0-7` → closer to quest vector (high first-8) → resonance > cit:03

**Pass criteria:** `ranked` order by resonance dimension aligns with manual rank within Stage 4 weight tolerance. Specifically: `cit:05.resonance_score > cit:04.resonance_score > cit:03.resonance_score`.

**Cold-start check:** `cit:01` (no vector) gets `resonance_score=0.5` (neutral).

---

### TC-04: Stage 4 Scalarization — Composite Verification

**Precondition:** Pick one eligible citizen per tier quest. Compute expected composite manually.

**T3 example — `q:t3-global-01`, `cit:04`:**

TIER_WEIGHTS T3: `resonance=0.40, reputation=0.40, freshness=0.10, workload=0.05, exploration=0.05`

1. `resonance_score`: cosine(cit:04.vector, q:t3-global-01.vector) — compute manually (see §TC-03 method)
2. `reputation_score`: T3 uses LCB. `state.lcb = 2.0 - 1.5×0.8 = 0.80`. `sigmoid(0.80/3.0) = 1/(1+e^{-0.267}) ≈ 0.566`
3. `freshness_score`: no prior offers → `1.0`
4. `workload_score`: `_fetch_active_assignment_count` = 0 → `1.0`
5. `exploration_score`: `_fetch_last_offered` = 0 offers → `1.0`
6. `raw_composite` = 0.40×res + 0.40×0.566 + 0.10×1.0 + 0.05×1.0 + 0.05×1.0 = 0.40×res + 0.3764
7. `frc_score` = 1.0 (no verdicts) → `composite = raw_composite`

**Pass criteria:** `abs(result.ranked[i].composite_score - expected) < 1e-4` for each verified citizen.

**Weight sum invariant:** For all quests: `sum(TIER_WEIGHTS[tier].values()) == 1.0` (assert at fixture-load time).

**T4 reputation dominance check (`q:t4-global-03`, opposite vector `[0.1]*8+[0.9]*8`):**
- cit:05 vector `[0.9]*8+[0.1]*8` is OPPOSITE to quest → low resonance ≈ 0.004 (shifted cosine)
- But T4 TIER_WEIGHTS: `reputation=0.55, resonance=0.30`
- `reputation_score(cit:05)` = sigmoid(LCB/3.0) = sigmoid(2.60/3.0) ≈ 0.701 (uses LCB for T4)
- Expected composite ≈ 0.30×0.004 + 0.55×0.701 + 0.10×fresh + 0.05×work + 0.05×expl ≈ domination by reputation
- Assert: cit:05 composite_score > 0.35 despite low resonance (reputation weight dominates at T4)

---

### TC-05: Stage 5 Deterministic Exploration

**Precondition:** `q:t1-global-01` has 2 prior offers for cit:10 (from fixture §2.3). Citizens cit:02, cit:03, cit:10 all eligible.

**Pass criteria:**
1. `explore_candidate_id != 'cit:10'` — cit:10 has highest offer_count (2), should not be explore pick
2. `explore_candidate_id` is cit:02 or cit:03 (offer_count=0 for both)
3. Run with cit:02 also having 1 prior offer injected → assert `explore_candidate_id == 'cit:03'` (fewest offers)
4. Tie-break: if cit:02 and cit:03 both have 0 offers but cit:02 was last offered more recently → assert `explore_candidate_id == 'cit:03'` (longer since last offered)
5. Re-run after cit:02 and cit:03 both offered once, same `last_offered` → stable sort means highest composite (cit:03 if rep ≥ cit:02) is picked — verify against pre-computed composites

---

### TC-06: Hungarian Assignment — Optimal vs Greedy

**Precondition:** Subset the pool: 4 global T1 quests × 4 eligible citizens (cit:02, cit:03, cit:07, cit:10). Compute the 4×4 composite matrix manually.

**Greedy baseline:** For each quest in created_at order, assign the highest-scoring unassigned candidate.

**Hungarian result:** `scipy.optimize.linear_sum_assignment(-matrix)`.

**Pass criteria:**
1. Total assignment cost (sum of composites) for Hungarian ≥ greedy total (or equal when greedy is already optimal).
2. For a designed asymmetric matrix where greedy is provably suboptimal, assert Hungarian achieves strictly higher total.
3. **Designed suboptimal case:**
```
matrix (4 quests × 4 candidates):
           cit:02  cit:03  cit:07  cit:10
q:t1-01:  [0.90,   0.50,   0.35,   0.40]   # q1 strongly prefers cit:02
q:t1-02:  [0.60,   0.85,   0.35,   0.40]   # q2 strongly prefers cit:03
q:t1-03:  [0.55,   0.55,   0.70,   0.40]   # q3 prefers cit:07
q:t1-04:  [0.50,   0.50,   0.40,   0.80]   # q4 prefers cit:10
```
Greedy: q1→cit:02(0.90), q2→cit:03(0.85), q3→cit:07(0.70), q4→cit:10(0.80) = 3.25 (happens to be optimal here)
Use a case where greedy fails: swap q1/q2 preferences and make q3 prefer cit:02 heavily.
Assert `sum(Hungarian scores) >= sum(greedy scores)` with at least one case where `>`.

---

### TC-07: Outcome Injection

**Precondition:** Run one full tick. Record all `match_id` values from `record_assignment()` calls.

**Inject 8 outcomes (2-3 per outcome type):**

| match_id | Candidate | Quest | Outcome | Expected reputation event |
|----------|-----------|-------|---------|--------------------------|
| M1 | cit:03 | q:t1-global-01 | accepted | task_completed, weight=+1.0 |
| M2 | cit:04 | q:t2-global-01 | accepted | task_completed, weight=+1.0 |
| M3 | cit:05 | q:t4-global-01 | accepted | task_completed, weight=+1.0 |
| M4 | cit:02 | q:t1-alpha-01  | rejected | task_failed, weight=-1.0 |
| M5 | cit:10 | q:t1-global-02 | rejected | task_failed, weight=-1.0 |
| M6 | cit:03 | q:t2-beta-01   | abandoned | task_abandoned, weight=-0.5 |
| M7 | cit:04 | q:t3-global-01 | abandoned | task_abandoned, weight=-0.5 |
| M8 | cit:01 | q:t1-global-01 | accepted | task_completed, +1.0 + cold-start seed |

(M8 uses cit:01 who has no prior state or vector — tests cold-start path end-to-end.)

**record_outcome() calls:**
```python
from sos.contracts.matchmaking import record_outcome
record_outcome(M1, 'accepted')
record_outcome(M2, 'accepted')
# ... etc
```

---

### TC-08: Learning Loop — process_outcomes()

**Precondition:** TC-07 outcomes injected, `reputation_processed_at IS NULL` on all 8 rows.

**Pass criteria:**

**8a. Stats return:**
```python
stats = process_outcomes()
assert stats['processed'] == 8
assert stats['errors'] == 0
assert stats['vector_updates'] >= 3  # M1, M2, M3, M8 seeding + M4, M5 rejected nudges
```

**8b. Glicko-2 math — cit:03 post-accepted:**

Pre-state: `μ=1.5, φ=1.2, σ=0.05, sample_size=8`
Event: `task_completed, weight=+1.0` → s=1.0

Expected update (Glickman 2012 §4):
- g(φ_ref=0) = 1.0
- E = 1/(1 + exp(-1×(1.5 - 0))) = 1/(1+exp(-1.5)) ≈ 0.8176
- v = 1 / (1²×0.8176×0.1824) ≈ 6.711
- Δ = v × 1×(1.0 - 0.8176) ≈ 1.224
- σ' = _new_sigma(0.05, 1.2, 6.711, 1.224, τ=0.5)  [Illinois root-finding]
- φ* = √(1.2² + σ'²)
- φ' = 1/√(1/φ*² + 1/v)
- μ' = 1.5 + φ'² × (1.0 - 0.8176)

Assert: `abs(new_state.mu - expected_mu) < 0.01` and `new_state.phi < 1.2` (uncertainty decreases on new data).

**8c. Vector nudge — cit:03 (accepted q:t1-global-01):**

Pre-vector: `cit:03 = [0.5]*16`
Quest vector: `q:t1-global-01 = [0.8]*8 + [0.2]*8`
Expected: `v_new[i] = 0.5 + 0.1×(q[i] - 0.5)`
→ dims 0-7: `0.5 + 0.1×0.3 = 0.53`
→ dims 8-15: `0.5 + 0.1×(-0.3) = 0.47`

Assert: `new_citizen_vector[0:8] ≈ 0.53 ± 1e-9` and `new_citizen_vector[8:16] ≈ 0.47 ± 1e-9`.

**8d. Cold-start seed — cit:01 (accepted q:t1-global-01):**

No prior citizen_vector. Quest vector: `[0.8]*8 + [0.2]*8`
Expected seed: `[0.4]*8 + [0.1]*8`

Assert: `citizen_vectors row for cit:01` exists post-process with values `[0.4]*8 + [0.1]*8 ± 1e-9`.

**8e. Abandoned — no vector nudge (cit:03, M6):**

M6 outcome is abandoned. cit:03 already has a vector from M1. After process_outcomes, cit:03's vector should reflect only the M1 nudge — M6 must not further modify it.

Assert: `citizen_vectors[cit:03]` unchanged from M1 nudge state after M6 processing.

**8f. Idempotency:**

Re-run `process_outcomes()` after all 8 outcomes processed.
```python
stats2 = process_outcomes()
assert stats2['processed'] == 0
assert stats2['errors'] == 0
```
Query: `SELECT COUNT(*) FROM match_history WHERE reputation_processed_at IS NULL AND outcome IS NOT NULL` → 0.

**8g. evidence_ref traceability:**

For each processed row, assert a `reputation_events` row exists with `evidence_ref = f'match:{match_id}'`.
```sql
SELECT COUNT(*) FROM reputation_events WHERE evidence_ref LIKE 'match:%';
-- Must equal number of processed outcomes (8)
```

---

### TC-09: Second Tick — Updated State

**Precondition:** TC-08 complete. reputation_state updated for cit:01, cit:03, cit:04, cit:05 (accepted), cit:02, cit:10 (rejected).

**Pass criteria:**

**9a. LCB shifts:** For accepted citizens, LCB increases (μ↑ or φ↓). For rejected, LCB decreases.
```python
# Pre-tick-2 LCBs (after learning loop):
# cit:03: was μ=1.5, φ=1.2, LCB=-0.30. Post-accepted: μ↑, φ↓ → LCB higher
# cit:02: was μ=0.0, φ=2.014, LCB=-3.02. Post-rejected: μ↓ → LCB lower
for cit_id, expected_direction in [('cit:03', '>'), ('cit:02', '<')]:
    pre_lcb = lcb_before_tick_2[cit_id]
    post_lcb = get_state_raw(cit_id, 'overall', None).lcb
    assert (post_lcb > pre_lcb) if expected_direction == '>' else (post_lcb < pre_lcb)
```

**9b. Rank order shift:** cit:03 rank improves vs cit:02 on a shared T1 global quest between tick 1 and tick 2 (better reputation → higher Stage 4 score).

**9c. exploration_score for cit:03:** After M1 assignment recorded in match_history, cit:03's `exploration_score` for `q:t1-global-01` = `1/(1+1) = 0.5` (was 1.0 in tick 1). Verify in tick-2 CandidateScore.

---

### TC-10: Audit Chain Reconstruction

**Precondition:** Full cycle complete (TC-01 through TC-09).

**Pass criteria:**

Walk `audit_events` for every action in the cycle that emits an audit record.

**Expected minimum audit records:**
1. Guild membership grants (cit:02..cit:10 added to guilds) — `event_kind = 'guild_member_add'` or equivalent
2. Inventory grants — `event_kind = 'inventory_grant'` 
3. Reputation events → audit chain entries per G10 design
4. Quest creation — if quests are created via a guarded path

**Full chain integrity check:**
```python
from sos.kernel.audit_chain import reconstruct_chain
chain = reconstruct_chain(chain_id='sprint004-e2e', limit=200)
# Assert: each event's prev_hash matches the prior event's hash
# Assert: no gaps in sequence
# Assert: chain starts from known genesis event
```

**Traceability spot-check:**
- Pick 3 reputation_events by `evidence_ref` (e.g., `match:M1`, `match:M2`, `match:M3`)
- Walk backward: `reputation_events.evidence_ref` → `match_history` → `match_history.quest_id` → `quests`
- Assert full chain reconstructable with no broken foreign keys

---

### TC-11: Performance Budget

**Measurement method:** `time.perf_counter()` around `run_tick()`.

**Fixture size for perf test:** 20 quests × 10 citizens (full fixture as defined in §2).

**Budget thresholds:**

| Metric | Budget | Rationale |
|--------|--------|-----------|
| tick p50 latency | < 2.0s | 30s timer, pipeline overhead < 7% |
| tick p99 latency (5 runs) | < 5.0s | DB jitter tolerance |
| memory delta | < 100MB | One-shot process; no leaks expected |
| CPU peak (single tick) | < 80% single core | scipy Hungarian on 20×10 is O(n³) trivial |

**Measurement script:**
```python
import time, tracemalloc
times = []
for _ in range(5):
    tracemalloc.start()
    t0 = time.perf_counter()
    stats = run_tick()
    elapsed = time.perf_counter() - t0
    _, mem_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    times.append((elapsed, mem_peak))

p50 = sorted(times)[2][0]
p99 = max(t[0] for t in times)
mem_max = max(t[1] for t in times) / 1e6  # MB
print(f"p50={p50:.3f}s p99={p99:.3f}s mem_peak={mem_max:.1f}MB")
assert p50 < 2.0, f"p50 budget exceeded: {p50:.3f}s"
assert p99 < 5.0, f"p99 budget exceeded: {p99:.3f}s"
assert mem_max < 100, f"memory budget exceeded: {mem_max:.1f}MB"
```

---

## 4. Pass / Fail Summary Table

| TC | Name | Pass Condition | Hard/Soft |
|----|------|---------------|-----------|
| TC-01 | Stage 1 eligibility | All 10 citizens correctly classified per fixture | **Hard** |
| TC-02 | Stage 2 FRC veto | cit:06 vetoed, cit:07 degraded at 0.7× | **Hard** |
| TC-03 | Stage 3 cosine rank | Rank order matches manual calc ± float tolerance | **Hard** |
| TC-04 | Stage 4 composite | All composites within 1e-4 of manual calculation | **Hard** |
| TC-05 | Stage 5 exploration | Explore pick = fewest-offers citizen; tie-break works | **Hard** |
| TC-06 | Hungarian optimality | Hungarian ≥ greedy for all cases; > for suboptimal case | **Hard** |
| TC-07 | Outcome injection | 8 outcomes recorded, reputation_processed_at NULL | **Hard** |
| TC-08a | process_outcomes stats | processed=8, errors=0 | **Hard** |
| TC-08b | Glicko-2 math | μ', φ' within 0.01 of manual Glickman §4 calculation | **Hard** |
| TC-08c | Vector nudge math | New vector within 1e-9 of analytic formula | **Hard** |
| TC-08d | Cold-start seed | cit:01 seeded at 0.5× quest_vec | **Hard** |
| TC-08e | No abandoned nudge | cit:03 vector unchanged after abandoned outcome | **Hard** |
| TC-08f | Idempotency | Re-run returns processed=0, errors=0 | **Hard** |
| TC-08g | evidence_ref trace | 8 reputation_events rows with match:* refs | **Hard** |
| TC-09a | LCB direction | Accepted→LCB up, rejected→LCB down | **Hard** |
| TC-09b | Rank order shift | Tick 2 ranking reflects updated reputation state | Hard |
| TC-09c | Exploration decay | cit:03 exploration_score = 0.5 in tick 2 | Hard |
| TC-10 | Audit chain integrity | No hash gaps; spot-check traces cleanly | **Hard** |
| TC-11 | Performance budget | p50 < 2s, p99 < 5s, mem < 100MB | **Soft** (advisory) |

Total hard gates: 18. All must pass for matchmaker go-live sign-off.

---

## 5. Known Gaps (from Gate Reviews)

These are pre-existing soft notes. Tests should document but not block on:

1. **G17 constitutional flag**: `_emit_reputation_event()` writes directly to `reputation_events` bypassing audit chain. Verify `evidence_ref` present (TC-08g). Sprint 005 G17b required.
2. **G16 soft note 4**: Verify scipy + numpy installed: `python3 -c "from scipy.optimize import linear_sum_assignment; import numpy"` before test run.
3. **G16 soft note 1**: `_fetch_candidate_pool` LIMIT without ORDER BY. TC-11 notes pool non-determinism as advisory.
4. **G15 soft note 1**: Quest status stays `'open'` after assignment (contract doesn't update it). Tick 2 may re-assign same quests — expected and acceptable for E2E test.

---

## 6. Test Execution

### Prerequisites
```bash
# Verify services
systemctl --user status sos-engine  # must be active
psql $MIRROR_DATABASE_URL -c "SELECT version();"
python3 -c "from scipy.optimize import linear_sum_assignment; import numpy; print('scipy ok')"

# Apply any pending migrations
cd ~/mirror && python migrate.py status

# Run in DRY_RUN mode throughout E2E test
export MATCHMAKER_DRY_RUN=1
```

### Fixture setup
```bash
cd ~/SOS && python3 tests/e2e/setup_sprint004_fixtures.py
# Kasra to write: loads all §2 fixtures into mirror DB
# Must be idempotent (INSERT ... ON CONFLICT DO NOTHING or fixture-keyed IDs)
```

### Run order
```bash
pytest tests/e2e/test_sprint004_e2e.py -v --tb=short 2>&1 | tee /tmp/e2e_sprint004.log
```

### Teardown
```bash
python3 tests/e2e/teardown_sprint004_fixtures.py
# Deletes all rows with IDs starting with 'cit:', 'q:t', 'guild:alpha', 'guild:beta'
```

---

## 7. Sign-Off

```
E2E Test Result: [ PASS / FAIL ]
Date: ________________

Athena (Correctness):
  All 18 hard gates: [ PASS / FAIL ]
  Notes: ________________________________________________
  Signature: ____________________________________________

Kasra (Implementation + Execution):
  Fixture setup: [ CLEAN / ERRORS ]
  Test run output: [ ATTACHED / PENDING ]
  Signature: ____________________________________________

Loom (Observability + Go-Live Decision):
  Adversarial subagent result: [ ATTACHED / PENDING ]
  DRY_RUN → Live decision: [ APPROVED / HOLD ]
  Sprint 005 open: [ YES / CONDITIONAL ]
  Signature: ____________________________________________
```

---

## 8. Go-Live Decision Matrix

| Condition | DRY_RUN→Live | Sprint 005 |
|-----------|-------------|------------|
| All 18 hard gates PASS + adversarial CLEAN | ✅ Approve | ✅ Open |
| ≤ 2 hard gates FAIL (non-constitutional) | ⚠️ Hold; Kasra patches | ⚠️ Conditional |
| Any constitutional issue from adversarial | ❌ Block; re-gate | ❌ Hold |
| Performance > 2× budget | ⚠️ Investigate; Kasra optimizes | ✅ Open (not blocking) |
