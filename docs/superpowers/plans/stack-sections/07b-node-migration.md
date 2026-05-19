# Phase 3 Node Primitive Migration & Wiring Spec

**Document:** Non-disruptive unification of `tasks`, `projects`, `opportunities` into a composite `nodes` model via SQL view + specialized-table routing.

---

## Section 1: Strategy — View-First + Thin Identity Table

The zero-disruption approach avoids collapsing existing tables into a monolithic `nodes` schema. But a pure UNION view has a structural flaw: **a view cannot be the target of a foreign-key constraint.** If a task's `parent_id` points to a project, opportunity, or goal interchangeably, referential integrity on the parent chain cannot be enforced through the view. Left implicit, "view-first" silently degrades to "FK-less by accident" — orphan risk everywhere.

The fix: a **thin `nodes_identity` table** that all four sources FK into.

```sql
CREATE TABLE nodes_identity (
  id           SERIAL PRIMARY KEY,
  node_type    node_type NOT NULL,
  workspace_id INTEGER NOT NULL,
  created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX nodes_identity_workspace ON nodes_identity(workspace_id);
```

Every specialized row (task/project/opportunity) gains an `identity_id INTEGER REFERENCES nodes_identity(id)` column. The `nodes` physical table gets one too. All `parent_id` references point to `nodes_identity(id)` — integer FK, referentially sound, works across all four sources.

Then:

1. **`nodes_identity` is the referential anchor** — 3 columns, immutable once created, effectively a polymorphic FK target
2. **`nodes` as a new physical table** — for goals, milestones, outcomes, patterns; joins `nodes_identity` on `identity_id`
3. **Existing tables gain `identity_id`** — tasks/projects/opportunities backfill on migration
4. **`nodes_view` unions all four sources on top** — reads compose; writes still route to specialized tables
5. **Fractal navigation APIs query the view; parent_id resolution uses `nodes_identity`**
6. **APIs route writes** — a mapping layer interprets composite IDs and routes INSERT/UPDATE/DELETE to the correct physical table

**Result:** Nothing existing breaks. Parent-chain referential integrity is real. Specialized tables remain maintainable. When/if full unification becomes necessary, `nodes_identity` is the bridge that already exists.

> **Gate feedback applied 2026-04-24 (Athena):** The identity-table pattern replaces the earlier loose-FK design. Without it, "view-first" was an FK-less accident waiting to happen. Named explicitly here so no future reader thinks the parent chain is loose.
>
> **Also (critical):** bus events MUST emit from specialized write paths, not from the view. A view cannot fire events. When a task is inserted into `tasks`, the task handler emits `node:created` with `composite_id = 'task:' || task.id`. When an opportunity updates, the opportunity handler emits `node:updated`. If any specialized write path fails to emit, the node event layer fractures — that type of node stops showing up in Mirror subscriptions, business-graph ingest, and partner-digest rollups. Implementation checklist: every specialized table's insert/update/delete hook must include a `node:*` emission alongside its existing legacy event. See Section 6 for payload shape.

---

## Section 2: The UNION View

```sql
CREATE VIEW nodes_view AS
SELECT
  'task:' || t.id AS composite_id,
  t.id AS specialized_id,
  'task' AS type,
  t.parent_id,
  t.title,
  t.description,
  t.status,
  t.assignee AS owner_id,
  t.workspace_id,
  t.project_id AS entity_id,
  NULL::text AS visibility_tier,
  t.coherence_score,
  NULL::numeric AS target_value,
  NULL::numeric AS current_value,
  NULL::date AS target_date,
  NULL::numeric AS estimated_value,
  t.pay_amount,
  t.metadata_json,
  NULL::integer AS template_id,
  t.created_at,
  t.updated_at,
  NULL::timestamp AS archived_at
FROM tasks t

UNION ALL

SELECT
  'project:' || p.id,
  p.id,
  'project',
  p.parent_id,
  p.name,
  p.description,
  p.status,
  p.owner_id,
  p.workspace_id,
  p.id,
  p.visibility_tier,
  NULL,
  NULL,
  NULL,
  NULL,
  p.estimated_value,
  NULL,
  p.metadata_json,
  NULL,
  p.created_at,
  p.updated_at,
  p.archived_at
FROM projects p

UNION ALL

SELECT
  'opportunity:' || o.id,
  o.id,
  'opportunity',
  o.parent_id,
  o.title,
  o.description,
  o.status,
  o.owner_id,
  o.workspace_id,
  o.customer_id,
  o.visibility_tier,
  NULL,
  NULL,
  NULL,
  o.target_date,
  o.estimated_value,
  NULL,
  o.metadata_json,
  NULL,
  o.created_at,
  o.updated_at,
  o.archived_at
FROM opportunities o

UNION ALL

SELECT
  'node:' || n.id,
  n.id,
  n.type,
  n.parent_id,
  n.title,
  n.description,
  n.status,
  n.owner_id,
  n.workspace_id,
  n.entity_id,
  n.visibility_tier,
  n.coherence_score,
  n.target_value,
  n.current_value,
  n.target_date,
  n.estimated_value,
  n.pay_amount,
  n.metadata_json,
  n.template_id,
  n.created_at,
  n.updated_at,
  n.archived_at
FROM nodes n;
```

**Composite ID scheme:** Each row gets a type-prefixed ID (`task:123`, `project:456`, `opportunity:789`, `node:101`) to avoid ID collisions across tables. The API mapping layer translates composite_id ↔ (type, specialized_id) pairs.

---

## Section 3: Writes — Routing Layer

All node writes are routed by type:

| Type | Write Target | Composite ID Prefix |
|------|--------------|-------------------|
| task | `tasks` | `task:` |
| project | `projects` | `project:` |
| opportunity | `opportunities` | `opportunity:` |
| goal, milestone, outcome, pattern, other | `nodes` | `node:` |

**Example flow:**

```python
def create_node(workspace_id, type_, title, description, parent_id=None):
    if type_ in ('task', 'project', 'opportunity'):
        # Route to specialized table
        row = insert_into_specialized_table(type_, title, description, parent_id)
        return f'{type_}:{row.id}'
    else:
        # Route to nodes table
        row = nodes.insert({
            'type': type_,
            'title': title,
            'description': description,
            'parent_id': parent_id,
            'workspace_id': workspace_id,
            'status': 'draft',
        })
        return f'node:{row.id}'

def update_node(composite_id, updates):
    type_, specialized_id = parse_composite_id(composite_id)
    table = get_table_for_type(type_)
    table.update(specialized_id, updates)

def promote_node(composite_id, target_type):
    """Example: promote a task to a project."""
    source_type, source_id = parse_composite_id(composite_id)
    source_row = get_from_table(source_type, source_id)
    
    # Insert into target table
    target_row = insert_into_specialized_table(target_type, source_row.title, source_row.description)
    new_composite_id = f'{target_type}:{target_row.id}'
    
    # Update all children's parent_id
    children = nodes_view.filter(parent_id=source_id)
    for child in children:
        child_type, child_id = parse_composite_id(child.composite_id)
        update_node(child.composite_id, {'parent_id': target_row.id})
    
    # Update node_edges
    for edge in node_edges.filter(source_id=composite_id):
        edge.update({'source_id': new_composite_id})
    
    # Delete from source table
    get_table_for_type(source_type).delete(source_id)
    
    return new_composite_id
```

---

## Section 4: Backfill — Adding Fields to Existing Tables

Existing tables need (a) an `identity_id` FK anchoring them into `nodes_identity`, (b) a `parent_identity_id` FK referencing another node's identity (cross-type parenting), and (c) the new metadata columns. All additions are backward-compatible:

```sql
-- tasks
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS identity_id INTEGER REFERENCES nodes_identity(id);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS parent_identity_id INTEGER REFERENCES nodes_identity(id) ON DELETE SET NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS visibility_tier TEXT DEFAULT 'project';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS metadata_json JSONB DEFAULT '{}';
CREATE UNIQUE INDEX IF NOT EXISTS tasks_identity_id_uq ON tasks(identity_id);
CREATE INDEX IF NOT EXISTS tasks_parent_identity_id ON tasks(parent_identity_id);

-- projects
ALTER TABLE projects ADD COLUMN IF NOT EXISTS identity_id INTEGER REFERENCES nodes_identity(id);
ALTER TABLE projects ADD COLUMN IF NOT EXISTS parent_identity_id INTEGER REFERENCES nodes_identity(id) ON DELETE SET NULL;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS visibility_tier TEXT DEFAULT 'project';
CREATE UNIQUE INDEX IF NOT EXISTS projects_identity_id_uq ON projects(identity_id);
CREATE INDEX IF NOT EXISTS projects_parent_identity_id ON projects(parent_identity_id);

-- opportunities
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS identity_id INTEGER REFERENCES nodes_identity(id);
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS parent_identity_id INTEGER REFERENCES nodes_identity(id) ON DELETE SET NULL;
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS visibility_tier TEXT DEFAULT 'project';
CREATE UNIQUE INDEX IF NOT EXISTS opportunities_identity_id_uq ON opportunities(identity_id);
CREATE INDEX IF NOT EXISTS opportunities_parent_identity_id ON opportunities(parent_identity_id);

-- nodes physical table also joins through the identity anchor
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS identity_id INTEGER REFERENCES nodes_identity(id);
ALTER TABLE nodes DROP CONSTRAINT IF EXISTS nodes_parent_id_fkey;
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS parent_identity_id INTEGER REFERENCES nodes_identity(id) ON DELETE SET NULL;
-- Legacy parent_id column on nodes becomes unused; drop in a follow-up migration after backfill completes
```

**Backfill (data migration, run after schema change):** for every existing row in `tasks`/`projects`/`opportunities`/`nodes`, insert a `nodes_identity` row matching `(workspace_id, node_type)` and set the specialized row's `identity_id` to the new identity's PK. `parent_identity_id` starts NULL for everything; cross-type parent chains are established only when APIs explicitly set them going forward, or via a one-shot ETL that resolves old same-type parent references into identity references.

> **Gate feedback applied 2026-04-24 (Athena):** Section 1's narrative said "all parent_id references point to nodes_identity(id)" but the original Section 4 DDL used self-referential FKs (task→task, project→project, opportunity→nodes). That broke cross-type parent chains entirely and the core fractal claim. Fixed: added `identity_id` + `parent_identity_id` on every table; removed self-referential FKs. Cross-type parenting (task → goal, opportunity → project) now works because every parent reference targets the identity anchor.

**Note:** `coherence_score`, `pay_amount`, `estimated_value`, `target_date`, `workspace_id`, `owner_id` already exist in these tables from Phase 1. No data loss; existing rows get NULL/default for new columns.

---

## Section 5: Engrams + Inkwell FK Additions

Mirror and Inkwell need optional links to nodes:

```sql
-- Mirror: engrams table
ALTER TABLE engrams ADD COLUMN IF NOT EXISTS node_id TEXT;
CREATE INDEX IF NOT EXISTS engrams_node_id ON engrams(node_id);
COMMENT ON COLUMN engrams.node_id IS 'Composite node ID (task:123, project:456, etc.) linking this engram to a node.';

-- Inkwell: pages (or whichever Astro store)
ALTER TABLE inkwell_pages ADD COLUMN IF NOT EXISTS node_id TEXT;
CREATE INDEX IF NOT EXISTS inkwell_pages_node_id ON inkwell_pages(node_id);
COMMENT ON COLUMN inkwell_pages.node_id IS 'Composite node ID for pages generated from or linked to a node.';
```

Both optional, nullable. Existing engrams and pages continue to work without a node_id.

---

## Section 6: Bus Events — Include Composite ID

When existing types fire CRUD events, include the composite_id:

```python
# tasks.create event
emit_event('sos:event:squad:task.created', {
    'id': task.id,
    'node_id': f'task:{task.id}',  # new field
    'workspace_id': task.workspace_id,
    'title': task.title,
    # ... other fields
})

# projects.update event
emit_event('sos:event:squad:project.updated', {
    'id': project.id,
    'node_id': f'project:{project.id}',  # new field
    # ... other fields
})
```

Legacy subscribers that ignore `node_id` continue to work. New subscribers use it for fractal operations.

---

## Section 7: Migration Sequence

Migrations land in this order:

| Order | Service | Migration | Purpose |
|-------|---------|-----------|---------|
| 1 | Squad Service | `0017_nodes_identity` | Create `nodes_identity` table (must land BEFORE any FK that references it) |
| 2 | Squad Service | `0018_nodes_table` | Create `nodes`, `node_edges`, `node_templates`, `node_template_instances` — all parent references FK into `nodes_identity(id)` |
| 3 | Squad Service | `0019_extend_existing_tables` | Add `identity_id`, `parent_identity_id`, `visibility_tier`, `metadata_json` to tasks/projects/opportunities/nodes; backfill identity rows |
| 4 | Squad Service | `0020_nodes_view` | Create `nodes_view` UNION across tasks/projects/opportunities/nodes using `identity_id` + `parent_identity_id`. Must run after 0019 because the view selects the newly-added columns. |
| 3 | Squad Service | `0019_nodes_view` | Create UNION view (`nodes_view`) |
| 4 | Mirror | `0016_engram_node_fk` | Add node_id to engrams table |
| 5 | Inkwell | `00NN_page_node_fk` | Add node_id to Inkwell pages table |
| 6 | API Layer | Deploy | Fractal navigation endpoints + routing layer |
| 7 | Squad Service | Seed script | Instantiate 5 templates: customer_knight_onboarding, sred_case, iso_42001_audit, partner_onboarding, customer_to_digid_upsell |

All migrations are idempotent (IF NOT EXISTS). Zero downtime deployment.

---

## Section 8: Test Checklist

- [ ] Creating a task via `POST /tasks` still works, returns `task_id` as before
- [ ] Querying `GET /nodes/task:123` returns the task via the view
- [ ] `GET /nodes/task:123/children` returns subtasks once subtask type is introduced
- [ ] Creating a goal via `POST /nodes` lands in the `nodes` physical table
- [ ] Promoting a task to a project moves row between tables, preserves children, updates node_edges
- [ ] Engrams without node_id still recall correctly (no breakage for floating memory)
- [ ] Bus subscribers missing the `node_id` field continue to work
- [ ] Template instantiation produces correct subtree in specialized tables + `nodes` as appropriate
- [ ] Composite IDs (`task:123`) are stable across view queries
- [ ] parent_id relationships work cross-table (e.g., task → project parent)

---

## Section 9: Rollback Path

If the UNION view causes performance issues under load:

1. Disable fractal navigation API endpoints (feature flag)
2. `DROP VIEW nodes_view`
3. Specialized tables continue to work standalone
4. New node types (goals, outcomes, etc.) require separate, non-unified APIs
5. No data loss; all rows remain in their physical tables

Rollback is safe and can be done in < 1 minute.

---

## Section 10: Future — Optional Full Unification

If specialized tables become a maintenance burden, a future phase can collapse them into true `nodes` rows. Criteria to trigger:

- \> 3 bugs per quarter traced to specialization drift
- API complexity dominated by routing logic
- Team consensus that duplicated validation is a liability

**Do not preemptively unify.** The view + routing layer is the durable design. Specialization remains valuable for:
- Backward compatibility
- Type-specific constraints (e.g., opportunities must have a target_date)
- Schema clarity for domain experts
- Incremental adoption of the node model

---

**Status:** Ready for implementation. No architectural blockers.
