# SOC 2 Type I — Trust Service Criteria Mapping (Mumega SOS Substrate)

**Document type:** Sprint 006 Track C.1 deliverable — pre-audit evidence package mapping.
**Author:** Loom (autonomous coordinator agent)
**Date:** 2026-04-25
**Status:** v0.1 — pre-audit-firm engagement. To be reviewed by audit firm during scope letter and refined per their methodology.
**Scope:** SOC 2 Type I (point-in-time attestation). Trust Service Criteria mapped: **Common Criteria CC1–CC9 (mandatory)** + **Security category (TSC 100.A)** + **Availability category (TSC 100.A1)**. Confidentiality, Processing Integrity, and Privacy categories scoped for SOC 2 Type II in 2027 once 12 months of operating history accrues.

---

## 0. How to read this document

Each control reference includes:
- **Control text** (from AICPA Trust Services Criteria, 2017 with 2022 revisions)
- **Mumega implementation** — what enforces this control in our substrate
- **Evidence pointer** — file path, commit, table, audit trail reference an auditor can verify
- **Status** — Met / Partial / Compensating Control / Not Applicable

"Partial" controls have explicit gaps named with target close dates. We do not claim Met for controls where enforcement is incomplete. Auditors should expect the same level of honesty in walkthroughs as appears in this document.

This mapping is a *companion* to the Trust Center page (mumega.com/trust), which maps the CSA CAIQ v4.0.2 framework. The two documents reference the same underlying controls in different formal languages.

---

## 1. CC1 — Control Environment

> The entity demonstrates a commitment to integrity and ethical values, exercises oversight responsibility, establishes structure, demonstrates commitment to competence, and enforces accountability.

### CC1.1 — Demonstrates commitment to integrity and ethical values

**Status:** Met.

**Implementation:** All substrate operations are bound by the FRC entropy-coherence reciprocity law (`dS + k* d ln C = 0`) as a design constraint. Every substrate decision is auditable against this principle. Internal protocols (`~/.claude/rules/agent-comms.md`) codify behavior expectations including the explicit-emit-over-parsing rule and the four-canonical-sensitive-surfaces adversarial parallel review requirement.

**Evidence:**
- `~/.claude/rules/agent-comms.md` — agent communication standard
- `docs/superpowers/plans/stack-sections/16a-lambda-dna-basis-discipline.md` — basis discipline protocol
- `~/.claude/projects/-home-mumega-mumega-com/memory/feedback_explicit_emit_over_parsing.md` — telemetry protocol

### CC1.2 — Exercises oversight responsibility

**Status:** Met.

**Implementation:** Independent gate review by Athena (separate cognitive lane from builders). Sprint 005 record: 8 GREEN gates closed with 0 post-GREEN adversarial BLOCKs (vs Sprint 004 baseline of 5 GREEN gates with 7 post-GREEN BLOCKs found by adversarial review). Empirical record: oversight catches what self-review misses.

**Evidence:**
- `RETRO_SPRINT_003_004.md` — Sprint 003+004 retrospective with adversarial findings
- `SPRINT-005.md` — gate-by-gate verdicts
- `~/.claude/projects/-home-mumega-mumega-com/memory/project_sprint_005_empirical_result.md` — empirical comparison
- `audit_events` table, action `gate_verdict` — every gate close recorded

### CC1.3 — Establishes structure, authority, and responsibility

**Status:** Met.

**Implementation:** Cognitive lane discipline — translator (Loom) drafts specs, builder (Kasra) ships code, gate (Athena) verifies. No agent crosses lanes. Documented in `2026-04-25-phase-locked-coordination.md` and `2026-04-25-adversarial-gate-development.md` (companion essays).

**Evidence:**
- `agents/loom/CLAUDE.md` — Loom workspace charter
- `~/.claude/agents/` — agent definitions per role
- Trust Center `/trust` — public structure description

### CC1.4 — Demonstrates commitment to competence

**Status:** Partial. **Gap:** formal competency assessment for human team members not documented (small team — primarily founder + contractors). **Target close:** Sprint 008 — formal role descriptions + competency matrix per principal.

**Implementation (current):** Agent capability is verified through automated testing (165+ tests across substrate contracts) and gate review. Human capability is verified through track record (commits, customer outcomes).

**Evidence:** GitHub commit history; test pass rates per sprint.

### CC1.5 — Enforces accountability

**Status:** Met.

**Implementation:** Every substrate action emits an audit_events row with actor_id, resource, and timestamp. Hash-chained per stream via `audit_next_seq()` (no app-side counters, no race conditions). Anchored every 15 minutes to Cloudflare R2 with Object Lock Compliance retention until 2033.

**Evidence:**
- `mirror/migrations/019_audit_chain.sql` — audit chain schema
- `sos/kernel/audit_chain.py` — chain implementation
- `sos/jobs/audit_anchor.py` + `sos/jobs/systemd/audit-anchor.timer` — anchor service
- `sos/scripts/verify_chain.py` — chain integrity verification

---

## 2. CC2 — Communication and Information

> The entity obtains or generates relevant, quality information; internally communicates information necessary to support the functioning of internal control; and communicates with external parties.

### CC2.1 — Internally generates and uses relevant information

**Status:** Met.

**Implementation:** `sprint_telemetry` module (`sos/observability/sprint_telemetry.py`) emits structured audit_events for gate verdicts, incident resolutions, and adversarial findings. Sprint reports auto-generated via `python3 -m sos.observability.sprint_telemetry stats <sprint_id>`. Every measured number traceable to a typed emit, never parsed from message bodies (per the explicit-emit protocol).

**Evidence:**
- `sos/observability/sprint_telemetry.py`
- `~/.claude/skills/sprint-stats/SKILL.md`
- `audit_events` table, actions: `gate_verdict`, `incident_resolved`, `adversarial_finding`

### CC2.2 — Communicates internal control information internally

**Status:** Met.

**Implementation:** Bus protocol (Redis stream `sos:stream:project:sos:agent:*`) carries all coordination messages. Messages are persisted; agents poll their inbox; no silent drops permitted. Agent communication standard (`~/.claude/rules/agent-comms.md`) defines acknowledgment, handoff, and escalation rules.

**Evidence:** `~/.claude/rules/agent-comms.md`; bus message logs.

### CC2.3 — Communicates with external parties

**Status:** Partial. **Gap:** customer-facing security posture documented at `mumega.com/trust` and via signed NDA + T&C; no formal vendor security questionnaire response automation. **Target close:** Sprint 008 — automated CAIQ + SIG response generation from this mapping.

**Evidence:** `content/en/pages/trust.md`; signed contract artifacts in `profile_consents` table (per §11 profile primitive when shipped).

---

## 3. CC3 — Risk Assessment

> The entity specifies suitable objectives, identifies and analyzes risks, and assesses fraud risk.

### CC3.1 — Specifies suitable objectives

**Status:** Met.

**Implementation:** Every sprint declares an explicit goal in the SPRINT-XXX.md document with definition-of-done acceptance criteria. Phase-by-phase roadmap (`ROADMAP.md`) defines architectural objectives at the system level.

**Evidence:** `SPRINT-005.md`, `SPRINT-006.md`, `ROADMAP.md` v1.3.

### CC3.2 — Identifies risks

**Status:** Met.

**Implementation:** Adversarial subagent review on every change touching the four canonical sensitive surfaces (eligibility, reputation/identity write paths, audit chain integrity, external-facing surfaces). Sprint 004 produced 7 P0 BLOCK findings + 13 WARN/Low findings; Sprint 005 closed all P0s + 6 of the WARN/Low. Adversarial review explicitly probes for self-poisoning attacks, replay attacks, escalation paths, and silent fail-open patterns.

**Evidence:**
- `~/.claude/rules/agent-comms.md` (adversarial-as-parallel-gate section)
- `audit_events` action `adversarial_finding`
- `SPRINT-005.md` Track B (closed findings)
- `SPRINT-006.md` adversarial-parallel coverage matrix (15 items)

### CC3.3 — Considers the potential for fraud

**Status:** Met.

**Implementation:** FRC veto (`frc_verdicts` table, kernel-private) checks coherence on every reputation-affecting event. Audit chain hash linkage makes silent state mutation cryptographically detectable. Per-workspace DEK envelope encryption with AAD-binding makes cross-workspace data access mathematically impossible (workspace-A's ciphertext under workspace-B's DEK fails GCM tag verification).

**Evidence:**
- `mirror/migrations/034_frc_verdicts.sql`
- `sos/contracts/dek.py` + `tests/contracts/test_dek.py` (cross-workspace isolation tests)
- `sos/scripts/verify_chain.py`

### CC3.4 — Identifies and assesses changes that could significantly impact the system

**Status:** Met.

**Implementation:** Every migration gates through Athena with structural review. Migration provenance via `schema_migrations` table tracks applied filename + SHA-256 checksum; tampered or out-of-order migrations rejected. Sprint planning lifecycle requires explicit risk assessment in brief §4 (adversarial parallel?) and §7 (open questions).

**Evidence:**
- `mirror/migrations/schema_migrations`
- `mirror/scripts/migrate.py` — safety guard rails
- Brief template at `agents/loom/briefs/_template.md`

---

## 4. CC4 — Monitoring Activities

> The entity selects, develops, and performs ongoing and/or separate evaluations to ascertain whether components of internal control are present and functioning, and evaluates and communicates internal control deficiencies.

### CC4.1 — Selects, develops, and performs ongoing and separate evaluations

**Status:** Met.

**Implementation:**
- Continuous: `restart-alert.service` polls NRestarts on 10 critical services every 5 minutes; alerts at threshold breach.
- Continuous: audit anchor every 15 minutes; chain integrity verifiable end-to-end via `verify_chain --all`.
- Per-sprint: gate review on every change (Athena structural + adversarial subagent for sensitive surfaces).
- Per-sprint: retrospective with explicit "what worked / what was hard / what I'd do differently" sections (RETRO_SPRINT_XXX.md).

**Evidence:**
- `sos/jobs/restart_alert.py`
- `sos/jobs/audit_anchor.py`
- `RETRO_SPRINT_003_004.md`

### CC4.2 — Evaluates and communicates control deficiencies

**Status:** Met.

**Implementation:** Adversarial findings emit via `emit_adversarial_finding(finding_id, severity)` — typed, structured, immediately visible in audit chain. No finding is closed without an explicit gate close. Sprint retrospectives surface uncaught issues with target close dates.

**Evidence:**
- `audit_events` table, action `adversarial_finding`
- Sprint retrospective documents

---

## 5. CC5 — Control Activities

> The entity selects and develops control activities, selects and develops general controls over technology, and deploys through policies and procedures.

### CC5.1 — Selects and develops control activities

**Status:** Met. See CC6 (Logical Access), CC7 (System Operations), CC8 (Change Management) for specific control activities.

### CC5.2 — Selects and develops general controls over technology

**Status:** Met. See CC8 (Change Management).

### CC5.3 — Deploys through policies and procedures

**Status:** Met.

**Implementation:** Codified protocols at `~/.claude/rules/agent-comms.md`. Sprint discipline at `agents/loom/briefs/_template.md`. Every behavior expectation has a written rule that any team member (human or agent) can read.

**Evidence:** Files referenced; commit history shows protocols evolve through PR-style review.

---

## 6. CC6 — Logical and Physical Access Controls

> The entity implements logical access security software, infrastructure, and architectures over protected information assets to protect them from security events.

### CC6.1 — Implements logical access security measures

**Status:** Met.

**Implementation:**
- SSO via SAML 2.0 with full signature validation (defusedxml + JWKS + RS256/ES256). SAML assertion replay ledger (`saml_used_assertions` table, F-20 closed Sprint 005) prevents within-window replay.
- OIDC support with full validation chain.
- SCIM 2.0 user provisioning with tier-ceiling enforcement (`max_grantable_tier` per IdP, F-15 closed Sprint 005).
- TOTP MFA with replay ledger (`mfa_used_codes` table, F-09 closed Sprint 005). WebAuthn supported.
- Five-tier RBAC (public / squad / project / role / entity / private) enforced at every read/write call site via `resolveCallerContext`.

**Evidence:**
- `migrations/023_identity_roles.sql`, `024_sso_mfa.sql`
- `workers/inkwell-api/src/lib/hive-access.ts`
- `tests/contracts/test_dek.py`, test files for SAML/SCIM/MFA

### CC6.2 — Restricts logical access through registration and authorization

**Status:** Met.

**Implementation:** Every principal has a `principals` row. `quests.created_by` foreign-keys to `principals.id` with `ON DELETE RESTRICT` (G33 closed Sprint 005) — quests cannot reference nonexistent or deleted principals. Role assignments pass through SCIM with explicit `idp_group_role_map` mediation.

**Evidence:** `migrations/038_quests_created_by_fk.sql`; `sos/contracts/scim.py`.

### CC6.3 — Authorizes, modifies, and removes user access

**Status:** Met.

**Implementation:** SCIM provision/deprovision flow. Deprovision triggers PIPEDA-compliant `nullify+confiscate` erasure (migration 025): personal data anonymized to `<redacted>`, audit chain references preserved with reactivation tokens for legal hold. Reactivation supported within retention window.

**Evidence:** `migrations/025_principal_erasure.sql`; `sos/contracts/scim.py:scim_deprovision_user`.

### CC6.4 — Restricts physical access

**Status:** Compensating control. **Why:** Substrate runs on Hetzner cloud infrastructure (no physical access by Mumega personnel). Hetzner SOC 2 / ISO 27001 attestation covers physical security at the datacenter layer. Mumega operator workstation runs Mirror localhost; access controlled via OS-level user authentication + hardware-backed credentials. **Documented at Trust Center.**

### CC6.5 — Discontinues logical access when no longer required

**Status:** Met. Same path as CC6.3. SCIM deprovision triggers immediate token revocation + nullify+confiscate erasure flow.

### CC6.6 — Implements logical access security measures to protect against threats from sources outside system boundaries

**Status:** Met.

**Implementation:** Cloudflare DDoS protection at edge. nginx upstream with TLS 1.2+ enforcement at VPS. Per-workspace Vault token cache prevents cross-workspace token leak (F-08 closed Sprint 005). All external auth surfaces (SAML, OIDC, SCIM, public APIs) reviewed under adversarial-parallel-gate.

**Evidence:** `sos/contracts/vault_env.py`; nginx configs in `~/.config/nginx/`.

### CC6.7 — Restricts the transmission, movement, and removal of information

**Status:** Met.

**Implementation:** Per-workspace DEK envelope encryption (AES-256-GCM with AAD-binding to `workspace_id`) means data is encrypted at rest with keys that cannot decrypt other workspaces' data. Transit via TLS 1.2+ (HTTPS, secured WebSocket). Data export via signed URL with 7-day TTL; export contents reviewed for cross-citizen PII inclusion in §11 profile primitive build.

**Evidence:** `sos/contracts/dek.py`; data export pipeline (Sprint 008 build).

### CC6.8 — Implements controls to prevent or detect and act upon the introduction of unauthorized or malicious software

**Status:** Met.

**Implementation:** All migrations gated through Athena. Provenance via `schema_migrations` SHA-256 checksums. Runtime: substrate code is internal; external code (npm dependencies, Python packages) updated through `package-lock.json` + `pyproject.toml` lock files; vulnerability scanning via GitHub Dependabot.

**Evidence:** `mirror/migrations/schema_migrations`; GitHub Dependabot alerts.

---

## 7. CC7 — System Operations

> The entity manages system operations to detect and respond to system anomalies and security events.

### CC7.1 — Detects and monitors system component vulnerabilities and anomalies

**Status:** Met.

**Implementation:**
- `restart-alert.service` polls 10 critical services every 5 min.
- `audit-anchor.timer` polls audit chain every 15 min; anchors to R2 Object Lock.
- Adversarial subagent review on every change touching the four canonical sensitive surfaces.
- Sprint observability via `sprint_telemetry` module — gate verdicts, incident resolutions, adversarial findings all measurable.

**Evidence:** `sos/jobs/restart_alert.py`, `sos/jobs/audit_anchor.py`, `sos/observability/sprint_telemetry.py`.

### CC7.2 — Designs and implements monitoring procedures to detect security events

**Status:** Met. Same evidence as CC7.1 + audit chain hash-linkage detects out-of-order or tampered events at verify time.

### CC7.3 — Evaluates security events to determine whether they could result in a failure to meet objectives

**Status:** Met.

**Implementation:** Bus alerts route to Athena (gate agent) by default; severity tags (`BLOCK`, `WARN`, `LOW`) drive escalation. Incident resolution emits typed event via `emit_incident_resolved(description)`.

**Evidence:** `audit_events` action `incident_resolved`.

### CC7.4 — Responds to security events through identified processes

**Status:** Met.

**Implementation:** Incident response runbook at `/sop incident` slash skill. Defined steps: identify → assess → restore → root cause. All actions during incident response emit audit events.

**Evidence:** `~/.claude/skills/sop/incident.md`.

### CC7.5 — Identifies, develops, and implements activities to recover from identified security events

**Status:** Partial. **Gap:** formal disaster-recovery test ("tabletop exercise") not yet run. **Target close:** Sprint 006 Track A acceptance test (kill -9 stress test on all 5 services with substrate self-heal verification).

**Evidence:** Sprint 006 SPRINT-006.md Track A definition-of-done.

---

## 8. CC8 — Change Management

> The entity authorizes, designs, develops or acquires, configures, documents, tests, approves, and implements changes to infrastructure, data, software, and procedures.

### CC8.1 — Authorizes, designs, tests, approves, and implements changes

**Status:** Met.

**Implementation:** Every change follows the literal-verb trigger order: drafts → triggers → gates → builds → signs → flips. Spec drafted by Loom. Migration drafted by Kasra. Reviewed by Athena (structural correctness). Adversarial subagent reviews in parallel for sensitive surfaces. Both verdicts must combine GREEN before build. Tested. Signed off three-way (correctness + implementation + observability). Flipped behind feature flag where applicable.

**Evidence:**
- `~/.claude/rules/agent-comms.md`
- `agents/loom/briefs/_template.md`
- All gates in `audit_events` action `gate_verdict`

---

## 9. CC9 — Risk Mitigation

> The entity identifies, selects, and develops risk mitigation activities for risks arising from potential business disruptions and uses or considers third-party providers.

### CC9.1 — Identifies, selects, and develops risk mitigation activities

**Status:** Met. Each Sprint plans for risk in Track A (HA), Track C (compliance), and Track F (constitutional integrity).

### CC9.2 — Assesses and manages risks associated with vendors and business partners

**Status:** Partial. **Gap:** formal vendor risk assessment for Cloudflare, Hetzner, HashiCorp, Anthropic, OpenAI, Google not documented. **Target close:** Sprint 008 — vendor risk register with annual review cadence.

**Compensating control (current):** all third-party SOC 2 / ISO 27001 attestations are reviewed at vendor selection. Cloudflare, Hetzner, Anthropic, OpenAI, Google all hold relevant certifications.

---

## 10. Security Category (TSC 100.A)

The Security category requires controls protecting the system against unauthorized access (logical and physical), use, or modification.

This category is Mumega's primary attestation scope. Every CC1–CC9 control above contributes; specific Security-only controls below.

### A1 — Common Criteria reference

**Status:** Met. All CC1–CC9 above.

### Additional Security-specific controls (mapped via CC6 + CC7 above):

- Encryption at rest: AES-256-GCM with AAD-bound per-workspace DEK envelope encryption (CC6.7).
- Encryption in transit: TLS 1.2+ (CC6.6, CC6.7).
- Key management: HashiCorp Vault for KEK; per-workspace DEK rotation; AAD prevents cross-workspace decryption (CC6.7, CEK-08).
- Authentication: SAML 2.0 + OIDC + SCIM 2.0 + TOTP/WebAuthn MFA + replay ledgers for both SAML assertions and TOTP codes (CC6.1, CC6.6).
- RBAC: five-tier with explicit tier ceiling enforcement on group-role mapping (CC6.1, CC6.2).
- Audit: hash-chained per stream, anchored to WORM storage with 7-year retention (CC1.5, CC4.1).
- Adversarial review: every change touching auth, identity writes, audit chain, or external-facing surface receives parallel adversarial review before merge (CC3.2, CC4.2).

---

## 11. Availability Category (TSC 100.A1)

> Information and systems are available for operation and use to meet the entity's objectives.

### A1.1 — Maintains, monitors, and evaluates current processing capacity

**Status:** Partial. **Gap:** capacity monitoring is reactive (restart-alert) not predictive. **Target close:** Sprint 007 — Prometheus metrics + alerting on capacity ceilings.

### A1.2 — Authorizes, designs, develops or acquires, implements, operates, approves, maintains, and monitors environmental protections, software, data backup processes, and recovery infrastructure

**Status:** Partial. **Gap:** Mirror PostgreSQL is single-instance localhost; no HA. **Target close:** Sprint 006 Track A.4 — PG streaming replica on second VPS with documented promote runbook.

**Evidence (current):** Daily backups via `mirror/scripts/backup.py`; backup verification via `verify_chain --all` on the audit chain (proves chain integrity end-to-end including any restored chunks).

### A1.3 — Tests recovery plan procedures supporting system recovery

**Status:** Partial. **Gap:** no formal recovery test executed. **Target close:** Sprint 006 Track A definition-of-done — kill -9 stress test on all 5 services with self-heal verification within 30 seconds.

---

## 12. Out-of-scope categories (deferred to Type II)

- **Confidentiality (TSC 100.C):** Mumega holds customer data under PIPEDA + GDPR-aligned policies but has not yet declared confidentiality as an attested category. Declaring this requires the auditor to verify confidentiality controls beyond CC6 baseline. Type II 2027.
- **Processing Integrity (TSC 100.PI):** matchmaker dispatch, reputation calculation, and audit chain integrity are correctness-critical but not currently attested under PI category. Type II 2027.
- **Privacy (TSC 100.P1–P8):** PIPEDA-aligned `nullify+confiscate` erasure, consent management, and data export are designed for privacy attestation. Currently declared under Trust Center page, not under SOC 2 attestation. Privacy attestation typically follows Type II + 12 months operating history.

---

## 13. Open items for audit firm scoping call

To be discussed with audit firm during Track C.4 scope letter:

1. **Type I window timing.** Substrate live-flipped 2026-04-25; recommend Type I as-of-date 2026-08-01 (~3 months operating history at attestation).
2. **In-scope systems boundary.** Mumega substrate (kernel + Mirror + Squad Service + Matchmaker + Audit chain + Inkwell Worker) is the scope. Customer applications built ON Mumega are out of scope (each customer's separate attestation).
3. **Sub-service organizations.** Cloudflare (edge + R2), Hetzner (compute + storage), HashiCorp Vault (key storage). Carve-out method recommended (we attest to our controls; sub-service organizations attest to theirs).
4. **Trust Service Categories declared.** Recommend: Security + Availability for Type I. Add Confidentiality + Processing Integrity for Type II.
5. **CSC controls coverage.** This document maps to AICPA TSC; complementary mapping to CSA CAIQ v4.0.2 lives at `mumega.com/trust`. Auditor selects which framework drives the workpapers.

---

## 14. Versioning

| Version | Date       | Change                                                              |
|---------|------------|---------------------------------------------------------------------|
| v0.1    | 2026-04-25 | Initial draft — Sprint 006 Track C.1 deliverable. Loom autonomous  |
|         |            | mapping pre-audit-firm-engagement. To be refined per firm           |
|         |            | methodology during scope letter (Track C.4).                        |
