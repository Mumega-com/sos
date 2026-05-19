# Section 12 — SOS Docs (Service Module)

**Author:** Loom
**Date:** 2026-04-24
**Phase:** 4 — `sos-docs` as a service module (NOT a kernel primitive), substrate-adjacent
**Depends on:** Section 1 (1A role registry, 1C engram tier schema), Section 6 (plugin.yaml contract), Burst 2A (Inkwell-Hive IH-1/IH-2)
**Gate:** Athena
**Owner:** Kasra
**Priority:** Burst 2A

---

## 1. Background & Mandate

### Why microservice, not kernel

Documentation is a read-heavy, schema-volatile surface. If it lived in kernel, every doc-schema change (new relation type, new tier mapping, new Inkwell host consuming it) would force a kernel upgrade and ripple across every plugin. That is the wrong blast radius. The MAP's microkernel discipline (§3.1) is explicit: default answer is service module.

`sos-docs` is therefore a peer service alongside Mirror, Squad, Dispatcher, and `sos-datalake` (see §8 for the pattern). It owns its own DB namespace (prefix `docs_`), registers via `plugin.yaml` (§6), exposes MCP tools plus a query API, and emits bus events. Kernel provides substrate (auth, bus, role registry, plugin loader, 1C tier semantics); `sos-docs` provides the canonical doc-node graph and the consumption contract for Inkwell hosts.

### Why graph, not file tree

Tonight's audit (2026-04-24) found **9 overlapping file-tree silos** of documentation — `docs/`, `docs/superpowers/plans/`, `agents/*/briefs/`, `content/en/`, `MAP.md`, `ROADMAP.md`, plus per-plugin READMEs. Each silo encoded tier (who can read) implicitly via filesystem location. None enforced the 5-tier Hive RBAC the architecture commits to. Forking customers (Digid Internal, AgentLink partner portal, future tenants) had no way to consume "their authorized slice" without hand-curating a fork.

A graph fixes this. Each doc is a **node** tagged with `tier`, `entity_id`, `permitted_roles[]`. Relations between nodes are explicit edges (`articulates`, `derives_from`, `specced_in`, etc.). Any Inkwell host queries `sos-docs` with the viewer's token, gets back exactly the subgraph that viewer is authorized to see, and renders it. One canonical source; N authorized slices.

---

## 2. Doc-Node Schema

Mirrors §1C engram fields exactly — same tier semantics, same gating primitives, same `permitted_roles` GIN index pattern. This is deliberate: kernel already knows how to enforce the 5-tier Hive; `sos-docs` reuses it verbatim rather than re-implementing.

```sql
CREATE TABLE docs_nodes (
  id               TEXT PRIMARY KEY,          -- slug, e.g. 'sos/stack-sections/12-sos-docs'
  tier             TEXT NOT NULL DEFAULT 'project',  -- public | squad | project | role | entity | private
  entity_id        TEXT,                      -- customer_id / workspace_id when tier='entity'
  permitted_roles  TEXT[],                    -- when tier='role'
  project_id       TEXT,
  squad_id         TEXT,
  author_id        TEXT NOT NULL,
  title            TEXT NOT NULL,
  summary          TEXT,
  body             TEXT NOT NULL,             -- markdown
  body_format      TEXT NOT NULL DEFAULT 'markdown',
  frontmatter      JSONB,                     -- Astro-compatible metadata
  version          TEXT NOT NULL DEFAULT '1.0',
  supersedes       TEXT REFERENCES docs_nodes(id),
  created_at       TIMESTAMPTZ DEFAULT now(),
  updated_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_docs_nodes_tier            ON docs_nodes (tier);
CREATE INDEX idx_docs_nodes_entity_id       ON docs_nodes (entity_id);
CREATE INDEX idx_docs_nodes_permitted_roles ON docs_nodes USING GIN (permitted_roles);
CREATE INDEX idx_docs_nodes_project         ON docs_nodes (project_id);

-- Auto-update updated_at on row mutation (per Athena G2 review)
CREATE OR REPLACE FUNCTION docs_nodes_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_docs_nodes_updated_at
  BEFORE UPDATE ON docs_nodes
  FOR EACH ROW EXECUTE FUNCTION docs_nodes_touch_updated_at();
```

---

## 3. Relations

Edges are a separate table so a node can participate in many relationships without rewriting its body. Edge types are a fixed enum at v1.0; extending requires a migration (intentional — prevents drift).

```sql
CREATE TABLE docs_relations (
  id          BIGSERIAL PRIMARY KEY,
  from_node   TEXT NOT NULL REFERENCES docs_nodes(id),
  to_node     TEXT NOT NULL REFERENCES docs_nodes(id),
  edge_type   TEXT NOT NULL,  -- enum below
  weight      NUMERIC,
  created_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE (from_node, to_node, edge_type)
);

CREATE INDEX idx_docs_relations_from ON docs_relations(from_node, edge_type);
CREATE INDEX idx_docs_relations_to   ON docs_relations(to_node, edge_type);
```

**Edge vocabulary (v1.0):**

| edge_type | meaning |
|---|---|
| `articulates` | node states a principle that `to_node` concretely enacts |
| `derives_from` | node's content is derived or quoted from `to_node` |
| `sequences` | ordered chain (e.g. step N → step N+1 of a procedure) |
| `specced_in` | an implementation node references its spec node |
| `supersedes` | node replaces `to_node` (also mirrored in `docs_nodes.supersedes` for fast lookup) |
| `exemplifies` | node is a concrete example of the abstract `to_node` |

---

## 4. API Surface

HTTP on the `sos-docs` service port; MCP-mirrored for agent callers. Every read path enforces the 1C tier filter via the caller's token claims (`roles`, `entity_id`, `squads`, `project`).

| Method | Path | Scope | Purpose |
|---|---|---|---|
| `GET` | `/docs/nodes` | any valid token | List nodes visible to caller; filters: `tier`, `project_id`, `entity_id`, `tag` |
| `GET` | `/docs/nodes/:id` | any valid token | Single node (404 if invisible under tier gate — never 403, to avoid leaking existence) |
| `GET` | `/docs/relations/:id` | any valid token | All edges where node is source or target, filtered by tier-visibility of the other endpoint |
| `POST` | `/docs/nodes` | coordinator / author | Create node (auth-gated; writes are rarer than reads) |
| `PATCH` | `/docs/nodes/:id/tier` | coordinator | Promote/demote tier (same semantics as Mirror 1C) |
| `POST` | `/docs/nodes/:id/relations` | coordinator | Add edge |

MCP mirror: `mcp__sos_docs__list`, `mcp__sos_docs__get`, `mcp__sos_docs__relations`, `mcp__sos_docs__create` (write path gated on coordinator/author roles).

---

## 5. Plugin Contract Integration

`sos-docs` registers via §6's `plugin.yaml`:

```yaml
name: sos-docs
version: 1.0.0
display_name: SOS Docs Service
kind: service
depends_on:
  - kernel-role-registry  # §1A
  - mirror                 # tier vocabulary mirrors 1C
routes:
  - /docs/*
mcp_tools:
  - sos_docs__list
  - sos_docs__get
  - sos_docs__relations
  - sos_docs__create
events_emit:
  - sos:event:docs:node_created
  - sos:event:docs:node_promoted
  - sos:event:docs:relation_added
```

Consuming plugins (Inkwell hosts) declare `consumes: [sos-docs]` in their own `plugin.yaml`. Kernel refuses registration if `sos-docs` is not installed.

---

## 6. Host Consumption Pattern

Inkwell instances (mumega.com, Digid Internal, Kaveh's customer fork, future tenants) are **thin renderers** over `sos-docs`. Pattern — aligned with Burst 2A Inkwell-Hive IH-1/IH-2:

1. Viewer authenticates to Inkwell; Inkwell already holds a session with their roles/entity_id/project claims.
2. Page render calls `GET /docs/nodes?tier=public&project_id=mumega` (plus any route-derived filters) with the viewer's token forwarded.
3. `sos-docs` applies the 1C recall SQL filter — never the Inkwell host — so a misconfigured host cannot leak across tiers.
4. Returned nodes render as Astro server components; relations become in-page navigation.
5. Static generation is allowed **only for `tier='public'` nodes**. Everything else is server-rendered per-request under the viewer's token.

Five viewer tokens over the same node set produce five distinct page trees. No host code knows about tiers; tiers are enforced at the service.

---

## 7. Migration Strategy — Tonight's 9 Silos → Nodes

Reference ingestion script lives in Burst 2A.5 (Kasra's task). Strategy:

1. **Classify** each file in each silo by inferred tier (file path heuristic: `docs/superpowers/plans/` → `project`; `agents/*/briefs/` → `role`; `content/en/blog/` → `public`; per-customer dirs → `entity`).
2. **Emit** each file as a `docs_nodes` row with `id` = stable slug, `tier` = inferred (coordinator reviews the emitted manifest before commit), `body` = original markdown.
3. **Link** `supersedes` edges where a newer file replaces an older one (git history is the authority).
4. **Emit** `specced_in`, `articulates`, `sequences` edges detected from explicit markdown links between files.
5. **Freeze** the filesystem silos to read-only snapshots under `docs/_archive/2026-04-24-silos/` for audit; canonical reads now go through `sos-docs`.

Ingest is idempotent: re-running against the same git ref yields identical `docs_nodes` rows (id is deterministic from path).

---

## 8. Tier Enforcement

**Never re-implemented here.** `sos-docs` imports the kernel's tier-filter function from `sos.contracts.tiers` (Python module) — explicit `from sos.contracts.tiers import apply_tier_filter`, **not** a copy-paste of §1C's SQL snippet. The contract module is the kernel's public surface for tier semantics; `sos-docs` is one consumer alongside Mirror, Squad, and Dispatcher. Any future change to tier semantics happens in `sos/contracts/tiers.py` and propagates automatically across all consumers; `sos-docs` does not own tier logic, only node storage and edge semantics.

The service's own `workspace_id` outer fence wraps the contract-supplied filter, ensuring per-workspace isolation in addition to per-tier visibility.

---

## 9. Test Plan

| Test | Pass Condition |
|---|---|
| Five tiers, five slices | Same node set queried with `observer`, `worker`, `knight`, `coordinator`, `principal` tokens returns five distinct visible-node counts; no token sees a node outside its tier gate |
| Existence-hiding | `GET /docs/nodes/:id` for an invisible node returns 404, not 403 |
| Relation gating | `GET /docs/relations/:id` hides edges to invisible nodes from caller's view |
| Supersedes chain | Node A supersedes B; a `public`-tier viewer who can see A but not B gets A only, no dangling edge |
| Host forwarding | Inkwell host with misconfigured role filter still cannot leak — service enforces, not host |
| Ingest idempotency | Re-run migration script over unchanged git ref; zero row churn |
| Plugin registration | Inkwell host declaring `consumes: [sos-docs]` fails to register when service is absent |
| PATCH tier | Coordinator promotes `project` → `squad`; squad-role token now sees node |

---

## 10. Phase + Owner

- **Phase:** 4 (substrate-adjacent service module)
- **Burst:** 2A
- **Owner:** Kasra (schema, service, ingest script, MCP tools)
- **Gate:** Athena (migration manifest review before silo freeze; tier-classification spot-check)
- **Consumers (downstream):** Burst 2A Inkwell-Hive IH-1/IH-2 (mumega.com, Digid Internal)

Coordination: Loom holds the spec; Kasra implements; Athena gates the migration commit and the first production slice render.

---

## Versioning

- **v1.0 (2026-04-24):** initial spec. Doc-node schema mirrors §1C engram fields. Six-edge vocabulary (articulates, derives_from, sequences, specced_in, supersedes, exemplifies). Four-route API. Migration path from 9 file-tree silos. Tier enforcement delegated to §1 substrate — never re-implemented.
