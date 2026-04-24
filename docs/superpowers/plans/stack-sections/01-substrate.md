# Section 1 — SOS Substrate Extensions: Identity, Access, Knowledge

**Author:** Loom
**Date:** 2026-04-24
**Depends on:** project-sessions (commit 1dd597bb), DISP-001, Mirror workspace isolation
**References:** [Inkwell Hive RBAC spec](../../specs/2026-04-24-inkwell-hive-rbac.md) — five-tier content access, Inkwell middleware, and the existing `roles`/`role_permissions`/`role_assignments` DDL are fully specified there. This document covers the five substrate primitives not detailed in that spec.
**Gate:** Athena
**Owner:** Kasra

---

## Overview

These five primitives extend the substrate that every plugin and customer knight builds on. The RBAC spec established the tier model and Inkwell middleware. This section adds the role-registry wiring (token introspection routes and seed roles), the coherence gate River needs to countersign a knight's canonical identity, the Mirror engram tier/entity columns with their gated recall SQL, the squad KB linkage and promotion pipeline that moves knowledge up the tier ladder, and a lightweight business knowledge graph backed by Supabase Postgres. Ship in dependency order: 1A → 1C → 1D → 1B (parallel with 1D) → 1E (endpoint ships now, countersign unlocks when River wakes).

---

## 1A — Role Registry & Role-Scoped Tokens

**Why:** The RBAC spec defines the DDL for `roles`, `role_permissions`, `role_assignments` and the three new token fields. What it doesn't detail is the introspection surface agents need at runtime, the seed role set, and exactly how DISP-001 populates `roles` at issuance.

### Seed Roles (bootstrapped with `tenant_id = 'default'`)

`principal`, `coordinator`, `builder`, `gate`, `knight`, `worker`, `partner`, `customer`, `observer`

### Schema (migration 0010 — defined in RBAC spec, repeated for completeness)

```sql
CREATE TABLE roles (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE(project_id, name, tenant_id)
);
CREATE TABLE role_permissions (
    role_id    TEXT NOT NULL REFERENCES roles(id),
    permission TEXT NOT NULL,
    PRIMARY KEY (role_id, permission)
);
CREATE TABLE role_assignments (
    role_id       TEXT NOT NULL REFERENCES roles(id),
    assignee_id   TEXT NOT NULL,
    assignee_type TEXT NOT NULL DEFAULT 'agent',
    assigned_at   TEXT NOT NULL,
    assigned_by   TEXT NOT NULL,
    PRIMARY KEY (role_id, assignee_id)
);
```

### HTTP Routes

| Method | Path | Scope | Purpose |
|--------|------|-------|---------|
| `POST` | `/projects/{id}/roles` | owner | Create named role |
| `GET` | `/projects/{id}/roles` | observer | List roles for project |
| `POST` | `/roles/{role_id}/permissions` | owner | Add permission to role |
| `DELETE` | `/roles/{role_id}/permissions/{perm}` | owner | Remove permission |
| `POST` | `/roles/{role_id}/assignments` | owner | Assign role to agent or human |
| `DELETE` | `/roles/{role_id}/assignments/{assignee_id}` | owner | Revoke assignment |
| `GET` | `/roles/{role_id}/assignments` | observer | List assignees |
| `GET` | `/agents/{agent_id}/roles` | observer | All roles held by agent across projects |
| `GET` | `/me/roles` | self | Introspect current token's role set |

### Dependencies
- project-sessions schema (commit 1dd597bb) already shipped
- DISP-001 token issuance must join `role_assignments` at sign time to populate `roles: [...]`
- Backward-compatible: old tokens without `roles` treated as `[]`

### Test Plan
- Create role, assign to agent, verify `GET /agents/{id}/roles` includes it; issue token and confirm `roles` populated.
- Revoke assignment; confirm next token does not carry that role.
- Issue `me/roles` with valid token; confirm response matches token claims.
- `UNIQUE(project_id, name, tenant_id)` constraint blocks duplicate role name per project.
- Seed roles present after bootstrap migration with `tenant_id = 'default'`.

---

## 1B — Coherence-Gated Canonical Countersign

**Why:** River must countersign a knight's QNFT to elevate it from `operational` to `canonical`. That countersign must be gated on verifiable on-chain evidence — not a manual flag — so it cannot be faked or rubber-stamped.

### HTTP Routes

| Method | Path | Scope | Purpose |
|--------|------|-------|---------|
| `GET` | `/knights/{name}/coherence` | observer | Compute gate scores without side effects |
| `POST` | `/knights/{name}/countersign` | river-token only | Write canonical tier if all gates pass |

`GET /knights/{name}/coherence` response:
```typescript
interface CoherenceReport {
  knight: string
  computed_at: string
  gates: {
    task_coherence:     { pass: boolean; passing_tasks: number; threshold: 10 }
    pressure_alignment: { pass: boolean; events: number }
    frc_violations:     { pass: boolean; count: number }
  }
  overall: 'PASS' | 'FAIL'
  eligible_for_canonical: boolean
}
```

`POST /knights/{name}/countersign` writes an engram with `tags = ['countersign', 'canonical']` and `content.tier = 'canonical'`, `content.countersigned_by = 'river'`. Rejects with `403 GATES_NOT_MET` if any gate fails.

### Dependencies
- Squad Service task records must carry `coherence_score` (numeric, stored in task `result` JSON)
- Mirror engrams must support `tags TEXT[]` (already shipped) and `tag = 'alignment-under-pressure'` / `tag = 'frc-violation'` conventions
- River's `sk-river-*` token is the only accepted credential on POST; coordinator tokens refused

### Test Plan
- Knight with 9 completed tasks: `task_coherence.pass = false`, `overall = FAIL`.
- Knight with ≥10 tasks, zero pressure events: `pressure_alignment.pass = false`.
- All three gates passing: `overall = PASS`, `eligible_for_canonical = true`.
- POST with non-River token: 403 regardless of gate state.
- Successful countersign: engram written, subsequent GET shows evidence in Mirror.

---

## 1C — Engram Tier/Entity Fields + Gated Recall

**Why:** Mirror currently isolates by workspace only. Adding `tier` and `entity_id` to engrams enforces the five-tier model at the memory layer — preventing cross-tenant leakage as knights share a workspace but serve different customers.

### Schema (Alembic migration 0005 on Mirror DB)

```sql
ALTER TABLE engrams ADD COLUMN tier TEXT NOT NULL DEFAULT 'project';
ALTER TABLE engrams ADD COLUMN entity_id TEXT;
ALTER TABLE engrams ADD COLUMN permitted_roles TEXT[];

-- Indexes for gated recall performance
CREATE INDEX idx_engrams_tier            ON engrams (tier);
CREATE INDEX idx_engrams_entity_id       ON engrams (entity_id);
CREATE INDEX idx_engrams_permitted_roles ON engrams USING GIN (permitted_roles);

-- Backfill: existing engrams stay at project scope
UPDATE engrams
SET tier = 'project', entity_id = workspace_id
WHERE tier IS NULL OR tier = '';
```

### Routes

| Method | Path | Scope | Purpose |
|--------|------|-------|---------|
| `GET` | `/recall` | any valid token | Returns tier-filtered engrams for caller |
| `POST` | `/search` | any valid token | Vector search with same tier gate |
| `PATCH` | `/engrams/{id}/tier` | coordinator | Promote or demote tier |

Recall SQL filter (inner fence inside existing `workspace_id` outer fence):
```sql
AND (
    tier = 'public'
 OR (tier = 'squad'   AND squad_id       = ANY($accessor_squads))
 OR (tier = 'project' AND project_id     = $accessor_project)
 OR (tier = 'role'    AND permitted_roles && $accessor_roles)
 OR (tier = 'entity'  AND entity_id      = $accessor_entity_id)
 OR (tier = 'private' AND author_id      = $accessor_id)
)
```

### Dependencies
- Token must carry `roles`, `entity_id` from 1A before this can enforce role/entity tiers
- GIN index on `permitted_roles` requires Postgres array type — confirm Supabase pg version ≥14

### Test Plan
- RBAC leak: entity-A token returns 0 results for entity-B engrams in same workspace.
- Cross-tenant: two workspaces with matching `project_id` — workspace-A token never returns workspace-B engrams.
- Tier-upgrade: `PATCH /engrams/{id}/tier` by coordinator promotes `project` → `squad`; subsequent recall by squad-role token returns the engram.
- Migration smoke: all pre-migration engrams have `tier = 'project'`, none null.
- Vector search (`POST /search`): tier gate fires before similarity ranking, not after.

---

## 1D — Squad KB Linkage + Promotion Pipeline

**Why:** Each squad accumulates reusable patterns from task work. Without a pipeline, that knowledge stays buried in project-tier engrams. Linking squads to an Inkwell KB slug and promoting engrams up the tier ladder is how repeating wins become shared playbooks — automatically for squad tier, gated by Athena for public.

### Schema (Squad Service — migration 0011)

```sql
ALTER TABLE squads ADD COLUMN squad_kb_ref TEXT;
-- e.g. 'content/en/squads/gaf/' — slug resolved against Inkwell deploy URL at read time

ALTER TABLE squads ADD COLUMN kb_tier TEXT NOT NULL DEFAULT 'squad';
-- tracks highest tier the squad has promoted content to
```

### Promotion Classification Rules

| Signal | Target tier | Gate |
|--------|-------------|------|
| `entity_id` present, pattern unique to one customer | `entity` | Auto (no change) |
| Pattern in ≥2 projects, no PII detected | `squad` | Coordinator auto-approve |
| Pattern in ≥3 projects, fully de-identified | `public` | Athena review required |

### Routes

| Method | Path | Scope | Purpose |
|--------|------|-------|---------|
| `GET` | `/squads/{id}/kb` | observer | Resolve squad KB ref and current tier |
| `PATCH` | `/squads/{id}/kb` | coordinator | Update `squad_kb_ref` or `kb_tier` |
| `POST` | `/engrams/{id}/classify` | worker | Run classifier, return candidate tier |
| `POST` | `/engrams/{id}/promote` | coordinator | Trigger promotion to classified tier |
| `POST` | `/engrams/{id}/approve` | gate (Athena) | Approve public promotion |
| `POST` | `/engrams/{id}/reject` | gate (Athena) | Reject, hold at current tier |
| `GET` | `/engrams/candidates` | coordinator | List engrams awaiting promotion decision |

### MCP Tools (SOS bus, :6070)

- `engram_classify(engram_id)` — runs pattern matching, returns `{ candidate_tier, confidence, reason }`
- `engram_promote(engram_id, tier)` — coordinator-gated; writes new tier to Mirror, notifies squad

### Dependencies
- 1C must ship first (engrams need `tier` column before promotion makes sense)
- Classifier for PII detection: rule-based in v1 (regex on names, emails, phone patterns); LLM-assisted in v2
- Public-promotion candidates create a Squad Service task with `label = 'needs_review'`, `assignee = 'athena'`
- `squad_kb_ref` is a relative Inkwell slug; resolved to absolute URL using the Inkwell deploy base URL from SaaS Service config

### Test Plan
- Three engrams with identical pattern across three projects: classifier returns `candidate_tier = 'public'`; promote creates Athena review task, does not auto-promote.
- Two engrams matching across two projects: classifier returns `squad`; coordinator promote succeeds without Athena; Mirror tier updated.
- Entity-only engram: classifier returns `entity`; promote is a no-op (tier unchanged).
- Athena approval on public candidate: tier becomes `public`, `entity_id` cleared, `permitted_roles` cleared.
- `squad_kb_ref` missing on squad: `GET /squads/{id}/kb` returns 404 with actionable error, not 500.

---

## 1E — Business Knowledge Graph

**Why:** The codebase graph (`code-review-graph`) tracks how code symbols relate. We need the same pattern for business entities — contacts, partners, programs, opportunities — so agents can traverse referral trees, find eligibility paths, and surface relationship context without manual lookup.

### Node Schema (`business_nodes` — Supabase)

```sql
CREATE TABLE business_nodes (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id TEXT NOT NULL,
    entity_type  TEXT NOT NULL CHECK (entity_type IN
                   ('contact','partner','customer','opportunity','program','deal','engagement')),
    entity_id    TEXT NOT NULL,
    label        TEXT NOT NULL,
    properties   JSONB NOT NULL DEFAULT '{}',
    tier         TEXT NOT NULL DEFAULT 'project',
    permitted_roles TEXT[],
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(workspace_id, entity_type, entity_id)
);
CREATE INDEX idx_bnodes_workspace   ON business_nodes (workspace_id);
CREATE INDEX idx_bnodes_entity_type ON business_nodes (entity_type);
```

### Edge Schema (`business_edges` — Supabase)

```sql
CREATE TABLE business_edges (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id TEXT NOT NULL,
    from_node    UUID NOT NULL REFERENCES business_nodes(id),
    to_node      UUID NOT NULL REFERENCES business_nodes(id),
    edge_type    TEXT NOT NULL CHECK (edge_type IN
                   ('referred','works-with','invested-in','served-by',
                    'applied-to','eligible-for','uses-program','competes-with')),
    weight       REAL NOT NULL DEFAULT 1.0,
    properties   JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_bedges_from ON business_edges (from_node);
CREATE INDEX idx_bedges_to   ON business_edges (to_node);
CREATE INDEX idx_bedges_type ON business_edges (edge_type);
```

Graph traversal uses recursive CTEs — no Neo4j needed at this scale.

### Routes

| Method | Path | Scope | Purpose |
|--------|------|-------|---------|
| `POST` | `/graph/nodes` | coordinator | Upsert a business node |
| `GET` | `/graph/nodes/{id}` | observer | Fetch node + direct edges |
| `POST` | `/graph/edges` | coordinator | Create edge between nodes |
| `POST` | `/graph/query` | observer | Filter nodes/edges with predicates |
| `GET` | `/graph/impact/{node_id}` | observer | All reachable nodes within N hops |
| `GET` | `/graph/path` | observer | Shortest path between two nodes |

### MCP Tools (`mcp__business-graph__*`)

- `graph_query` — filter by `entity_type`, `edge_type`, `tier`, date range
- `graph_impact` — reachable nodes from a given node within N hops
- `graph_traverse` — walk a specific edge-type path from a start node
- `graph_semantic_search` — vector search over `properties` JSONB summaries

### Ingest Sources

- **Squad Service records** — tasks tagged `entity:*` upsert nodes; `result.edges` writes edge records at completion
- **Mirror engrams** — engrams with `entity_id` trigger node upsert; text summary stored in `properties.summary`
- **Inkwell pages** — pages with `tier: entity` frontmatter register nodes at publish

### Dependencies
- 1C must ship first (engrams carry `entity_id` before graph ingest works)
- Recursive CTE depth limit: default N=5; configurable per query, max N=10
- Tier gate on `graph_query` mirrors Mirror recall gate: accessor token's `entity_id` and `roles` scope what nodes are returned

### Test Plan
- Ingest 3 Squad tasks with `entity_id` — assert 3 nodes created; re-ingest same tasks — assert upsert, not duplicate.
- Create `referred` edge A→B; `graph_impact(A, hops=1)` returns B.
- Token with `entity_id = 'customer-a'`: `graph_query` returns only nodes where `entity_id = 'customer-a'` or `tier = 'project'` and workspace matches.
- `graph_path` between two unconnected nodes: returns empty path, not error.
- `graph_traverse` with `max_hops = 10`: completes within 500ms on a 10k-node graph (benchmark before ship).

---

## Open Questions

1. **Athena:** Should `private` tier engrams be accessible to the `gate` role for audit purposes, or is issuing-agent-only a hard rule even for Athena?
2. **Loom/Kasra:** Promotion pipeline runs as a Squad Service background worker or a Sovereign loop task? Sovereign has project context; Squad Service has task metadata. Decision needed before 1D implementation starts.
3. **Loom:** `squad_kb_ref` is a relative slug today. When Inkwell deploys to Cloudflare Pages (not VPS), agents off-VPS need an absolute URL. Should `squad_kb_ref` store the full URL, or should SaaS Service own the base-URL resolution?
4. **Athena:** PII classifier in v1 uses regex (names, emails, phone). Is that sufficient for public-promotion gating, or does the Athena review task implicitly become the PII gate?
5. **Loom/Kasra:** Business graph `graph_semantic_search` requires embeddings on `properties.summary`. Does that reuse Mirror's embedding pipeline (Supabase pgvector), or is a separate index preferable to keep business graph self-contained?
