# Section 7 — Fractal Node Primitive

**Author:** Loom
**Date:** 2026-04-24
**Gate:** Athena
**Owner:** Kasra
**Phase:** 3 — lands after 4A (partner workspace) ships
**Depends on:** 01-substrate (role registry, visibility tiers), 03-structured-records (contacts/partners/opportunities shape)
**Migration numbers:** 0020–0022 (sequenced after Phase 2 migrations)

---

## 1 — Background & Mandate

Every project management system eventually fragments: tasks live in one table, projects in another, goals in a spreadsheet, and opportunities in a CRM. The fractal node is the answer to that fragmentation. One recursive type replaces them all — not by collapsing their semantics but by composing them under a shared primitive. A goal can parent a project. That project can parent tasks. Each task can spawn subtasks. Every level uses identical traversal APIs, identical permission gates, identical edge semantics. The shape does not change — the meaning of each instance is determined entirely by its `type` field and what sits above and below it in the tree.

This matters in practice because scope changes. A task promoted to a project is not a migration event — it is a type change on a single row. The entire subtree follows. An opportunity that spawned a project no longer requires a foreign-key dance across two tables; the relationship is a tree edge. Goals aggregate upward from whatever children they hold — tasks, opportunities, sub-goals — using typed rollup functions that understand each child's contribution. Workspace isolation, entity scoping, and visibility tiers all compose orthogonally on top: a node's access semantics are determined by its `workspace_id`, `entity_id`, and `visibility_tier`, the same vocabulary established in Section 01 and 03.

Phase 3 lands after the 4A partner workspace is live, which means the role registry is fully operational, Inkwell RBAC is in production, and we have real partner workspaces with real entity isolation to test against. The node primitive does not replace the existing `tasks`, `projects`, and `opportunities` tables immediately — migration spec 07b covers the coexistence strategy and eventual cutover. This spec focuses entirely on the new primitive and its APIs.

---

## 2 — Node Schema

### Enum and Core Table

```sql
CREATE TYPE node_type AS ENUM (
  'goal', 'project', 'opportunity', 'task', 'subtask',
  'milestone', 'outcome', 'pattern'
);

CREATE TABLE node_templates (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL,
  node_type   node_type NOT NULL,
  schema_json JSONB NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- nodes_identity is the referential anchor for cross-type parent chains and edges.
-- See 07b Section 1 for the full migration rationale.
CREATE TABLE nodes_identity (
  id           SERIAL PRIMARY KEY,
  node_type    node_type NOT NULL,
  workspace_id INTEGER NOT NULL,
  created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX nodes_identity_workspace ON nodes_identity(workspace_id);

CREATE TABLE nodes (
  id                 SERIAL PRIMARY KEY,
  identity_id        INTEGER NOT NULL REFERENCES nodes_identity(id),
  type               node_type NOT NULL,
  parent_identity_id INTEGER REFERENCES nodes_identity(id) ON DELETE SET NULL,
  title              TEXT NOT NULL,
  description        TEXT,
  status             TEXT,
  owner_id           INTEGER REFERENCES user_accounts(id),
  workspace_id       INTEGER NOT NULL,
  entity_id          TEXT,
  visibility_tier    TEXT,                  -- matches Inkwell hive tier vocabulary: public/squad/project/role/entity/private
  coherence_score    NUMERIC,
  target_value       NUMERIC,               -- for goals: numeric target (e.g. $1M ARR, 100 customers)
  current_value      NUMERIC,               -- rolls up from children via rollup function
  target_date        DATE,
  estimated_value    NUMERIC,               -- for opportunities: deal size
  pay_amount         NUMERIC,               -- for tasks: bounty / compensation
  metadata_json      JSONB,
  template_id        INTEGER REFERENCES node_templates(id),
  created_at         TIMESTAMPTZ DEFAULT now(),
  updated_at         TIMESTAMPTZ DEFAULT now(),
  archived_at        TIMESTAMPTZ
);

CREATE UNIQUE INDEX nodes_identity_id_uq ON nodes(identity_id);
CREATE INDEX nodes_parent_identity_id ON nodes(parent_identity_id);
CREATE INDEX nodes_workspace_entity   ON nodes(workspace_id, entity_id);
CREATE INDEX nodes_type_status        ON nodes(type, status);

CREATE TABLE node_edges (
  source_identity_id INTEGER NOT NULL REFERENCES nodes_identity(id),
  target_identity_id INTEGER NOT NULL REFERENCES nodes_identity(id),
  edge_type          TEXT NOT NULL,  -- blocks, references, derived_from, instantiated_from, supports, duplicates
  created_at         TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (source_identity_id, target_identity_id, edge_type)
);
```

> **Gate feedback applied 2026-04-24 (Athena):** `duplicates` edge type added for deduplication graphs (GHL imports, merged contacts). Cheap now, painful to retrofit later. See Section 5.
>
> **Gate feedback applied 2026-04-24 (Athena, second pass):** The schema now uses `nodes_identity` as the referential anchor. `nodes.parent_identity_id` and `node_edges.source_identity_id / target_identity_id` all FK into `nodes_identity(id)` — not `nodes(id)`. This lets cross-table edges work (a task → goal blocks edge, an opportunity → project parent chain). The earlier self-referential FK would have limited edges to same-type pairs and broken the core fractal claim. `nodes_identity` must be created BEFORE `nodes`; see 07b Section 7 migration sequence.

### Status Vocabulary Per Type

| Type | Valid statuses |
|------|---------------|
| `goal` | `on_track`, `at_risk`, `blocked`, `complete` |
| `project` | `planning`, `active`, `on_hold`, `complete`, `cancelled` |
| `opportunity` | `prospect`, `active`, `won`, `lost`, `on_hold` |
| `task` | `queued`, `claimed`, `in_progress`, `blocked`, `complete` |
| `subtask` | `queued`, `claimed`, `in_progress`, `blocked`, `complete` |
| `milestone` | `pending`, `at_risk`, `achieved`, `missed` |
| `outcome` | `projected`, `confirmed`, `failed` |
| `pattern` | `draft`, `validated`, `deprecated` |

### Status Validation Trigger

```sql
CREATE OR REPLACE FUNCTION validate_node_status()
RETURNS TRIGGER AS $$
DECLARE
  valid_statuses TEXT[];
BEGIN
  valid_statuses := CASE NEW.type
    WHEN 'goal'        THEN ARRAY['on_track','at_risk','blocked','complete']
    WHEN 'project'     THEN ARRAY['planning','active','on_hold','complete','cancelled']
    WHEN 'opportunity' THEN ARRAY['prospect','active','won','lost','on_hold']
    WHEN 'task'        THEN ARRAY['queued','claimed','in_progress','blocked','complete']
    WHEN 'subtask'     THEN ARRAY['queued','claimed','in_progress','blocked','complete']
    WHEN 'milestone'   THEN ARRAY['pending','at_risk','achieved','missed']
    WHEN 'outcome'     THEN ARRAY['projected','confirmed','failed']
    WHEN 'pattern'     THEN ARRAY['draft','validated','deprecated']
  END;
  IF NEW.status IS NOT NULL AND NOT (NEW.status = ANY(valid_statuses)) THEN
    RAISE EXCEPTION 'Invalid status % for node type %', NEW.status, NEW.type;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER check_node_status
  BEFORE INSERT OR UPDATE ON nodes
  FOR EACH ROW EXECUTE FUNCTION validate_node_status();
```

### Cycle Detection Trigger

```sql
CREATE OR REPLACE FUNCTION check_node_cycle()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.parent_id IS NULL THEN
    RETURN NEW;
  END IF;
  -- Walk ancestors; if we encounter NEW.id, it's a cycle
  IF EXISTS (
    WITH RECURSIVE ancestors AS (
      SELECT parent_id FROM nodes WHERE id = NEW.parent_id
      UNION ALL
      SELECT n.parent_id FROM nodes n JOIN ancestors a ON n.id = a.parent_id
    )
    SELECT 1 FROM ancestors WHERE parent_id = NEW.id
  ) THEN
    RAISE EXCEPTION 'Cycle detected: node % cannot be parented to %', NEW.id, NEW.parent_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER prevent_node_cycle
  BEFORE INSERT OR UPDATE ON nodes
  FOR EACH ROW EXECUTE FUNCTION check_node_cycle();
```

---

## 3 — Fractal Navigation APIs

All endpoints enforce the requester's `workspace_id` + `entity_id` + `visibility_tier` gates derived from their role token (same middleware as Section 01 routes). A node outside the requester's scope returns as if it doesn't exist — no 403, no information leak.

### `GET /nodes/{id}/children`

```
Query: type=goal|project|... (optional), status=active|... (optional), limit=N (default 50)
Response: { nodes: Node[], total: number, cursor: string | null }
```

Returns direct children of `{id}` matching the optional filters. Pagination via cursor.

### `GET /nodes/{id}/ancestors`

```
Response: { chain: Node[] }  -- ordered root-first
```

Walks `parent_id` chain from `{id}` to the root. Terminates at the first node outside the requester's scope (returns chain up to that boundary, not an error).

### `GET /nodes/{id}/descendants`

```
Query: type=task|subtask|... (optional), depth=N (default unbounded, max 10)
Response: { nodes: Node[], depth_reached: number }
```

Full subtree rooted at `{id}`. Uses recursive CTE. `depth` caps recursion and is enforced server-side regardless of query param.

### `GET /nodes/{id}/siblings`

```
Response: { nodes: Node[] }
```

Nodes sharing the same `parent_id`. Peer view. Excludes `{id}` itself. Applies same visibility gates.

### `GET /nodes/{id}/rollup`

```
Response: {
  node_id: number,
  current_value: number | null,
  completion_pct: number | null,
  child_count: number,
  breakdown: { type: node_type, count: number, contribution: number }[]
}
```

Computed aggregate. See Section 4 for aggregator semantics.

### `POST /nodes/{id}/promote`

```
Body: { to: node_type }
Response: { node: Node }
```

Changes `type` upward (task → project, project → goal, etc.). Validates the target type's status vocabulary and remaps `status` to the closest equivalent. Requires `node_promote` permission. Rejects if the promotion would create a type inconsistency with the current parent (e.g. promoting a task whose parent is another task to `goal` — the parent would need to be promoted first or the node reparented).

### `POST /nodes/{id}/move`

```
Body: { parent_id: number }
Response: { node: Node }
```

Reparents `{id}` to a new parent. Preserves entire descendant subtree. Re-checks `node_move` permission against both old and new parent's workspace/entity scope. Cycle detection trigger fires at DB level as a backstop.

---

## 4 — Rollup Semantics

The architectural challenge: a goal's `current_value` must aggregate meaningfully when children are heterogeneous — some are sub-goals with numeric targets, some are tasks with completion states, some are opportunities with deal values and probabilities.

The rollup function dispatches on parent type and groups children by type before aggregating:

```python
def rollup(node_id: int) -> RollupResult:
    node = get_node(node_id)
    children = get_children(node_id, include_archived=False)

    if not children:
        # Leaf node: return self-reported value or status-implied completion
        return RollupResult(
            current_value=node.current_value,
            completion_pct=1.0 if node.status == 'complete' else 0.0
        )

    groups = group_by_type(children)
    aggregator = TYPED_AGGREGATORS[node.type]
    return aggregator(node, groups)
```

**Aggregators per parent type:**

| Parent | Child types | Aggregation rule |
|--------|-------------|-----------------|
| `goal` with `goal` children | sub-goals | `sum(child.current_value) / sum(child.target_value)` as pct; `current_value = pct * node.target_value` |
| `goal` with `opportunity` children | opportunities | `sum(won.estimated_value) + sum(active.estimated_value * probability)` → `current_value` |
| `goal` with `task`/`subtask` children | tasks | `count(complete) / count(all)` as completion pct |
| `project` with mixed children | any | weighted combination; weights stored in `metadata_json.rollup_weights`; defaults: tasks 0.6, milestones 0.4 |
| `opportunity` with `task` children | tasks | `completion_pct(tasks) * deal_stage_weight[stage]` where stage weights: prospect=0.1, active=0.5, won=1.0 |
| `milestone` | tasks | `count(complete) / count(all)` |

Rollup is computed on-read in Phase 3 via recursive CTE. Explicit threshold for promotion to write-time materialization: **subtree depth > 3 OR leaf count > 50**. Until one of those thresholds is crossed, do not build write-time rollup triggers — premature optimization for a primitive that does not yet have real data. When a subtree exceeds the threshold, add a trigger on `nodes` INSERT/UPDATE that walks the `parent_id` chain and writes `current_value` on each ancestor; dashboards then read materialized values directly.

> **Gate feedback applied 2026-04-24 (Athena):** explicit threshold (depth>3 OR leaves>50) added so a future coordinator doesn't optimize pre-emptively. The fall-off to write-time is a signal the system gives us, not a target we plan for.

This design introduces intentional coupling: aggregators encode which child types are semantically valid under each parent. A goal parenting a `subtask` would fall through to a sensible default (task-style completion pct), but it signals an abstraction slip. The allowed-children matrix is domain-meaningful, not accidental:

| Parent type | Preferred children |
|-------------|-------------------|
| `goal` | goal, project, opportunity, outcome |
| `project` | task, subtask, milestone, outcome |
| `opportunity` | task, subtask |
| `task` | subtask |
| `milestone` | task, subtask |
| `outcome` | (leaf) |
| `pattern` | (leaf) |

The DB does not enforce this matrix — the rollup simply degrades gracefully. Future validation can add a CHECK or trigger once the matrix stabilizes.

---

## 5 — Edge Types and Usage

`node_edges` supplements the parent-child tree with non-hierarchical relationships. All edges are directional. Edge creation requires `node_write` on both source and target (or `node_promote` for cross-workspace edges if permitted — see Section 8).

| Edge type | Semantics | Enforcement |
|-----------|-----------|-------------|
| `blocks` | Target cannot advance to `in_progress` or `active` until source reaches `complete` | Status transition trigger checks incoming `blocks` edges |
| `references` | Contextual link — no semantic enforcement | Informational only |
| `derived_from` | Target was created because source; provenance chain | Informational; preserved on promote/move |
| `instantiated_from` | Target subtree was produced from a template node | Set automatically by template instantiation (spec 07a) |
| `supports` | Target contributes to source's goal without being a strict child | Used in rollup when `metadata_json.rollup_weights.supports` is set. Direction convention: work item → goal (upward strategic alignment). Do NOT use for goal → objective or parent → child (use `parent_id` for hierarchy, `blocks` for dependency). |
| `duplicates` | Source and target represent the same underlying entity | Deduplication graph without deletion; GHL contact merges, opportunity consolidation. Both nodes remain queryable; traversal dedups downstream |

**Concrete examples:**

A GAF eligibility review task (`id=441`, workspace=GAF) has a `blocks` edge to an AgentLink onboarding task (`id=892`, workspace=AgentLink). Task 892's status transition API checks incoming blocks before allowing `queued → in_progress`; if task 441 is not `complete`, the transition is rejected with `BLOCKED_BY: 441`.

An opportunity `customer_to_digid_upsell` (`id=200`) carries a `derived_from` edge pointing to a completed GAF case node (`id=175`). When an agent traces the opportunity's provenance — "why did this deal originate?" — it walks `derived_from` edges backward to the GAF engagement, giving full context without a JOIN across separate tables.

---

## 6 — Integration with Existing Systems

**Engrams (Mirror):**
Engrams gain an optional `node_id INTEGER REFERENCES nodes(id)` column (nullable). Floating memory — kasra_102's letters, free-form observations, ambient context — remains valid without a node anchor. When an agent completes a task and generates an outcome engram, it attaches `node_id` to tie memory to the work item. Recall queries can filter `node_id IS NOT NULL` to surface memory specifically about structured work.

**Inkwell pages:**
Pages gain an optional `node_id` frontmatter field. A project's documentation page, a goal's narrative, or a pattern's write-up can link back to the node that owns them. The link is one-directional in the DB — pages reference nodes, not vice versa. Reverse lookup is a join: `SELECT * FROM inkwell_pages WHERE node_id = $1`.

**Bus events:**
All node mutation events carry `node_id` in their payload alongside existing `workspace_id` and `entity_id`. Event schema:
```json
{
  "event_type": "node:{created|updated|archived|promoted|moved}",
  "node_id": 441,
  "node_type": "task",
  "workspace_id": "gaf",
  "entity_id": "customer-abc",
  "timestamp": "2026-04-24T18:00:00Z",
  "actor": "agent:kasra"
}
```
Bus topic: `sos:event:squad:nodes:*`. Mirror subscribes for engram-node linkage. Business graph (Section 1E) subscribes to upsert node records. **Crucial:** events emit from the specialized write paths (tasks.create → emits node:created; opportunities.update → emits node:updated), not from the union view. The view is read-only and cannot trigger events.

**Role registry:**
Five node-specific permissions added to the registry:

| Permission | Description |
|-----------|-------------|
| `node_read` | Read node and its children within scope |
| `node_write` | Create and update nodes within scope |
| `node_promote` | Change a node's type (promote/demote) |
| `node_move` | Reparent a node |
| `node_archive` | Soft-delete a node and its subtree |

Seed roles receive defaults: `coordinator` gets all five; `builder` gets `node_read`, `node_write`; `observer` gets `node_read` only; `partner` gets `node_read` scoped to their `entity_id`.

**Existing tables:**
`tasks`, `projects`, and `opportunities` coexist with `nodes` during Phase 3. New work is created as nodes. Existing records remain in their original tables; migration spec 07b defines the coexistence strategy.

> **Gate feedback applied 2026-04-24 (Athena):** The migration strategy uses a thin identity table pattern (see 07b Section 1) rather than loose TEXT foreign keys. A `nodes_identity` table (id, node_type, workspace_id) serves as the referential anchor; specialized tables (`tasks`, `projects`, `opportunities`) gain FKs into it; the union view reads across all specialized tables plus the physical `nodes` table. This keeps parent-chain referential integrity real — not "FK-less by accident." See 07b for the full mechanism.
>
> **Also:** bus events MUST fire from specialized write paths (`tasks`, `projects`, `opportunities` insert/update/delete handlers), not from the union view. A view cannot emit events. If the event layer is implicit, it fractures silently — task-type nodes would stop emitting `node:*` events the moment writes bypass the view. All specialized tables must emit `node:{created|updated|archived|promoted|moved}` with `node_id` in payload when they mutate.

---

## 7 — Test Plan

- **Workspace isolation:** Node created in workspace A is not returned by any navigation endpoint when called with a workspace B token. Subtree traversal stops at workspace boundary rather than leaking.
- **Entity isolation:** Node with `entity_id = 'customer-x'` is invisible to a token with `entity_id = 'customer-y'` in the same workspace.
- **Cycle detection:** `POST /nodes/{id}/move` with `parent_id` that would create a cycle returns 422 with `CYCLE_DETECTED`. Self-parenting (`parent_id = id`) similarly rejected.
- **Status validation:** `PATCH /nodes/{id}` with `status = 'won'` on a `task`-type node returns 422 with `INVALID_STATUS_FOR_TYPE`. Valid status updates succeed.
- **Rollup — pure tasks:** Goal with 4 task children, 2 complete → rollup returns `completion_pct = 0.5`, `current_value = 0.5 * target_value`.
- **Rollup — mixed (project):** Project with 3 tasks and 1 milestone; milestone achieved, 2 tasks complete, 1 queued → weighted rollup matches `metadata_json.rollup_weights` calculation.
- **Rollup — opportunity children:** Goal with 2 opportunities: one `won` ($50K), one `active` ($100K estimated at 0.4 probability) → `current_value = $50K + $40K = $90K`.
- **Promote:** Task promoted to project → `type` changes, `status` remapped from `complete → complete` (passthrough), descendants unmodified. Promote with insufficient role → 403.
- **Move — preserves subtree:** Node with 3 descendants reparented → all 3 descendants accessible via `GET /nodes/{new_parent}/descendants`.
- **Move — role scope:** Move to parent in a different entity scope without `node_move` permission on target → 403.
- **Blocks edge enforcement:** Task with incoming `blocks` edge from an incomplete source → status transition to `in_progress` rejected with `BLOCKED_BY: {source_id}`.
- **Cross-workspace edge:** `blocks` edge from workspace A node to workspace B node requires `node_write` on both sides; single-side token → 403.
- **Template instantiation:** `instantiated_from` edge set automatically by template engine; reflected in `GET /nodes/{id}/children` edge metadata (tested in spec 07a, referenced here).

---

## 8 — Open Questions

1. **`node_id` on engrams: optional or required?** Lean optional. Free-floating memory (ambient observations, letters, research notes) has value without a node anchor. Making it required would force agents to create placeholder nodes just to store memory — the wrong incentive.

2. **Rollup frequency: on-read vs. materialized?** Start on-read — simpler, always accurate, sufficient for Phase 3 scale. Materialize with a trigger (or a scheduled recompute) when dashboards aggregating across 500+ nodes show p95 latency above 200ms. Do not pre-optimize.

3. **Cross-workspace edges: allowed?** If yes, require `node_write` on both sides (enforced at API layer; the DB trigger on `node_edges` cannot check cross-workspace permissions). If no, simpler model — no cross-workspace FK needed and the schema's `workspace_id` fence stays clean. Recommendation: allow with mutual-consent enforcement in Phase 3; revisit if the complexity cost exceeds the value.

4. **Template versioning:** Out of scope for this spec; covered in spec 07a. The `node_templates` table is intentionally minimal here — `schema_json` carries the template definition, and versioning strategy (snapshot vs. append-only) is 07a's domain.

5. **UI surface:** Tree view for hierarchy exploration, kanban rollup for status-by-type boards, both ultimately needed. Defer to the Phase 3 UI track. This spec makes no assumptions about presentation — the API shape supports both patterns equally.
