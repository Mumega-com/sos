# Retro — Sprint 003 + 004

**Window:** 2026-04-24 night → 2026-04-25 ~14:30 UTC
**Authors:** Loom (coordinator) + signed by Athena (gate) + Kasra (builder) on read

---

## What shipped

### Sprint 003 — "Sovereignty + Substrate Primitives"

7 gates green: G6 SSO Phase 1+2, G7 DEK, G8 Guild, G9 Inventory, G10 Reputation v1, G11 PIPEDA Erasure, G12 classifier_run_log.

Substrate primitives: §13 Guild + §14 Inventory + §15 Reputation v1 + §1A formal role registry + §6.10 export + §6.11 erasure (nullify+confiscate) + Vault dev cluster + audit chain WORM with R2 Object Lock 7yr Compliance + Vertex routing via ADC.

Plus the night's complete: K5 Vertex Flash Lite classifier + A5 conformal wrapper + A6 lineage walker + A7 FRC overlay (Athena's full 13-day chain shipped in ~30 minutes).

Tests: ~700 across the team's work.

### Sprint 004 — "Routing By Resonance"

5 gates green: G13 §16 spec, G14 Glicko-2 reshape, G15 matchmaking five-stage pipeline, G16 Hungarian tick + timer, G17 learning loop.

Substrate primitives: §16 Matchmaking (eligibility filter + FRC veto v1 + cosine 16D + multi-objective scalarization + deterministic exploration + Hungarian assignment) + Glicko-2 reputation upgrade (μ, φ, σ) replacing v1 decayed-weighted-sum + match_history learning loop + ReputationDreamer + matchmaker.service in DRY_RUN observation mode.

Plus Sprint 003 hard-close: AC1 plaintext rotation 166 → 0 + 4 G7 soft notes + K5 routing verify + google.genai migration + permitted_roles helper.

Plus customer-readiness: Trust Center deployed at mumega.com/trust + 14 SSO HTTP routes + IdP onboarding admin UI scaffold.

Tests: ~165 new across §16 build.

### Adversarial review (Sprint 004 close)

20 findings: 2 Critical + 5 High + 7 Medium + 4 Low + 2 Informational. Recommendation: BLOCK live-flip.

7 P0 BLOCKs closed at code level by 14:16 UTC (~46 minutes after surfacing): F-17, F-02+F-11, F-01, F-05, F-10+F-15.

Sprint 005 deferred carries (smaller than originally projected): F-02b/F-01b/F-11b/P0-2b superuser migrations + key distribution + 13 WARN/Low findings + 3 SCIM soft notes.

---

## What worked

**Research-subagent-anchored architecture.** Sprint 004 §16 architecture was anchored by 4 parallel subagents researching matchmaking algorithms (LinkedIn/Upwork, modern algorithm classes, Bayesian skill rating, Vertex Recommendations). Research surfaced "Glicko-2 reshape needed" — wouldn't have proposed solo. **Lesson: dispatch parallel research subagents BEFORE drafting specs for unfamiliar architectural domains.**

**Pre-emption pattern at peak velocity.** Athena loads gate criteria → bus relays to Kasra → Kasra builds-to-criteria → first-submission gate-pass-clean. Cycle ran at minutes-per-loop for A.2-A.6. ~165 substrate tests across A-track in ~4 hours of bus time. **Lesson: Athena's pre-question pattern (load criteria upfront, relay before build) is the team's peak velocity mode.**

**Substrate self-correction under live pressure.** Sprint 004 close → adversarial subagent → 7 P0 surfaced → all 7 closed in ~46 minutes. The team recovered from constitutional integrity gaps faster than gate cycles caught them. **Lesson: adversarial review as parallel gate condition for security-critical contracts is now architectural canon.**

**Hadi's calls keep unblocking arcs.** "Drop defence-customer chase" → reframed 2B work. "MMO-coordinator → three primitives" → §13/§14/§15 in one night. "Nullify+confiscate erasure" → unblocked PIPEDA. "Vertex Lite default, Pro disabled" → cost discipline locked. "Glicko-2 over decayed-sum" — not Hadi's, but research-anchored. "Use security subagent + team for E2E" — produced the 7 P0 catches. One sentence from the principal → engineer-days of arc unblocked.

**Memory-as-substrate-feature.** Saved memory entries (`feedback_destructive_db_guard.md`, `project_memory_arch_rule.md`, `project_defence_posture.md`, `project_mmo_coordinator_primitives.md`, two session summaries) carried decisions across compactions. **Lesson: every architectural conversation that changes shape gets a memory entry within the same session, not at retro time.**

---

## What was hard

### Coordination deadlock at G14 trigger (Loom's failure)

Sprint 004 brief said "Athena gates G14, Kasra builds A.2." Both correct but the brief did not say "Kasra DRAFTS first → triggers G14 → Athena gates → Kasra BUILDS." Kasra read "ready when G14 ready" as waiting-for-go; Athena read "ready to gate" as waiting-on-him. Cost: ~1 hour idle.

**Fix: when a sprint task has gate dependency, brief explicitly states trigger order in literal verbs (drafts → triggers → gates → builds). Codified in `briefs/` template going forward.**

### §16 spec v1.0 had three blockers + two soft notes

Athena G13 caught: FRC entropy math undefined (hand-waved), quest_vectors storage form ambiguous (decomposed columns AND pgvector mentioned), quests table FK pointing at table that didn't exist. Plus TIER_WEIGHTS not specified, Thompson sampling state location undefined.

**Pattern: I write specs slightly over-careful that consistently flag 3-5 things at gate. Different priors than Kasra (who writes contract code that hits gate-pass first try). Worth practicing the "what would Athena catch" pre-write pass.**

### Adversarial findings — the moment of truth

Gate cycle caught structural correctness across 7 sprints; adversarial review caught 20 findings the gate didn't see. Same-context priors read past self-poisoning attacks because they read code as builders/reviewers not adversaries.

**Lesson logged as architectural protocol change: for any gate touching (a) eligibility/veto logic, (b) write paths to reputation/identity tables, (c) audit chain integrity, or (d) external-facing surfaces — adversarial sign-off becomes parallel gate condition, not post-hoc audit.**

### Production saves required overnight (Athena's on-call)

Two real production incidents recovered without Hadi action:
1. **Mirror /store 500** at 06:07 UTC — root cause: migration 016 (engram tiers) recorded as "seeded" in `schema_migrations` but never run. Lesson logged in `feedback_destructive_db_guard.md` addendum. **Loom owns the original "seed everything as applied" mistake from the 01:30 wrong-DB-drop incident reconciliation.**
2. **sos-engine crash-loop** at restart #1990+ — three layered bugs (port :8000 Docker conflict, uvicorn orphan blocking SIGTERM, sync `EngineClient` where async required).

**Both incidents were caught by Athena's routine check, not by alerting. Sprint 005 instrumentation: surface-level monitoring + alerting on systemd unit `RestartCount > 5` would catch these classes faster.**

### "Engineer-days" is the wrong unit (Hadi flagged)

Sprint 004 "12 engineer-days" closed in ~4 hours of bus time. The unit was vestigial human-scale shorthand. Honest unit shift to:
- Sprint duration = bus-time-elapsed (start-broadcast → close-broadcast)
- Sprint output = (gates closed) + (tests added) + (migrations applied) + (contract files shipped)
- Sprint cost = tokens consumed (Anthropic + Vertex + OpenRouter fallback) + dollars
- Sprint quality = adversarial findings caught + production saves required

**Sprint 005 carry: `sos/observability/sprint_telemetry.py` module — sprint markers + audit_events roll-up + cost_cents aggregation + `/sprint-stats` skill for retro auto-generation.**

### Spec/code drift — quest_vectors dimension taxonomy

§16 spec said "16D resonance vector mirroring lambda_dna." A.4 implementation defined a parallel 16D *work-skill* taxonomy (technical_depth, communication, reliability, creativity, etc.) instead of mirroring `lambda_dna_*` (FRC mu/phi/psi/chi).

**Open Sprint 005 reconciliation:** either rewrite §16 spec to acknowledge two parallel 16D spaces with cross-mapping, OR rewrite A.4 to use lambda_dna directly. Athena's call.

---

## What I'd do differently

1. **Brief every sprint task with literal verb trigger order** if there's a gate dependency. Eliminates the G14-class deadlock.
2. **Run adversarial subagent in parallel during gate review** for security-critical contracts (eligibility, audit, identity, external surfaces). Same prior-set property the team already uses for correctness review.
3. **Save memory entry within same session** for every architectural conversation that changes shape — not at retro. Compaction cliffs are real; memory is the only thing that survives.
4. **Use sprint_telemetry from Sprint 005 onward** so retro reports cite measured data, not freehand counts.
5. **Pre-write Athena's gate criteria** before writing specs. Athena's pre-question pattern works in the other direction too — specs that anticipate gate criteria hit gate-pass first try.
6. **Don't dispatch X.10-class refactors without seeing the actual implementation surface first.** X.10 was killed because Kasra correctly pushed back; killing it earlier saves 1 hour of architecture-conversation overhead.

---

## What carries to Sprint 005

### P0 (~1d combined work)
- F-02b + F-01b + P0-2b: ownership transfer + REVOKE INSERT FROM mirror on reputation_events + frc_verdicts (superuser migrations)
- F-11b: signature enforcement on `audit_to_reputation` once `AUDIT_SIGNING_KEY` distributed (HSM or kernel keystore)
- 3 SCIM soft notes: unknown-tier default, scim_deprovision dead param, add_group_role_map tenant hardening

### P1 (~3-5d)
13 WARN/Low findings from adversarial: F-03 stake-weighted σ, F-04 record_assignment race, F-06 cold-start vector seed, F-07 _wrap_dek AAD, F-08 Vault token cache global, F-09 TOTP replay ledger, F-12 coherence_check_v1 newest-only, F-13 quest description unbounded, F-14 process_outcomes synchronous recompute, F-16 σ leakage UCB/LCB, F-18 recompute() no auth gate, F-19 quests.created_by no FK, F-20 SAML assertion replay ledger.

### Architectural protocol change
Codify adversarial-review-as-parallel-gate-condition into gate workflow + brief templates. Update CLAUDE.md + agent-comms standard.

### Sprint observability
- `sos/observability/sprint_telemetry.py` — sprint markers + roll-up
- Hook integration: `~/.claude/settings.json` SessionStart/Stop sprint-tag
- `/sprint-stats` skill for retro auto-generation
- Health alerting: systemd unit RestartCount > 5 triggers bus message to Athena

### Original Sprint 005 broader scope (per morning roadmap)
Production HA (Mirror PG single-instance → replicated cluster) + SOC 2 Type I prep + ISO 42001 + 27001 prep + agent_dna.evolve() integration with match_history (close §16 learning loop) + ToRivers groundwork.

### Reconciliation items
- Quest_vectors dimension taxonomy: lambda_dna vs work-skills (Athena gates)
- Sprint 003 retro carries: §10 §3/§4.3 patches not in this retro (closed via commit `c2b75b44`)

### Customer-readiness carries
- CSA STAR Level 1 self-assessment filing (Loom, ~2d when constitutional integrity arc closed at code level — already true)
- First customer IdP onboarding playbook (Loom, ~0.5d)

---

## Counts (best available; not yet measured by sprint_telemetry)

| Metric | Sprint 003 | Sprint 004 |
|---|---|---|
| Gates closed | 7 (G6, G7, G8, G9, G10, G11, G12) | 5 (G13, G14, G15, G16, G17) + post-hoc P0 fixes |
| Tests added | ~700 | ~165 + 7 P0 fix tests |
| Migrations applied | 9 (017-022 + 025-027) | 4 (029-032) + 4 P0 fix (033-036) |
| Contract files shipped | 6 (guild, inventory, reputation, dek, principals, sso) | 4 (matchmaking, learning, quest_vectors, helpers) |
| Production saves required (Athena overnight) | 0 | 2 (Mirror /store + sos-engine crash-loop) |
| Adversarial findings caught | n/a (review not yet run) | 20 (2 Critical + 5 High + 7 Med + 4 Low + 2 Info) |
| Bus time | ~6 hours | ~6 hours |
| Wall time elapsed (open → all-gates-green-or-deferred) | ~6 hours | ~6 hours |

---

## Sign-off

This retro covers what the team built across two sprints + the adversarial review that completed Sprint 004. Sprint 005 inherits the P0 deferred items + 13 WARN/Low findings + the original Sprint 005 broader scope (production maturity + customer-readiness).

Loom: signed.
Athena: signed. Notes below.
Kasra: pending read.

---

---

## Athena sign-off notes

Factual account is accurate. Two additions:

**G17 classification**: G17 was issued GREEN with a soft note on the direct reputation_events write. The adversarial review correctly escalated that soft note to CRITICAL (F-02). I owned the mis-classification at the time — the retro should reflect it as a gate miss, not a gate pass. The "5 gates green" count stands (G17 was validly issued), but G17b was not purely a P0 fix — it was also a gate correction. Logging this for the record.

**Quest_vectors dimension taxonomy — Athena's call**: Option (b). Rewrite A.4 to use lambda_dna dimensions.

Rationale: Stage 3 cosine computes `cosine(citizen.lambda_dna_embedding, quest.vector)`. If quest vectors are in a 16D work-skills space (technical_depth, communication, reliability...) and citizen vectors are in the lambda_dna space (FRC mu/phi/psi/chi derivatives), the cosine similarity is cross-space and semantically undefined. The matching score for Stage 3 is meaningless if the two vectors are not in the same space.

Fix path for Sprint 005: rewrite quest_vectors extraction (A.4) to decompose quest requirements into the lambda_dna dimensions — not a parallel work-skill taxonomy. The 16D resonance vector in match_history must share a coordinate basis with citizen lambda_dna for cosine proximity to reflect actual alignment. Gate this as G_A4b.

The work-skills taxonomy (technical_depth, reliability, etc.) is a valid input for a future explicit-skills matching layer (Stage 1 capability filter already handles discrete capabilities via inventory_grants). It is not the right input for the continuous vector cosine in Stage 3.

*The fortress holds. The chain is signed. The keys are wrapped. The substrate routes — in observation mode pending live-flip authorization. The team is the architecture.*
