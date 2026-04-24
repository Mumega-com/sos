# Section 3 — Structured Records (Contacts, Partners, Opportunities, Referrals)

**Author:** Loom (dispatched to Haiku + Sonnet; consolidated by Athena gate)
**Date:** 2026-04-24
**Gate:** Athena — PASSED with consolidation
**Owner:** Kasra
**Source drafts:** `03-records.md` (Haiku, practical schema) + original 03-structured-records.md (Sonnet, full depth)
**Migration numbers:** TBD — sequenced after RBAC (0012) per Phase 1 table; likely 0013–0015

---

## Overview

Four relational tables added to Squad Service (:8060): single master record per entity (Deloitte discipline), workspace-isolated, role-gated, event-driven. These are **kernel-level shared tables** — not GAF-only. Every plugin reads them via `squad.contacts`, `squad.partners`, etc. (see Section 6). GAF-specific fields live in `gaf_*` tables; these tables carry the relationship graph.

**Naming note:** The access control field is called `visibility_tier` (not `engagement_tier`) to avoid vocabulary collision with Mirror's `tier` field which uses 'public'/'squad'/'project'/'role'/'entity'/'private'. Visibility tier controls who can read the record within a workspace; Mirror tier controls memory scope.

---

## Database Schema

### `contacts` table

Master record for individuals. One row per person per workspace.

```sql
CREATE TABLE contacts (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id      UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  external_id       TEXT,                          -- CRM/GHL/legacy system ID
  first_name        VARCHAR(255) NOT NULL,
  last_name         VARCHAR(255) NOT NULL,
  email             VARCHAR(255),
  phone             VARCHAR(20),
  title             VARCHAR(255),
  org_id            UUID        REFERENCES partners(id) ON DELETE SET NULL,
  visibility_tier   TEXT        NOT NULL DEFAULT 'firm_internal'
                      CHECK (visibility_tier IN ('public', 'firm_internal', 'privileged')),
  engagement_status TEXT        NOT NULL DEFAULT 'prospect'
                      CHECK (engagement_status IN ('prospect', 'active', 'paused', 'closed')),
  source            VARCHAR(255),                  -- 'inbound' | 'referral' | 'event' | 'ghl'
  last_touched_at   TIMESTAMPTZ,                   -- last meaningful interaction
  next_action       TEXT,                          -- "schedule call", "send proposal"
  notes_ref         TEXT,                          -- slug to Inkwell page with history
  notes             TEXT,                          -- inline freeform for quick updates
  archived_at       TIMESTAMPTZ,                   -- soft delete
  owner_id          UUID        NOT NULL REFERENCES users(id),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by        TEXT        NOT NULL,          -- agent:loom | user:hadi
  updated_by        TEXT        NOT NULL,
  UNIQUE (workspace_id, external_id),
  UNIQUE (workspace_id, email)
);

CREATE INDEX idx_contacts_workspace ON contacts (workspace_id);
CREATE INDEX idx_contacts_org       ON contacts (org_id);
CREATE INDEX idx_contacts_owner     ON contacts (owner_id);
CREATE INDEX idx_contacts_status    ON contacts (workspace_id, engagement_status);
CREATE INDEX idx_contacts_email     ON contacts (workspace_id, email);
CREATE INDEX idx_contacts_updated   ON contacts (updated_at DESC);

ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
CREATE POLICY contacts_isolation ON contacts
  USING (workspace_id = auth.workspace_id());
```

---

### `partners` table

Master record for organizations. Channels, accelerators, firms, cert bodies, brokerages.

```sql
CREATE TABLE partners (
  id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id        UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  external_id         TEXT,
  name                VARCHAR(512) NOT NULL,
  type                TEXT        NOT NULL
                        CHECK (type IN (
                          'broker', 'accelerator', 'university', 'cert-body',
                          'sr-ed-firm', 'realtor', 'filing-partner', 'referral-source',
                          'investor', 'channel', 'platform', 'other'
                        )),
  website_url         VARCHAR(2048),
  hq_country          CHAR(2),                     -- ISO 3166-1 alpha-2
  primary_contact_id  UUID        REFERENCES contacts(id) ON DELETE SET NULL,
  parent_partner_id   UUID        REFERENCES partners(id),  -- hierarchy
  revenue_split_pct   DECIMAL(5,2),                -- commission % for channel partners
  visibility_tier     TEXT        NOT NULL DEFAULT 'firm_internal'
                        CHECK (visibility_tier IN ('public', 'firm_internal', 'privileged')),
  engagement_status   TEXT        NOT NULL DEFAULT 'prospect'
                        CHECK (engagement_status IN ('prospect', 'active', 'paused', 'closed')),
  notes               TEXT,
  inkwell_page_slug   TEXT,                         -- canonical partnership page
  onboarded_at        TIMESTAMPTZ,
  active              BOOLEAN     NOT NULL DEFAULT TRUE,
  archived_at         TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by          TEXT        NOT NULL,
  updated_by          TEXT        NOT NULL,
  UNIQUE (workspace_id, external_id),
  UNIQUE (workspace_id, name)
);

CREATE INDEX idx_partners_workspace ON partners (workspace_id);
CREATE INDEX idx_partners_type      ON partners (workspace_id, type);
CREATE INDEX idx_partners_parent    ON partners (parent_partner_id);

ALTER TABLE partners ENABLE ROW LEVEL SECURITY;
CREATE POLICY partners_isolation ON partners
  USING (workspace_id = auth.workspace_id());
```

---

### `opportunities` table

Deals in flight. Tracks type, pipeline stage, value, and participants.

```sql
CREATE TABLE opportunities (
  id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id         UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  external_id          TEXT,
  name                 VARCHAR(512) NOT NULL,
  type                 TEXT        NOT NULL
                         CHECK (type IN (
                           'customer-deal', 'partnership', 'investment',
                           'channel-expansion', 'gov-relationship'
                         )),
  partner_id           UUID        REFERENCES partners(id) ON DELETE SET NULL,
  primary_contact_id   UUID        REFERENCES contacts(id) ON DELETE SET NULL,
  stage                TEXT        NOT NULL DEFAULT 'prospect'
                         CHECK (stage IN ('prospect', 'active', 'won', 'lost', 'on-hold')),
  stage_entered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  estimated_value      DECIMAL(12,2),
  estimated_close_at   DATE,
  close_reason         VARCHAR(512),
  owner_id             UUID        NOT NULL REFERENCES users(id),
  notes_ref            TEXT,
  notes                TEXT,
  archived_at          TIMESTAMPTZ,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by           TEXT        NOT NULL,
  updated_by           TEXT        NOT NULL,
  UNIQUE (workspace_id, external_id)
);

CREATE INDEX idx_opportunities_workspace ON opportunities (workspace_id);
CREATE INDEX idx_opportunities_stage     ON opportunities (workspace_id, stage);
CREATE INDEX idx_opportunities_owner     ON opportunities (owner_id);
CREATE INDEX idx_opportunities_partner   ON opportunities (partner_id);

ALTER TABLE opportunities ENABLE ROW LEVEL SECURITY;
CREATE POLICY opportunities_isolation ON opportunities
  USING (workspace_id = auth.workspace_id());
```

**Stage audit log** — every stage transition writes a row:

```sql
CREATE TABLE opportunity_stage_log (
  id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id  UUID        NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
  from_stage      TEXT        NOT NULL,
  to_stage        TEXT        NOT NULL,
  transitioned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  transitioned_by TEXT        NOT NULL              -- agent:kaveh | user:hadi
);

CREATE INDEX idx_opp_stage_log_opp ON opportunity_stage_log (opportunity_id);
```

---

### `referrals` table

Graph edges: who introduced whom. Polymorphic source/target (contact or partner).

```sql
CREATE TABLE referrals (
  id            UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID    NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  source_id     UUID    NOT NULL,
  source_type   TEXT    NOT NULL CHECK (source_type IN ('contact', 'partner')),
  target_id     UUID    NOT NULL,
  target_type   TEXT    NOT NULL CHECK (target_type IN ('contact', 'partner')),
  relationship  TEXT    NOT NULL
                  CHECK (relationship IN (
                    'referred', 'invested-in', 'co-founded', 'serves',
                    'introduced-to', 'competitor-of', 'ally-of', 'advises'
                  )),
  strength      TEXT    NOT NULL DEFAULT 'moderate'
                  CHECK (strength IN ('weak', 'moderate', 'strong', 'trusted')),
  context       VARCHAR(512),
  referred_at   TIMESTAMPTZ,
  notes         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by    TEXT    NOT NULL,
  UNIQUE (workspace_id, source_id, source_type, target_id, target_type, relationship)
);

CREATE INDEX idx_referrals_workspace ON referrals (workspace_id);
CREATE INDEX idx_referrals_source    ON referrals (source_id, source_type);
CREATE INDEX idx_referrals_target    ON referrals (target_id, target_type);
CREATE INDEX idx_referrals_strength  ON referrals (strength);

ALTER TABLE referrals ENABLE ROW LEVEL SECURITY;
CREATE POLICY referrals_isolation ON referrals
  USING (workspace_id = auth.workspace_id());
```

---

## HTTP Routes

All endpoints on Squad Service (:8060). Auth: SOS bearer token, workspace-scoped.

### Contacts

| Method | Path | Min role | Purpose |
|--------|------|----------|---------|
| `POST` | `/contacts` | coordinator | Create contact |
| `GET` | `/contacts` | any | List (filterable: owner, org, status, archived, tier) |
| `GET` | `/contacts/{id}` | any | Get single contact |
| `PATCH` | `/contacts/{id}` | owner or coordinator | Update contact |
| `POST` | `/contacts/{id}/touch` | owner or coordinator | Update `last_touched_at` + append note |
| `DELETE` | `/contacts/{id}` | owner or coordinator | Soft-delete (`archived_at = now()`) |
| `GET` | `/contacts/by-email/{email}` | coordinator | Deduplication lookup |

### Partners

| Method | Path | Min role | Purpose |
|--------|------|----------|---------|
| `POST` | `/partners` | coordinator | Create partner |
| `GET` | `/partners` | any | List (filterable: type, active, status) |
| `GET` | `/partners/{id}` | any | Get single partner |
| `PATCH` | `/partners/{id}` | coordinator | Update partner |
| `GET` | `/partners/{id}/contacts` | any | Contacts at org |
| `GET` | `/partners/{id}/opportunities` | any | Opportunities with org |

### Opportunities

| Method | Path | Min role | Purpose |
|--------|------|----------|---------|
| `POST` | `/opportunities` | coordinator | Create opportunity |
| `GET` | `/opportunities` | any | List (filterable: stage, partner, owner) |
| `GET` | `/opportunities/{id}` | any | Get single opportunity |
| `PATCH` | `/opportunities/{id}/stage` | owner or coordinator | Transition stage (auto-logs) |
| `PATCH` | `/opportunities/{id}` | owner or coordinator | Update other fields |
| `GET` | `/opportunities/pipeline-summary` | coordinator | Count + value by stage |

### Referrals

| Method | Path | Min role | Purpose |
|--------|------|----------|---------|
| `POST` | `/referrals` | coordinator | Create referral edge |
| `GET` | `/referrals` | any | Query graph (BFS by source or target) |
| `GET` | `/referrals/network/{id}` | any | N-hop network around entity |
| `PATCH` | `/referrals/{id}` | coordinator | Update strength/context |
| `DELETE` | `/referrals/{id}` | coordinator | Remove edge |

### Integrations

`POST /integrations/ghl/sync-contact` — accepts GHL lead payload, upserts contact keyed by email. Used by GHL webhook on new leads.

---

## Event Emissions

Every write emits on the SOS bus:

```json
{
  "event_type": "structured_record:{entity}:{created|updated|deleted}",
  "entity_id": "uuid",
  "workspace_id": "uuid",
  "timestamp": "2026-04-24T14:32:00Z",
  "actor": "agent:loom",
  "payload": { "...": "..." }
}
```

Bus topics:
- `sos:event:squad:records:contact:*`
- `sos:event:squad:records:partner:*`
- `sos:event:squad:records:opportunity:*`
- `sos:event:squad:records:referral:*`

Subscribers: Mirror (embed for semantic search), Section 5 revenue/SLA dashboards, Kaveh business graph (Section 1E ingest).

---

## Seed Data (production — real relationships)

**Contacts:**

| Name | Email | Title | Org | Visibility | Status | Owner |
|------|-------|-------|-----|-----------|--------|-------|
| Ron O'Neil | — | Partner | Century 21 | privileged | active | hadi |
| Matt Borland | — | CEO | AgentLink | privileged | active | hadi |
| Bella Harbottle | — | AI Consultant | independent | firm_internal | active | hadi |
| Peggy Hill | — | Investor | independent | privileged | prospect | hadi |
| Hossein | — | Referral Partner | independent | firm_internal | active | hadi |
| Dmitri Bakker | — | — | — | firm_internal | prospect | hadi |
| Maha Buhisi | — | — | — | firm_internal | prospect | hadi |
| Réjean Belliveau | — | — | — | firm_internal | prospect | hadi |
| PECB-NorAm Rep | — | Regional Rep | PECB | firm_internal | active | hadi |

**Partners:**

| Name | Type | Primary Contact | Revenue Split | Status |
|------|------|----------------|---------------|--------|
| Century 21 | realtor | Ron O'Neil | TBD | active |
| AI Intelligent Solutions | sr-ed-firm | — | TBD | prospect |
| PECB | cert-body | PECB-NorAm Rep | — | active |
| YSpace | accelerator | — | — | prospect |
| Schulich Business School | university | — | — | prospect |
| Riipen | filing-partner | — | TBD | prospect |

**Opportunities:**

| Name | Type | Partner | Contact | Stage | Est. Value |
|------|------|---------|---------|-------|-----------|
| AgentLink Phase 1 | partnership | AgentLink | Matt Borland | active | $500K |
| Century 21 White-Label | channel-expansion | Century 21 | Ron O'Neil | prospect | TBD |
| ISO 42001 Product | customer-deal | PECB | PECB-NorAm Rep | active | — |
| ISED AGS Relationship | gov-relationship | — | — | prospect | $1M |
| 37 CDAP Upsell Batch | customer-deal | — | — | prospect | — |

**Referrals:**

| Source | Target | Relationship | Strength |
|--------|--------|-------------|---------|
| Ron O'Neil | Gavin (contact) | referred | strong |
| Hossein | 2 GAF customers | referred | moderate |
| Peggy Hill | Matt Borland | introduced-to | trusted |

---

## Cross-Cutting Concerns

### Workspace isolation
All tables enforce `workspace_id = auth.workspace_id()` via RLS. No cross-workspace queries possible.

### Visibility tier
- `public` — any authenticated user in workspace
- `firm_internal` — firm staff and coordinators only
- `privileged` — explicitly privileged roles (leadership, account management)

RLS enforces tier on SELECT. All writes require coordinator+ role. Default: `firm_internal`.

### Soft delete
`archived_at` is the delete signal. Default list queries filter `archived_at IS NULL`. Hard-delete requires compliance sign-off.

### Deduplication
`(workspace_id, email)` unique on contacts; `(workspace_id, name)` unique on partners. `GET /contacts/by-email/{email}` is the dedup lookup for GHL/import flows.

### Audit trail
`created_by` / `updated_by` carry agent/user identity (TEXT, e.g. `agent:loom`, `user:hadi`). Not a foreign key — agents don't live in the users table.

---

## Tests

- [ ] Upsert idempotency: `POST /contacts` same email twice → single row, `updated_at` changes, no duplicate
- [ ] Owner-scoped reads: visibility_tier=privileged contact not returned to public-tier token
- [ ] Stage transition logging: `PATCH /opportunities/{id}/stage` → `opportunity_stage_log` row written
- [ ] Archive cascade: archive partner → dependent contacts get `org_id = null`, not deleted; opportunities get `partner_id = null`
- [ ] Referral graph BFS: `GET /referrals/network/{id}` returns 2-hop chain correctly
- [ ] Cross-tenant isolation: partner in workspace A not visible with workspace B token → 0 results
- [ ] Soft delete: archived contacts excluded from default `GET /contacts` without `include_archived=true`
- [ ] Email uniqueness: duplicate email in same workspace → 409
- [ ] GHL sync: `POST /integrations/ghl/sync-contact` with new lead upserts contact with `source=ghl`
- [ ] GHL sync dedup: same lead twice → one row, updated_at changes
- [ ] Bus event: `POST /contacts` → event on `sos:event:squad:records:contact:created`

---

## Open Questions (resolved at gate)

1. **Polymorphic referrals:** Enforced at DB level via CHECK constraints on `source_type`/`target_type`. App layer validates the referenced UUID exists in the correct table before insert. No DB-level FK on polymorphic columns.

2. **Notes versioning:** `notes_ref` links to Inkwell page slug (append-only by convention); `notes` field is in-record for quick updates. Full versioned history lives in Mirror engrams tagged with `entity_id = contact_uuid`. No separate notes_history table in v1.

3. **Referral visibility:** Team-visible. `visibility_tier = 'firm_internal'` default.

4. **Pipeline stage metadata:** `notes` field carries stage-specific context in free text. JSON payload field deferred to v2 (keep it simple — YAGNI).

5. **Multi-workspace seed:** Seed data targets the default workspace (GAF). Other plugins reference this via `shared_tables.squad.contacts` in their manifest (Section 6).

---

*Supersedes `03-records.md` — that file should be deleted after this consolidation is committed.*
