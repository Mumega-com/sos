# 07a — Node Template System

**Status:** Draft  
**Track:** Stack Section — Nodes  
**Relates to:** `nodes` table, Squad Service, SOS bus

---

## Section 1 — Template Primitive

The `nodes` table supports a recursive hierarchy via `parent_id`. Any subtree in that table — a project with its tasks, milestones, and opportunities — can be captured as a reusable template. Templates allow the same skeleton to be stamped out repeatedly without duplication.

```sql
CREATE TABLE node_templates (
  id            SERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  version       INTEGER NOT NULL DEFAULT 1,
  description   TEXT,
  root_shape    JSONB NOT NULL,   -- serialized subtree with {{placeholders}}
  parameters    JSONB NOT NULL,   -- required variables at instantiation
  created_by    INTEGER REFERENCES user_accounts,
  created_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE(name, version)
);

CREATE TABLE node_template_instances (
  id              SERIAL PRIMARY KEY,
  template_id     INTEGER REFERENCES node_templates NOT NULL,
  root_node_id    INTEGER REFERENCES nodes NOT NULL,
  parameters      JSONB NOT NULL,  -- values at instantiation
  instantiated_by INTEGER REFERENCES user_accounts,
  instantiated_at TIMESTAMPTZ DEFAULT now()
);
```

**`root_shape` format.** Each node spec is a JSON object with these fields:

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | yes | goal, project, opportunity, task, subtask, milestone, outcome, pattern |
| `title` | string | yes | Display title — may contain `{{placeholders}}` |
| `description` | string | no | Longer text — may contain `{{placeholders}}` |
| `status` | string | yes | Initial status: open, todo, pending, etc. |
| `children` | array | no | Nested node specs — same shape, recursive |

**`parameters` format.** An array of parameter descriptors:

```json
[
  { "name": "customer_name", "required": true, "type": "string" },
  { "name": "fiscal_year",   "required": true, "type": "string" },
  { "name": "owner_id",      "required": false, "type": "integer" }
]
```

Placeholders in `root_shape` must match parameter names exactly. Missing required parameters at instantiation time are a hard error.

---

## Section 2 — Instantiation API

### `POST /templates/{template_id}/instantiate`

**Request body:**

```json
{
  "workspace_id": 42,
  "entity_id": 7,
  "parameters": {
    "customer_name": "Acme Corp",
    "fiscal_year": "2025"
  },
  "parent_node_id": 101
}
```

**Processing steps (in order):**

1. **Validate parameters.** Check every `required: true` parameter descriptor is satisfied. Return `400` with a list of missing names if not.
2. **Validate workspace + role.** Confirm the requesting user holds a role in the target workspace at or above the minimum rank for the template. Return `403` if not.
3. **Walk `root_shape` depth-first.** For each node spec, substitute all `{{placeholder}}` tokens with the supplied parameter values. Create the node in the `nodes` table.
4. **Link parent.** Set `parent_id` on each created node to the node created one level up. The root node receives `parent_node_id` from the request (or null if none supplied).
5. **Create `instantiated_from` edge.** Insert a `node_edges` row with type `instantiated_from` connecting the new root node → the `node_templates` row.
6. **Record instance.** Insert a `node_template_instances` row with the template ID, new root node ID, and parameter values used.
7. **Emit bus events.** For each created node, emit `node.created` on the SOS bus including `template_id` and `instance_id` in the payload. Consumers (Squad Service, brain) pick these up normally.
8. **Return** `{ "root_node_id": <id>, "instance_id": <id>, "node_count": <n> }`.

---

## Section 3 — Template Versioning

Templates are **immutable once instantiated.** The `UNIQUE(name, version)` constraint enforces this at the database level. No `UPDATE` is permitted on `root_shape` or `parameters` after any row exists in `node_template_instances` referencing that template.

**Creating a new version:** Insert a new row with `version = N+1`. The previous row is untouched. All existing instances remain attached to version N via their `template_id` foreign key. Querying "which template produced this subtree" is always deterministic.

**Version migration (opt-in, manual):** A coordinator may request a migration preview via `POST /templates/{template_id}/instances/{instance_id}/migrate?target_version=N+1`. This returns a diff object — a list of nodes to add, nodes to remove, and field updates — without committing anything. The coordinator reviews the diff and calls `POST /…/migrate/commit` to apply. Migration is never automatic.

**Rationale.** Silent template updates would make the statement "template X produced this case" unreproducible. If the shape changes out from under a live instance, audit trails break and recovery becomes guesswork. Immutability is cheap; audit loss is not.

> **Gate feedback applied 2026-04-24 (Athena):** The `POST /templates/{template_id}/instances/{instance_id}/migrate` machinery is scoped OUT of Phase 3 v1. Immutable versions ship; auto-migration/preview-diff/commit flow is deferred. Rationale: no real migration patterns exist yet, and building "upgrade instance" before any template has actually changed is speculative. When a template version first needs to propagate to live instances, design the migration flow from that concrete case — not from a hypothetical one. Until then: if you want a new shape, instantiate the new version for new work; old instances stay on their original version.

---

## Section 4 — Five Seed Templates

### 4.1 `customer_knight_onboarding`

**Why it exists.** Every new Mumega customer needs the same sequence: scope confirmation, QNFT minting, Discord provisioning, repo indexing, and a first SR&ED scan. Without a template this work gets rediscovered each time. This template makes "new customer" a single instantiate call.

**Minimum rank to instantiate:** `coordinator` (Hadi or Noor close the customer).

**Edges created automatically:** `entity_of` edge from the root project node → the customer entity record.

**Parameters:**
```json
[
  { "name": "customer_slug",    "required": true,  "type": "string" },
  { "name": "customer_name",    "required": true,  "type": "string" },
  { "name": "customer_domain",  "required": false, "type": "string" },
  { "name": "initial_owner_id", "required": false, "type": "integer" }
]
```

**`root_shape`:**
```json
{
  "type": "project",
  "title": "{{customer_name}}",
  "status": "open",
  "description": "Onboarding project for {{customer_name}} ({{customer_slug}})",
  "children": [
    { "type": "task",        "title": "Intake: confirm scope + consent",    "status": "todo" },
    { "type": "task",        "title": "Mint customer knight",               "status": "todo" },
    { "type": "task",        "title": "Provision Discord channel",          "status": "todo" },
    { "type": "task",        "title": "Index customer repo (if applicable)","status": "todo" },
    { "type": "opportunity", "title": "First SR&ED case",                   "status": "open" },
    { "type": "milestone",   "title": "Knight operational",                 "status": "pending" }
  ]
}
```

---

### 4.2 `sred_case`

**Why it exists.** SR&ED preparation follows a fixed sequence mandated by CRA rules. Any deviation or missing step creates filing risk. This template encodes the full sequence so each case is consistent and auditable.

**Minimum rank to instantiate:** `specialist` (Digid practitioner or delegated agent).

**Edges created automatically:** `case_for` edge from the root opportunity → the customer project node.

**Parameters:**
```json
[
  { "name": "customer_project_id",  "required": true, "type": "integer" },
  { "name": "fiscal_year",          "required": true, "type": "string" },
  { "name": "estimated_recovery",   "required": false, "type": "string" }
]
```

**`root_shape`:**
```json
{
  "type": "opportunity",
  "title": "SR&ED Case FY{{fiscal_year}}",
  "status": "open",
  "description": "SR&ED claim for FY{{fiscal_year}}. Estimated recovery: {{estimated_recovery}}",
  "children": [
    { "type": "task", "title": "Consent + data access",                  "status": "todo" },
    { "type": "task", "title": "Ingest GitHub commits",                  "status": "todo" },
    { "type": "task", "title": "Ingest QBO transactions",                "status": "todo" },
    { "type": "task", "title": "Reconcile against payroll (T4/T4A)",     "status": "todo" },
    { "type": "task", "title": "Synthesize T661 narrative draft",        "status": "todo" },
    { "type": "task", "title": "Practitioner review + attest",           "status": "todo" },
    { "type": "task", "title": "Lock binder with Merkle root",           "status": "todo" },
    { "type": "task", "title": "Hand off to filing partner",             "status": "todo" },
    { "type": "milestone", "title": "Filed with CRA",    "status": "pending" },
    { "type": "milestone", "title": "Recovery received", "status": "pending" }
  ]
}
```

---

### 4.3 `iso_42001_audit`

**Why it exists.** ISO 42001 readiness engagements have a predictable shape: gap analysis, evidence gathering, policy work, PECB prep, internal audit. Encoding it as a template ensures no step is skipped and billing milestones are trackable.

**Minimum rank to instantiate:** `coordinator`.

**Edges created automatically:** `engagement_for` edge from the root opportunity → the customer entity.

**Parameters:**
```json
[
  { "name": "customer_project_id", "required": true,  "type": "integer" },
  { "name": "customer",            "required": true,  "type": "string" },
  { "name": "engagement_fee",      "required": false, "type": "string" }
]
```

**`root_shape`:**
```json
{
  "type": "opportunity",
  "title": "ISO 42001 Readiness for {{customer}}",
  "status": "open",
  "description": "ISO 42001 readiness engagement. Engagement fee: {{engagement_fee}}",
  "children": [
    { "type": "task", "title": "Gap analysis against ISO 42001 controls",  "status": "todo" },
    { "type": "task", "title": "Evidence gathering from current AI systems","status": "todo" },
    { "type": "task", "title": "Policy + procedure documentation",          "status": "todo" },
    { "type": "task", "title": "PECB certification prep",                   "status": "todo" },
    { "type": "task", "title": "Internal audit + remediation",              "status": "todo" },
    { "type": "milestone", "title": "Certification submitted", "status": "pending" }
  ]
}
```

---

### 4.4 `partner_onboarding`

**Why it exists.** Ecosystem partnerships (brokers, accelerators, CPA firms) follow the same lifecycle: relationship → terms → agreement → channel → pilot referral. Capturing it as a template means any partner brought in by Noor or Hadi hits the same quality bar and nothing falls through the cracks.

**Minimum rank to instantiate:** `coordinator`.

**Edges created automatically:** `partner_entity` edge from the root project → the partner entity record; `led_by` edge to `ecosystem_lead_id`.

**Parameters:**
```json
[
  { "name": "partner_name",        "required": true,  "type": "string" },
  { "name": "partner_type",        "required": true,  "type": "string",
    "enum": ["broker", "accelerator", "cpa_firm"] },
  { "name": "primary_contact_id",  "required": false, "type": "integer" },
  { "name": "ecosystem_lead_id",   "required": false, "type": "integer" }
]
```

**`root_shape`:**
```json
{
  "type": "project",
  "title": "Onboard {{partner_name}}",
  "status": "open",
  "description": "Partner onboarding — type: {{partner_type}}",
  "children": [
    { "type": "task", "title": "Initial conversation + rapport",                "status": "todo" },
    { "type": "task", "title": "Define referral terms / commission structure",   "status": "todo" },
    { "type": "task", "title": "Sign partnership agreement",                     "status": "todo" },
    { "type": "task", "title": "Provision Discord entity channel",               "status": "todo" },
    { "type": "task", "title": "Create partner record in Squad Service",         "status": "todo" },
    { "type": "task", "title": "First referral handoff pilot",                  "status": "todo" },
    { "type": "milestone", "title": "Partner activated (first customer referred)", "status": "pending" }
  ]
}
```

---

### 4.5 `customer_to_digid_upsell`

**Why it exists.** When GAF surfaces a recovery for a customer, that capital often unlocks appetite for AI implementation. This template captures the upsell arc and, critically, tracks the AI build so it feeds next year's SR&ED claim — closing the loop.

**Minimum rank to instantiate:** `specialist` or above.

**Edges created automatically:** `derived_from` edge from the root opportunity → the `sred_case` opportunity that funded the customer's reinvestment capacity (identified via `gaf_case_id`). This edge makes the causality explicit in the graph: recovery → upsell → next SR&ED.

**Parameters:**
```json
[
  { "name": "customer_project_id",          "required": true,  "type": "integer" },
  { "name": "customer",                     "required": true,  "type": "string" },
  { "name": "gaf_case_id",                  "required": true,  "type": "integer" },
  { "name": "estimated_ai_engagement_value","required": false, "type": "string" }
]
```

**`root_shape`:**
```json
{
  "type": "opportunity",
  "title": "Digid AI engagement for {{customer}}",
  "status": "open",
  "description": "Post-GAF AI upsell. Estimated value: {{estimated_ai_engagement_value}}",
  "children": [
    { "type": "task", "title": "Review GAF-surfaced capital + reinvestment plan", "status": "todo" },
    { "type": "task", "title": "Propose AI implementation scope",                 "status": "todo" },
    { "type": "task", "title": "Contract + kickoff",                             "status": "todo" },
    { "type": "task", "title": "AI build + deployment",                          "status": "todo" },
    { "type": "task", "title": "Document for SR&ED (next year's claim)",         "status": "todo" },
    { "type": "milestone", "title": "AI system in production", "status": "pending" }
  ]
}
```

---

## Section 5 — Template Registry Governance

**Who can create a template.** Only users with `coordinator` rank or higher may propose a new template. Proposals are created in `template_draft` state — they cannot be instantiated yet.

**Review gate.** Every new template must pass an Athena architectural review before it reaches `published` state. Athena checks: Does this pattern genuinely recur? Are the parameters minimal and well-typed? Does the shape conflict with an existing template? The review produces a structured sign-off stored on the template row (`reviewed_by`, `reviewed_at`, `review_notes`).

**Promotion.** After sign-off, a coordinator calls `POST /templates/{id}/publish`. State transitions: `draft` → `under_review` → `published`. Rollback to `draft` is allowed at any stage before `published`.

**Deprecation.** Published templates can be marked `deprecated` (not deleted). Deprecated templates can still be instantiated for 30 days to allow in-flight work to complete. After 30 days, instantiation returns `410 Gone` with a pointer to the replacement template ID.

**Why this gate.** Without governance, templates multiply. Every operator invents a slight variant. Within six months there are forty templates, most abandoned, and the concept becomes noise. The `coordinator` floor and Athena review are the minimum viable gate to keep the registry useful.

---

## Section 6 — Test Plan

| Test | Expected result |
|---|---|
| Instantiate with a missing required parameter | `400` with `{ "missing": ["parameter_name"] }` |
| Instantiate with caller's role rank below minimum | `403` |
| Instantiate the same template twice with identical parameters | Two distinct subtrees created — no deduplication, no conflict |
| Instantiate with `parent_node_id` pointing to a node in a different workspace | `403` — cross-workspace parent not allowed |
| Instantiate a `deprecated` template outside the 30-day window | `410` with `{ "replacement_template_id": N }` |
| Verify `instantiated_from` edge | `node_edges` row exists: `source = root_node_id`, `type = instantiated_from`, `target_template_id` correct |
| Verify `node_template_instances` row | Row exists, `parameters` JSONB matches request, `instantiated_by` matches caller |
| Bus events | One `node.created` event per created node, each carrying `template_id` and `instance_id` |
| Version migration preview | `POST /…/migrate?target_version=2` returns diff with `add`, `remove`, `update` arrays; no nodes created |
| Version migration commit after preview | Nodes mutated to match v2 shape; v1 instance row updated with `migrated_to_version = 2` |
| v1 instance unchanged when v2 is published | v1 instance `template_id` still points to v1 row; shape unaffected |
| Placeholder with no matching parameter | `400` — `root_shape` references `{{unknown_var}}` not in `parameters` |
| Depth-first creation order | First child appears before grandchild in `nodes` table insertion order (observable via `created_at` ms) |

---

## Section 7 — Open Questions

**Conditional branches.** Should `root_shape` support a conditional construct like `{ "if": "partner_type == broker", "include": [...] }`? This would allow a single `partner_onboarding` template to vary its shape by type. Decision: defer. Conditional logic in a JSON DSL adds a parser and test surface that is not justified by current volume. When three or more templates diverge only in one branch, revisit.

**Async instantiation.** For large templates (deep nesting, many children), depth-first creation inside a single HTTP request could time out. Proposed boundary: sync under 50 nodes (covers all five seed templates comfortably), async above 50 — return `202 Accepted` with a job ID, poll `GET /templates/jobs/{job_id}`. Implement async path in Phase 2 when a template exceeding 50 nodes actually exists.

**Template authoring UI.** Currently, templates are authored as raw JSON by coordinators. A visual tree editor would lower the barrier. Decision: defer to Phase 3 UI track. In the interim, a JSON schema validator and a CLI preview command (`sos templates preview --file shape.json --params params.json`) are sufficient.

**Parameter types beyond string/integer.** Should parameters support `date`, `enum`, `entity_ref`? `enum` is already used in `partner_type`. Formal type validation at the API layer would catch errors earlier. Propose: add a `validate` step that checks parameter values against declared types before touching the database. Implement alongside the first template that needs it.
