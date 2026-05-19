# Burst 2B-3 — Profile Primitive: Self-Serve Surface + Data Rights

**Author:** Loom
**Date:** 2026-04-24
**Phase:** Sprint 002 — Burst 2B hardening (citizenship + PIPEDA/GDPR readiness)
**Depends on:** Section 11 (Profile Primitive), Section 1 (RBAC tiers), Section 3 (contacts), Section 3.5 (contracts + goals), Section 10 (metabolic loop)
**Gate:** Athena
**Owner:** Kasra (build) — spec owned by Loom
**Effort:** ~10 days

---

## 1. Goal

Ship the self-serve slice of §11 so every contracted person — contractor, partner, customer, lead — can log in and exercise their rights. No new design; §11 already defines schema, principles, and surface. This spec freezes the build scope for Burst 2B to seven sub-stages:

- **6.1** Profile schema (per §11 §3.1–3.5, as-is)
- **6.2** `/people/{slug}` SSR, auth-aware tiering (public tier for unauth)
- **6.3** Magic-link login (email one-time code, 10-min TTL)
- **6.4** Self-tier view (what the system knows about me, rendered from the RBAC-gated query)
- **6.8** Access log (who viewed my data, when, why)
- **6.10** Data export (zip package, delivered within 24h)
- **6.11** Erasure request workflow (receipt lists deletable vs. retained-by-law rows)

Stages 6.5 (NDA signing), 6.6 (prefs), 6.7 (tool connect), 6.9 (consent granular) stay deferred to Burst 2C.

## 2. Schema

No changes. Use §11 §3.1–3.5 verbatim: `profiles`, `profile_consents`, `profile_access_log`, `profile_requests`, `profile_exports`. Erasure receipts live in `profile_requests` with `type = 'erasure'` and a JSON breakdown of deletable vs. retained.

## 3. Build Notes (non-normative)

- `/people/{slug}` resolves `slug → principal_id`, then applies the §1 tier rule set. Missing sections are stubs labelled by category (per §11 Principle 1).
- Magic-link: email token, single-use, scoped to `slug`; on verify, DISP-001 mints a session with `role=self_profile` for 8h.
- Self-view reuses existing RBAC-gated recall SQL (§1C) — no new query layer.
- Access log: writes to `profile_access_log` on every read above public tier; also emits `audit_events` (Burst 2B-2).
- Export: asynchronous. Request → job → zip → signed URL emailed. 24h SLA enforced by monitor.
- Erasure: request triggers a review queue (human-approved for retained-by-law cases); receipt is the visible artifact, not silent success.

## 4. Acceptance Criteria

1. **Magic-link roundtrip.** A contractor enters their email on `/people/{slug}`, receives a one-time code, logs in, and lands on the self-tier view with their own data — no other tier's content leaks.
2. **Self-tier completeness.** The view surfaces every category the system stores about the principal (contract, goals, engrams at self tier, audit subset). Categories above tier show a labelled stub, never nothing.
3. **Export delivered ≤ 24h.** Requesting export produces a signed-URL zip within the SLA; zip contains every table row pointing to the principal plus a manifest.json mapping files to source tables.
4. **Erasure receipt.** Erasure request returns a receipt (UI + PDF) listing rows deleted, rows retained (with legal basis per retained row), and the date deletion will complete for async removals.
5. **Access log visible.** Principal sees a reverse-chronological list of every read of their data above public tier, with viewer role, reason, and timestamp.
6. **Audit parity.** Every read, login, export, and erasure action emits a Burst 2B-2 `audit_events` row.
