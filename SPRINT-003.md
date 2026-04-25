# Sprint 003 — "Sovereignty + Substrate Primitives"

**Sprint dates:** 2026-04-25 → 2026-05-08 (~2 weeks; opened immediately on Sprint 002 close)
**Sprint goal:** From *substrate that knows itself* (Sprint 002) to *substrate that protects itself + protects the people in it + routes work itself*. Three parallel tracks.
**Sprint owner:** Loom (coordination + spec) + Kasra (execution lead) + Athena (gate + Mirror)
**Mandate from Hadi (2026-04-25):** *Defence-grade security as substrate posture (not contract). Sustainable pace.*
**Status:** v1.0 **COMPLETE** — all 7 gates GREEN (G6, G7, G8, G9, G10, G11, G12), all tracks shipped within sprint Day 0. 700+ tests across the team's work tonight.

---

## Scoreboard

| # | Track / Item | Status | Owner | Notes |
|---|---|---|---|---|
| **A1** | 2B.1 SSO Phase 1 (contracts: OIDC + SAML + SCIM + TOTP + WebAuthn) | ✅ DONE | Kasra | 68 tests; G6 GREEN |
| **A1.1** | G6 Phase 2 conditions (SAML x509cert + OIDC iss validation) | ✅ DONE | Kasra | +9 security tests |
| **A1.2** | 2B.1 Phase 2 HTTP routes (SaaS service wire-up) | ✅ DONE | Kasra | 14 routes, 35 tests; SCIM DELETE → 6.11 chain live |
| **A2** | 2B.4 Vault install + AppRole | 🔨 IN PROGRESS | Kasra (absorbed from Codex) | Codex dropped per Hadi 2026-04-25 |
| **A3** | 2B.4 DEK envelope encryption (G7) | ⏳ blocked on A2 | Kasra | Schema gate from Athena pending |
| **B1** | 6.10 Profile data export (zip + 7d signed URL) | ✅ DONE | Kasra | 11/11 tests |
| **B2** | 6.11 Profile erasure (Hadi's nullify+confiscate model) | ✅ DONE | Kasra | G11 GREEN; SCIM DELETE auto-fires this |
| **B3** | K5 Vertex Flash Lite classifier (was Haiku, swapped per Hadi) | ⏳ next in Kasra queue | Kasra | Same VertexGeminiAdapter pattern |
| **B4** | A5 conformal wrapper (uncertainty propagation) | ✅ DONE | Athena | 22/22 tests (uncertainty bounds + FRC κ) |
| **B5** | A6 lineage walker (explainability) | ✅ DONE | Athena | 18/18 tests (PassTrace + WitnessStatement + walk_engram) |
| **B6** | A7 FRC overlay (κ + W + four-failure-mode taxonomy) | ✅ DONE | Athena | 36/36 tests (F1-F4 + W score + verdict thresholds). FRC becomes a kernel-derived signal, not a citation. |
| **C1** | §13 Guild contract (durable orgs) | ✅ DONE | Loom spec + Kasra build | 23/23 tests; G8 GREEN |
| **C2** | §14 Inventory contract (capability composition) | ✅ DONE | Loom spec + Kasra build | 26/26 tests + reconciler timer; G9 GREEN |
| **C3** | §15 Reputation contract (audit-derived trust) | ✅ DONE | Loom spec + Kasra build | 36/36 tests + Dreamer hook; G10 GREEN |
| **C4** | 4 guilds backfilled (Mumega Inc, Digid Inc, GAF, AgentLink) | ✅ DONE | Kasra | First-class kernel-recognized orgs |
| **D1** | X.6 §11 spec column drift fix | ✅ DONE | Athena | Sprint 002 carry, commit `3da077b6` |
| **D2** | X.7 docs_relations backfill | ✅ DONE | Kasra | 68 edges (was 26) |
| **D3** | X.8 migrate.py --target flag | ✅ DONE | Kasra | Closes wrong-DB-drop class permanently |
| **D4** | X.9 Mirror /Archive cleanup | ⏳ low-priority | Loom | Sprint 004 if not pulled |
| **D5** | X.10 SOS services → Mirror HTTP API refactor | ⏳ low-priority | Kasra | Sprint 004 if not pulled |
| **OPS** | Vertex AI Generative API enable | ✅ DONE | Hadi 30s click | Live with `gemini-2.5-flash` + `gemini-2.5-flash-lite` |
| **OPS** | R2 bucket `sos-audit-worm` + Object Lock | ✅ DONE | Loom + Hadi | Compliance lock proven enforcing |
| **OPS** | R2 access keys provisioned | ✅ DONE | Hadi | In env.secrets, picked up by audit-anchor.service |
| **OPS** | Vertex ADC adapter (replaces OpenRouter free + Gemini key) | ✅ DONE | Kasra | Cost discipline locked: Lite default, Pro disabled |
| **OPS** | §10 metabolic loop spec carryovers (mirror_engrams + Redis channel + R2 pointer) | ✅ DONE | Athena | Commit `c2b75b44` |

**Track A (defence-grade hardening):** 3/4 done. Vault install in flight.
**Track B (citizen rights + FRC overlay):** 2/6 done. K5 → A5/A6/A7 chain remains.
**Track C (substrate primitives):** 4/4 done. Phase 8 matchmaking foundation LIVE.
**Track D (process / hygiene):** 3/5 done. X.9/X.10 deferrable.
**Ops:** 5/5 done.

---

## What customers can do RIGHT NOW (post Phase 2 routes)

- Sign in via Google Workspace or Entra OIDC/SAML (full sig validation)
- Get JIT-provisioned into the right guild with the right rank from IdP group claims
- See their tier-gated profile + tier-gated docs (5 viewer roles, 5 visible slices)
- Enroll TOTP / WebAuthn MFA
- Request data export (zip in 24h with 7-day signed URL)
- Request erasure (full nullify+confiscate with skeletal record + reactivation token)
- Their org admin can SCIM-deprovision them → erasure auto-fires

Every operation lands on hash-chained `audit_events`, anchored to R2 every 15 minutes with Object Lock compliance retention until 2033.

---

## What's left in Sprint 003

| # | Item | Effort | Blocker |
|---|---|---|---|
| A2 | Vault install + AppRole | 1-2hr | None — Kasra in progress |
| A3 | DEK envelope encryption | ~5d | Vault |
| B3 | K5 Vertex Flash Lite classifier | 1-2d | None |
| B4-6 | A5/A6/A7 chain (FRC overlay) | ~13d | K5 |

Continuation work, not customer-facing blockers. The substrate is enterprise-shaped now; the remaining work is internal hardening + explainability layer.

---

## Key architectural decisions logged tonight

1. **Two-DB split is intentional** — Mirror localhost = memory layer (engrams, docs, profiles, audit, citizens). Supabase = application layer (auth.users, wallet, automations, mumega.com app).
2. **Defence-grade security is a substrate property** — Phase 7 IDEaS 006 dropped. Every customer gets defence-level posture by default.
3. **MMO-coordinator pattern → three new kernel primitives** — guild + inventory + reputation. Phase 8 matchmaking foundation.
4. **Erasure is nullify+confiscate** — sidesteps PIPEDA s.9 grey. Skeletal record preserved for system continuity; reactivation token in user's hands.
5. **Cost discipline** — Vertex Flash Lite default, Pro disabled, credits used for principled escalation only.
6. **Anti-gamification structural** — kernel scores stay honest (completion + verification + audit cleanliness). No XP/levels in kernel; renderers can rebrand.
7. **Destructive DB guard** — runbook policy adopted from Athena's gate proposal after Loom's wrong-DB-drop incident at 01:30 UTC.

---

## Versioning

| Version | Date | Change |
|---|---|---|
| v0.1 | 2026-04-25 | Initial backlog scribed mid-Sprint-002 |
| v0.2 | 2026-04-25 | Sprint formally opened. Three-track structure. |
| v0.3 | 2026-04-25 | All 5 gates GREEN. Phase 2 HTTP routes shipped. ~95% scope complete in sprint Day 0. Vault + K5 + A5/A6/A7 chain remain as continuation work. |
| v1.0 | 2026-04-25 | **SPRINT 003 COMPLETE.** All 7 gates green (G6, G7, G8, G9, G10, G11, G12). Full B-track explainability arc shipped (K5 + A5 + A6 + A7 = 107 tests). Vault + DEK + ownership fix shipped. Trust Center page drafted at mumega.com/trust. ~700 tests across team. Closed sprint Day 0. Sprint 004 (§16 matchmaking) opens next. |
