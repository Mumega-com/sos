# Sprint 006 — "Customer-Readiness Arc" (DRAFT v0.2, loom branch)

**Sprint window:** 2026-05-10 → 2026-05-23 (opens after Sprint 005 close)
**Sprint goal:** From *audit-clean substrate* (Sprint 005) to *substrate that can host a paying customer's IdP, agents, and quests with production HA*. Move from "the substrate's claims are defensible" to "the substrate is *delivering* under load."
**Sprint owner:** Loom (coordination + spec) + Kasra (execution lead) + Athena (gate + Mirror)
**Mandate from Hadi:** *All phases done majestically.* Sprint 005 closed the constitutional integrity arc with 8/8 GREEN gates and 0 post-GREEN adversarial BLOCKs. Sprint 006 opens the first-customer arc.
**Status:** v0.2 DRAFT on `loom` branch — folds in Sprint 005 confirmed carries (Athena 2026-04-25 17:40 UTC). Awaiting Athena structural gate before merge to main.

---

## Why this sprint

Sprint 005 closed clean: substrate audit-clean as code. Empirical record on the parallel adversarial protocol now stands at 5 GREEN + 7 post-GREEN BLOCKs (Sprint 004) → 8 GREEN + 0 post-GREEN BLOCKs (Sprint 005). The protocol works.

Sprint 006 makes the substrate audit-clean *as a running, customer-facing system*. Different proof surface — same constitutional contracts. Plus the carries from Sprint 005 that scoped separately (superuser migrations, R2 Object Lock v2, SCIM soft notes, post-G27/G34 hardening, marker drain).

---

## Track A — Production HA (~4d)

The matchmaker is single-instance. Mirror is single-instance. Squad Service is single-instance. None of this survives a process restart cleanly. Customers cannot accept that.

| # | Task | Owner | Effort | Gate |
|---|---|---|---|---|
| A.1 | matchmaker.service: dual-instance with PG advisory leader-lock; loser observes-only | Kasra | 1d | G50 |
| A.1b | New R2 bucket `sos-audit-worm-v2` created with `--object-lock` flag at creation time; update `AUDIT_R2_BUCKET` env; re-anchor existing chain to v2; sunset v1 bucket. **Carry from Sprint 005** (the v1 bucket cannot retroactively get Object Lock; v2 is required to restore full WORM). | Kasra | 0.5d | G51 |
| A.2 | Mirror API :8844 dual-instance behind nginx upstream w/ active health check | Kasra | 0.5d | G52 |
| A.3 | Squad Service :8060 dual-instance + sticky-task assignment via `claim_owner_pid` extension | Kasra | 1d | G53 |
| A.4 | Postgres: streaming replica on second VPS; promote runbook documented | Kasra | 1d | G54 |
| A.5 | Audit chain anchor service: dual-instance with quorum (don't double-anchor; one writes, other verifies) | Kasra | 0.5d | G55 |

**Acceptance:** kill -9 any one of the 5 services and the substrate continues dispatching within 30s. PG primary loss detected within 60s. Audit anchors continue uninterrupted with v2 Object Lock enforcing.

---

## Track B — First customer IdP playbook + SCIM hardening (~3d)

Sprint 005 closed SCIM/SAML at code level. Sprint 006 (a) makes the *operational* path documented + smoke-tested against a real IdP, and (b) closes the three SCIM soft notes Athena flagged at G_SCIM gate close.

| # | Task | Owner | Effort | Gate |
|---|---|---|---|---|
| B.1 | Connect Okta dev tenant via SAML; smoke-test full SSO flow citizen → kernel | Loom (specs) + Kasra (build) | 1d | G56 |
| B.2 | Connect Okta SCIM; smoke-test user provision/deprovision; verify nullify+confiscate fires on deprovision | Kasra | 0.5d | G57 |
| B.3 | First-customer IdP playbook doc: stepwise IdP-onboarding runbook + customer-facing template | Loom | 0.5d | G58 |
| B.4a | SCIM soft note: unknown-tier `-1 → 999` default → reject explicitly with `tier_unrecognised` error. **Carry from Sprint 005.** | Kasra | 0.1d | G59 |
| B.4b | SCIM soft note: dead `tenant_id` parameter in `scim_deprovision_user` (already derived from idp_id; remove dead param). **Carry from Sprint 005.** | Kasra | 0.1d | G60 |
| B.4c | SCIM soft note: pre-existing high-tier `idp_group_role_map` rows audit query — surface any current entries that exceed an IdP's `max_grantable_tier` so coordinator can reconcile. **Carry from Sprint 005.** | Kasra | 0.25d | G61 |
| B.5 | G27 follow-up: per-principal MFA INSERT flood quota + wire cleanup job (the 5-min-retention DELETE noted in F-09 brief). **Carry from Sprint 005.** | Kasra | 0.25d | G62 |
| B.6 | G34 follow-up: SAML assertion ID prediction warn (detect IdP issuing predictable IDs); connection pooling on `saml_used_assertions` insert path; DB CHECK clamp on `not_on_or_after` (reject assertions claiming TTL > 24h). **Carry from Sprint 005.** | Kasra | 0.5d | G63 |

**Acceptance:** Loom can hand a customer a 1-page runbook + 1 dashboard URL and have them SSO into substrate in under 30 min. SCIM deprovision triggers PIPEDA-compliant erasure flow. End-to-end audit chain shows the citizen joining, doing one quest, and being deprovisioned. All three SCIM soft notes closed; G27/G34 hardening shipped.

---

## Track C — SOC 2 Type I prep + observability carries (~2.5d)

SOC 2 Type II requires 12 months of operation; Type I is a point-in-time snapshot we can file now.

| # | Task | Owner | Effort | Gate |
|---|---|---|---|---|
| C.1 | Trust Service Criteria (CC1-CC9) mapping to existing substrate controls | Loom | 1d | G64 |
| C.2 | Evidence package: audit chain samples, R2 anchor proofs, RBAC test runs, MFA/SSO logs | Kasra | 0.5d | G65 |
| C.3 | Trust Center page update: SOC 2 Type I in-progress badge + audit firm contact | Loom | 0.25d | none |
| C.4 | Engage audit firm (Prescient / A-LIGN / Drata) — discovery call + scope letter | Hadi | 0.25d | none |
| C.6 | `.sprint_markers/ → audit_events` drain. One-shot script: scan `.sprint_markers/*.json`, emit each via audit_chain if kernel reachable, rename to `.sprint_markers/ingested/` to avoid double-emit. Either systemd oneshot on kernel startup OR CLI `python3 -m sos.observability.sprint_telemetry drain-markers`. **Carry from Sprint 005** (closes the visibility gap surfaced by Sprint 005 mid-snapshot — gate verdicts existing on disk but not in audit_events when emit happens kernel-disconnected). | Athena (drafts) | 0.25d | none |

**Acceptance:** Type I evidence package complete; firm engaged; readiness assessment scheduled. C.6 drain ingests `.sprint_markers/` cleanly into `audit_events` so future sprint-stats snapshots include kernel-disconnected emit history.

---

## Track D — CSA STAR + ISO 27001 groundwork (~1d)

CSA STAR Level 1 is self-attest — we can file inside the sprint. ISO 27001 is an external timeline; we prep documentation.

| # | Task | Owner | Effort | Gate |
|---|---|---|---|---|
| D.1 | CAIQ v4.0.2 self-assessment (already mapped on Trust Center) → CSA STAR Level 1 submission | Loom | 0.5d | G66 |
| D.2 | ISO 27001 Statement of Applicability draft (114 controls × applicability matrix) | Loom | 0.5d | G67 |

**Acceptance:** STAR Level 1 filed. SOA draft ready for legal/audit review next sprint.

---

## Track E — ToRivers groundwork (~1d)

Hadi's strategic plan: Mumega Inc. (Delaware C-corp) holds the IP; ToRivers Ltd. (Canadian opco) holds the customer contracts; Digid Inc. (Canadian opco) holds Hadi's existing Digid customer base. ToRivers is the future revenue vehicle.

This sprint = legal + technical groundwork only; not a customer-pitching sprint.

| # | Task | Owner | Effort | Gate |
|---|---|---|---|---|
| E.1 | ToRivers Ltd. domain reservation + Cloudflare zone + DNS pointing to substrate | Loom | 0.25d | none |
| E.2 | Tenant onboarding flow: Discord intake → GitHub OAuth → Stripe quote — adapt for ToRivers UX | Kasra | 0.5d | G68 |
| E.3 | Per-customer agent knight minting hook — minted on contract sign | Loom (spec) + Kasra (build) | 0.25d | G69 |

**Acceptance:** ToRivers domain live with substrate landing page. Tenant onboarding flow tested end-to-end with synthetic customer. Knight-minting hook fires on Stripe webhook.

---

## Track F — Track A constitutional integrity superuser migrations (~1d, scoped separately)

These four migrations require Postgres superuser access (they touch role grants on tables that mirror role does not own). Sprint 005 deferred them by Athena's call — execution requires a coordinated maintenance window with Hadi present, not autonomous gate-and-build.

| # | Task | Owner | Effort | Gate |
|---|---|---|---|---|
| F.1 | F-02b: superuser migration to REVOKE INSERT FROM mirror on `reputation_events` (closes the audit-chain bypass enforcement gap). **Carry from Sprint 005.** | Athena (schema gate) + Kasra (build) + Hadi (superuser) | 0.25d | G19 |
| F.2 | F-01b + P0-2b: superuser migration for `frc_verdicts` REVOKE EXECUTE FROM PUBLIC + GRANT to classifier_role; backfill existing `mirror_engrams.classifier_run_log` rows into `frc_verdicts` (fail-open transition). **Carry from Sprint 005.** | Athena + Kasra + Hadi | 0.5d | G20 |
| F.3 | F-11b: signature enforcement in `audit_to_reputation` once `AUDIT_SIGNING_KEY` distributed (HSM or kernel keystore). **Carry from Sprint 005.** | Kasra + Hadi | 0.25d | G21 |

**Acceptance:** all 4 P0 superuser items closed with REVOKE actually enforcing. `frc_emit_verdict()` callable only by classifier_role. Audit chain trigger validates Ed25519 signature.

**Why scoped separately:** these require live superuser session with Hadi; cannot run via the Athena+Kasra+Loom autonomous protocol. Treat as a single half-day maintenance window, not a track of independent items.

---

## Open questions

- **Q1: Track A HA cost.** Streaming replica on second VPS adds Hetzner cost (~€20-40/mo per node). Confirm with Hadi before provisioning. If Hadi prefers single-instance + faster recovery via systemd auto-restart, A.4 RESHAPE.
- **Q2: Which IdP for B.1?** Okta dev (free) recommended. Auth0 free tier is workable. Hadi confirms or RESHAPE.
- **Q3: SOC 2 audit firm choice (C.4) is Hadi's call.** Loom can prep RFP comparison if helpful.
- **Q4: ToRivers domain registration is a real-money + legal move.** Hadi authorizes specifically before E.1 fires.
- **Q5: Track F maintenance window timing.** Track F requires Hadi present. Schedule before sprint open or mid-sprint?

---

## Definition of done

- **Track A:** kill-9 stress test passes on all 5 services; substrate self-heals within 30s; v2 Object Lock bucket enforcing.
- **Track B:** First-customer IdP runbook handed to Loom-as-customer-proxy and works end-to-end; all 3 SCIM soft notes closed; G27/G34 hardening shipped.
- **Track C:** SOC 2 Type I evidence package complete; firm engaged; sprint marker drain operational.
- **Track D:** STAR Level 1 filed; SOA draft ready.
- **Track E:** ToRivers domain live; onboarding flow smoke-tested.
- **Track F:** All 4 P0 superuser migrations enforcing; classifier_role-only on `frc_emit_verdict()`; audit chain Ed25519 signature validating.

After Sprint 006 close: substrate is **production-ready for first paying customer.** Sprint 007 opens with first-customer onboarding (Ron O'Neil's PEI lead pipeline, Gavin's warm leads, Riipen student program — whichever lands first) plus Phase 5 metabolic-loop arc (§10 + §10a).

---

## Adversarial-parallel surface coverage

Per AGD discipline (Athena's protocol): tracks touching the four canonical sensitive surfaces require parallel adversarial review.

| Track | Surface touched | Parallel required? |
|---|---|---|
| A.1 (matchmaker HA) | Eligibility logic + reputation write paths (matchmaker writes outcomes) | YES |
| A.5 (audit chain HA) | Audit chain integrity | YES |
| B.1/B.2 (Okta SAML+SCIM) | External-facing auth surface | YES |
| B.4a/b/c (SCIM soft notes) | External-facing auth surface | YES |
| B.5 (G27 follow-up) | External-facing auth (MFA) | YES |
| B.6 (G34 follow-up) | External-facing auth (SAML) | YES |
| F.1/F.2/F.3 (superuser migrations) | Reputation/identity write paths + audit chain | YES |
| C.6 (marker drain) | Audit chain integrity (writes audit_events) | YES |
| E.2 (tenant onboarding GitHub OAuth → Stripe quote) | External-facing auth surface (path produces session + principal) | YES |
| E.3 (knight-minting hook on Stripe webhook) | Identity write path (creates principals on webhook) | YES |
| Other tracks | none of the four | NO |

15 of the gate items require parallel adversarial review. Schedule subagent capacity accordingly.

---

## Versioning

| Version | Date | Change |
|---|---|---|
| v0.1 | 2026-04-25 | DRAFT on `loom` branch — Loom autonomous scoping post-Sprint 005 mandate. Pending Athena gate + Hadi review before merge to main. |
| v0.2 | 2026-04-25 | Folds in Sprint 005 confirmed carries (Athena 17:40 UTC): A.1b R2 v2 bucket, B.4a/b/c SCIM soft notes, B.5 G27 follow-up, B.6 G34 follow-up, C.6 marker drain, Track F superuser migrations as separate maintenance-window scope. Adversarial-parallel surface coverage matrix added per AGD discipline. Awaits Athena structural gate before merge to main. |
| v0.3 | 2026-04-25 | Athena gate fixes (BLOCK→GREEN same-turn): adversarial-parallel matrix gains E.2 (tenant onboarding auth path) + E.3 (Stripe webhook identity write) → 15 items requiring parallel review. Q6 removed (§10a decided to Sprint 007/008, not an opportunistic Sprint 006 fill). |

---

## CGL note (FRC 841.004 corrected stance)

This sprint is *high-α* work — production HA, IdP integration, SOC 2 — every track has high uncertainty + high information gain. Not consolidation. The substrate is in α>0 right now (Sprint 005 closed clean), so the right move is to *spend* that stability dividend on this hard-transition sprint, not to curate Sprint 005 indefinitely. Per Hadi's correction 2026-04-25: skip easy gradients to spend on hard ones.
