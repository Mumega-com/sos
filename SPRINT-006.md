# Sprint 006 — "Customer-Readiness Arc" (DRAFT v0.1, loom branch)

**Sprint window:** 2026-05-10 → 2026-05-23 (opens after Sprint 005 close)
**Sprint goal:** From *audit-clean substrate* (Sprint 005) to *substrate that can host a paying customer's IdP, agents, and quests with production HA*. Move from "the substrate's claims are defensible" to "the substrate is *delivering* under load."
**Sprint owner:** Loom (coordination + spec) + Kasra (execution lead) + Athena (gate + Mirror)
**Mandate from Hadi:** *All phases done majestically.* Sprint 005 closes the constitutional integrity arc. Sprint 006 opens the first-customer arc.
**Status:** v0.1 DRAFT on `loom` branch — pre-gate scope outline. Awaiting Sprint 005 close before opening canonical.

---

## Why this sprint

Per `project_phase_roadmap.md` and Sprint 005 close mandate:
> After Sprint 005 close: substrate is audit-clean. Sprint 006 opens with focused customer-readiness arc (production HA + SOC 2 prep + first customer IdP playbook + CSA STAR filing + ToRivers groundwork).

The Sprint 005 → 006 handoff: substrate is audit-clean *as code*. Sprint 006 makes it audit-clean *as a running, customer-facing system*. Different proof surface — same constitutional contracts.

---

## Track C — Sprint 006 observability carries (~0.5d)

| # | Task | Owner | Effort | Gate |
|---|---|---|---|---|
| C.6 | `.sprint_markers/ → audit_events` drain. One-shot script: scan `.sprint_markers/*.json`, emit each via audit_chain if kernel reachable, rename to `.sprint_markers/ingested/` to avoid double-emit. Either systemd oneshot on kernel startup OR CLI `python3 -m sos.observability.sprint_telemetry drain-markers`. Closes the visibility gap surfaced by Sprint 005 mid-snapshot (gate verdicts existing on disk but not in audit_events when emit happens kernel-disconnected). | Athena (drafts) | 0.25d | none (observability item) |

**Acceptance:** Running drain script on a populated `.sprint_markers/` dir migrates entries to `audit_events`, idempotent across re-runs (already-ingested files don't double-emit).

---

## Track A — Production HA (~4d)

The matchmaker is single-instance. Mirror is single-instance. Squad Service is single-instance. None of this survives a process restart cleanly. Customers cannot accept that.

| # | Task | Owner | Effort | Gate |
|---|---|---|---|---|
| A.1 | matchmaker.service: dual-instance with PG advisory leader-lock; loser observes-only | Kasra | 1d | G35 |
| A.2 | Mirror API :8844 dual-instance behind nginx upstream w/ active health check | Kasra | 0.5d | G36 |
| A.3 | Squad Service :8060 dual-instance + sticky-task assignment via `claim_owner_pid` extension | Kasra | 1d | G37 |
| A.4 | Postgres: streaming replica on second VPS; promote runbook documented | Kasra | 1d | G38 |
| A.5 | Audit chain anchor service: dual-instance with quorum (don't double-anchor; one writes, other verifies) | Kasra | 0.5d | G39 |

**Acceptance:** kill -9 any one of the 5 services and the substrate continues dispatching within 30s. PG primary loss detected within 60s. Audit anchors continue uninterrupted.

---

## Track B — First customer IdP playbook (~2d)

Sprint 005 closes SCIM/SAML at code level. Sprint 006 makes the *operational* path documented + smoke-tested against a real IdP (Okta dev account, Auth0 free tier, or internal AD).

| # | Task | Owner | Effort | Gate |
|---|---|---|---|---|
| B.1 | Connect Okta dev tenant via SAML; smoke-test full SSO flow citizen → kernel | Loom (specs) + Kasra (build) | 1d | G40 |
| B.2 | Connect Okta SCIM; smoke-test user provision/deprovision; verify nullify+confiscate fires on deprovision | Kasra | 0.5d | G41 |
| B.3 | First-customer IdP playbook doc: stepwise IdP-onboarding runbook + customer-facing template | Loom | 0.5d | G42 |

**Acceptance:** Loom can hand a customer a 1-page runbook + 1 dashboard URL and have them SSO into substrate in under 30 min. SCIM deprovision triggers PIPEDA-compliant erasure flow. End-to-end audit chain shows the citizen joining, doing one quest, and being deprovisioned.

---

## Track C — SOC 2 Type I prep (~2d)

SOC 2 Type II requires 12 months of operation; Type I is a point-in-time snapshot we can file now.

| # | Task | Owner | Effort | Gate |
|---|---|---|---|---|
| C.1 | Trust Service Criteria (CC1-CC9) mapping to existing substrate controls | Loom | 1d | G43 |
| C.2 | Evidence package: audit chain samples, R2 anchor proofs, RBAC test runs, MFA/SSO logs | Kasra | 0.5d | G44 |
| C.3 | Trust Center page update: SOC 2 Type I in-progress badge + audit firm contact | Loom | 0.25d | none |
| C.4 | Engage audit firm (Prescient / A-LIGN / Drata) — discovery call + scope letter | Hadi | 0.25d | none |

**Acceptance:** Type I evidence package complete; firm engaged; readiness assessment scheduled. (Type I attest doesn't land in this sprint — it's an external timeline.)

---

## Track D — CSA STAR + ISO 27001 groundwork (~1d)

CSA STAR Level 1 is self-attest — we can file inside the sprint. ISO 27001 is an external timeline; we prep documentation.

| # | Task | Owner | Effort | Gate |
|---|---|---|---|---|
| D.1 | CAIQ v4.0.2 self-assessment (already mapped on Trust Center) → CSA STAR Level 1 submission | Loom | 0.5d | G45 |
| D.2 | ISO 27001 Statement of Applicability draft (114 controls × applicability matrix) | Loom | 0.5d | G46 |

**Acceptance:** STAR Level 1 filed. SOA draft ready for legal/audit review next sprint.

---

## Track E — ToRivers groundwork (~1d)

Hadi's strategic plan: Mumega Inc. (Delaware C-corp) holds the IP; ToRivers Ltd. (Canadian opco) holds the customer contracts; Digid Inc. (Canadian opco) holds Hadi's existing Digid customer base. ToRivers is the future revenue vehicle.

This sprint = legal + technical groundwork only; not a customer-pitching sprint.

| # | Task | Owner | Effort | Gate |
|---|---|---|---|---|
| E.1 | ToRivers Ltd. domain reservation + Cloudflare zone + DNS pointing to substrate | Loom | 0.25d | none |
| E.2 | Tenant onboarding flow: Discord intake → GitHub OAuth → Stripe quote — adapt for ToRivers UX | Kasra | 0.5d | G47 |
| E.3 | Per-customer agent knight minting hook — minted on contract sign | Loom (spec) + Kasra (build) | 0.25d | G48 |

**Acceptance:** ToRivers domain live with substrate landing page. Tenant onboarding flow tested end-to-end with synthetic customer. Knight-minting hook fires on Stripe webhook.

---

## Open questions (§7 of brief template)

- **Q1: Is Track A scope right?** PG streaming replica on a second VPS adds Hetzner cost. Confirm with Hadi before provisioning.
- **Q2: Which IdP for B.1?** Okta dev is free; Auth0 free tier is also workable. Pick one. (Loom recommends Okta — most common in customer environments.)
- **Q3: SOC 2 audit firm choice (C.4) is Hadi's call.** Loom can prep RFP if helpful.
- **Q4: ToRivers domain registration is a real-money + legal move.** Hadi authorizes specifically before E.1 fires.

---

## Definition of done

- **Track A:** kill-9 stress test passes on all 5 services; substrate self-heals within 30s.
- **Track B:** First-customer IdP runbook handed to Loom-as-customer-proxy and works end-to-end.
- **Track C:** SOC 2 Type I evidence package complete; firm engaged.
- **Track D:** STAR Level 1 filed; SOA draft ready.
- **Track E:** ToRivers domain live; onboarding flow smoke-tested.

After Sprint 006 close: substrate is **production-ready for first paying customer.** Sprint 007 opens with first-customer onboarding (Ron O'Neil's PEI lead pipeline, Gavin's warm leads, Riipen student program — whichever lands first).

---

## Versioning

| Version | Date | Change |
|---|---|---|
| v0.1 | 2026-04-25 | DRAFT on `loom` branch — Loom autonomous scoping post-Sprint 005 mandate. Pending Athena gate + Hadi review before merge to main. |

---

## CGL note (FRC 841.004 corrected stance)

This sprint is *high-α* work — production HA, IdP integration, SOC 2 — every track has high uncertainty + high information gain. Not consolidation. The substrate is in α>0 right now (Sprint 004 dispatching clean), so the right move is to *spend* that stability dividend on this hard-transition sprint, not to curate Sprint 005 indefinitely. Per Hadi's correction 2026-04-25: skip easy gradients to spend on hard ones.
