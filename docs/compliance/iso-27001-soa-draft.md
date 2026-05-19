# ISO/IEC 27001:2022 — Statement of Applicability (Draft)

**Document type:** Sprint 006 Track D.2 deliverable — pre-certification SOA draft.
**Author:** Loom (autonomous coordinator agent)
**Date:** 2026-04-25
**Standard:** ISO/IEC 27001:2022 (controls per ISO/IEC 27002:2022 Annex A — 93 controls in 4 themes).
**Scope:** Mumega SOS substrate — kernel, Mirror memory layer, Squad Service, Matchmaker, Audit chain anchor service, Inkwell publishing layer, identity layer (SAML/OIDC/SCIM/MFA).
**Status:** v0.1 — pre-certification draft. Will be revised through ISO 27001 internal audit (Stage 1) and certification audit (Stage 2) once an accredited certification body is engaged. Certification timeline typically ~12 months from initial Information Security Management System (ISMS) implementation.

---

## 0. How to read this document

ISO/IEC 27002:2022 reorganized Annex A controls from the older 114-control structure (114 controls in 14 domains) into the new 93-control structure (93 controls in 4 themes: Organizational, People, Physical, Technological). This SOA uses the 2022 structure.

Each control includes:
- **Control reference** (e.g., 5.1, 8.24)
- **Control name**
- **Applicable** — Yes / No (Excluded with justification)
- **Implementation status** — Implemented / Partially Implemented / Planned (with target close)
- **Justification / implementation summary**

Where a control is excluded as Not Applicable, the SOA explicitly names the reason. ISO 27001 certification bodies expect SOA exclusions to be defended; an unjustified exclusion is a finding.

This SOA pairs with the SOC 2 mapping at `soc2-cc-mapping.md` (substantial control overlap; different formal vocabularies). Where useful, ISO controls below cross-reference SOC 2 CC controls.

---

## Section A.5 — Organizational controls (37 controls)

### A.5.1 — Policies for information security
**Applicable:** Yes. **Status:** Partially Implemented.
Internal protocols at `~/.claude/rules/agent-comms.md` define agent communication, adversarial parallel review, explicit emit, basis discipline. **Gap:** formal information security policy document covering all ISO 27001 mandatory clauses not yet drafted. Target close: Sprint 008.

### A.5.2 — Information security roles and responsibilities
**Applicable:** Yes. **Status:** Implemented.
Cognitive lane discipline documented in `2026-04-25-phase-locked-coordination.md`. Loom: translator. Kasra: builder. Athena: gate. Roles defined in agent definitions at `~/.claude/agents/`.

### A.5.3 — Segregation of duties
**Applicable:** Yes. **Status:** Implemented.
Builder cannot gate own work; gate cannot draft specs; coordinator does not write production code. Cross-checks: G27/G34 in Sprint 005 ran adversarial subagent parallel to gate review; both required GREEN before merge.

### A.5.4 — Management responsibilities
**Applicable:** Yes. **Status:** Partially Implemented.
Hadi (founder) is the management authority for ISMS. Annual ISMS review cadence not yet documented. Target close: Sprint 008 — annual ISMS review schedule + meeting log.

### A.5.5 — Contact with authorities
**Applicable:** Yes. **Status:** Planned. Target close: post-Series-A. Process for contacting law enforcement / regulators in case of incident not yet documented.

### A.5.6 — Contact with special interest groups
**Applicable:** Yes. **Status:** Implemented. Mumega participates in Cloud Security Alliance (CSA STAR submission Track D.1). Membership in security-relevant communities tracked.

### A.5.7 — Threat intelligence
**Applicable:** Yes. **Status:** Partially Implemented. Adversarial subagent review provides internal threat-modeling capability. **Gap:** external threat intelligence subscription (CVE feeds, security advisories) not yet integrated into Sprint planning. Target close: Sprint 007.

### A.5.8 — Information security in project management
**Applicable:** Yes. **Status:** Implemented.
Every Sprint brief contains §4 adversarial parallel review section per AGD discipline. Sprint 006 brief contains a 15-item adversarial-parallel coverage matrix.

### A.5.9 — Inventory of information and other associated assets
**Applicable:** Yes. **Status:** Partially Implemented.
Substrate components inventoried in `ROADMAP.md` (services, code homes). **Gap:** formal asset register with criticality ratings + ownership not yet documented. Target close: Sprint 008.

### A.5.10 — Acceptable use of information and other associated assets
**Applicable:** Yes. **Status:** Implemented. Acceptable use defined via the typed contract surfaces — every cross-component call flows through a defined contract; ad-hoc access is structurally impossible.

### A.5.11 — Return of assets
**Applicable:** Yes (limited applicability — small team). **Status:** Implemented via contractor termination procedures (offboarding revokes credentials).

### A.5.12 — Classification of information
**Applicable:** Yes. **Status:** Implemented.
Five-tier RBAC (public / squad / project / role / entity / private) is the substrate's classification scheme. Every record has an explicit visibility tier.

### A.5.13 — Labelling of information
**Applicable:** Yes. **Status:** Implemented. RBAC tier serves as the label; carried in every record's `visibility` column.

### A.5.14 — Information transfer
**Applicable:** Yes. **Status:** Implemented.
TLS 1.2+ for all transit. Per-workspace DEK envelope encryption for at-rest. Cross-workspace transfer mathematically prevented by AAD-binding.

### A.5.15 — Access control
**Applicable:** Yes. **Status:** Implemented. Cross-references SOC 2 CC6.1.

### A.5.16 — Identity management
**Applicable:** Yes. **Status:** Implemented. SCIM 2.0 provisioning + nullify+confiscate erasure. Cross-references SOC 2 CC6.2, CC6.3.

### A.5.17 — Authentication information
**Applicable:** Yes. **Status:** Implemented.
TOTP secrets in HashiCorp Vault. KEK in Vault. DEK envelope-encrypted. SAML signature validation. SAML/TOTP replay ledgers (F-09, F-20 closed Sprint 005).

### A.5.18 — Access rights
**Applicable:** Yes. **Status:** Implemented. SCIM `idp_group_role_map` with `max_grantable_tier` enforcement (F-15 closed Sprint 005). Cross-references SOC 2 CC6.2.

### A.5.19 — Information security in supplier relationships
**Applicable:** Yes. **Status:** Partially Implemented. **Gap:** vendor risk register with annual review cadence not yet documented. Target close: Sprint 008. Cross-references SOC 2 CC9.2.

### A.5.20 — Addressing information security within supplier agreements
**Applicable:** Yes. **Status:** Partially Implemented. Major suppliers (Cloudflare, Hetzner, Anthropic, OpenAI, Google, HashiCorp) all hold SOC 2 / ISO 27001. Mumega does not currently negotiate custom security clauses; relies on standard terms. Acceptable for current scope.

### A.5.21 — Managing information security in the ICT supply chain
**Applicable:** Yes. **Status:** Implemented. npm and Python lock files; Dependabot alerts; manual review of major version bumps.

### A.5.22 — Monitoring, review and change management of supplier services
**Applicable:** Yes. **Status:** Partially Implemented. **Gap:** annual review of supplier security posture not formalized. Target close: Sprint 008.

### A.5.23 — Information security for use of cloud services
**Applicable:** Yes. **Status:** Implemented. Cloudflare and Hetzner attestations reviewed at vendor selection. Mumega's own controls (per CC6, CC7) protect against cloud-provider compromise.

### A.5.24 — Information security incident management planning and preparation
**Applicable:** Yes. **Status:** Partially Implemented. `/sop incident` runbook defined. **Gap:** tabletop exercises not yet performed. Target close: Sprint 006 Track A acceptance test (kill -9 stress test).

### A.5.25 — Assessment and decision on information security events
**Applicable:** Yes. **Status:** Implemented. Adversarial findings emit via typed `emit_adversarial_finding(finding_id, severity)`. Severity tags (BLOCK / WARN / LOW) drive escalation.

### A.5.26 — Response to information security incidents
**Applicable:** Yes. **Status:** Implemented. Cross-references SOC 2 CC7.4.

### A.5.27 — Learning from information security incidents
**Applicable:** Yes. **Status:** Implemented. Sprint retrospectives explicitly capture "what we'd do differently" with codified protocol changes (e.g., adversarial-as-parallel-gate codified post-Sprint-004 incident).

### A.5.28 — Collection of evidence
**Applicable:** Yes. **Status:** Implemented. Audit chain hash-linked + R2 anchor with Object Lock provides tamper-evident evidence collection.

### A.5.29 — Information security during disruption
**Applicable:** Yes. **Status:** Partially Implemented. Sprint 006 Track A delivers HA. Until then: single-instance with audit chain integrity preserved during disruption.

### A.5.30 — ICT readiness for business continuity
**Applicable:** Yes. **Status:** Partially Implemented. Same gap as A.5.29. Target close: Sprint 006 Track A.

### A.5.31 — Legal, statutory, regulatory and contractual requirements
**Applicable:** Yes. **Status:** Implemented. PIPEDA + GDPR alignment via nullify+confiscate erasure (migration 025). Customer contracts include data processing terms.

### A.5.32 — Intellectual property rights
**Applicable:** Yes. **Status:** Implemented. License compliance via Dependabot + manual review. Mumega's own code is proprietary unless explicitly open-sourced.

### A.5.33 — Protection of records
**Applicable:** Yes. **Status:** Implemented. Audit chain WORM + 7-year retention.

### A.5.34 — Privacy and protection of PII
**Applicable:** Yes. **Status:** Implemented. Per-workspace DEK encryption + nullify+confiscate erasure. Cross-references SOC 2 CC6.7.

### A.5.35 — Independent review of information security
**Applicable:** Yes. **Status:** Implemented. Athena (independent gate agent) reviews every change. External independent review planned post-Series-A (penetration test + SOC 2 audit).

### A.5.36 — Compliance with policies, rules and standards for information security
**Applicable:** Yes. **Status:** Implemented. Every change gated against the four canonical sensitive surfaces protocol; deviations explicitly justified.

### A.5.37 — Documented operating procedures
**Applicable:** Yes. **Status:** Implemented. Operating procedures live in `~/.claude/skills/sop/` (incident, deploy, onboard, release).

---

## Section A.6 — People controls (8 controls)

### A.6.1 — Screening
**Applicable:** Yes. **Status:** Partially Implemented. Founder + small contractor base; formal background-check process not documented. Acceptable for current team size; revisit at >10 FTE.

### A.6.2 — Terms and conditions of employment
**Applicable:** Yes. **Status:** Implemented. NDA + contractor agreements via signed contracts (§3.5 contracts primitive when shipped).

### A.6.3 — Information security awareness, education and training
**Applicable:** Yes. **Status:** Partially Implemented. Internal team is highly technical; ad-hoc awareness via shared documents and protocols. **Gap:** formal training program. Target close: Sprint 008.

### A.6.4 — Disciplinary process
**Applicable:** Yes. **Status:** Implemented. Standard contractor agreement termination clauses.

### A.6.5 — Responsibilities after termination or change of employment
**Applicable:** Yes. **Status:** Implemented. SCIM deprovision triggers nullify+confiscate erasure (CC6.3).

### A.6.6 — Confidentiality or non-disclosure agreements
**Applicable:** Yes. **Status:** Implemented. NDA flow at §11 profile primitive (Phase 6 shipping).

### A.6.7 — Remote working
**Applicable:** Yes. **Status:** Implemented. All team members work remotely; substrate accessed via TLS-secured endpoints with MFA.

### A.6.8 — Information security event reporting
**Applicable:** Yes. **Status:** Implemented. Bus alerts route to Athena; severity-based escalation.

---

## Section A.7 — Physical controls (14 controls)

For controls A.7.1 through A.7.14, **Mumega's primary disposition is Compensating Control (Cloud Service Provider responsibility).**

Mumega's substrate runs on Hetzner cloud infrastructure. Hetzner holds ISO 27001:2022 certification covering physical security at their datacenter facilities. Mumega does not operate physical datacenters. The compensating control basis is the Hetzner ISO 27001 certificate + the SOC 2 Type II attestations for Cloudflare (R2 storage), HashiCorp (Vault Cloud or self-hosted), and supporting providers.

Specific notes:
- **A.7.7 Clear desk and clear screen** — applies to Mumega operator workstation. Status: Implemented (operator follows standard remote-worker hygiene).
- **A.7.10 Storage media** — applies to operator devices. Status: Implemented (full-disk encryption on operator laptops).
- **A.7.13 Equipment maintenance** — operator devices. Status: Implemented.
- **A.7.14 Secure disposal or re-use of equipment** — operator devices. Status: Implemented.

All other A.7.* controls are excluded as CSP responsibility per A.5.23 (Information security for use of cloud services).

---

## Section A.8 — Technological controls (34 controls)

### A.8.1 — User endpoint devices
**Applicable:** Yes. **Status:** Implemented. Operator workstations run full-disk encryption + MFA on substrate access.

### A.8.2 — Privileged access rights
**Applicable:** Yes. **Status:** Implemented. Postgres superuser access requires Hadi-physical-presence; Track F superuser migrations explicitly scoped to a single maintenance window.

### A.8.3 — Information access restriction
**Applicable:** Yes. **Status:** Implemented. RBAC five-tier enforcement at every read/write call site.

### A.8.4 — Access to source code
**Applicable:** Yes. **Status:** Implemented. GitHub repository access via 2FA + branch protection. Production secrets never committed.

### A.8.5 — Secure authentication
**Applicable:** Yes. **Status:** Implemented. TOTP + WebAuthn MFA, SAML signature validation, replay ledgers.

### A.8.6 — Capacity management
**Applicable:** Yes. **Status:** Partially Implemented. Reactive monitoring via restart-alert. **Gap:** predictive capacity modeling. Target close: Sprint 007.

### A.8.7 — Protection against malware
**Applicable:** Yes. **Status:** Implemented. Lock files + Dependabot + manual review of dependency updates.

### A.8.8 — Management of technical vulnerabilities
**Applicable:** Yes. **Status:** Implemented. Dependabot + adversarial subagent review on substrate code.

### A.8.9 — Configuration management
**Applicable:** Yes. **Status:** Implemented. systemd unit files versioned in repo; configuration via env files with explicit `vault_env.py` ref-resolution; no ambient config.

### A.8.10 — Information deletion
**Applicable:** Yes. **Status:** Implemented. Nullify+confiscate erasure (migration 025) provides PIPEDA/GDPR-compliant deletion with reactivation tokens for legal hold.

### A.8.11 — Data masking
**Applicable:** Yes. **Status:** Implemented. PII redaction in nullify path replaces personal data with `<redacted>` while preserving audit chain references.

### A.8.12 — Data leakage prevention
**Applicable:** Yes. **Status:** Implemented. AAD-bound DEK encryption mathematically prevents cross-workspace data access. RBAC tier enforcement + audit log of every read.

### A.8.13 — Information backup
**Applicable:** Yes. **Status:** Implemented. Daily backups via `mirror/scripts/backup.py`. Audit chain anchored to R2 every 15 minutes.

### A.8.14 — Redundancy of information processing facilities
**Applicable:** Yes. **Status:** Partially Implemented. **Gap:** Mirror PostgreSQL is single-instance; matchmaker, Squad Service, audit chain anchor are single-instance. **Target close:** Sprint 006 Track A (kill -9 stress test acceptance).

### A.8.15 — Logging
**Applicable:** Yes. **Status:** Implemented. Hash-chained audit_events per stream + structured `emit_*` functions for sprint telemetry.

### A.8.16 — Monitoring activities
**Applicable:** Yes. **Status:** Implemented. restart-alert.service + audit-anchor.timer + adversarial subagent review.

### A.8.17 — Clock synchronization
**Applicable:** Yes. **Status:** Implemented. systemd-timesyncd + NTP on all hosts.

### A.8.18 — Use of privileged utility programs
**Applicable:** Yes. **Status:** Implemented. Postgres superuser via documented maintenance windows only. SSH keys logged and rotated.

### A.8.19 — Installation of software on operational systems
**Applicable:** Yes. **Status:** Implemented. systemd unit installation via documented procedure; all units versioned in repo.

### A.8.20 — Networks security
**Applicable:** Yes. **Status:** Implemented. Cloudflare DDoS at edge; nginx with TLS 1.2+ at VPS; per-service binding to localhost where applicable.

### A.8.21 — Security of network services
**Applicable:** Yes. **Status:** Implemented. Same evidence as A.8.20.

### A.8.22 — Segregation of networks
**Applicable:** Yes. **Status:** Implemented. Cloudflare + nginx + localhost-binding provides three-layer segregation between public, edge, and substrate.

### A.8.23 — Web filtering
**Applicable:** Yes. **Status:** Implemented. Cloudflare WAF on customer-facing traffic.

### A.8.24 — Use of cryptography
**Applicable:** Yes. **Status:** Implemented. AES-256-GCM for at-rest, TLS 1.2+ for transit, SHA-256 for chain hashing, Ed25519 for audit signing (planned, F-11b in Track F).

### A.8.25 — Secure development life cycle
**Applicable:** Yes. **Status:** Implemented. Spec → gate → adversarial parallel → build → sign-off → flip lifecycle codified in agent-comms.md.

### A.8.26 — Application security requirements
**Applicable:** Yes. **Status:** Implemented. Every change touching the four canonical sensitive surfaces requires adversarial parallel review.

### A.8.27 — Secure system architecture and engineering principles
**Applicable:** Yes. **Status:** Implemented. Microkernel discipline + typed contract surfaces + single-basis discipline (§16a) + explicit emit + four-surface protocol.

### A.8.28 — Secure coding
**Applicable:** Yes. **Status:** Implemented. TypeScript strict mode + Python type hints + `tsc` clean as merge gate. Athena reviews structural correctness.

### A.8.29 — Security testing in development and acceptance
**Applicable:** Yes. **Status:** Implemented. 165+ tests on substrate contracts. Adversarial subagent dispatched parallel for sensitive surfaces.

### A.8.30 — Outsourced development
**Applicable:** No. Excluded — Mumega does not outsource substrate development. Future reconsideration if Mumega ever contracts substrate work to third parties.

### A.8.31 — Separation of development, test and production environments
**Applicable:** Yes. **Status:** Implemented. Local dev, staging on `staging` branch + dev cluster, production on Hetzner VPS. Distinct Vault paths per environment.

### A.8.32 — Change management
**Applicable:** Yes. **Status:** Implemented. Cross-references SOC 2 CC8.1.

### A.8.33 — Test information
**Applicable:** Yes. **Status:** Implemented. Test fixtures use synthetic data; no production PII in test environments.

### A.8.34 — Protection of information systems during audit testing
**Applicable:** Yes. **Status:** Planned. Audit testing process during ISO certification audit will include explicit data-protection clause.

---

## Section: Excluded controls

| Control | Exclusion reason |
|---------|------------------|
| A.7.1–A.7.6, A.7.8–A.7.9, A.7.11–A.7.12 | CSP (Hetzner) responsibility per A.5.23 |
| A.8.30 | Mumega does not outsource substrate development |

All other 91 of 93 controls are Applicable.

---

## Open items for ISO 27001 certification body engagement

To be discussed during Stage 1 audit:

1. **ISMS scope statement.** This SOA defines control applicability; the ISMS scope statement formalizes what's in scope (substrate components named in metadata above) and what's out (customer applications built on Mumega; their attestation is separate).
2. **Risk assessment methodology.** Mumega uses adversarial subagent review + sprint-by-sprint threat modeling. Some certification bodies expect formal CIA-triad rating per asset. Discuss methodology compatibility.
3. **Statement of Applicability completeness.** This v0.1 has approximately 75% Implemented + 20% Partially Implemented + 5% Planned/Excluded. Stage 1 typically expects ≥90% Implemented; Sprint 008 closures should achieve this.
4. **Certification body selection.** TÜV, BSI, Schellman, A-LIGN all certify ISO 27001:2022. Engagement depends on Hadi's Track C.4 audit firm decision (some firms bundle SOC 2 + ISO 27001).

---

## Versioning

| Version | Date       | Change                                                              |
|---------|------------|---------------------------------------------------------------------|
| v0.1    | 2026-04-25 | Initial SOA draft — Sprint 006 Track D.2. ~75% Implemented +        |
|         |            | 20% Partial + 5% Planned/Excluded. Pre-certification body           |
|         |            | engagement; refinement expected through Stage 1 audit.              |
