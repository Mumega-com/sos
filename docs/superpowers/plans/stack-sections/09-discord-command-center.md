# Section 09 — Discord Command Center + Bounty Board

**Phase:** 2.5 — lands after Section 4A (partner workspace MVP) ships  
**Owner:** Loom (coordinator), Kasra (implementation), bot subagent (slash commands)  
**Status:** Spec — pending build order kick-off

---

## 1. Background & Mandate

Discord is ubiquitous, mobile-native, and requires zero new account creation for most of the humans Mumega works with — partners, ecosystem leads, customers, and freelancers all already live there. Its role system maps cleanly onto Mumega's rank hierarchy, its thread model mirrors project-scoped conversations, and its reaction API gives us a one-tap claim UX without any custom frontend. The auth surface is mature (OAuth 2.0), the bot API is stable, and the mobile experience is production-grade. For a protocol-city where humans and agents coexist as first-class citizens, Discord is the natural off-ramp that requires nothing from the user but a phone.

The distinction between a command center and chat is not cosmetic. A command center means: slash commands that mutate state in Squad Service, reaction emojis that trigger bounty lifecycle transitions, role-gated channel visibility computed from live rank data, and bots that act as action surfaces rather than notification pipes. The web dashboard (Section 4A) and Discord are two views on the same underlying data — Squad Service tasks, bounties, commissions ledger, and the SOS bus. A partner can open Discord on their phone and `/claim 101` a bounty; Hadi can see the same claim appear in the web dashboard's commission queue within seconds. There is no separate Discord database — Discord is a rendering layer over Squad Service + bus.

---

## 2. Discord Architecture Layers

Six channel categories, each mapped to an Inkwell Hive visibility tier. The rule: **principal (100), coordinator (90), gate (85), and queen roles see all categories**. Every other rank sees only what their membership and role allow.

| Channel Category | Inkwell Hive Tier | Min Rank | Who Sees It | Example |
|---|---|---|---|---|
| `#general` | public | 10 (observer) | everyone | Onboarding landing, community announcements |
| `#announcements` | public (read-only except mods) | 10 | everyone | Shipping, bounty drops, system status |
| Squad channels | squad | 40 (worker) | squad members | `#squad-outreach`, `#squad-seo` |
| Project channels | project | 50 (partner) | project team | `#project-gaf-metrobit` |
| Role channels | role | role-specific | role members | `#partners`, `#customers`, `#agents` |
| Entity channels | entity | 50 (partner) | entity team | `#entity-century21-ron` |
| Bounty boards | project | bounty.min_rank | rank-eligible members | `#bounties-gaf`, `#bounties-universal` |
| Private DMs | private | coordinator | 1:1 with agents/coordinators | DM Kaveh, DM Kasra |

Channel permission sync runs every 5 minutes via a Cloudflare Worker cron (see Section 7). No manual role grants — all visibility derives from the rank registry.

---

## 3. OAuth Login & First-Time Ceremony Flow

### Sequence

```
User clicks invite link
        │
        ▼
Discord OAuth 2.0 → returns discord_user_id + discord_username + email (if scoped)
        │
        ▼
POST /auth/discord/callback  (mumega-edge Worker)
        │
        ├─ SELECT * FROM user_accounts WHERE discord_id = $discord_user_id
        │
        ├─ FOUND + has rank role
        │       │
        │       └─ Assign Discord role matching rank
        │          Unlock scoped channels
        │          Drop user into their home channel
        │          Done.
        │
        └─ NOT FOUND (or found but unranked)
                │
                ▼
        First-Time Ceremony
                │
                ├─ Bot DMs user:
                │   "Welcome to Mumega. I need to place you correctly.
                │    Please tell me:
                │    1. Your intended role (partner / customer / ecosystem-lead / unknown)
                │    2. Who referred you (name or Discord handle)
                │    3. What you're here to do (one sentence)"
                │
                ├─ User replies → bot captures responses
                │
                ├─ Bot creates SOS task:
                │   type: user_onboarding_pending
                │   payload: { discord_id, discord_username, responses, referred_by }
                │   assigned_to: [hadi, loom]
                │   priority: high
                │
                ├─ User dropped into #general with observer visibility only
                │   Bot sends: "You're in #general while we review your access.
                │               We'll DM you when your role is confirmed — usually same day."
                │
                └─ Hadi or Loom reviews SOS task
                        │
                        ├─ Decides role + rank
                        ├─ Mints QNFT entry if appropriate
                        ├─ Grants Discord role via bot API
                        ├─ Channels unlock automatically (next sync cycle)
                        └─ Bot DMs user: "You're in. Welcome — see #squad-outreach"
                           Task marked complete in Squad Service.
```

The ceremony is non-blocking: the user can participate in `#general` immediately. Role unlock happens within minutes of Hadi/Loom reviewing the task, not after a queue delay.

---

## 4. Bot as Command Surface

All slash commands are registered on both the `kasra` and `mumega` bot accounts. Commands route: Discord → bot process → SOS bus (`mcp__sos__send`) → Squad Service API → response back to Discord thread. All commands are ephemeral by default (only the caller sees the response) unless the command is explicitly public (e.g., `/approve` announces to the channel).

| Command | Min Rank | Purpose |
|---|---|---|
| `/claim <bounty_id>` | bounty.min_rank | Claim a bounty; fails if already claimed or rank insufficient |
| `/bounties [project]` | observer (10) | List open bounties filtered by caller's rank and eligibility |
| `/status` | observer (10) | Show caller's tasks, open bounty claims, commission balance |
| `/ask <question>` | observer (10) | Route question to scoped AI agent for caller's role/project |
| `/complete <bounty_id> <proof>` | bounty.min_rank | Submit proof of work for a claimed bounty |
| `/approve <task_id>` | coordinator (90) | Approve a pending task or bounty completion; posts public confirmation |
| `/deny <task_id> <reason>` | coordinator (90) | Reject a submission with reason; returns to IN_PROGRESS |
| `/partner <name>` | coordinator (90) | Pull named partner's task, bounty, and commission activity |
| `/mint <slug>` | coordinator (90) | Trigger knight mint ceremony for a new customer |
| `/ceremony <discord_user>` | coordinator (90) | Manually re-trigger onboarding ceremony for a user |
| `/broadcast <message>` | gate (85) | Send message to all members of a project or squad |

Rank enforcement: every command handler calls `GET /roles/rank?discord_id=X` before executing. If rank is below threshold, bot replies with an ephemeral error and logs the attempt to the SOS bus.

---

## 5. Bounty Board Schema

```sql
CREATE TYPE bounty_type AS ENUM ('one_time', 'recurring', 'subscription_limited');
CREATE TYPE bounty_status AS ENUM ('open', 'claimed', 'in_progress', 'completed', 'expired', 'cancelled');
CREATE TYPE claim_status AS ENUM ('claimed', 'in_progress', 'completed', 'abandoned');
CREATE TYPE claimant_type AS ENUM ('person', 'squad');

CREATE TABLE bounties (
  id                  SERIAL PRIMARY KEY,
  workspace_id        INTEGER NOT NULL,
  project_id          INTEGER,                          -- NULL = universal bounty
  title               TEXT NOT NULL,
  description         TEXT,
  bounty_type         bounty_type NOT NULL,
  payout_amount       NUMERIC,
  payout_currency     TEXT DEFAULT 'CAD',
  min_rank            INTEGER DEFAULT 10,               -- maps to role rank hierarchy
  eligible_squads     TEXT[],                           -- NULL = any squad eligible
  max_claimants       INTEGER,                          -- NULL = unlimited (one_time gets 1 enforced)
  schedule_cron       TEXT,                             -- cron expr for recurring type
  active_until        TIMESTAMPTZ,                      -- NULL = no expiry
  success_criteria    JSONB,                            -- structured acceptance criteria
  posted_by           INTEGER NOT NULL,                 -- user_accounts.id
  posted_at           TIMESTAMPTZ DEFAULT now(),
  status              bounty_status NOT NULL DEFAULT 'open',
  discord_message_id  TEXT,                             -- pinned bounty message in channel
  discord_channel_id  TEXT                              -- channel where bounty is posted
);

CREATE TABLE bounty_claims (
  id              SERIAL PRIMARY KEY,
  bounty_id       INTEGER NOT NULL REFERENCES bounties(id),
  claimant_type   claimant_type NOT NULL,
  claimant_id     TEXT NOT NULL,                        -- user_accounts.id or squad slug
  claimed_at      TIMESTAMPTZ DEFAULT now(),
  status          claim_status NOT NULL DEFAULT 'claimed',
  completed_at    TIMESTAMPTZ,
  proof_of_work   TEXT,                                 -- URL, description, or file path
  verified_by     INTEGER,                              -- user_accounts.id of verifier
  verified_at     TIMESTAMPTZ,
  payout_tx_id    TEXT,                                 -- Stripe Connect transfer ID
  UNIQUE (bounty_id, claimant_type, claimant_id)        -- one claim per entity per bounty
);

CREATE TABLE bounty_recurrences (
  id              SERIAL PRIMARY KEY,
  bounty_id       INTEGER NOT NULL REFERENCES bounties(id),  -- parent template bounty
  fired_at        TIMESTAMPTZ DEFAULT now(),
  child_bounty_id INTEGER REFERENCES bounties(id)            -- the instantiated child
);
```

**Type rationale:**

- **`one_time`** — Single completion, first verified claimant wins. `max_claimants` is enforced as 1. Most cold-call, deal-close, and one-off deliverable bounties fit here. Once COMPLETED, the bounty flips to `completed` permanently.

- **`recurring`** — A cron template. The parent bounty row never moves past `open`; the cron worker fires on `schedule_cron`, creates a child bounty row (cloned from parent, new `id`), posts it to Discord, and inserts a `bounty_recurrences` record linking parent → child. Each child lives through the normal lifecycle independently. Use case: weekly content audit, monthly SEO report, bi-weekly outreach list refresh.

- **`subscription_limited`** — N independent slots. `max_claimants` is set to N. Parent stays `open` until N distinct `bounty_claims` rows exist with `status != abandoned`, at which point parent moves to `claimed`. Each claimant completes their own slot independently. Use case: "first 5 partners to run an SEO audit on a new customer get $150 each."

---

## 6. Bounty Lifecycle

```
                    ┌─────────────────────┐
                    │        OPEN         │◄──────────────────────┐
                    └──────────┬──────────┘                       │
                               │  /claim or ✋ reaction           │ abandon
                               ▼                                  │
                    ┌─────────────────────┐                       │
                    │       CLAIMED       │                        │
                    └──────────┬──────────┘                       │
                               │  begin work                      │
                               ▼                                  │
                    ┌─────────────────────┐                       │
                    │     IN_PROGRESS     │◄──── reject ──────────┤
                    └──────────┬──────────┘                       │
                               │  /complete or ✅ reaction        │
                               ▼                                  │
                    ┌─────────────────────┐                       │
                    │   PENDING_REVIEW    │                        │
                    └──────────┬──────────┘                       │
                    /approve ──┤── /deny ──────────────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │      COMPLETED      │
                    └──────────┬──────────┘
                               │  Stripe Connect transfer fires
                               ▼
                    ┌─────────────────────┐
                    │       PAID          │
                    └─────────────────────┘
```

**`recurring` variant:** Parent bounty row stays `open` indefinitely. Each cron firing creates a child bounty that traverses the full lifecycle above. The Discord channel gets a fresh message for each child; the parent message is pinned as a "standing bounty" with a link to the latest child.

**`subscription_limited` variant:** Parent stays `open` until `max_claimants` is reached (counting non-abandoned claims). Individual claim rows each traverse their own lifecycle. The Discord bounty message shows a slot counter: `Slots: 3/5 claimed`.

**Discord behavior:** The bot posts each bounty as an embed message with action buttons and auto-reacts with:
- ✋ — claim this bounty (triggers `/claim` equivalent)
- ✅ — submit completion (triggers `/complete` flow; prompts for proof in DM)
- 🔍 — request review ping (pings coordinator role)

Reaction handler validates rank before mutating state. Invalid claims (wrong rank, already claimed) respond with an ephemeral error reaction (❌) that auto-clears after 10 seconds.

---

## 7. Encapsulation Model

Every Discord channel, bounty, and task has a computed visibility derived from four fields:

```typescript
type VisibilityScope = {
  workspace_id: number
  entity_id: number | null    // null = workspace-wide
  min_rank: number            // from role rank hierarchy
  squad_slugs: string[]       // [] = not squad-gated
}
```

Discord permission sync is a Cloudflare Worker cron running every 5 minutes. It:

1. Fetches all Discord role → rank mappings from `user_accounts JOIN roles`
2. For each channel, computes the set of Discord roles that satisfy `visibility_scope`
3. Calls Discord API `PATCH /channels/{id}/permissions` for each role override that has drifted
4. Creates new channels (with correct permissions) for newly-provisioned squads/projects/entities
5. Archives (not deletes) channels for deprovisioned entities — preserves history

Result — a partner like Gavin (rank=50, squad=outreach, project=gaf) sees exactly:

- `#general`, `#announcements` (public)
- `#squad-outreach` (squad member)
- `#project-gaf-*` (project team)
- `#partners` (role channel)
- `#bounties-gaf` (GAF bounties at or above rank 50)
- `#bounties-universal` (any open bounty with min_rank ≤ 50)
- DM with Kasra (bot-initiated, not auto-visible)

Nothing else. Hadi (principal, rank=100) and Loom (coordinator, rank=90) see every channel. Channel creation is automated — when a new squad, project, or entity is provisioned in Squad Service, the sync job creates the Discord channel within 5 minutes, no manual step.

---

## 8. Interactive Work Examples

### Scenario A: Gavin's morning

Gavin opens Discord on his phone at 8am.

1. `#bounties-gaf` has 2 new bounty embeds: "Cold call — Toronto dental lead #341, $50" and "Cold call — Mississauga chiro #342, $50".
2. Gavin taps ✋ on bounty #341. Bot validates: rank=50 ≥ min_rank=40. Claim created. Embed updates to `CLAIMED — Gavin`.
3. Gavin makes the call, logs disposition in GHL. GHL webhook fires → SOS bus event → Squad Service updates the contact record.
4. Gavin types `/complete 341 "Contact signed consent form. Case #842 in GHL."` Bot opens DM: "Proof received. Submitted for review."
5. Hadi gets a `#coordinator-queue` ping: "Gavin completed bounty #341 — review needed." Hadi types `/approve 341`.
6. Bot announces in `#project-gaf-metrobit`: "Gavin completed Cold call #341 — $50 earned." Commission row written to ledger. Stripe Connect transfer scheduled. Gavin's `/status` now shows +$50 pending.

### Scenario B: Unknown user enters

1. Noor shares an invite link with a colleague from YSpace. Colleague joins, hits the OAuth flow.
2. No `user_accounts` row found for their `discord_user_id`. Bot DMs: "Welcome to Mumega. I need to place you correctly. Who referred you, what's your role here, and what are you looking to do?"
3. User replies: "Noor from YSpace referred me. I'm a real estate agent, want to understand what Mumega can do for Century 21."
4. Bot creates SOS task: `type=user_onboarding_pending, priority=high, payload={discord_id, responses, referred_by="Noor/YSpace"}`. Assigned to Hadi + Loom.
5. User sits in `#general` with observer visibility. Bot: "You're in #general while we review your access. Usually same-day."
6. Loom reviews task, decides: partner, ecosystem-lead scope, entity=Century 21. Mints QNFT entry. Grants Discord role `partner`. Next sync cycle (≤5 min): `#partners`, `#entity-century21-*`, `#bounties-universal` unlock.
7. Bot DMs user: "You're in. Welcome — your channels are live. Start in #partners."

### Scenario C: Hadi dispatches work

1. Hadi types `/mint gaf-new-customer-x` in `#coordinator-ops`. Bot triggers knight mint ceremony: forks Kaveh-template, provisions workspace, creates project channels.
2. Hadi opens `#partner-gavin` (Gavin's personal channel): "Gavin — LinkedIn cold-call list for this week. $200 bounty, 50 leads."
3. Bot detects the bounty intent, Hadi confirms with a reaction. Bot creates `bounty` row: `type=subscription_limited, max_claimants=1, payout_amount=200, min_rank=40, project_id=gaf`. Posts embed to `#bounties-gaf`.
4. Gavin sees the embed, taps ✋, claims. The 1/1 slot fills. Parent moves to `claimed`. Gavin works. Submits proof. Standard lifecycle from there.

---

## 9. Integration with Existing Phases

| Phase / Section | Integration Point |
|---|---|
| **Section 1 — Role registry** | `roles.rank` (10–100) maps directly to Discord role hierarchy. Every Discord role grant is a write-through from the registry — Discord is never the source of truth for rank. |
| **Section 3 — Structured records** | `user_accounts.discord_id` column (added in Section 9 migration) links Discord identity to the structured record. Contact, opportunity, and commission records join through this key. |
| **Section 4A — Partner workspace** | Web dashboard and Discord are two surfaces on the same Squad Service API. A partner's task list, bounty claims, and commission balance render identically in both. No separate sync — they read the same rows. |
| **Section 4B — Chat primitive** | Discord threads and web-side threads share the same backend thread model. A coordinator reply in Discord and one in the web UI both appear in the thread history. |
| **Section 4C — Discord provisioning** | Section 4C covers basic bot setup and role seeding. Section 9 extends it to the full command center: slash commands, bounty board, ceremony flow, permission sync cron, and reaction handlers. Section 9 is additive — it does not replace 4C. |
| **Phase 4 — Datalake** | Every bounty lifecycle event (`claimed`, `completed`, `paid`, `abandoned`) fires an event to the SOS bus. The datalake ingests these alongside GHL webhooks, contact events, and opportunity changes — giving Hadi a unified activity stream per partner and per project. |
| **Section 7 — Node primitive** | Bounties are currently modeled as rows in `bounties`. If the node primitive matures and graphs become the canonical data model, bounty rows could become nodes with `type=bounty` and edges to `person`, `squad`, `project`. This is deferred — no action in Phase 2.5. |

---

## 10. Build Order (Phase 2.5, ~7 days)

| # | Item | Owner | Days | Notes |
|---|---|---|---|---|
| 1 | `user_accounts.discord_id` column + Discord OAuth callback endpoint | Kasra | 1 | Adds `discord_id TEXT UNIQUE` to existing `user_accounts` table; callback at `/auth/discord/callback` in mumega-edge Worker |
| 2 | First-time ceremony flow — bot DM + SOS task creation | Kasra + bot subagent | 1 | Bot listens for guild join event; DM template; `mcp__sos__task_create` call |
| 3 | Channel permission sync cron (Cloudflare Worker, 5-min interval) | Kasra | 1 | Reads rank registry + squad membership; diffs against Discord channel overwrites; applies patches |
| 4 | Bounty schema migration + Squad Service API (`/bounties`, `/bounty-claims`) | Kasra | 1 | SQL DDL from Section 5; REST endpoints: `POST /bounties`, `GET /bounties`, `POST /bounty-claims`, `PATCH /bounty-claims/:id` |
| 5 | Bot slash commands — `/claim`, `/bounties`, `/status`, `/ask`, `/complete`, `/approve`, `/deny` | bot subagent | 1 | Register commands via Discord API; each command validates rank then calls Squad Service |
| 6 | Recurring bounty cron worker | Kasra | 0.5 | Cloudflare Worker cron; queries `bounties WHERE bounty_type='recurring' AND status='open'`; fires child creation on schedule |
| 7 | Discord ↔ bus bridge enhancement — reaction handler → state machine | Kasra | 1 | Map ✋/✅/🔍 reactions to `claim`/`complete`/`review_request` transitions in Squad Service |
| 8 | Payout flow — Stripe Connect transfer on bounty completion | Kasra | 1 | `POST /payouts` in Squad Service; calls Stripe Connect transfer API; writes `payout_tx_id` to `bounty_claims`; updates commissions ledger |

Total: ~7.5 days. Parallelizable: items 5 and 6 can run concurrently with item 4 once the schema is merged.

---

## 11. Open Questions

1. **`subscription_limited` slot payouts** — Does each slot carry the same `payout_amount`, or can individual slots have different amounts? Current schema has one `payout_amount` on the parent. If variable, `bounty_claims` needs an optional `payout_override` column.

2. **`/ask` routing** — How does `/ask <question>` know which agent to route to? Leaning: map caller's highest-rank role to a scoped agent (e.g., `partner` → Kasra-fork, `customer` → Kaveh-fork, `observer` → public Kasra). Needs a `role → agent_slug` routing table in the registry.

3. **Squad bounty payout split** — When a squad claims a bounty and one member does the work, does payout go to the member's personal ledger or the squad's shared wallet? Squad wallet is not yet modeled. Interim: payout goes to the submitting member (`claimant_id` in `bounty_claims` defaults to the member who ran `/complete`).

4. **Unknown-user default role** — Should the ceremony auto-assign `observer` rank (10) immediately so the user can interact with `#general` bots and commands, or hold them in pure purgatory (no rank, no commands) until manual review? Leaning: auto-assign `observer` on join — gives them `/ask` access and `#general` participation while the review happens.

5. **Cross-workspace bounties** — A universal content-writing bounty should be claimable by partners across any project. Current schema has `workspace_id NOT NULL`. Options: (a) a `universal` workspace row that all projects inherit from, (b) nullable `workspace_id` with a `is_universal` flag, or (c) a separate `universal_bounties` table. Decision deferred to Section 9 implementation sprint.

---

*Each section above is dense and concrete. This spec is self-contained — no other files required to begin implementation. Start with item 1 (OAuth + discord_id column) and item 4 (bounty schema) in parallel; everything else gates on those two.*
