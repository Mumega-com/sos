# Sprint 002 — "Logos-like Operation"

**Sprint dates:** 2026-04-25 → 2026-05-08 (~2 weeks; aggressive, fast & furious mandate)
**Sprint goal:** Make the substrate eat its own dog food. Stop authoring duplication. Land enterprise-readiness foundations. Two parallel bursts, both close in 2 weeks.
**Sprint owner:** Loom (coordination) + Kasra (execution lead, bursts run parallel)
**Mandate from Hadi (2026-04-24):** *"Fast and furious. Time is the only important thing. Reliability, security, accessibility — no constraint."*

---

## v1.1 — Agreed Plan (post Athena gates + Kasra commitments + graph audit)

Five resolutions locked before build:

1. **2A.2 middleware shape** — shared SDK in `workers/inkwell-api/`, NOT per-fork Astro middleware. Worker is the enforcement chokepoint (graph: `routes/mcp.ts` size 23 + `routes/dashboard.ts` size 10 are the hubs). One library, many fork consumers. Microkernel-correct.
2. **G2 §12 sos-docs reuse mechanism** — tier enforcement function lives in `sos/contracts/storage.py` (or new `sos/contracts/tiers.py`). sos-docs imports from contracts. That IS the kernel public surface.
3. **G5 Vault/DEK Workers boundary** — VPS-only this sprint, Workers Sprint 003. Graph confirms `sos/vendors/cloudflare.py` (cohesion 0.64) has no Vault binding today; bleeding scope is risk.
4. **2B.3 PIPEDA erasure stage 6.11** — slip Sprint 003. Athena flagged s.9 grey area; Kasra ships 6.1–6.4 only this sprint.
5. **agents-onboarding ↔ kernel-capability leak** — graph audit found 11 cross-edges. Athena gates the diff before 2B.3 magic-link extends onboarding. Either fix the leak first or accept the diff with eyes open.

---

## Sprint Goals

1. **Burst 2A — Inkwell-Hive + sos-docs microservice** — closes doc duplication; ships rendering layer; substrate operates logos-like at the doc surface.
2. **Burst 2B — Enterprise hardening (partial)** — audit chain WORM (8d, ships) + profile primitive 6.1–6.4 (5d, ships); SSO/SCIM/MFA + Vault/DEK + erasure 6.11 → Sprint 003.

---

## Burst 2A — Inkwell-Hive + sos-docs microservice (~14d, fits sprint)

**Owner:** Kasra (lead, dispatches Subagent A + B) + Loom (spec + coordination)

| # | Task | Effort | Status | Depends on |
|---|---|---|---|---|
| 2A.0 | §12 sos-docs spec | 1d | ✅ shipped (`stack-sections/12-sos-docs.md`) | — |
| 2A.1 | Inkwell schema upgrade — 6-value Hive enum (`tier`, `entity_id`, `permitted_roles[]`); add `newsletter_gate` + `paywall` flags so members/paid migration isn't lossy (Athena G1) | 3d | ready | 2A.0 |
| 2A.2 | Shared SDK in `workers/inkwell-api/` — render-time tier enforcement (NOT per-fork middleware) | 5d | ready | 2A.1 |
| 2A.3 | sos-docs microservice scaffold — peer service via mesh, exposes graph API; imports tier function from `sos/contracts/` | 3d | ready | 2A.0 |
| 2A.4 | Doc-node schema in Mirror (tier + entity_id + permitted_roles + relations); add `updated_at` auto-update trigger (Athena G2) | 1d | ✅ ingestion already wrote 11 docs as nodes; migration formalizes schema | Athena gate |
| 2A.5 | (Doc ingestion script — already shipped + executed; 11 nodes in Mirror) | — | ✅ done | — |
| 2A.6 | mumega.com Inkwell wired as first sos-docs consumer | 2d | queued day 8–10 | 2A.2, 2A.3 |
| 2A.7 | Render verification: visitor / customer / partner / agent / principal each see their authorized slice | 1d | queued end of sprint | 2A.6 |

**Acceptance:** same `MAP.md` content, 5 viewer tokens, 5 distinct slices. Files become rendered cache; graph is canonical.

---

## Burst 2B — Enterprise hardening (partial) (~13d, fits sprint)

**Owner:** Kasra (lead) + Athena (gate + Mirror migrations) + Codex (security review)

| # | Task | Effort | Status | Depends on |
|---|---|---|---|---|
| 2B.2 | Hash-chained `audit_events` → R2 WORM with object-lock; payload JSONB ≤8KB at emission; PG sequence per stream_id (NOT app counter); Ed25519 mandatory on dispatcher stream, optional elsewhere (Athena G4) | 8d | ready, no blockers — start tomorrow | — |
| 2B.3 | Profile primitive — stages 6.1–6.4 only (schema + `/people/{slug}` SSR + magic-link login + self-tier view) | 5d | ready after Athena gate | §11 patched (below); `self_profile` role registered in §1A; DISP-001 (deployed) |

**Slipped to Sprint 003:**
- 2B.1 SSO/SCIM/MFA (20d — full sprint alone)
- 2B.3 stages 6.8 (access log), 6.10 (export), 6.11 (erasure — PIPEDA s.9 grey, needs legal)
- 2B.4 Vault/DEK (13d, needs Codex on Vault install + Workers boundary work)

**Acceptance (partial):** audit chain writing to WORM with verifiable hash chain. One human contractor logs in via magic-link, sees their own profile slice. Other enterprise gaps documented for Sprint 003.

---

## Sprint 001 carryover — RESOLVED

| Item | Verdict | Notes |
|---|---|---|
| §10 §3/§4.3 | GREEN | Patched (commit `97abb5e8`). Kasra must pull before §4.3 build. |
| §11 schema | GREEN with 4 patches **applied tonight** (see below) | granted_at NOT NULL, workspace_id, status CHECK, retain_reason |
| A4 sporulation | LIVE | No flag. `MIRROR_HOT_STORE_THRESHOLD` env var, default 100k. |
| A5/A6 | SLIP Sprint 003 | Blocked on K5 Haiku classifier |
| A7 FRC overlay | SLIP Sprint 003 | Defers with A5/A6 |

**§11 patches applied to spec doc 2026-04-25 (this update):**
1. `profile_consents.granted_at TIMESTAMPTZ NOT NULL`
2. `profile_tool_connections.workspace_id TEXT NOT NULL`
3. `profile_tool_connections.status` + CHECK in `('active','revoked','expired')`
4. New `profile_requests` table (§3.6) with `retain_reason TEXT` for PIPEDA receipts
5. Bonus: `profile_export_jobs.status` CHECK constraint added (cleanup)

---

## Athena's required-before-build patch list (apply in order)

| Patch | Where | Done |
|---|---|---|
| §11 4-patch set | `stack-sections/11-profile-primitive.md` | ✅ |
| `self_profile` role registered in §1A role registry | `stack-sections/01-substrate.md` (or kernel role registry) | ⏳ Kasra before 2B.3 |
| §12 spec: explicit `sos.contracts.tiers` import (not "verbatim comment") | `stack-sections/12-sos-docs.md` | ⏳ Loom |
| §12 spec: `updated_at` auto-update trigger | same | ⏳ Loom |
| Audit chain spec: 8KB JSONB cap + PG sequence + Ed25519 dispatcher mandatory | `burst-2b/02-audit-chain-worm.md` | ⏳ Loom |
| K9/K10 absorption confirmation (sprint 001 audit overlap) | bus to Athena + Kasra | ⏳ Loom |

---

## Cross-cutting

| # | Task | Owner | Effort |
|---|---|---|---|
| X.1 | Commit dirty trees (mirror, shabrang-cms, cli) — lock in tonight's substrate work | Kasra | 0.5d |
| X.2 | Telegram pairing / channel restart in active session | Hadi + Loom | done |
| X.3 | Sprint 001 carryover triage | Loom | ✅ |
| X.4 | Memory file updates after each ship | Loom | ongoing |
| X.5 | Sprint 003 backlog scribed (slipped 2B.1/2B.4 + A5/A6/A7 + 6.8/6.10/6.11 + Mirror archive cleanup) | Loom | 0.5d |

---

## Dispatch plan

**Kasra direct:** 2B.2 (audit chain — architecture work, not rote)
**Subagent A (Kasra-dispatched):** 2A.1 + 2A.2 (schema → SDK chain)
**Subagent B (Kasra-dispatched):** 2A.3 + 2A.4 (sos-docs scaffold + doc-node schema)
**Subagent C (Kasra-dispatched, day 6+):** 2B.3 stages 6.1–6.4
**Athena direct:** A.1 + A.2 + A.3 Mirror migrations ✅ shipped 2026-04-24 night (mirror/main commit `a50a69f`, migrations 017–019). A.4 (DEK) starts after A.3 settles.
**Loom:** spec patches above, coordination, memory updates, X.5 Sprint 003 backlog

---

## Cadence

- Daily async via bus — no meetings
- 48h checkpoint — Loom syncs progress, escalates blockers
- End of week 1 — first acceptance test (one rendered tier-gated page; 2B.2 hash chain verifying)
- End of sprint — full retro, ROADMAP version bump, Sprint 003 kickoff

---

## Risk register

| Risk | Mitigation |
|---|---|
| Inkwell schema migration breaks existing content | Backfill all existing posts to `tier=public` before enforcement turns on |
| sos-docs schema disagrees with §1 engram tier model | Athena gates; tier function imported from `sos/contracts/`, not re-implemented |
| 2B.4 slipping = plaintext secrets persist | Documented; Sprint 003 priority. Hadi knows. |
| 2B.3 magic-link extends `agents-onboarding ↔ kernel-capability` leak | Athena gates the diff; if leak deepens, fix-first |
| K9/K10 audit overlap with 2B.2 | Loom confirms absorption with Athena before Kasra ships |
| Kasra at capacity | Two internal subagents + Athena absorbing Mirror migrations |

---

## Definition of done

- Burst 2A: visit mumega.com as anonymous → public slice. Auth as customer → customer slice. Auth as agent → role slice. Same sos-docs graph backing all three. 11 root docs no longer canonical files — graph is.
- Burst 2B: audit chain writing to WORM, hash chain verifies, R2 anchor proves no tampering. One contractor logs in via magic-link, sees self-tier profile.
- Sprint 003 backlog scribed with everything slipped (no dropped work).

---

## Versioning

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-04-24 | Initial Sprint 002 plan. Two parallel bursts proposed. |
| v1.1 | 2026-04-25 | Agreed plan locked: Athena gates GREEN with patches; Kasra commitments confirmed; 5 resolutions (middleware shape, §12 reuse, Vault scope, erasure slip, onboarding leak); §11 4-patch set applied; SSO/Vault/erasure 6.11 slipped to Sprint 003. |
