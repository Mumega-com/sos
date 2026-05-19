# Section 11 — Profile Primitive (Every Person Has an Inkwell)

**Author:** Loom
**Date:** 2026-04-24
**Version:** v1.0 (draft)
**Phase:** 6 — surface layer riding on the metabolic loop (§10) + living graph
**Depends on:** Section 1 (role registry, five-tier RBAC), Section 3 (structured records: contacts, partners), Section 3.5 (contracts + goals), Section 7 (fractal node primitive), Section 10 (metabolic loop — access-log, decay, patterns)
**Gate:** Athena
**Owner:** Loom (spec) → Kasra (build)

---

## 0. TL;DR

Every person who has a contract with us — human or agent, customer or partner or contractor or lead — gets a **profile**. They can log in. They see what the system knows about them at their authorized tier. They can sign NDA + T&C online, manage communication preferences, connect their tools (read-only OAuth), request data export, and revoke consent.

This is not CRM. It is the **citizenship primitive** — the surface every member of the protocol-city uses to see themselves inside it and to control what the system does on their behalf.

The Inkwell Hive five-tier RBAC (§1) already exists to enforce what each viewer sees. This section specifies the **Profile view layer** + the **self-service surface** + the **consent/export/erasure flows** that ride on top.

---

## 1. Principles (constitutional)

1. **Transparency by default.** A person should be able to see what the system knows about them, as far as their tier permits. Hidden records at higher tiers are *labelled* (by category and count) — never completely invisible — so they know something exists.
2. **Consent is first-class.** Separate from delete. *Revoke consent* = stop processing going forward. *Delete* = remove what's legally removable. Both are buttons, both are honest.
3. **Read-mostly surface.** Mutable data that affects money, legal, or role goes through a **request workflow** with human approval. Only communication prefs, tool connections, and consent flags are self-serve.
4. **Export over migration.** Data portability is a **zip export**, not a live "move to your own cloud" pipeline. BYOC is deferred until a contract requires it.
5. **Every access is logged.** The profile owner can see the audit log of who viewed their data and why.
6. **Agents are citizens too.** Agents (Loom, Kaveh, etc.) have profiles too. Their "NDA" is their QNFT cause statement and charter. Their "tool connections" are their MCP config. Uniform schema.

---

## 2. Components

Ten components. Keep, reframed, added, cut — from the brainstorm.

### 2.1 Profile view at `/people/{slug}` (KEEP)
The canonical URL per person. Server-side rendering with auth-aware tiering. Public tier renders SEO-friendly bio. Self tier renders the full Inkwell view. Higher tiers (role, entity, private) render additional sections based on the viewer's RBAC.

### 2.2 Online NDA + T&C acceptance (KEEP)
E-signature flow: email magic-link → review document → click accept → server stores `{signer_id, document_version, accepted_at, ip_hash, user_agent}`. Ties into contracts-as-primitive (§3.5) — an accepted NDA is a `contract` row with `contract_type=nda` and `status=active`.

### 2.3 Communication preferences (KEEP)
Per-channel toggles (Discord / Email / SMS / in-app), per-category toggles (marketing / transactional / alerts), frequency caps, quiet hours. Stored on `contacts.comm_prefs` as JSONB. Respected by every outbound sender.

### 2.4 Tool connections via OAuth (KEEP — curated list, read-only first)
Phase-1 curated: Gmail, Google Calendar, Google Drive, GHL, QuickBooks. Read-only scopes. Stored with envelope encryption (same pattern as §8 datalake). Write scopes added per-tool in phase-2 after safety review. User can disconnect anytime → token revoked + grace period for in-flight processes to finish.

### 2.5 "What we know about you" view (KEEP)
Table of every record tagged with the person's `person_id`:
- Engrams (raw + patterns from §10)
- Events (meetings, calls, emails digested by intake service)
- Bounties, tasks, opportunities
- Commission ledger entries
- Signed contracts + version history
- Audit log entries

Each row labelled `deletable | legally_retained | revokable` with explicit reason shown for retention.

### 2.6 Erasure request workflow (REFRAMED from "delete button")
Delete button exists but is honest:
- Rows marked `deletable` — removed immediately
- Rows marked `legally_retained` (signed contracts, invoices, SR&ED filings for 7 years) — not deletable; user sees *why* they can't be
- Rows marked `revokable` — revoked + anonymized where possible (replace PII with `<redacted>`)

Result: a receipt with exact counts per category. Audit trail of the erasure itself.

### 2.7 Data export (REFRAMED from "move to Cloudflare/GCP")
Self-serve "request export" button → system generates a zip (JSON + any attachments) → delivered to verified email within 24h → signed URL with 7-day TTL. That is the sovereignty story. True BYOC deferred until a $100K+ contract requires it.

### 2.8 Audit log of access (ADDED)
Every read of this person's data emits a `profile_access` event with `{viewer_id, viewer_role, access_type, accessed_at, purpose_if_provided}`. Visible to the profile owner at self tier. Satisfies compliance narrative + builds trust.

### 2.9 Contract status panel (ADDED)
What they've signed, what's pending, effective dates, version history. Every contract version immutable (matches §3.5 contract primitive). Pending renegotiations show as `pending-amendment` with the proposed diff.

### 2.10 Impersonation-consent log + revoke (ADDED)
When an agent acts on this person's behalf (Kaveh emails a prospect *as* Ron), the action fires a `profile_impersonation_event`. Profile owner sees every impersonation + can revoke future consent for a specific agent → tool → action combo.

### CUT: Full self-serve edit of contract/commission/role
Mutable data that affects money or legal is **request-by-workflow**, not free-edit. Otherwise audit nightmare + adversarial-user surface.

### CUT: BYOC data migration (live replication)
Deferred. Export is the portability story until signed demand exists.

---

## 3. Data Model

### 3.1 Profile record

A profile is a **view** over several source tables — not a new table per se. The anchoring record is `contacts` (§3) with profile-specific additions:

```sql
ALTER TABLE contacts ADD COLUMN profile_slug TEXT UNIQUE;  -- /people/{slug}
ALTER TABLE contacts ADD COLUMN profile_bio_public TEXT;   -- the thing the person writes about themselves
ALTER TABLE contacts ADD COLUMN profile_visibility TEXT DEFAULT 'role'; -- public|role|entity|private
ALTER TABLE contacts ADD COLUMN comm_prefs JSONB;
ALTER TABLE contacts ADD COLUMN self_login_enabled BOOLEAN DEFAULT false;
ALTER TABLE contacts ADD COLUMN self_login_provider TEXT;  -- magic-link | oauth-google | oauth-github
```

### 3.2 Consent records

```sql
CREATE TABLE profile_consents (
  id                SERIAL PRIMARY KEY,
  contact_id        INTEGER REFERENCES contacts(id),
  consent_type      TEXT NOT NULL,  -- data_processing | marketing | impersonation | tool_connection
  target_agent_id   TEXT,            -- nullable; used for impersonation
  target_tool       TEXT,            -- nullable; used for tool_connection
  granted_at        TIMESTAMPTZ NOT NULL,
  revoked_at        TIMESTAMPTZ,
  scope             JSONB,           -- fine-grained (e.g. {tools:[email], actions:[send]})
  evidence          TEXT             -- reference to NDA/T&C version, or IP+timestamp hash
);
```

### 3.3 Access log

```sql
CREATE TABLE profile_access_log (
  id              BIGSERIAL PRIMARY KEY,
  contact_id      INTEGER REFERENCES contacts(id),   -- subject
  viewer_id       TEXT NOT NULL,                      -- who viewed (agent_id or user_id)
  viewer_role     TEXT NOT NULL,                      -- from role registry
  access_type     TEXT NOT NULL,                      -- read | export | impersonate
  accessed_at     TIMESTAMPTZ DEFAULT now(),
  purpose         TEXT,                               -- optional; surfaced to subject
  tier            TEXT NOT NULL                       -- which tier the viewer reached
);
CREATE INDEX ON profile_access_log(contact_id, accessed_at DESC);
```

### 3.4 Tool connections

```sql
CREATE TABLE profile_tool_connections (
  id               TEXT PRIMARY KEY,
  profile_id       TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  workspace_id     TEXT NOT NULL,         -- workspace scope; cross-workspace leak vector if missing
  tool_name        TEXT NOT NULL,         -- gmail | gcal | gdrive | ghl | quickbooks
  scopes           TEXT[],
  status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked','expired')),
  oauth_token_ref  TEXT,                  -- Vault path (never plaintext; envelope-encrypted §8 pattern)
  connected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at       TIMESTAMPTZ,
  UNIQUE (profile_id, tool_name)
);
```

### 3.5 Export jobs

```sql
CREATE TABLE profile_export_jobs (
  id              SERIAL PRIMARY KEY,
  contact_id      INTEGER REFERENCES contacts(id),
  requested_at    TIMESTAMPTZ,
  status          TEXT NOT NULL CHECK (status IN ('queued','running','ready','expired','failed')),
  signed_url      TEXT,
  expires_at      TIMESTAMPTZ,
  row_counts      JSONB            -- { engrams: N, events: M, ... } for the receipt
);
```

### 3.6 Erasure / amendment requests

```sql
CREATE TABLE profile_requests (
  id              TEXT PRIMARY KEY,
  profile_id      TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  workspace_id    TEXT NOT NULL,
  type            TEXT NOT NULL CHECK (type IN ('erasure','export','correction','access')),
  status          TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','in_progress','completed','rejected')),
  requested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at    TIMESTAMPTZ,
  retain_reason   TEXT,            -- PIPEDA legal-basis paper trail for retained rows
  receipt         JSONB            -- breakdown: deleted rows, retained rows + legal basis per row
);
```

---

## 4. RBAC Tier Mapping

Inkwell Hive five-tier RBAC (§1): **public / squad / project / role / entity / private**. Profile content gated:

| Section | Public | Squad | Project | Role | Entity | Private (self) |
|---|---|---|---|---|---|---|
| Name + public bio | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Public meetings (if marked) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Squad membership | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| Project involvement | — | — | ✓ | ✓ | ✓ | ✓ |
| Relationship graph | — | — | — | ✓ | ✓ | ✓ |
| Full meeting history | — | — | — | ✓* | ✓ | ✓ |
| Commission / contract terms | — | — | — | — | ✓ | ✓ |
| Internal strategy notes | — | — | — | — | ✓ | — |
| Full audit log | — | — | — | — | — | ✓ |
| Consent management | — | — | — | — | — | ✓ |
| Erasure request | — | — | — | — | — | ✓ |

`✓*` = role-scoped: a squad-mate sees only meetings relevant to their shared project.

**Strategy notes are deliberately hidden from self** — that's where internal assessment lives (e.g. "Ron is a warm but non-urgent close, pace accordingly"). Self sees *what* but not always *how we're playing it*. This is standard CRM practice and explicitly permitted under PIPEDA where it relates to legitimate business assessment.

---

## 5. Self-Login Flow

Profile owner authenticates via magic-link to their verified email:

1. Visit `/people/{slug}/login` (or their private invite URL).
2. Enter email → server sends 6-digit code / signed magic-link.
3. On verify → session cookie, TTL 30 days, stored in KV.
4. Session resolves to `contact_id` → profile view rendered at self tier.
5. MFA optional (TOTP) for high-value profiles (contractors with >$10K earned, any human with admin role).

Matches Codex's earlier Inkwell-v4 auth recommendation: magic-link via GHL/Twilio, session in KV, user record in D1/Postgres.

---

## 6. Build Sequence & Phase Mapping

All under **Phase 6**, landing after §10 metabolic loop provides the living graph.

| # | Component | Effort | Depends on |
|---|---|---|---|
| 6.1 | Schema migrations (`profile_slug`, consents, access_log, tool_connections, export_jobs) | ~1 day | §3, §3.5 |
| 6.2 | `/people/{slug}` SSR page, public tier only | ~1 day | 6.1 |
| 6.3 | Magic-link self-login + session store (KV) | ~1 day | 6.1 |
| 6.4 | Profile view — self tier (meetings, engrams, contracts, consents) | ~2 days | 6.1, 6.3, §10 |
| 6.5 | NDA + T&C acceptance flow (e-sign → contract row) | ~1 day | 6.3, §3.5 |
| 6.6 | Communication prefs UI + enforcement in outbound senders | ~1 day | 6.4 |
| 6.7 | Tool connection OAuth flows (Gmail/GCal/Drive/GHL/QBO, read-only) | ~3 days | 6.4, §8 datalake |
| 6.8 | Access log write path + self view | ~0.5 day | 6.1, 6.4 |
| 6.9 | Consent management UI (grant, scope, revoke) | ~1 day | 6.1, 6.4 |
| 6.10 | Data export pipeline (async job → zip → signed URL) | ~2 days | 6.4 |
| 6.11 | Erasure request workflow + receipt | ~1.5 days | 6.1, 6.4, 6.10 |
| 6.12 | Impersonation event emission + self view | ~1 day | 6.4, 6.9 |
| 6.13 | Role/entity/project tiers (for non-self viewers) | ~2 days | 6.4 |
| 6.14 | Public bio editor (markdown, image upload) | ~0.5 day | 6.2, 6.4 |

**Total:** ~18 engineer-days for full Phase 6.
**Earliest value:** 6.1 + 6.2 + 6.3 + 6.4 (~5 days) — people can log in and see their data. The rest layers on incrementally.

---

## 7. Success Criteria

**Stage 6a (self-login + self view):**
- Ron logs in at `/people/ron-oneil` via magic-link.
- Sees his meetings (today's call), his profile info, his communication prefs.
- Time-to-first-value under 2 min from invite email.

**Stage 6b (NDA + contracts + prefs):**
- New partner accepts NDA online in under 60 seconds.
- Marketing email respects opt-out within 5 min of toggle.

**Stage 6c (tool connections):**
- Ron connects Gmail (read-only) in under 60 seconds via OAuth.
- Intake service (§10) picks up his Gmail signals → engrams on his profile.
- Disconnect button revokes token + stops ingestion within 1 min.

**Stage 6d (consent + audit + export + erasure):**
- Access log populates on every profile view.
- Export zip delivered within 24h; receipt accurate.
- Erasure receipt shows exact deletable/retained counts with reasons.

**Stage 6e (cross-tier views):**
- Kaveh views Ron's role-tier profile — sees relationship history but not commission terms.
- You view Ron's entity-tier — see everything including commission.
- Public URL renders Ron's public bio only.

---

## 8. Open Questions

1. **Profile slug strategy**: human-readable (`ron-oneil`) vs opaque (`c-a47f...`). Human is better for SEO + memorability; opaque is better for privacy-by-obscurity. Recommend human by default, allow opt-out to opaque.
2. **Self-tier redaction policy for strategy notes**: how strict? Should self see *that* a strategy note exists (without content), or nothing at all? Recommend "labelled existence" — preserves trust without leaking assessment.
3. **Agent profiles UX**: do agent profiles get the same `/people/{slug}` URL, or a different namespace like `/agents/{slug}`? Recommend `/agents/` for clarity but same underlying schema.
4. **NDA versioning on amendment**: when we update the NDA text, do all existing signers need to re-sign? Only for material changes (legal sign-off). Immaterial edits (typo fix) = new version, no re-sign required. Legal to define material-change criteria.
5. **Export scope when subject consents to impersonation**: if Ron gave Kaveh impersonation consent to send emails, do those emails appear in *Ron's* export or only Kaveh's? Recommend: yes, in Ron's export, labelled `source=impersonation:kaveh`. It's Ron's data.
6. **Commission records visibility at entity tier**: should entity-tier viewers see *gross* commission (their share) or *net* (their share minus platform cut)? Recommend gross; net is an internal calc.

---

## 9. Dependencies & Cross-References

- **Section 1** — role registry, five-tier RBAC (enforces visibility)
- **Section 3** — `contacts` table (profile anchor)
- **Section 3.5** — contracts + goals primitive (NDA/T&C become contract rows; goals per person surface on profile)
- **Section 7** — fractal node primitive (engrams link to nodes; profile surfaces them)
- **Section 8** — sos-datalake (tool connection data flows through it; envelope encryption pattern reused)
- **Section 10** — metabolic loop (access-log, engram decay, pattern nodes are the content profile surfaces)
- **Section 6** — plugin contract v2 (tool connectors are plugins with `datalake_sources`)

---

## 10. What This Unlocks

After Phase 6 closes:

- **Partners onboard themselves.** Ron signs his NDA + T&C online in minutes, connects his Gmail, sees his commission ledger — all without Hadi hand-holding.
- **Agents are citizens.** Loom's profile at `/agents/loom` shows its QNFT, charter, signed contracts with the city, audit of actions taken, consent scopes granted.
- **Customers trust the system.** Every view of their data is logged and visible to them. Export is one click. Erasure is honest.
- **Scales to 100+ members.** The profile is self-serve. Each new citizen costs minutes, not hours of coordinator time.
- **Compliance narrative is real.** PIPEDA + GDPR posture is demonstrable, not just claimed. Audit log + consent management + export + erasure = the table stakes, shipped.

Profile primitive is the **membership card of the protocol-city**. Without it, every citizen is a record we mutate. With it, every citizen has agency inside the system — which is the whole point of the city.

---

## 11. Versioning

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-04-24 | Initial draft. Written after the metabolic-loop spec (§10) lands. Pending Athena gate. |

**Supersedes:** none.
**Superseded by:** TBD.
