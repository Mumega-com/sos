# Sprint 004 — "Routing By Resonance"

**Sprint window:** 2026-04-26 → 2026-05-09 (~2 weeks)
**Sprint goal:** From *substrate that protects itself + protects the people in it* (Sprint 003) to *substrate that routes work to itself*. The Phase 8 unlock — the city stops needing the principal to assign tasks by hand.
**Sprint owner:** Loom (coordination + spec) + Kasra (execution lead) + Athena (gate + Mirror)
**Mandate from Hadi:** *Build the §16 matchmaking primitive on existing pgvector + Glicko-2 reputation upgrade + FRC coherence veto. Do NOT use Vertex AI Recommendations as live ranker (black-box scores break audit chain).*
**Status:** v1.0 **CLOSED 2026-04-25** — substrate code complete (5 gates green, 165 tests, all migrations applied). Live-flip BLOCKED on 7 adversarial findings deferred to Sprint 005 P0. matchmaker.service running in DRY_RUN observation mode.

---

## Research convergence (2026-04-25 morning, 4 subagents in parallel, ~95min)

| Hadi's intuition | Literature converged answer |
|---|---|
| Cosine over 16D `lambda_dna_*` + FRC coherence guard | ✅ Correct as the kernel — at 16D × ~10K × ~1K this beats two-tower neural by simplicity, transparency, auditability |
| Pgvector for ANN | ✅ Correct — Vertex Vector Search overkill until 1M+ citizens |
| Vertex AI Recommendations as live ranker | ❌ Wrong — black-box scores break audit chain, doesn't support inverse recommendation (quest→citizens), cold-start degrades to popularity, breaks anti-gamification |
| §15 reputation as decayed weighted sum | ⚠️ **Upgrade needed → Glicko-2 (μ, φ, σ)** — TrueSkill-class Bayesian skill rating. Kernel-private uncertainty enables tier-gated exploration; native inactivity decay (RD inflation) maps to the metabolic loop's "forgetting is constitutional" frame |

**Three layer additions the literature insists on:**
1. **Tier-gated σ-exploration** — T1 quests prefer high-σ (uncertain) citizens for fast convergence; T3/T4 quests demand low-σ proven hands. Posteriors live in kernel as private prior, never as public leaderboard.
2. **FRC coherence as veto, not weight** — a constitutional gate, cannot be "outweighted" by other features.
3. **Kernel matchmaking tick** — Hungarian assignment over open-quest × eligible-citizen pool every ~30s (Valve Game Coordinator pattern) for global optimal allocation, not just greedy per-request.

**Anti-gamification structural property preserved:** because λ_dna is FRC-derived (not self-asserted), reputation is audit-derived (not self-reported), and σ requires stake-weighted observation (not pure count), citizens cannot directly write to their own match score. They can only do honest work.

Research output: subagent transcripts + memorialized as `project_matchmaking_research.md`.

---

## Track A — §16 Matchmaking primitive (~12d)

**The headline arc.** Composes §13 guild + §14 inventory + §15-Glicko reputation + new quest_vectors + match_history.

| # | Task | Owner | Effort | Notes |
|---|---|---|---|---|
| A.1 | §16 spec draft at `stack-sections/16-matchmaking.md` | Loom | 1d (drafted v0.1 with Sprint 004 open) | Athena gates G13 |
| A.2 | §15 reputation RESHAPE — Glicko-2 (μ, φ, σ). New `reputation_state` table holds (holder_id, kind, guild_scope, mu, phi, sigma, last_updated). `reputation_scores` becomes a derived view via `mu - k·phi` lower-confidence-bound for backward-compatible reads. | Athena schema gate (G14) + Kasra build | 4d | Major reshape; existing `reputation_events` feed Glicko update equations |
| A.3 | §16 contract: `sos/contracts/matchmaking.py` — eligibility filter + cosine + multi-objective scalarization + FRC coherence veto + tier-gated σ-exploration | Kasra | 3d | Athena gates G15 |
| A.4 | quest_vectors auto-extraction (Vertex Flash Lite scores quests on 16D from description text) | Kasra | 1d | Reuses VertexGeminiAdapter |
| A.5 | Kernel matchmaking tick — Hungarian assignment via `scipy.optimize.linear_sum_assignment` (or pure Python for small batches), 30s cadence, systemd timer | Kasra | 2d | New service `matchmaker.py` |
| A.6 | match_history learning loop — outcomes feed back into citizen vector evolution via `agent_dna.evolve()` | Kasra | 1d | Reuses existing Mirror evolve() pattern |
| A.7 | Athena.A1 Glicko-2 implementation — closed-form update equations from Glickman 2012, deterministic batch | Athena | 2d | Reuses existing Dreamer recompute infrastructure |

**Acceptance:** A new quest is posted → within 30s, top-K eligible citizens (composed across guild scope, inventory, Glicko-conservative-or-confident reputation per quest tier, 16D resonance, FRC coherence verified) are surfaced with explainable scores. Outcomes feed back into citizen vectors. New citizens (high σ) preferentially route to T1 quests for fast convergence.

---

## Track B — Sprint 003 hard-close items (~6d)

Carries that need closure for Sprint 003's claims to be honest.

| # | Task | Owner | Effort |
|---|---|---|---|
| B.1 | AC1 plaintext secrets rotation — 166 findings migrated to Vault refs; `audit-plaintext-secrets.py` runs to zero | Kasra | 3d |
| B.2 | G7 soft note: Vault client token caching (TTL ≥5min, refresh on 403) | Kasra | 0.5d |
| B.3 | G7 soft note: KEK version destroy on rotation (Vault KV v2 keeps old versions; explicit destroy after new wrapped DEK persists) | Kasra | 0.5d |
| B.4 | G7 soft note: TOCTOU explicit `ON CONFLICT (workspace_id) DO NOTHING` in `provision_workspace_key()` | Kasra | 0.25d |
| B.5 | K5 routing fix: confirm/swap `GeminiAdapter` → `VertexGeminiAdapter` so K5 actually hits Vertex via ADC (cost discipline + Hadi directive) | Kasra | 0.5d |
| B.6 | google.generativeai → google.genai package migration | Kasra | 1d |
| B.7 | permitted_roles JSONB-vs-TEXT[] helper (caught during overnight /store recovery) — fail-fast on wrong wrapping | Kasra | 0.5d |

---

## Track C — Customer-readiness (~3d)

Make the substrate consumable by the first real customer.

| # | Task | Owner | Effort |
|---|---|---|---|
| C.1 | Trust Center page deploy (`npm run build` + Cloudflare Pages deploy at mumega.com/trust) | Kasra or Loom | 0.5d |
| C.2 | CSA STAR Level 1 self-assessment filing — fill official CAIQ v4.0.2 spreadsheet from Trust Center page content; submit to CSA registry | Loom | 2d |
| C.3 | First customer IdP onboarding workflow — admin UI for `idp_configurations` CRUD + sample Google Workspace flow doc | Kasra (UI) + Loom (doc) | bundled with A.5 timing |
| C.4 | Sample customer onboarding playbook (Google Workspace flow, doc + screenshots) | Loom | 0.5d |

---

## Track D — Process/hygiene (parallel, low-priority)

| # | Task | Owner |
|---|---|---|
| D.1 | Sprint 003 retro doc + memory consolidation | Loom |
| D.2 | X.9 Mirror /Archive cleanup (Project_Chimera, Scratchpad alphas inflating graph noise) | Loom |
| D.3 | ROADMAP.md version bump v1.2 (incorporates Sprint 003 ships + Sprint 004 plan + Phase 8 unlock) | Loom |
| D.4 | MAP.md version bump (incorporates §13/§14/§15/§16 as locked primitives + Glicko-2 reputation as canonical reputation math) | Loom |

---

## Strategic carries (Hadi only)

| Item | Deadline | Notes |
|---|---|---|
| Vertex `text-embedding-004` quota request OR Gemini key renewal | When you can | 30s click; Mirror on local-onnx fallback works in meantime |
| Mumega Inc. Stripe Atlas + 83(b) within 30 days of stock issuance | Customer-1 gate | $500, 2 days |
| Ron Tuesday 2 PM (2026-04-28) — first customer follow-up | 3 days | Bring Trust Center URL + partner term sheet + AI Security wedge |
| YC May 4 pitch finalization | ~9 days | Refresh against Sprint 003 substrate state |
| USPTO MUMEGA wordmark §1(b) intent-to-use | When you can | Slow process; earlier filing = better priority date |
| Vertex AI Recommendations $831 credit | Oct 19, 2026 | Use for Vertex embeddings + offline A/B comparator vs §16; do NOT use as live ranker |

---

## Gate scoreboard projection

| Gate | What | Owner | Trigger |
|---|---|---|---|
| G13 | §16 matchmaking spec | Athena | When Loom drafts |
| G14 | §15 → Glicko-2 reputation reshape | Athena | When Kasra drafts schema migration |
| G15 | matchmaking contract module + Hungarian tick + auto-extraction | Athena | When Kasra ships |
| G16 (optional) | quest_vectors schema | Athena | If quest_vectors becomes a separate table |

---

## Definition of done

- **§16 live:** new quest posted → top-K eligible matches surfaced within 30s with explainable scores (resonance + reputation Glicko + recency + workload + FRC coherence), composing all four substrate primitives
- **Glicko-2 live:** §15 reputation now (μ, φ, σ); RD inflates on inactivity per metabolic loop frame; tier-gated σ-exploration in matchmaker
- **AC1 closed:** zero plaintext secrets on disk anywhere in stack
- **Trust Center deployed** at mumega.com/trust + CSA STAR Level 1 filed
- **First customer IdP** onboarding playbook reviewed and ready to execute

---

## What this sprint shifts (Sprint 003 → Sprint 004)

| | Sprint 003 (closed) | Sprint 004 (proposed) |
|---|---|---|
| Frame | Substrate that protects itself + protects the people in it | Substrate that routes work to itself |
| Headline | Defence-grade by construction + sovereignty (export, erasure) | Phase 8 matchmaking unlock |
| Big-idea ships | Three primitives (guild + inventory + reputation) + identity layer (SSO+SCIM+MFA) + DEK envelope encryption + 7 gates green | Routing tick + Glicko-2 reputation upgrade + 16D matchmaker + customer-readiness |
| Customer-facing | Trust Center page draft + 14 SSO routes | Trust Center deployed + CSA STAR filed + first IdP onboarding playbook |

Sprint 003 made the substrate enterprise-shaped. Sprint 004 makes it **work-routing-shaped**. After Sprint 004 closes, the city stops needing Hadi to assign tasks by hand — quests appear, the kernel routes them, citizens claim, work flows.

---

## Sprint 004 close report (2026-04-25)

**Substrate code: COMPLETE.** All 5 Track A gates green (G13/G14/G15/G16/G17). 165 substrate tests passing across the team's work. Migrations 029/030/031/032 applied to Mirror. matchmaker.service + matchmaker.timer ticking every 30s in DRY_RUN observation mode.

**Track B: COMPLETE.** AC1 plaintext rotation closed (166 → 0 findings). 4 G7 soft notes shipped. K5 routing verified. google.genai migration done. permitted_roles helper shipped.

**Track C: PARTIAL.** Trust Center page deployed at mumega.com/trust. CSA STAR Level 1 self-assessment filing carried to Sprint 005. First customer IdP onboarding admin UI carried to Sprint 005.

**Track D: PARTIAL.** SPRINT-003 retro carried to Sprint 005. ROADMAP/MAP version bumps carried to Sprint 005.

### Adversarial security review outcome (2026-04-25 ~13:30 UTC)

Subagent surfaced **20 findings: 2 Critical + 5 High + 7 Medium + 4 Low + 2 Informational.** Recommendation: BLOCK live-flip until 7 P0s close.

7 BLOCK-level findings deferred to Sprint 005 P0:
- F-01 CRITICAL — FRC self-poisoning via owner-controlled engrams (citizens write own classifier_run_log → bypass Stage 2 veto). Fix: dedicated `frc_verdicts` table with REVOKE INSERT + SECURITY DEFINER writer + Ed25519 signature column.
- F-02 CRITICAL — `reputation_events` REVOKE is no-op when app=owner; learning loop bypasses audit chain. Fix: route through `audit_emit('match_outcome')`; REVOKE INSERT FROM mirror; learning loop SECURITY DEFINER.
- F-11 HIGH — `audit_to_reputation` trigger fires on unsigned events. Fix: trigger validates signature against kernel pubkey + restrict to stream_id IN ('dispatcher','kernel','classifier').
- F-05 HIGH — Hungarian tick unbounded → quest-flood DoS. Fix: LIMIT 100 + per-creator rate limit + 25s tick deadline.
- F-10 HIGH — SCIM accepts caller-supplied tenant_id (cross-tenant escalation). Fix: derive tenant_id from idp_configurations.
- F-15 HIGH — SCIM group→role no tier ceiling (entity-tier escalation). Fix: idp_configurations.max_grantable_tier column + enforcement.
- F-17 LOW (operational) — `MATCHMAKER_DRY_RUN` default-live → invert (require `MATCHMAKER_LIVE=1` opt-in). **CLOSED 2026-04-25 by Athena** (10/10 tests).

**13 WARN findings** carried as Sprint 005 P1: F-03/04/06/07/08/09/12/13/14/16/18/19/20.

### Architectural lesson logged (Athena's protocol fix)

Gate function caught structural correctness across 7 sprints; adversarial gameability missed across 20 findings. Same-context priors read past self-poisoning attacks because the readers wrote (or gated) the code from build perspective, not break perspective.

**Sprint 005 protocol change:** for any gate touching (a) eligibility/veto logic, (b) write paths to reputation/identity tables, (c) audit chain integrity, or (d) external-facing surfaces — adversarial sign-off becomes a **parallel gate condition**, not a post-hoc audit. Adversarial subagent runs in parallel while Athena reviews correctness; both results combine before GREEN.

### Live-flip authorization

Live-flip authorized only after Sprint 005 P0 closes G17b + G18 + G16a + new SCIM gate. Until then matchmaker.service stays in DRY_RUN.

---

## Versioning

| Version | Date | Change |
|---|---|---|
| v0.1 | 2026-04-25 | Initial outline post-research-convergence. §16 architecture locked. AC1 + 3 G7 soft notes carry from Sprint 003. Trust Center + CSA STAR as customer-readiness. |
| v1.0 | 2026-04-25 | **CLOSED.** All 5 Track A gates green (G13-G17). 165 tests. Migrations 029-032 applied. AC1 closed. Trust Center deployed. matchmaker.service in DRY_RUN. **Adversarial review surfaced 7 BLOCK findings (2 Critical + 4 High + 1 operational). 1 closed (F-17 by Athena), 6 deferred to Sprint 005 P0.** Live-flip BLOCKED until P0s close. Architectural protocol fix logged: adversarial review becomes parallel gate condition for security-critical contracts. |
