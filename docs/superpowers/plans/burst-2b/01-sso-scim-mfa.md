# Burst 2B-1 — SSO + SCIM + MFA at Platform Edge

**Author:** Loom
**Date:** 2026-04-24
**Phase:** Sprint 002 — Burst 2B hardening (enterprise readiness)
**Depends on:** Section 1A (role registry, token introspection), DISP-001 (session identity), Inkwell Hive RBAC
**Gate:** Athena
**Owner:** Kasra
**Effort:** ~20 days

---

## 1. Goal

Enterprise identity at the platform edge. Three capabilities, one surface:

1. **SAML 2.0 + OIDC SSO** for Google Workspace and Microsoft Entra (minimum two IdPs live). Any future IdP is a configuration row, not a code change.
2. **SCIM 2.0** user and group provisioning so IT admins create/update/deprovision from their IdP and role assignments follow automatically.
3. **MFA enforcement** (TOTP + WebAuthn) at login, evaluated by DISP-001 before a session token is minted.

Enterprise buyers (including the ISO 42001 pipeline and any partner-of-partner reselling SOS) will not procure without this. Ship it once in the kernel-adjacent edge; every plugin inherits it.

## 2. Schema Additions

Three new tables, all under the substrate DB namespace, tenant-scoped:

- `idp_configurations (id, tenant_id, protocol ENUM('saml','oidc'), display_name, metadata_url, entity_id, acs_url, client_id, client_secret_ref, group_claim_path, enabled, created_at)` — one row per configured IdP. Secrets live in Vault (see Burst 2B-4); this table stores only the reference.
- `sso_identity_links (id, tenant_id, idp_id, external_subject, principal_id, email, last_seen_at, created_at, UNIQUE(idp_id, external_subject))` — maps an IdP's stable subject to the SOS principal.
- `mfa_enrolled_methods (id, principal_id, method ENUM('totp','webauthn'), secret_ref, credential_id, label, last_used_at, created_at)` — method registry per principal. At least one enrolled method required before `mfa_required = true` roles can be assumed.

SCIM state persists into the existing `principals` and `role_assignments` tables — no new SCIM-specific table.

## 3. Integration With Role Registry (§1A)

- **IdP groups → roles.** `idp_configurations.group_claim_path` plus a per-tenant `idp_group_role_map (idp_id, group_name, role_id)` table translates IdP group membership into §1A role assignments on every login. SCIM group events update the same map in real time.
- **Just-in-time provisioning.** First SSO login creates a principal and links it via `sso_identity_links`. Role assignment is computed from claims, not the IdP sending SCIM first.
- **Token issuance.** DISP-001 consumes the authenticated identity + role set and mints a session token exactly as it does today — SSO/MFA is a pre-step, not a replacement.

## 4. MFA Challenge Flow

1. Principal authenticates via IdP or password.
2. Edge middleware reads the target role set; if any role has `mfa_required`, challenge fires.
3. TOTP or WebAuthn verified against `mfa_enrolled_methods`.
4. On success, DISP-001 issues the session token with an `mfa_verified` claim (TTL 8h).
5. Revocation: admin flips `mfa_enrolled_methods.enabled = false`; next request fails.

## 5. Acceptance Criteria (Test Plan)

1. **One IdP end-to-end.** Google Workspace SSO completes: user lands on protected route, is bounced to IdP, returns with SAML/OIDC assertion, gets a session token with correct role set derived from IdP groups.
2. **SCIM provisioning roundtrip.** IT admin creates a user and assigns a group in the IdP; within 60s the principal and role assignment exist in SOS. Deprovisioning revokes the session.
3. **MFA challenge fires.** A role flagged `mfa_required` cannot be assumed until a TOTP secret is enrolled and a code is accepted. WebAuthn path tested with one hardware key.
4. **Second IdP (Entra) drop-in.** Adding Microsoft Entra is a new `idp_configurations` row + group map — zero code changes.
5. **Audit events emitted.** Every login, SCIM event, MFA challenge, and role change produces an `audit_events` entry (see Burst 2B-2).
