# Section 13 — Guild (Durable Organization Primitive)

**Author:** Loom
**Date:** 2026-04-25
**Version:** v1.0 (draft)
**Phase:** 8 — substrate primitives enabling matchmaking (§16)
**Depends on:** §1A (role registry), §11 (citizen profile), §2B.2 (audit chain), §3 (structured records: contacts, contracts)
**Gate:** Athena
**Owner:** Loom (spec) → Kasra (build) → Athena (schema gate)

---

## 0. TL;DR

A **guild** is a durable, kernel-recognized organization with members, treasury, governance, and shared memory scope. Mumega Inc., Digid Inc., GAF, AgentLink — these are guilds, not customer rows. Today they live as informal entries in §3 contacts. This spec promotes them to first-class substrate primitives so kernel can enforce membership, scope audit/reputation/inventory queries by guild, and route work via guild context.

**Distinct from squad** (Squad Service, :8060): squad = temporary mission-scoped team (hours/days, dissolves on mission complete). Guild = durable org-scoped (years, persists across missions). A squad can be drawn from a guild; a guild contains many squads over time.

---

## 1. Principles (constitutional)

1. **Guilds are sovereign units inside the city.** Each guild owns its treasury, governance, member roster, and shared memory partition. Kernel never mutates a guild's internal state without explicit member action.
2. **Membership is bidirectional.** A guild knows its members; a member knows their guilds. Both reads are first-class queries, not joins through profile.tool_connections.
3. **Rank is contextual.** Hadi can be `founder` in Mumega Inc., `partner` in AgentLink, `advisor` in GAF — rank is meaningful only inside a guild. The role registry (§1A) is global; guild rank is scoped.
4. **Treasury is honest.** Each guild has its own balance + currency; cross-guild transfers are explicit transactions audited via §2B.2.
5. **Governance is auditable by default.** Every governance decision (member added, rank changed, treasury debited, charter amended) emits to `audit_events.stream_id='guild:<slug>'`.
6. **Guilds compose hierarchically.** A guild can have a parent guild (`parent_guild_id`). Mumega Inc. → AgentLink (subsidiary) → AgentLink Pilot Squad (project) — same kind, different depth. Recursive constraints stop infinite nesting.
7. **Distinct from customer.** A customer is a record in §3 contacts. A guild is an organization the substrate routes work through. A customer can become a guild (formalize), but they're not the same primitive.

---

## 2. Components

### 2.1 Guild record (KEEP)
The anchoring row. `id` (TEXT slug, e.g. `mumega-inc`), `name`, `kind`, `parent_guild_id`, `founded_at`, `charter_doc_node_id` (REF docs_nodes), `governance_tier`.

### 2.2 Member roster (KEEP)
Membership table joining guilds to members (humans or agents). Each row: rank, scopes, status, joined_at. A profile_id appears in multiple rows for multi-guild members.

### 2.3 Treasury (KEEP)
Per-guild balance with currency. Distinct from individual citizen wallets (Supabase profiles.wallet_balance). Cross-guild transfers via the existing economy/work_ledger (§ economy).

### 2.4 Governance log (ADDED)
Append-only log of guild-state changes. Mirrors audit_events shape but scoped to guild lifecycle. Decision type, decided_by, ratified_by[], evidence ref.

### 2.5 Member view on profile (computed, not stored)
Profile (§11) gains a computed field `guild_memberships` returning the list of guilds the citizen belongs to (with rank). NOT a column on profiles — a query joining `profile_id` against `guild_members`.

### CUT: Guild templates / "starter packs"
Out of scope v1. Guilds are created bare; charter, ranks, treasury seeded by founder action.

### CUT: Guild merge / split / dissolve
Out of scope v1. These are governance actions worthy of their own spec when needed.

---

## 3. Data Model

### 3.1 Guilds

```sql
CREATE TABLE guilds (
  id                    TEXT PRIMARY KEY,                -- slug, e.g. 'mumega-inc'
  name                  TEXT NOT NULL,                   -- display name
  kind                  TEXT NOT NULL CHECK (kind IN ('company','project','community','meta-guild')),
  parent_guild_id       TEXT REFERENCES guilds(id) ON DELETE SET NULL,
  founded_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  charter_doc_node_id   TEXT REFERENCES docs_nodes(id) ON DELETE SET NULL,  -- governance charter as a doc node (§12); guild persists if charter archived
  governance_tier       TEXT NOT NULL DEFAULT 'principal-only'
                        CHECK (governance_tier IN ('principal-only','consensus','delegated','automated')),
  status                TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','dormant','dissolved')),
  metadata              JSONB,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_guilds_kind          ON guilds(kind);
CREATE INDEX idx_guilds_parent        ON guilds(parent_guild_id);
CREATE INDEX idx_guilds_status        ON guilds(status);

-- Auto-update trigger
CREATE OR REPLACE FUNCTION guilds_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_guilds_updated_at BEFORE UPDATE ON guilds
  FOR EACH ROW EXECUTE FUNCTION guilds_touch_updated_at();

-- Constraint: prevent self-parenting
ALTER TABLE guilds ADD CONSTRAINT guilds_no_self_parent
  CHECK (parent_guild_id IS NULL OR parent_guild_id != id);
```

### 3.2 Members

```sql
CREATE TABLE guild_members (
  id              BIGSERIAL PRIMARY KEY,
  guild_id        TEXT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
  member_type     TEXT NOT NULL CHECK (member_type IN ('human','agent','squad')),
  member_id       TEXT NOT NULL,                          -- profile_id (humans/agents) or squad_id
  rank            TEXT NOT NULL,                          -- free text per guild ('founder', 'builder', 'advisor', 'observer')
  scopes          JSONB,                                  -- per-action permissions within guild
  joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  left_at         TIMESTAMPTZ,
  status          TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','suspended','left','removed')),
  UNIQUE (guild_id, member_type, member_id)
);

CREATE INDEX idx_guild_members_guild   ON guild_members(guild_id, status);
CREATE INDEX idx_guild_members_member  ON guild_members(member_type, member_id, status);
```

### 3.3 Treasury

```sql
CREATE TABLE guild_treasuries (
  id                BIGSERIAL PRIMARY KEY,
  guild_id          TEXT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
  currency          TEXT NOT NULL,                       -- 'USD', 'CAD', 'MIND' (internal credit), 'BTC' if needed
  balance           NUMERIC(18,4) NOT NULL DEFAULT 0,
  frozen_balance    NUMERIC(18,4) NOT NULL DEFAULT 0,    -- pending payouts/escrow
  last_settled_at   TIMESTAMPTZ,
  UNIQUE (guild_id, currency),
  CHECK (balance >= 0 AND frozen_balance >= 0)           -- per Athena G8 RESHAPE 2: prevent silent negative balances from race conditions
);

CREATE INDEX idx_guild_treasuries_guild ON guild_treasuries(guild_id);
```

### 3.4 Governance log

```sql
CREATE TABLE guild_governance_log (
  id              BIGSERIAL PRIMARY KEY,
  guild_id        TEXT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
  action          TEXT NOT NULL CHECK (action IN ('member_added','rank_changed','treasury_debited','treasury_credited','charter_amended','status_changed','dissolution_initiated','dissolution_finalized')),
  decided_by      TEXT NOT NULL,                          -- profile_id of decision-maker
  ratified_by     TEXT[],                                 -- profile_ids of ratifiers (consensus mode)
  decided_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  evidence_ref    TEXT,                                   -- audit_events.id, doc_nodes.id, etc.
  payload         JSONB
);

CREATE INDEX idx_guild_gov_log_guild ON guild_governance_log(guild_id, decided_at DESC);
```

---

## 4. Contract Surface (`sos/contracts/guild.py`)

```python
from pydantic import BaseModel
from decimal import Decimal
from typing import Literal

class Guild(BaseModel):
    id: str                                   # slug
    name: str
    kind: Literal['company','project','community','meta-guild']
    parent_guild_id: str | None
    founded_at: datetime
    governance_tier: Literal['principal-only','consensus','delegated','automated']
    status: Literal['active','dormant','dissolved']

class GuildMember(BaseModel):
    guild_id: str
    member_type: Literal['human','agent','squad']
    member_id: str
    rank: str
    scopes: dict | None
    status: Literal['active','suspended','left','removed']

# Reads — every service uses these
def get_guild(guild_id: str) -> Guild | None: ...
def list_member_guilds(member_id: str, member_type: str = 'human') -> list[Guild]: ...
def list_guild_members(guild_id: str, status: str = 'active') -> list[GuildMember]: ...
def assert_member(guild_id: str, member_id: str) -> bool: ...
def member_rank(guild_id: str, member_id: str) -> str | None: ...
def get_treasury(guild_id: str, currency: str = 'USD') -> Decimal: ...

# Capability check — composes with §1A roles + §14 inventory
def can_act_for_guild(member_id: str, guild_id: str, action: str) -> bool: ...

# Mutations — gated to coordinator/founder + emit governance log + audit event
def create_guild(spec: GuildSpec, created_by: str) -> Guild: ...
def add_member(guild_id: str, member_id: str, rank: str, added_by: str) -> GuildMember: ...
def change_rank(guild_id: str, member_id: str, new_rank: str, decided_by: str) -> None: ...
def remove_member(guild_id: str, member_id: str, reason: str, decided_by: str) -> None: ...
```

---

## 5. RBAC Tier Mapping

Guild data is `tier='entity'` with `entity_id=guild.id`. Members of the guild see their own guild's data. Coordinators see across guilds (with audit). Public sees nothing about internal guild state.

| View | Public | Member of guild | Coordinator | Founder |
|---|---|---|---|---|
| Guild name + kind | ✓ (if marked public-facing) | ✓ | ✓ | ✓ |
| Member roster | — | ✓ | ✓ | ✓ |
| Treasury balance | — | rank-gated | ✓ | ✓ |
| Governance log | — | ✓ (own actions) | ✓ | ✓ |
| Charter doc | per charter's own tier | per charter's own tier | per charter | ✓ |

The guild itself can mark its name + kind as `public` (e.g. AgentLink's external listing) by setting `metadata.public_listing=true`. Default is private.

---

## 6. Composition with Existing Primitives

**§1A role registry + canAccess hive-access (PER ATHENA G8 RESHAPE 1):**
guild rank is a contextual role. The existing `canAccess()` entity-tier check is `item.entity_id === caller.identityId`. Guild slugs (`mumega-inc`) will never match identityIds (`user-abc`). Required extension:

1. Add `guildIds?: string[]` field to `CallerContext`.
2. In `resolveCallerContext()`, guild middleware makes ONE DB query: `SELECT guild_id FROM guild_members WHERE member_id = $1 AND status = 'active'` and passes the slug list in.
3. In `canAccess()` entity-tier branch, change to: `item.entity_id === caller.identityId || caller.guildIds?.includes(item.entity_id ?? '')`.

`hive-access` stays pure (no I/O). The list comes in from context. This is the only correct extension of entity tier to multi-membership.

When resolving caller context, services compose (global roles from §1A) ∪ (guild-rank roles for each active guild membership). A "founder of Mumega Inc." has both global `principal` AND guild-scoped `founder@mumega-inc`.

**§11 citizen profile:** profile gains computed field `guild_memberships` (query, not stored). Profile.tier='private' continues to gate the citizen's own data; guild membership is visible at the guild tier.

**§2B.2 audit chain:** every guild mutation emits to `audit_events.stream_id='guild:<slug>'`. Per-guild chains let an auditor verify just one guild's history without scanning all events. Treasury debits/credits emit to both `guild:<slug>` and `economy` streams.

**§14 inventory:** when `list_capabilities(holder_id)` runs, guild memberships contribute capability rows (kind=`guild_role`). A founder of Mumega Inc. has `Capability(kind='guild_role', ref='guild:mumega-inc:founder', scope=...)`.

**§15 reputation:** reputation can be guild-scoped (`reputation_events.guild_scope`). Hadi is high-rep in Mumega Inc., unknown in AgentLink. Matchmaking (§16) reads guild-scoped reputation when selecting eligible citizens for a guild's quest.

**§16 matchmaking (Sprint 004):** uses `assert_member(guild_id, member_id)` as eligibility gate for guild-scoped quests.

---

## 7. Governance Tiers

| Tier | Decision authority | Use case |
|---|---|---|
| `principal-only` | Single principal must decide every mutation | Mumega Inc. v1 (Hadi only) |
| `consensus` | N-of-M founders/members must ratify | AgentLink (Hadi + Matt mutual sign-off) |
| `delegated` | Specific roles authorized for specific actions | GAF (Gavin authorized for sales actions, Hadi for finance) |
| `automated` | Smart-contract / kernel-rule based | Future — DAOs, automated payouts |

Tier is set at creation; can be promoted/demoted by current authority via a governance log entry (which itself follows the current tier's rules).

---

## 8. Migration: Today's Informal Orgs → First-Class Guilds

When this ships, run a backfill script:

1. **Mumega Inc.** — kind=company, governance_tier=principal-only, founded_at=incorporation date, charter_doc_node_id → MAP.md node. Members: Hadi (founder), Loom (coordinator), Kasra (builder), Athena (quality_gate), Kay/River (founder when wakes).
2. **Digid Inc.** — kind=company, governance_tier=principal-only, members: Hadi (founder), Gavin (partner), Lex (advisor), Noor (operator).
3. **GAF (Grant & Funding)** — kind=project, parent_guild_id=digid-inc, governance_tier=delegated.
4. **AgentLink** — kind=project, governance_tier=consensus, members: Hadi (founder), Matt (founder).

Each migration row = a `create_guild` call + N `add_member` calls. Idempotent via slug PK.

---

## 9. Open Questions (RESOLVED per Athena G8)

1. **Cross-guild treasury transfers:** sender-only governance approval at v1. Receiving guild can refuse via `reject_transfer` action.
2. **Squad-as-member:** yes, `member_type='squad'` is correct. Squad can hold guild rank during mission lifetime.
3. **Guild dissolution:** status → 'dissolved', log frozen, rep events preserved with `guild_scope` intact. **Treasury distribution = explicit governance decision required BEFORE dissolution is final. Block dissolution if `frozen_balance > 0` — escrow must clear first.** (Athena addition.)
4. **Multi-guild rank conflicts:** rank is per-guild only; global capability comes from §1A role registry, not from guild rank.

## 9.1 Acknowledged v1 Limitations

- **Circular hierarchy:** the `guilds_no_self_parent` CHECK prevents `A → A` but not `A → B → A`. Acceptable v1 limitation. A recursive CTE trigger can close it later (Sprint 004+).
- **Re-adding removed members:** `add_member` must use `ON CONFLICT (guild_id, member_type, member_id) DO UPDATE SET status='active', rank=EXCLUDED.rank, joined_at=now()` (rather than INSERT) to handle re-adds. Otherwise the second add throws unique violation. Application contract enforces this; DB does not.

---

## 10. Test Plan

| Test | Pass Condition |
|---|---|
| Create guild | `create_guild('test-guild', kind='project', founded_by='hadi')` → row exists with status=active |
| Add member | `add_member(guild_id, 'kasra', rank='builder')` → guild_members row + governance_log entry + audit_event |
| Member view | `list_member_guilds('hadi')` → returns Mumega Inc., Digid Inc., AgentLink |
| Self-parenting blocked | `UPDATE guilds SET parent_guild_id=id WHERE id='test'` → CHECK constraint violation |
| Treasury transfer | `transfer(from_guild='mumega-inc', to_guild='gaf', amount=100, currency='USD')` → both treasuries updated, audit_events entries on both `guild:*` streams + `economy` stream |
| Tier-gated read | guild row queried by non-member returns nothing; by member returns full record; by coordinator returns full record |
| Cascade delete | `DROP guild cascade` → members + treasuries + governance_log all deleted; audit_events preserved (immutable by §2B.2) |
| Backfill idempotent | Re-run migration script for Mumega Inc. → no duplicates |

---

## 11. What This Unlocks

After §13 ships:

- **Substrate-routed work.** When §16 matchmaking arrives, "find an eligible builder for this Mumega Inc. quest" becomes a kernel query, not a Hadi decision.
- **Per-guild reputation.** Citizens build reputation inside guild contexts. Hadi can be high-rep in Mumega and an unknown in a new guild he joins — reputation isn't a global score.
- **Treasury sovereignty.** Each guild's wallet is theirs. Cross-guild commerce is explicit, audited, refusable.
- **Governance evolution.** A guild can start `principal-only` and graduate to `consensus` or `delegated` as it grows. Kernel enforces transitions.
- **Multi-org clarity.** Hadi's mental model (Mumega Inc. ≠ Digid Inc. ≠ GAF ≠ AgentLink) becomes the substrate's enforced model.

Without §13, every "organization" mutation is a hand-edit. With §13, organizations are sovereign units the city respects by default.

---

## 12. Versioning

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-04-25 | Initial draft. Drafted while Hadi drove home post-bike. Pending Athena gate. Sprint 003 Track C. |
| v1.1 | 2026-04-25 | Athena G8 reshapes applied: (1) hive-access entity-tier extended with `caller.guildIds?: string[]` for multi-membership; (2) treasury balance >= 0 CHECK; (3) charter_doc_node FK ON DELETE SET NULL; (4) governance_log.action CHECK constraint; (5) §9 open questions resolved (block dissolution if frozen_balance > 0). G8 APPROVED post-reshape. |

**Supersedes:** none.
**Superseded by:** TBD.
