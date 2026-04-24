# Inkwell Hive RBAC — Five-Tier Access Architecture
**Date:** 2026-04-24
**Author:** Loom
**Gate:** Athena
**PM:** Loom
**Status:** Draft v1

---

## Background & Mandate

Mumega is building hidden infrastructure for Canadian SMB capital matching (GAF) and AI implementation (Digid). The mandate is orchestrator, not practitioner: the system must run GAF without Hadi present. That means knowledge — playbooks, patterns, case studies, client-specific context — must be organized so the right agents see the right content, customers never see each other's data, and de-identified wins flow to public SEO automatically.

Inkwell is already the content surface (Astro framework, shipped at mumega.com). It ingests markdown, generates pages, and is config-driven. What it lacks is access control: today every piece of content is either fully public or not served at all. As the first customer knight (kaveh, GAF) goes live, and the first human-in-the-loop (Noor, Riipen intern, YSpace) begins working inside the system, we need tiered access enforced at read time — not just at publish time.

This spec extends three shipped primitives: project-sessions (Squad Service :8060, shipped 2026-04-24 by Kasra, commit 1dd597bb), DISP-001 scoped tokens, and Mirror workspace isolation (Memory API :8844). Nothing here replaces those — it layers RBAC on top of the identity and session machinery that already exists.

---

## The Five Tiers

| Tier | Who reads | Who writes | Example content |
|------|-----------|-----------|-----------------|
| `public` | Anyone, no auth | Agents via promotion pipeline | Blog posts, SEO pages, de-identified case studies |
| `squad` | Agents with squad membership token | Agents in that squad | Squad playbooks, repeating patterns, toolkits |
| `project` | Agents + humans with project-scoped token | Project agents | Client intake notes, task summaries, draft deliverables |
| `role` | Holders of a specific named role | Project owner or coordinator | Noor's intern workspace, kaveh's GAF advisor view |
| `entity` | Holders of entity token only | Entity owner | A single customer's confidential documents |
| `private` | Issuing agent only | Issuing agent | Agent scratchpads, pre-publish drafts |

`public` requires no auth. Every other tier requires a valid token with matching scope, checked at SSR time.

---

## Primitive 1 — Role Registry + Role-Scoped Tokens

Extends `project_members` (project-sessions design spec) with a full role registry. Roles are named, carry explicit permission sets, and are assigned to agents or humans per project.

### Schema

```sql
-- New tables in Squad Service SQLite DB (migration 0010)

CREATE TABLE roles (
    id          TEXT PRIMARY KEY,           -- e.g. 'gaf-intern', 'gaf-advisor'
    project_id  TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE(project_id, name, tenant_id)
);

CREATE TABLE role_permissions (
    role_id     TEXT NOT NULL REFERENCES roles(id),
    permission  TEXT NOT NULL,              -- e.g. 'inkwell:read:role', 'inkwell:write:project'
    PRIMARY KEY (role_id, permission)
);

CREATE TABLE role_assignments (
    role_id     TEXT NOT NULL REFERENCES roles(id),
    assignee_id TEXT NOT NULL,              -- agent_id or human identifier
    assignee_type TEXT NOT NULL DEFAULT 'agent',  -- 'agent' | 'human'
    assigned_at TEXT NOT NULL,
    assigned_by TEXT NOT NULL,
    PRIMARY KEY (role_id, assignee_id)
);
```

### Token Extension

Tokens issued by DISP-001 gain a `roles` array. The existing `project` and `role` claims remain; `roles` is additive:

```typescript
interface ScopedToken {
  project: string       // existing — project_id scope
  role: 'owner' | 'member' | 'observer'  // existing — project_members role
  roles: string[]       // new — named role IDs from roles table
  entity_id?: string    // new — for entity-tier tokens
  tier_floor?: Tier     // new — minimum tier this token can access
}
```

### HTTP Routes

| Method | Path | Required role | Purpose |
|--------|------|---------------|---------|
| POST | `/projects/{id}/roles` | owner | Create named role |
| GET | `/projects/{id}/roles` | observer | List roles |
| POST | `/roles/{role_id}/permissions` | owner | Add permission to role |
| POST | `/roles/{role_id}/assignments` | owner | Assign role to agent or human |
| DELETE | `/roles/{role_id}/assignments/{assignee_id}` | owner | Revoke assignment |
| GET | `/roles/{role_id}/assignments` | observer | List assignees |

---

## Primitive 2 — Inkwell RBAC Middleware + Tier Schema

### Content Schema Extension

`src/content.config.ts` gains three fields on all collections:

```typescript
// Zod additions to every collection schema
tier: z.enum(['public', 'squad', 'project', 'role', 'entity', 'private'])
  .default('public'),
entity_id: z.string().optional(),       // required when tier = 'entity'
permitted_roles: z.array(z.string()).default([]),  // role IDs; checked when tier = 'role'
```

Frontmatter example:

```markdown
---
title: "GAF Intake Playbook v3"
tier: role
permitted_roles: ["gaf-advisor", "gaf-intern"]
entity_id: ~
---
```

### Middleware (Astro SSR)

A middleware at `src/middleware.ts` intercepts every non-public page request:

1. If `tier === 'public'` — pass through, no auth.
2. Extract `Authorization: Bearer <token>` header (or `?token=` param for SSE consumers).
3. Verify token signature (shared secret, same mechanism as DISP-001).
4. Decode `ScopedToken` claims.
5. Run tier gate:
   - `squad` — token must carry squad membership (checked via Squad Service membership endpoint).
   - `project` — `token.project` must match content's `project_id` frontmatter field.
   - `role` — `token.roles` must intersect `content.permitted_roles`.
   - `entity` — `token.entity_id` must equal `content.entity_id`.
   - `private` — `token.sub` (issuing agent) must match `content.author`.
6. On failure — return 403, never reveal content existence.

All SSR pages rendered by Astro. No client-side token exposure.

---

## Primitive 3 — Mirror Engram Tier/Entity Fields + Gated Recall

### Engram Schema Extension

Two new columns on the `engrams` table (Mirror API :8844, Supabase pgvector):

```sql
ALTER TABLE engrams ADD COLUMN tier TEXT NOT NULL DEFAULT 'project';
ALTER TABLE engrams ADD COLUMN entity_id TEXT;
ALTER TABLE engrams ADD COLUMN permitted_roles TEXT[];  -- postgres array
```

### Recall Gate

`GET /recall` and `POST /search` in Mirror API enforce:

1. Require `Authorization` header — no anonymous recall.
2. Decode token, extract `project`, `roles`, `entity_id`.
3. Filter SQL:

```sql
SELECT * FROM engrams
WHERE workspace_id = $workspace          -- existing isolation
  AND (
    tier = 'public'
    OR (tier = 'squad' AND squad_id = ANY($accessor_squads))
    OR (tier = 'project' AND project_id = $accessor_project)
    OR (tier = 'role' AND permitted_roles && $accessor_roles)
    OR (tier = 'entity' AND entity_id = $accessor_entity_id)
    OR (tier = 'private' AND author_id = $accessor_id)
  )
```

Zero cross-tenant leakage: `workspace_id` gate remains the outer fence. Tier gate is inner.

### Migration Plan for Existing Engrams

```sql
-- Run as Alembic migration on Mirror DB
UPDATE engrams
SET tier = 'project',
    entity_id = workspace_id  -- treat existing workspace as entity boundary
WHERE tier IS NULL;
```

No content is exposed wider than it currently is. Existing workspace isolation is preserved; the tier field simply formalizes it as `project` tier.

---

## Primitive 4 — Squad KB Linkage + Promotion Pipeline

### Squad Schema Extension

```sql
-- migration 0011 in Squad Service
ALTER TABLE squads ADD COLUMN kb_tier TEXT DEFAULT 'squad';
ALTER TABLE squads ADD COLUMN kb_ref TEXT;  -- e.g. 'content/en/squads/gaf/'
```

### Promotion Pipeline

Agents emit engrams during task work. A promotion worker runs after task completion:

**Classification rules (evaluated in order):**

| Signal | Target tier | Gate |
|--------|-------------|------|
| `entity_id` present AND unique to one customer | `entity` | Auto |
| Pattern seen in 2+ projects, no PII | `squad` | Coordinator auto-approve |
| Pattern seen in 3+ projects, fully de-identified | `public` | Athena review required |

**Promotion flow:**

```
task_complete event
  → promotion_worker.classify(engram)
    → if public candidate: create task(label=needs_review, assignee=athena)
    → if squad candidate: auto-promote, update tier in Mirror
    → if entity: no action (already scoped)
```

**HTTP Routes (Squad Service)**

| Method | Path | Required role | Purpose |
|--------|------|---------------|---------|
| POST | `/engrams/{id}/promote` | coordinator | Trigger promotion for one engram |
| GET | `/engrams/candidates` | coordinator | List promotion candidates |
| POST | `/engrams/{id}/approve` | athena (owner) | Approve public promotion |
| POST | `/engrams/{id}/reject` | athena (owner) | Reject, keep at current tier |

---

## Access Flow Examples

**Scenario A — Noor reads her role inkwell + GAF project inkwell**

Noor holds token: `{ project: "gaf", role: "observer", roles: ["gaf-intern"], entity_id: null }`.

She visits `/projects/gaf/playbooks/intake-v3` (tier: `role`, permitted_roles: `["gaf-advisor", "gaf-intern"]`). Middleware checks `token.roles` ∩ `["gaf-advisor", "gaf-intern"]` = `["gaf-intern"]` — non-empty, access granted.

She visits `/projects/gaf/updates/week-17` (tier: `project`, project_id: `gaf`). Middleware checks `token.project === "gaf"` — true, access granted.

She visits `/projects/digid/anything` — `token.project !== "digid"` — 403.

**Scenario B — Kaveh promotes a pattern from project to squad**

Kaveh (entity token: `{ project: "gaf", entity_id: "kaveh", roles: ["gaf-advisor"] }`) completes a task. Promotion worker inspects the resulting engram — no PII, same pattern appears in two prior GAF projects. Worker calls `POST /engrams/{id}/promote` with classifier result `squad`. Since coordinator auto-approve applies, Mirror updates `tier = 'squad'`, `entity_id = null`. Engram is now readable by any agent in the GAF squad.

**Scenario C — A customer reads their entity inkwell but not other customers'**

Customer A holds token `{ entity_id: "customer-a", project: "gaf" }`. They request `/clients/customer-a/report-q1` (tier: `entity`, entity_id: `customer-a`). `token.entity_id === content.entity_id` — access granted. They request `/clients/customer-b/report-q1` — `token.entity_id !== "customer-b"` — 403. The existence of customer-b's content is not revealed.

---

## Migration Plan

| Target | Action | Risk |
|--------|--------|------|
| Existing engrams | `UPDATE SET tier='project', entity_id=workspace_id` | None — narrower than current access |
| Existing Inkwell content (mumega.com) | Add `tier: public` to all existing frontmatter via `npm run ingest` post-migration | None — all existing content is already public |
| Squad Service DB | Migrations 0010 (roles) + 0011 (squad KB columns) | Additive only, no column drops |
| DISP-001 token issuance | Add `roles: []`, `entity_id: null` defaults to all issued tokens | Backward-compatible; old tokens without these fields treated as empty |

Rollout order: Mirror migration → Squad Service migrations → Inkwell middleware deploy → promotion pipeline.

---

## Test Plan

**RBAC leak test:** Issue two tokens for different projects. Confirm project-A token returns 403 on project-B content at all tiers. Confirm 403 does not reveal whether content exists.

**Promotion test:** Create three engrams with identical patterns across three projects. Run promotion worker. Confirm engram tier advances to `squad`. Confirm `public` candidate creates Athena review task and does not auto-promote. Confirm Athena approval changes tier to `public`.

**Cross-tenant test:** Two workspaces in Mirror with overlapping squad IDs. Confirm recall from workspace-A never returns engrams from workspace-B regardless of tier match.

**Token-scope test:** Issue `member` token. Confirm it satisfies `observer` routes. Confirm it fails `owner` routes. Issue role token without `roles: ["gaf-advisor"]`. Confirm it fails role-gated content with `permitted_roles: ["gaf-advisor"]`.

---

## Open Questions

1. **Athena (gate):** Should `private` tier be agent-only, or should human `owner` tokens also access their agents' private engrams for audit purposes?
2. **Loom (architecture):** Promotion pipeline runs as a Squad Service worker or a standalone sovereign task? Sovereign loop has project context; Squad Service has the engram metadata. Recommend sovereign task — decide before implementation.
3. **Loom:** `squad_kb_ref` is a file path today (`content/en/squads/gaf/`). Should it be a URL when Inkwell is deployed to Cloudflare Pages? Needs to be resolvable by agents not running on the VPS.
4. **Athena:** PII classification in the promotion pipeline — who owns the classifier? Athena review is the gate for public, but classification before that step needs a rule engine or LLM call. Scope and model tier TBD.
5. **Loom:** Entity tokens for human customers (e.g., kaveh) — are these issued via the SaaS Service (:8075) or via Squad Service? Currently SaaS owns customer onboarding. Need to decide which service mints entity-scoped tokens to avoid a split-authority problem.
