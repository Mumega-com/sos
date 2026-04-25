# Section 14 — Inventory (Unified Capability Read)

**Author:** Loom
**Date:** 2026-04-25
**Version:** v1.0 (draft)
**Phase:** 8 — substrate primitives
**Depends on:** §1A (role registry), §11 (citizen profile), §13 (guild), §2B.2 (audit chain), §6 (plugin contract)
**Gate:** Athena
**Owner:** Loom (spec) → Kasra (build) → Athena (schema gate)

---

## 0. TL;DR

A citizen (human or agent) accumulates capabilities across five source domains: D1 token tables (credentials), `plugin.yaml` (MCP tools), GHL workflows (automations), `sos/skills/` filesystem (templates), `profile_tool_connections` (OAuth). Today, "what does Loom have access to?" requires querying five places. Inventory promotes this query to a kernel primitive: **one read, one composable answer.**

Inventory is an **index**, not a new authority. Source domains stay where they are. `inventory_grants` is the unifying lookup table. `list_capabilities(holder_id)` returns the composed view. `assert_capability(holder_id, kind, ref, action)` is the kernel guard.

---

## 1. Principles (constitutional)

1. **One read, many sources.** A citizen's capabilities live across DBs, filesystems, and remote APIs. Inventory composes them at query time without forcing them into a single store.
2. **Soft pointers, hard verification.** `inventory_grants.capability_ref` is TEXT — no foreign keys across heterogeneous sources. Integrity comes from a periodic reconciler that reaps orphans + a `last_verified_at` field consumers can filter on.
3. **Grants are auditable.** Every grant + revoke + re-verification emits to `audit_events.stream_id='inventory'`. Capability changes are the foundation of trust; they leave a trail.
4. **No capability transcends its source.** Inventory exposes the existence of a capability; the source domain still owns enforcement. Revoking an OAuth token via Gmail console immediately invalidates the inventory row at next verification, even before the reconciler runs.
5. **Composition over copy.** Guild rank (§13) is a capability of kind `guild_role`. Inventory doesn't copy guild data — it queries §13 at composition time. Same for plugin tools, OAuth tokens, etc.

---

## 2. Components

### 2.1 inventory_grants (KEEP)
Single table. Polymorphic hub. Each row: `(holder_type, holder_id, capability_kind, capability_ref, scope, granted_by, granted_at, expires_at, last_verified_at, status)`.

### 2.2 Per-kind verifiers (KEEP)
Pluggable verification functions, one per capability_kind. `verify_credential(ref)`, `verify_oauth(ref)`, `verify_guild_role(ref)`, etc. Verifiers update `last_verified_at` and may demote `status` to `stale` or `revoked`.

### 2.3 Reconciler (ADDED)
Background job. Walks inventory_grants where `last_verified_at < now() - interval '24 hours'`. Calls per-kind verifier. Updates row. Reaps orphans (`status='orphaned'` → eligible for hard delete after grace period).

### 2.4 list_capabilities query (KEEP)
The unifying read. Joins inventory_grants with per-kind source tables (where stable) for full hydration. Returns Capability[] with kind/ref/scope/source-row-summary.

### CUT: Capability inheritance / transitive grants
Out of scope v1. A guild rank doesn't automatically grant a member access to all guild OAuth connections. Each capability is explicit.

### CUT: Capability marketplace / trade
Out of scope v1. Capabilities are granted by authorized parties, not traded between citizens. ToRivers (when it ships) handles paid capability provisioning.

---

## 3. Data Model

### 3.1 Inventory grants

```sql
CREATE TABLE inventory_grants (
  grant_id          TEXT PRIMARY KEY,                    -- e.g. 'inv:gmail:loom:abc123'
  holder_type       TEXT NOT NULL CHECK (holder_type IN ('human','agent','squad','guild')),
  holder_id         TEXT NOT NULL,                       -- profile_id, agent slug, squad_id, guild_id
  capability_kind   TEXT NOT NULL CHECK (capability_kind IN (
                      'credential', 'tool', 'automation', 'template',
                      'oauth_connection', 'guild_role', 'data_access', 'mcp_server'
                    )),
  capability_ref    TEXT NOT NULL,                       -- soft pointer into source domain
  source_domain     TEXT NOT NULL,                       -- 'd1:tokens', 'plugin:yaml', 'ghl:workflows', 'fs:sos/skills', 'pg:profile_tool_connections'
  scope             JSONB,                               -- per-action constraints {"read_only": true, "rate_limit": "100/h"}
  granted_by        TEXT NOT NULL,                       -- profile_id of grantor
  granted_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at        TIMESTAMPTZ,                         -- nullable; soft TTL
  last_verified_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  status               TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active','stale','orphaned','revoked','expired')),
  -- Per Athena G9 soft note: visibility into stuck verifiers
  last_error           TEXT,                                   -- last verifier error message; null when verification succeeded
  verify_attempt_count SMALLINT NOT NULL DEFAULT 0,            -- incremented on each failed verifier call; reset on success
  metadata             JSONB,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Operator query for stuck grants: WHERE verify_attempt_count > 10 AND status = 'active'
CREATE INDEX idx_inv_stuck ON inventory_grants(verify_attempt_count) WHERE status = 'active' AND verify_attempt_count > 0;

-- Holder lookup (the hot query — every session resolution hits this)
CREATE INDEX idx_inv_holder         ON inventory_grants(holder_type, holder_id, status);
CREATE INDEX idx_inv_holder_kind    ON inventory_grants(holder_type, holder_id, capability_kind, status);

-- Capability lookup (when checking "who has access to this oauth?")
CREATE INDEX idx_inv_capability     ON inventory_grants(capability_kind, capability_ref);

-- Reconciler scan (oldest verifications first)
CREATE INDEX idx_inv_verify         ON inventory_grants(last_verified_at) WHERE status = 'active';

-- Orphan reaping (eligible for deletion)
CREATE INDEX idx_inv_orphaned       ON inventory_grants(status, updated_at) WHERE status IN ('orphaned','revoked','expired');

-- Auto-update trigger
CREATE OR REPLACE FUNCTION inv_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_inv_updated_at BEFORE UPDATE ON inventory_grants
  FOR EACH ROW EXECUTE FUNCTION inv_touch_updated_at();

-- Uniqueness: one active grant per (holder, kind, ref). Re-grants update existing row.
CREATE UNIQUE INDEX idx_inv_unique_active
  ON inventory_grants(holder_type, holder_id, capability_kind, capability_ref)
  WHERE status = 'active';
```

### 3.2 No second table

Inventory is one table + queries against existing source domains. The discipline.

---

## 4. Contract Surface (`sos/contracts/inventory.py`)

```python
from pydantic import BaseModel
from datetime import datetime
from typing import Literal

class Capability(BaseModel):
    grant_id: str
    holder_type: Literal['human','agent','squad','guild']
    holder_id: str
    kind: Literal['credential','tool','automation','template',
                  'oauth_connection','guild_role','data_access','mcp_server']
    ref: str                          # soft pointer
    source_domain: str
    scope: dict | None
    granted_by: str
    granted_at: datetime
    expires_at: datetime | None
    last_verified_at: datetime
    status: Literal['active','stale','orphaned','revoked','expired']

# Reads — every service hits these at session resolution
def list_capabilities(
    holder_id: str,
    holder_type: str = 'human',
    kind: str | None = None,
    fresh_within_seconds: int | None = None  # only return rows verified within window
) -> list[Capability]: ...

def assert_capability(
    holder_id: str,
    kind: str,
    ref: str,
    action: str,
    fresh_within_seconds: int | None = 86400  # default: 24h fresh
) -> bool: ...

# Mutations — gated, audited
def grant_capability(
    holder_id: str, kind: str, ref: str,
    source_domain: str, scope: dict | None,
    granted_by: str, expires_at: datetime | None = None
) -> Capability: ...

def revoke_capability(grant_id: str, revoked_by: str, reason: str) -> None: ...

def reverify(grant_id: str) -> Capability: ...   # force per-kind verifier run

# Verifier registry
def register_verifier(kind: str, verify_fn: Callable[[str], bool]) -> None: ...
```

---

## 5. Per-Kind Verifiers

Pluggable. Registered at service boot. Each takes a `capability_ref` and returns `(is_valid: bool, status_hint: str)`.

| Kind | Verifier behavior |
|---|---|
| `credential` | Query `mirror_tokens` (or D1 token table) by token_id; valid if exists + not revoked |
| `tool` | Read `plugin.yaml` files; valid if plugin is registered + tool is in mcp_tools list |
| `automation` | GHL API call: `GET /workflows/{ref}`; valid if exists + status='active' |
| `template` | Filesystem stat on `sos/skills/{ref}/SKILL.md`; valid if file exists |
| `oauth_connection` | Query `profile_tool_connections` by id; valid if status='active' AND token_expires_at > now() |
| `guild_role` | Call `guild.member_rank(guild_id, holder_id)` (§13); valid if non-null AND active |
| `data_access` | Tier check via `sos.contracts.tiers.apply_tier_filter`; valid if caller's roles include any of permitted_roles |
| `mcp_server` | HEAD request to MCP server URL with token; valid if 200 |

Verifiers can return `'stale'` (need re-check, capability tentatively valid) or `'orphaned'` (source row gone, mark for reaping) or `'revoked'` (definitively dead).

---

## 6. Reconciler Job

Background process. Schedule: every 1 hour (configurable). Hot path (active grants verified in last 24h) is bypassed.

```python
async def reconcile_inventory():
    rows = pg.fetch("""
        SELECT grant_id, capability_kind, capability_ref FROM inventory_grants
        WHERE status = 'active' AND last_verified_at < now() - interval '24 hours'
        ORDER BY last_verified_at ASC
        LIMIT 1000
    """)
    for r in rows:
        verifier = VERIFIERS.get(r.capability_kind)
        if not verifier:
            continue
        ok, hint = verifier(r.capability_ref)
        new_status = 'active' if ok else hint
        pg.execute("""
            UPDATE inventory_grants
            SET last_verified_at = now(), status = $1
            WHERE grant_id = $2
        """, new_status, r.grant_id)
        if new_status != 'active':
            emit_audit('inventory', 'capability_demoted', r.grant_id, ...)
```

Hard delete of orphaned/revoked rows after 30-day grace period (separate periodic GC).

---

## 7. RBAC Tier Mapping

`tier='private'` — citizen sees their own inventory. Coordinators see by guild scope (compose with §13). Public sees nothing.

| View | Public | Self | Coordinator (own guild) | Coordinator (cross-guild) |
|---|---|---|---|---|
| List own capabilities | — | ✓ | — | — |
| List capabilities of guild member | — | — | ✓ | — |
| Grant a capability | — | — | ✓ | rank-gated |
| Revoke a capability | — | own only | ✓ | with audit |

---

## 8. Composition with Existing Primitives

**§1A role registry:** roles are capabilities. When `register_role(role_name, holder_id)`, a corresponding inventory_grants row is created (`kind='data_access', ref=role_name`).

**§11 profile:** `profile_tool_connections` rows mirror as `kind='oauth_connection'` inventory entries. The mirroring is via a trigger on profile_tool_connections insert/update, so adding a Gmail OAuth automatically appears in inventory.

**§13 guild:** guild_members rows mirror as `kind='guild_role'` inventory entries (`ref='guild:{slug}:rank'`). When a guild member's rank changes, the trigger updates inventory.

**§15 reputation:** doesn't mutate inventory. But reputation_events trigger checks `assert_capability(actor, 'task_executor', task_kind)` to validate the actor was authorized for the task before counting.

**§2B.2 audit chain:** every grant + revoke + reverify + status change emits to `audit_events.stream_id='inventory'`. Per-citizen capability history is queryable from audit alone.

**§16 matchmaking (Sprint 004):** `can_claim(holder_id, quest)` reads required capabilities from quest spec, then `assert_capability(holder_id, kind, ref, action)` for each. Eligibility is composable.

---

## 9. Migration Path

When this ships, run a one-shot mirror script:

1. **D1 tokens** → grant `kind='credential'` for each active row in token tables.
2. **plugin.yaml** → grant `kind='tool'` for each `mcp_tool` declaration in each plugin's manifest.
3. **GHL workflows** → grant `kind='automation'` for each active workflow in GHL (paginated API call).
4. **sos/skills/** → grant `kind='template'` for each `SKILL.md` directory.
5. **profile_tool_connections** → grant `kind='oauth_connection'` for each row with status='active'.

Idempotent: if the grant_id (deterministic from kind+ref+holder) already exists, skip. Re-runnable.

---

## 10. Open Questions

1. **Verifier failure modes.** What happens if a verifier itself errors (e.g., GHL API down)? Recommend: status stays unchanged; `last_verified_at` is NOT updated (so reconciler retries next cycle); error logged but not rethrown.
2. **Cross-domain capability composition.** Some capabilities require both an OAuth + an automation (e.g., "send Gmail via GHL workflow"). Is that one inventory row or two? Recommend: two rows (atomic units); composition lives in the workflow definition, not inventory.
3. **Capability revocation cascading.** If a guild dissolves (§13 status='dissolved'), do guild_role inventory rows auto-revoke? Recommend: yes — guild dissolution emits a `guild_dissolved` event; inventory listener marks all `kind='guild_role'` rows for that guild as revoked.
4. **Pre-emptive verification on assert_capability.** Should `assert_capability` ever trigger an inline verifier call when `last_verified_at > 24h`? Recommend: no — too slow for hot paths. Use `fresh_within_seconds` filter to require fresh; force `reverify(grant_id)` separately when stakes are high.

---

## 11. Test Plan

| Test | Pass Condition |
|---|---|
| Grant capability | `grant_capability('loom', 'tool', 'mcp__sos__send', ...)` → row exists + audit_event emitted |
| List capabilities | `list_capabilities('loom')` returns all active grants for Loom |
| Filter by kind | `list_capabilities('loom', kind='oauth_connection')` returns only oauth rows |
| Filter by freshness | `list_capabilities('loom', fresh_within_seconds=3600)` excludes stale |
| Assert positive | `assert_capability('loom', 'tool', 'mcp__sos__send', 'invoke')` → True for valid grant |
| Assert negative — expired | `assert_capability` for expired grant → False |
| Assert negative — orphaned | grant exists in inventory but source row deleted → reverify marks orphaned → assert False |
| Reconciler reaps | orphan grant > 30 days old → hard deleted by GC |
| Trigger on profile_tool_connections | INSERT into profile_tool_connections → corresponding inventory_grants row appears |
| Trigger on guild_members | INSERT into guild_members → corresponding `kind='guild_role'` row appears |
| Audit on grant | grant + revoke + reverify each emit to `audit_events.stream_id='inventory'` |
| Concurrent grant idempotent | Two parallel `grant_capability` calls for same (holder, kind, ref) → one row, no error (UNIQUE WHERE active) |

---

## 12. What This Unlocks

After §14 ships:

- **Onboarding by query.** "When Pricila joins GAF Student Program squad, what does she immediately see/access?" becomes `list_capabilities('pricila', fresh_within_seconds=300)`.
- **Capability audit.** "Show me everyone with active Gmail OAuth on the GAF guild" is a single inventory query, not a 5-system spelunking.
- **Matchmaking eligibility.** §16 routing depends on this. Without §14, matchmaking has no way to ask "does this candidate have the capabilities the quest requires?"
- **Revocation propagation.** Revoke Gmail OAuth → inventory row marked revoked → all dependent automations skip the row in next cycle.
- **Cross-domain queries.** "What can my AI agent Loom do today?" composes credentials + tools + skills + OAuth + guild roles into one answer.

---

## 13. Versioning

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-04-25 | Initial draft. Hub-table polymorphism per Athena G9 pre-question. Sprint 003 Track C. |
| v1.1 | 2026-04-25 | Athena G9 soft note applied: added `last_error TEXT` + `verify_attempt_count SMALLINT DEFAULT 0` to inventory_grants for visibility into stuck verifiers. Idx added for operator query (stuck grants WHERE attempt_count > 10). Reconciler must increment counter on failed verify; reset on success. G9 APPROVED. |
