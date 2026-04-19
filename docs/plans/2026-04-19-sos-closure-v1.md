# SOS v1.0.0 "Closure" — The Three-Layer Consolidation

**Date:** 2026-04-19
**Author:** loom (mumega-launch squad)
**Squad:** mumega-launch (loom + kasra + hermes + codex)
**Reference:** `docs/plans/2026-04-19-mumega-mothership.md` · `sos/squads/mumega-launch/charter.md`
**Replaces:** Option A/B/C fork in conversation 2026-04-19

---

## 1. Why this plan exists

Hadi's rule, 2026-04-19:

> "I want to never open SOS for a while and focus on product. Do not patch."

That rule changes the shape of the work. Not a minimum-hardening patch, not a greenfield rewrite. A **deliberate closure**: freeze SOS at a known-good shape (v1.0.0) that is **inhabitable for months without maintenance** — move all product logic to Inkwell + Mumega, give Hadi a three-to-six-month window where SOS is read-only.

Borrowed framing from [egregore-labs/egregore](https://github.com/egregore-labs/egregore): the goal of this closure is an *inhabitable production environment*, not a feature release. Inhabitable = a squad can live inside it indefinitely without the owner patching the walls.

## 2. Processed discoveries (2026-04-19)

Five symptoms, one root cause.

| # | Observed today | Root cause |
|---|---|---|
| 1 | Three bus messages to kasra dropped silently; payload field arrived empty | No shared envelope module — `sos/mcp/sos_mcp_sse.py:sos_msg()` and `scripts/bus-send.py` each reimplement; inline raw-Redis callers drift |
| 2 | Kasra acked the squad via a side-channel (Hadi's relay into claude.ai) rather than the bus | Internal vs external comms leak — agents still reach for the external surface when internal transport is unreliable |
| 3 | Hermes alive as PID 1801264 since Apr 17 but not on bus; zero messages received | No built-in bus consumer in hermes-agent — every new agent runtime re-implements |
| 4 | Inkwell MCP returns `network_required` on `create_task`/`remember` | Inkwell re-implements instead of consuming SOS SDK — has its own KV memory shim, no-op bus, disconnected task store |
| 5 | AGENT_NAME=UNSET in all 3,588 token-accounting rows today | No cross-substrate identity discovery; env-var convention drifts per runtime |

**One root cause:** SOS, Inkwell, and Mumega are entangled. Each patches around the others' gaps instead of consuming the layer below as a typed SDK. Every new agent runtime reimplements bus, identity, task board. The closure separates them.

## 3. The three layers (contract, not suggestion)

### 3.1 SOS — microkernel

**Scope:** substrate-agnostic coordination primitives.

- Bus: Redis streams + canonical envelope + XACK-backed ack/retry + trace_id
- Contracts: `Squad`, `SquadMember`, `SquadRole`, `SquadTask`, `Objective`, `AgentCard`, `UsageEvent`, `BusMessage` (all pydantic, versioned, frozen)
- Kernel gates: policy, arbitration, heartbeat, economy settlement
- Mesh: Ed25519-signed enrollment, 5m/15m heartbeat pruner
- Economy: UsageEvent ingest, WorkSettlement distribution, $MIND wallet
- SDKs: `sos/clients/*.py` (Python, exists); `@mumega/sos-client` (TS, new)

**Explicit non-scope:** no CRM, no onboarding flow, no SEO pages, no Stripe integration, no tenant provisioning UX, no content strategy, no marketing automation. Those belong to Inkwell or Mumega.

**Closure test:** `lint-imports` bans `redis` outside `sos/bus/` and `sos/services/*/`. Inkwell and Mumega source trees cannot import redis directly.

### 3.2 Inkwell — microkernel of the SaaS template

**Scope:** forkable product shell.

- `instances/_template/` — CF Pages deploy target per tenant
- 24 plugins (CMS, SEO, CRM, analytics, media, etc.)
- 41 MCP tools at `inkwell.mumega.com/mcp` (content, CRM, SEO, automation)
- Tenant config (`inkwell.config.ts`)
- Tenant-facing auth (site-level sessions, not SOS identity)
- Stripe → tenant's own account
- Bus SSE consumer (via `@mumega/sos-client`)

**Explicit non-scope:** never imports redis, never re-implements bus/mesh/economy, never speaks the bus envelope directly. Consumes SOS SDK or nothing.

**Today's gap:** Inkwell's `create_task`/`remember` MCP tools return `network_required` because they were stubbed instead of wired to `sos-client.task.create()` / `sos-client.memory.remember()`. Fix = wire them through SDK. No new contracts needed.

### 3.3 Mumega — storefront + flagship tenant

**Two surfaces under one brand:**

1. **Consumer:** `mumega.com` — an Inkwell instance named "mumega", flagship dogfood of the template. Same plugins, same MCP tools. Proves the shelf unit.
2. **Commerce:** `api.mumega.com` + the marketplace surface — lists SOS automations, Inkwell templates, and skill bundles; prices them; sells to tenants; routes settlement through SOS economy.

**Mental model:** Mumega is the shop. Inkwell is a shelf unit you can pick up and take home. SOS is the warehouse.

**Today's gap:** api.mumega.com topology (ml-003 in squad charter) — three-way decision (inkwell-api vs mumega-edge vs split-by-path) still open. Decides at the end of this plan, not inside SOS work.

### 3.4 Hermes — operator

**Scope:** consumer of SOS SDK, executor of ops.

- Owns prod CF routes, wrangler deploys, D1 remote state, `*.mumega.com/*` binding
- Reads bus via `hermes-agent/gateway/sos_bus.py` (built today by subagent, pending 6 flagged gaps)
- Home Channel: Discord `#agent-collab` today (bridged); Telegram deferred per charter
- Does not build SOS features
- Raises `ops.blocker` events on the bus when prod state would be touched; waits for squad ack before acting

**Today's gap:** consumer not wired; `redis>=5.0` missing from hermes-agent pyproject; no home channel configured. All six gaps flagged in subagent report — included in this plan.

## 4. SOS changes — the closure work itself

Tier 1 = blocks bus-only ops. Tier 2 = stability so SOS doesn't need reopening. Tier 3 = TS SDK + ops surface. Tier 4 = migration + closure lock.

### Tier 1 — bus-only ops unblock (2 days)

**T1.1 — Canonical bus envelope module**
- File: `/mnt/HC_Volume_104325311/SOS/sos/bus/envelope.py` (new)
- Extract from `sos/mcp/sos_mcp_sse.py:239:sos_msg()`; export `encode_chat(source, target, text) -> dict`, `decode_chat(entry) -> BusMessage`, `decode_tolerant(entry) -> BusMessage` (non-JSON payload → `{text: payload}`)
- Update `scripts/bus-send.py` to import from it (remove duplicate)
- Update all receivers (`sos/bus/bridge.py:221`, `sos/mcp/sos_mcp_sse.py`, hermes `gateway/sos_bus.py`) to use `decode_tolerant`
- Contract test: every `xadd` to `sos:stream:sos:channel:*` must round-trip via decoder
- Closes: BUS-HARDEN-001

**T1.2 — Wire `task.completed` → `WorkSettlement.settle`**
- File: `/mnt/HC_Volume_104325311/SOS/sos/services/squad/tasks.py` (modify `_emit_task_completed`)
- On task completion with `bounty != None` and `bounty.mind > 0`: emit `bounty.settle_request` bus event with task_id + claimant + bounty split
- File: `/mnt/HC_Volume_104325311/SOS/sos/services/economy/settlement_consumer.py` (new) — subscribe to `bounty.settle_request`, call `WorkSettlement.settle()` (worker 75% / observer 10% / staker 10% / energy 5%), emit `bounty.settled`
- Contract test: task created with bounty → claimed → completed → settlement record appears within 2s

**T1.3 — Structured done-definition**
- File: `/mnt/HC_Volume_104325311/SOS/sos/contracts/objective.py` (modify)
- Add `done_when: list[DoneCheck]` where `DoneCheck = {id, text, done, acked_by, acked_at}`
- File: `/mnt/HC_Volume_104325311/SOS/sos/contracts/squad.py` (modify `SquadTask`)
- Same `done_when` field on `SquadTask`
- File: `/mnt/HC_Volume_104325311/SOS/sos/services/squad/app.py` — block `POST /tasks/{id}/complete` unless all `done_when.done == True`
- Migration: free-text `completion_notes` stays; new field additive

**T1.4 — Skill-based task routing**
- File: `/mnt/HC_Volume_104325311/SOS/sos/services/squad/app.py:route_task` (modify)
- If `task.assignee is None`: fetch live squad members via registry, match `task.labels` against `SquadRole.skills`, assign highest-conductance match
- Fall through to `unassigned` if no match (existing behaviour)

**T1.5 — Board view**
- File: `/mnt/HC_Volume_104325311/SOS/sos/services/squad/app.py` (new endpoint)
- `GET /tasks/board?squad=<slug>` — returns tasks scored `priority*10 + blocks*5 + age_hours*2`, grouped by status, with resolved assignee name
- Read-only, no state change

### Tier 2 — stability so you don't have to reopen (2 days)

**T2.1 — DM ack-or-retry parity**
- File: `/mnt/HC_Volume_104325311/SOS/sos/bus/delivery.py` (modify)
- Extend v0.9.1 XACK retry pattern from squad broadcast streams to `sos:stream:sos:channel:private:agent:*`
- Dead-letter queue per agent at `sos:dlq:<agent>` after 3 failed retries; surfaced via existing dashboard DLQ route

**T2.2 — Expose task dependency graph**
- File: `/mnt/HC_Volume_104325311/SOS/sos/services/squad/app.py`
- `GET /tasks/{id}/deps` → returns transitive closure of `blocked_by` + `blocks`
- Deadlock detection: cycle in closure → mark all tasks `BLOCKED` with reason

**T2.3 — Escalation automation on stale heartbeat**
- File: `/mnt/HC_Volume_104325311/SOS/sos/services/registry/pruner.py` (modify)
- On stale-mark (5m): emit `agent.stale` to squad channel; squad handles (not Hadi)
- On remove (15m): emit `agent.offline`; if agent held a `CLAIMED` task, auto-release back to `QUEUED`

**T2.4 — Crew primitive**
- File: `/mnt/HC_Volume_104325311/SOS/sos/contracts/squad.py` (modify)
- Add `Crew` type: `{id, squad_id, name, members: list[agent_id], purpose, created_at}`
- Role-scoped subset of squad members for a specific objective
- Endpoints: `POST /squads/{id}/crews`, `GET /crews/{id}/board` (board view filtered to crew members)

**T2.5 — Git-backed squad memory (egregore-inspired)**
- New dirs per squad: `sos/squads/<slug>/{memory,decisions,handoffs}/` — git-tracked markdown
- File: `/mnt/HC_Volume_104325311/SOS/sos/services/squad/git_memory.py` (new)
- On `remember(scope="squad:<slug>", ...)` → Redis write + append to `memory/YYYY-MM-DD.md`
- On squad decision (new `decision.recorded` bus event) → write `decisions/YYYY-MM-DD-NNN-<slug>.md` with `{decision, rationale, trade-offs, ack_by}`
- On `task.handoff` event → write `handoffs/<task_id>.md` with `{from, to, context, resume_at}`
- Fast path stays Redis (bus + inbox); durable path is git (survives Redis loss, human-readable, provenanced)
- Rationale: inhabitable environments need durable memory that outlives the substrate

### Tier 3 — consumption surfaces (3 days)

**T3.1 — TypeScript SOS client**
- New package: `/mnt/HC_Volume_104325311/SOS/packages/sos-client-ts/` (published as `@mumega/sos-client`)
- Mirrors `sos/clients/*.py` shape: `SosClient` with `bus.*`, `task.*`, `objective.*`, `economy.*`, `memory.*`, `mesh.*`
- SSE transport for CF Workers (Hermes, Inkwell, mumega-edge)
- Bundle size target: <80 KB gzipped
- Types generated from existing Python pydantic models via `pydantic-to-typescript`

**T3.2 — SSE bus adapter for CF Workers**
- File: `/mnt/HC_Volume_104325311/SOS/workers/sos-bus-sse/` (existing, extend)
- Expose `sos.mumega.com/bus/sse?token=<bus_token>` streaming peer-bus + squad channels
- Token minted via existing `sos/bus/tokens.json` flow
- Workers authenticate once per session, stream events

**T3.3 — SOS ops dashboard (read-only)**
- URL: `sos.mumega.com/ops`
- Shows: mesh members (live/stale/offline), squad boards, task flow, bounty ledger, usage-event rate, DLQ depth, pulse schedule
- Read-only — no mutation UI, Hadi checks without touching code
- Stack: Inkwell instance "sos-ops" consuming SOS SDK (dogfood)

### Tier 4 — migration + closure lock (3 days)

**T4.1 — Inkwell cuts raw-Redis**
- Repo: `/home/mumega/inkwell/`
- Replace Inkwell's KV memory shim, no-op bus, disconnected task store with `@mumega/sos-client` calls
- Owner: kasra
- Gate: Inkwell's 41 MCP tools all pass contract test against live SOS

**T4.2 — Mumega-edge uses SDK only**
- Repo: `/home/mumega/mumega-edge/`
- All bus interactions via `@mumega/sos-client`
- No direct redis imports
- Owner: loom

**T4.3 — Hermes consumer ships**
- File: `/home/mumega/.hermes/hermes-agent/gateway/sos_bus.py` (built by subagent today, 6 gaps flagged)
- Add `redis>=5.0,<6` to hermes-agent pyproject
- Configure Discord Home Channel (`#agent-collab` already bridged)
- Wire `post_reply()` shim into hermes response pipeline
- Owner: hermes, assisted by codex

**T4.4 — Lint-imports closure**
- File: `/mnt/HC_Volume_104325311/SOS/.importlinter`
- Rule: `redis` package may only be imported from `sos.bus.*`, `sos.services.*`, `sos.mcp.*`
- Contract test in CI: green or PR blocks

**T4.5 — Release v1.0.0**
- CHANGELOG.md — "SOS Closure"
- Version bump in `pyproject.toml` + `sos/__init__.py`
- Tag `v1.0.0`
- GH release notes point at this plan + done-definition

## 5. Inkwell changes

No new features. Remove duplicated primitives.

- I5.1 — replace KV memory shim with `sosClient.memory.remember/recall` (T4.1)
- I5.2 — replace no-op bus stub with `sosClient.bus.publish/subscribe` (T4.1)
- I5.3 — wire `create_task` MCP tool to `sosClient.task.create`, drop `network_required` error (ml-002 closes)
- I5.4 — Inkwell MCP tool registry publishes capability list to SOS mesh on boot (so skill-based routing works)
- I5.5 — tenant signup webhook → SOS `spawn_tenant(slug)` task (existing `sos init` flow)

## 6. Mumega changes

- M6.1 — `api.mumega.com` topology decision (ml-003) — three-way squad agreement; runbook authored by loom, acked by hermes + kasra BEFORE any CF route flip
- M6.2 — marketplace backend consumes SOS economy via SDK: lists `SkillCard` inventory, prices from tags, settles via `WorkSettlement`
- M6.3 — Mumega storefront site = Inkwell instance "mumega" (already built per kasra status); no separate codebase
- M6.4 — Stripe webhook on Mumega side → `UsageEvent` with `tenant_id=mumega`, `cost_micros=<price>` → economy settles into $MIND wallet

## 7. Hermes changes

- H7.1 — merge subagent's consumer file, address the 6 gaps (redis dep, home channel, reply shim, cold-start, consumer groups, squad-stream sanity)
- H7.2 — subscribe to `sos:channel:squad:mumega-launch` + `sos:channel:private:agent:hermes` + `sos:channel:ops.alerts`
- H7.3 — emit `ops.ack_required` whenever a squad task lands on prod CF routes or wrangler; block until squad acks
- H7.4 — hermes pyproject adds `redis>=5.0,<6` + `@mumega/sos-client` (if TS path chosen)

## 8. Done-definition (closure criteria)

SOS v1.0.0 ships when:

- [ ] All Tier 1+2+3+4 steps complete
- [ ] `lint-imports` contract green: no raw redis outside SOS
- [ ] Inkwell MCP tools pass contract test against live SOS
- [ ] Hermes consumer processes a roundtrip squad message (loom → bus → hermes → ops.ack → loom) end-to-end
- [ ] `sos.mumega.com/ops` shows live mesh + squad board
- [ ] Bounty settlement verified: squad task created with $MIND bounty → claimed → completed → WorkSettlement record
- [ ] `v1.0.0` git tag + CHANGELOG + GH release
- [ ] Squad members (loom, kasra, hermes, codex) each post `ack: closure` to squad bus channel

After closure, this file becomes the reference for what SOS commits to NOT breaking. Any change that touches a closed primitive reopens SOS — avoid.

**Inhabitability test:** a new agent joins the squad with only the SDK + the squad's git dir (`sos/squads/<slug>/`). They read `charter.md` + `memory/*.md` + `decisions/*.md` + their inbox on the bus, and can pick up work without the owner's help. If that works, SOS is inhabitable.

## 9. After closure — product focus window

Hadi's directive: "I want to never open SOS for a while and focus on product."

Post-closure product work (Inkwell + Mumega only):

1. Mumega v1.0 launch — Phase 4 cutover, Phase 8 onboarding of the 8 projects
2. Marketplace fill — 20 skills listed with prices, 3 templates, bounty flywheel
3. Tenant onboarding polish — `sos init <slug>` → live Inkwell tenant in <5 min
4. Growth: content + SEO + CRM on Inkwell plugins

SOS-side work during this window is maintenance only: security patches, bug fixes with failing tests, no new surface. Triage via squad bus channel; if a gap can't be fixed in Inkwell/Mumega layer, squad decides whether it justifies reopening SOS. Default is no.

## 10. Timeline

| Day | Work | Owner |
|---|---|---|
| 1 | T1.1–T1.5 (Tier 1 wiring) | loom |
| 2 | Tier 1 tests + merge | loom + codex |
| 3 | T2.1–T2.4 (Tier 2 stability) | loom |
| 4–5 | T3.1 (TS SDK) | codex |
| 5 | T3.2 + T3.3 (SSE + ops dash) | loom + kasra |
| 6 | T4.1 (Inkwell migration) | kasra |
| 7 | T4.2 + T4.3 (mumega-edge + hermes) | loom + hermes |
| 8 | T4.4 + T4.5 (lint-imports + release) | loom |
| 9 | Closure ack from squad; product focus window opens | all |

Total: ~2 weeks for a sharp squad, one sprint. Closure tag on day 9 if no blockers.

## 11. Bounty

**B1 — Ship Mumega v1.0** — assigned to mumega-launch squad, settles on v1.0.0 tag (via Mumega, not SOS).
**B2 — SOS closure v1.0** — also assigned to mumega-launch squad, settles on SOS `v1.0.0` tag after this plan's done-definition passes.

Both claimed by the same squad. Self-referential: the squad hardens the primitive it runs on.

Bounty split (per `WorkSettlement`): worker 75% among loom/kasra/hermes/codex by conductance contribution; observer 10% to Hadi; staker 10% to the $MIND pool; energy 5% to substrate (Hetzner + CF + Anthropic/OpenAI). Settled by T1.2's economy consumer once live.

---

**Sign-off gate:** Hadi picks this plan or amends it. Once chosen, squad acks on bus channel `squad:mumega-launch` and work begins.
