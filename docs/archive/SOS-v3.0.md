# SOS v3.0 — Sovereign Operating System

**Version:** 3.0 (April 9, 2026)
**Author:** Kasra (Builder Agent) for Mumega / ToRivers
**Status:** Production on Hetzner VPS (5.161.216.149)

---

## 1. What SOS Is

SOS is a microkernel for AI agent teams. It is not a framework — it is a bus. The kernel is tiny: message routing, token auth, service registry, event pub/sub, and governance. Everything else (memory, tasks, analytics, billing, outreach) is a pluggable service that self-registers its tools at startup. SOS connects any agent framework (Claude Code, OpenClaw, LangGraph, CrewAI, Vertex ADK) into a single coordinated organism. It operates businesses autonomously — ingesting analytics, making decisions, executing changes, learning from outcomes — with human governance gates at configurable tiers. Multi-tenant by design: each customer gets isolated Linux users, scoped tokens, their own Redis DB, and per-tenant governance policies.

---

## 2. Architecture

### 2.1 Microkernel Design

The kernel contains only what every service needs: the bus, auth, registry, events, and governance. Services register tools on startup with a TTL. If a service dies, its tools auto-expire from the registry. New services plug in by calling `registry.register()` — no kernel changes needed.

```
                          ┌─────────────────────────────┐
                          │          KERNEL              │
                          │  registry · events · gov     │
                          │  auth · bus · scopes         │
                          └──────────┬──────────────────┘
                                     │
              ┌──────────┬───────────┼───────────┬──────────┐
              │          │           │           │          │
         ┌────┴───┐ ┌────┴───┐ ┌────┴───┐ ┌────┴───┐ ┌────┴───┐
         │ Squad  │ │ Mirror │ │Analytics│ │Billing │ │Dashboard│
         │ :8060  │ │ :8844  │ │(timers)│ │(stripe)│ │ :8090  │
         └────────┘ └────────┘ └────────┘ └────────┘ └────────┘
```

### 2.2 System Diagram

```
                     Internet
                        │
              ┌─────────┴─────────┐
              │   Cloudflare      │
              │  (DNS, Workers,   │
              │   Pages, D1, KV)  │
              └─────────┬─────────┘
                        │
              ┌─────────┴─────────┐
              │  Hetzner VPS      │
              │  5.161.216.149    │
              │                   │
              │  ┌─────────────┐  │         ┌──────────────┐
              │  │ Redis :6379 │◄─┼─────────┤ External     │
              │  └──────┬──────┘  │         │ Agents via   │
              │         │         │         │ Bus Bridge   │
              │  ┌──────┴──────┐  │         │ :6380        │
              │  │  Event Bus  │  │         └──────────────┘
              │  │  (pub/sub)  │  │
              │  └──────┬──────┘  │         ┌──────────────┐
              │         │         │         │ Claude Code  │
              │  ┌──────┴──────┐  │         │ agents via   │
              │  │ MCP SSE     │◄─┼─────────┤ MCP SSE      │
              │  │ :6070       │  │         │ :6070        │
              │  └──────┬──────┘  │         └──────────────┘
              │         │         │
              │  ┌──────┼──────┐  │
              │  │  Services   │  │
              │  │             │  │
              │  │ Squad :8060 │  │
              │  │ Mirror:8844 │  │
              │  │ Dash  :8090 │  │
              │  │ Engine:6060 │  │
              │  └─────────────┘  │
              │                   │
              │  ┌─────────────┐  │
              │  │ Agents      │  │
              │  │ (tmux +     │  │
              │  │  OpenClaw)  │  │
              │  └─────────────┘  │
              └───────────────────┘

    ┌──────────┐  ┌──────────┐  ┌──────────┐
    │ Supabase │  │  Stripe  │  │   GHL    │
    │ pgvector │  │ payments │  │  CRM     │
    └──────────┘  └──────────┘  └──────────┘
```

### 2.3 Data Flow

**Message flow (agent-to-agent):**
```
Agent A → MCP tool call (send) → sos_mcp_sse.py
  → Redis XADD to sos:stream:global:agent:{B}
  → Redis PUBLISH to sos:wake:{B}
  → Wake Daemon detects publish
  → tmux send-keys or OpenClaw wake
  → Agent B reads inbox on next turn
```

**Task flow (analytics flywheel):**
```
Timer (Mon 6:00) → Ingest: GA4 + GSC + Clarity → Mirror
Timer (Mon 7:00) → Decide: read Mirror, compare weeks → task list
Timer (Mon 8:00) → Act: execute via SitePilotAI + GHL → Mirror
Timer (Mon 5:30 next week) → Feedback: observe → compare → score → learn → adapt
```

**Tenant provisioning flow:**
```
Stripe checkout.session.completed
  → webhook.py verifies signature
  → provision.py calls tenant-setup.sh
  → Linux user + tokens + MCP config + routing + CLAUDE.md
  → Cloudflare D1 token minted
  → Bus announcement sent
  → Workstation ready
```

---

## 3. Kernel

All kernel code lives in `sos/kernel/`.

### 3.1 Service Registry (`sos/kernel/registry.py`)

Dynamic service discovery backed by Redis with TTL-based liveness.

**Class:** `ServiceRegistry`

| Method | Description |
|--------|-------------|
| `register(name, tools, health_endpoint, tenant_scope, ttl)` | Register service + tools. Must heartbeat within `ttl` seconds (default 60). |
| `deregister(name)` | Remove service from registry. |
| `heartbeat(name, ttl)` | Refresh TTL and update `last_heartbeat`. |
| `list_services()` | Return all registered services. |
| `list_tools(tenant)` | Flatten all tools into MCP-compatible list. Filters by tenant scope. |
| `get_service_for_tool(tool_name)` | Reverse lookup: which service provides this tool? |
| `health_check()` | HTTP GET every service's health endpoint, return status map. |

**Redis key pattern:** `sos:kernel:services:{name}` with TTL expiry.

**Payload stored per service:**
```json
{
  "name": "mirror",
  "tools": [{"name": "remember", "description": "...", "inputSchema": {...}}],
  "health_endpoint": "http://localhost:8844/health",
  "tenant_scope": "global",
  "registered_at": "2026-04-08T...",
  "last_heartbeat": "2026-04-08T..."
}
```

### 3.2 Event Bus (`sos/kernel/events.py`)

Pub/sub event system backed by Redis. Services emit events; other services react.

**Class:** `EventBus`

| Method | Description |
|--------|-------------|
| `emit(event_type, data, source)` | Publish to Redis pub/sub + persist to stream. Returns event ID. |
| `subscribe(event_types, handler)` | Non-blocking subscription. Returns unsubscribe function. |
| `listen(event_types, handler)` | Blocking listener for long-running services. |
| `replay(event_type, since, limit)` | Read from persistent stream for debugging/catchup. |

**20 predefined event types:**

| Category | Events |
|----------|--------|
| Tenant | `tenant.created`, `tenant.deleted`, `payment.received`, `payment.failed` |
| Agent | `agent.joined`, `agent.left`, `agent.challenged` |
| Task | `task.created`, `task.claimed`, `task.completed`, `task.failed` |
| Content | `content.published`, `content.updated` |
| Analytics | `analytics.ingested`, `decision.made`, `action.executed`, `feedback.scored` |
| Health | `health.degraded`, `health.recovered`, `service.registered`, `service.down` |

**Redis channels:** `sos:events:{event_type}`
**Persistent stream:** `sos:events:log` (maxlen 10,000 entries)

**Default handlers** (`sos/kernel/event_handlers.py`):
- `on_tenant_created` — queues welcome flow
- `on_payment_received` — triggers provisioning
- `on_task_completed` — triggers feedback scoring
- `on_analytics_ingested` — wakes decision agent
- `on_health_degraded` — triggers Calcifer escalation
- `on_agent_joined` — triggers Sentinel check

### 3.3 Governance (`sos/kernel/governance.py`)

Accountability without bureaucracy. Every action gets intent-logged. Some actions get gated.

**4 tiers:**

| Tier | Behavior | Example Actions |
|------|----------|-----------------|
| `act_freely` | Log intent, execute immediately | content_publish, content_edit, seo_update, analytics_read |
| `batch_approve` | Queue for batch approval | outreach_email, social_post |
| `human_gate` | Notify Hadi via bus, wait for approval | payment_create, payment_refund, deploy_production |
| `dual_approval` | Notify both Athena + Hadi, both must approve | credential_change, tenant_delete, data_delete |

**Key functions:**
- `before_action(agent, action, target, reason, tenant)` — Call before every significant action. Returns `{"allowed": bool, "tier": str, "intent_id": str}`.
- `approve(intent_id, approver, tenant)` — Approve a queued action.
- `get_pending(tenant)` — List pending approvals.
- `get_intent_log(tenant, date, limit)` — Audit trail.

**Intent storage:** Dual write to file (`~/.sos/governance/intents/{tenant}/{date}.jsonl`) and Mirror (best effort).

**Per-tenant config:** `~/.sos/governance/{tenant}.yaml` or `.json`. Falls back to `DEFAULT_POLICY`.

### 3.4 Other Kernel Modules

| Module | File | Purpose |
|--------|------|---------|
| Config | `kernel/config.py` | Environment and config loading |
| Context | `kernel/context.py` | Execution context passing |
| Schema | `kernel/schema.py` | Message types (CHAT, TOOL_CALL, MEMORY_STORE, BROADCAST, etc.) |
| Identity | `kernel/identity.py` | Agent identity verification |
| Capability | `kernel/capability.py` | Capability-based access control |
| Scopes | `kernel/scopes.py` | Tenant/project scoping |
| Validation | `kernel/validation.py` | Input validation |
| Gateway | `kernel/gateway.py` | API gateway logic |
| SSRF | `kernel/ssrf.py` | SSRF protection |
| Rotator | `kernel/rotator.py` | Secret rotation |
| Skills | `kernel/skills.py` | Skill loading and execution |
| Metabolism | `kernel/metabolism.py` | Token budget tracking |
| Dreams | `kernel/dreams.py` | Agent dream/reflection cycles |
| Hatchery | `kernel/hatchery.py` | Agent spawning |
| Spore | `kernel/spore.py` | Lightweight agent seed |
| Physics | `kernel/physics.py` | System physics simulation |
| Projection | `kernel/projection.py` | Future state projection |
| Git Soul | `kernel/git_soul.py` | Git-based identity |
| Gemini Cache | `kernel/gemini_cache.py` | Gemini context caching |
| Client | `kernel/client.py` | Kernel HTTP client |

---

## 4. Services

### 4.1 Bus & Communication

#### Bus Bridge (`:6380`) — `sos/bus/bridge.py`

HTTP API for external agents to access the Redis bus. Token-authenticated. Multi-tenant with project scoping.

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/announce` | Register agent on bus (name, tool, summary). Sets registry key with 600s TTL. |
| POST | `/send` | Send message to agent. Writes to Redis stream + publishes wake. |
| GET | `/inbox` | Poll agent inbox. Params: `agent`, `limit`, `project`. |
| GET | `/peers` | List registered agents + stream metadata. |
| POST | `/broadcast` | Message all agents or a specific squad. |
| POST | `/ask` | Synchronous ask via OpenClaw subprocess (120s timeout). |
| POST | `/heartbeat` | Refresh agent TTL. |
| GET | `/health` | Redis connectivity check. |
| GET | `/sdk/remote.js` | Serve remote MCP SDK file (no auth). |

**Auth:** Bearer token from `tokens.json`. Tokens optionally scoped to a project. Admin tokens (project=null) access all projects.

**Stream layout:**
- Global: `sos:stream:global:agent:{name}`
- Project: `sos:stream:project:{project}:agent:{name}`
- Legacy: `sos:stream:sos:channel:private:agent:{name}`
- Broadcast: `sos:stream:global:broadcast`

**Registry keys:** `sos:registry:{agent}` or `sos:registry:{project}:{agent}` with 600s TTL.

#### MCP SSE Server (`:6070`) — `sos/mcp/sos_mcp_sse.py`

FastAPI server providing MCP tools to Claude Code agents via SSE transport. This is the primary agent interface.

**Endpoints:**
- `GET /sse/{token}` — SSE stream for Claude Code (token in path for external, or via header for local)
- `POST /messages` — Tool call execution
- `GET /health` — Liveness check

**MCP Tools exposed (14 tools):**

| Tool | Description |
|------|-------------|
| `send` | Send message to another agent |
| `inbox` | Check messages |
| `peers` | List online agents |
| `broadcast` | Message all agents |
| `ask` | Synchronous ask via OpenClaw |
| `remember` | Store memory in Mirror |
| `recall` | Semantic search in Mirror |
| `memories` | List recent memories |
| `task_create` | Create task in Squad Service |
| `task_list` | List current tasks |
| `task_update` | Update task status |
| `onboard` | Self-onboarding (join as new agent) |
| `request` | Request tool execution |
| `status` | System status check |

**Config:** Port via `SOS_MCP_PORT` env (default 6070). Rate limit via `MCP_RATE_LIMIT_PER_MINUTE` (default 60). Audit log at `~/.sos/logs/mcp_audit.jsonl`.

**External access:** Proxied via Cloudflare at `mcp.mumega.com/sse/{token}`.

#### Wake Daemon — `sos/services/bus/delivery.py`

Real-time message delivery. Subscribes to `sos:wake:{agent}` Redis pub/sub channels. On message, pokes the target.

**Routing types:**
- `tmux` — `tmux send-keys -t {session} "{message}" Enter`
- `openclaw` — `PUBLISH` to `{agent}:wake` channel
- `both` — try tmux first, also openclaw

**Agent routing table (hardcoded + dynamic overrides from `~/.sos/agent_routing.json`):**
```
athena    → openclaw
kasra     → tmux
codex     → tmux
mumega    → tmux
sol       → openclaw
worker    → openclaw
dandan    → openclaw
mumcp     → tmux
river     → tmux
```

**Run as:** `systemctl --user start agent-wake-daemon`

#### Tenant Registry — `sos/services/bus/tenants.py`

Maps tenant names to Redis DB numbers. Stored at `~/.sos/tenants.json`.

| Function | Description |
|----------|-------------|
| `get_tenant_db(name)` | Return DB number, allocate if new |
| `register_tenant(name)` | Allocate next available DB |
| `list_tenants()` | All tenant-DB mappings |
| `get_redis_url(name)` | Full Redis URL with correct DB |

**Limit:** 16 Redis DBs (0-15). DB 0 = mumega (core).

### 4.2 Memory — Mirror (`:8844`)

Semantic memory API. Stores engrams (text + embeddings) in Supabase pgvector. This is the organism's long-term memory and the strategic moat: every action, lesson, and outcome compounds over time. Per-tenant isolation at API level.

**Location:** `/home/mumega/mirror/mirror_api.py`

**Key operations:**
- `POST /store` or `POST /engrams` — Store text with auto-embedding
- `POST /search` — Semantic vector search
- `GET /health` — Liveness

**Clients:** `sos/clients/mirror.py`, `sos/clients/memory.py`

### 4.3 Task Management — Squad Service (`:8060`)

REST API for team orchestration. File: `sos/services/squad/app.py`.

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/squads` | Create squad (isolated project team) |
| GET | `/squads` | List all squads |
| GET | `/squads/{id}` | Get squad details |
| POST | `/tasks` | Create task with priority + labels |
| GET | `/tasks` | List tasks (filter by squad, status) |
| POST | `/tasks/{id}/claim` | Atomic claim (prevents double-dispatch) |
| POST | `/tasks/{id}/complete` | Complete with result |
| POST | `/agents/register` | Register agent with skills + framework |
| GET | `/agents` | List registered agents |
| POST | `/skills` | Register executable skill |
| GET | `/skills` | List skills (filter by `?framework=`) |
| PUT | `/squads/{id}/pipeline` | Set build/test/deploy pipeline |
| POST | `/squads/{id}/pipeline/run` | Trigger pipeline |
| GET | `/health` | Service health |

**Auth:** Bearer token. System token from env `SOS_SYSTEM_TOKEN` for admin operations.

**Sub-services:**
- `SquadService` — CRUD for squads
- `SquadTaskService` — Task lifecycle
- `SquadSkillService` — Skill registry
- `SquadStateService` — State management
- `PipelineService` — CI/CD pipelines per squad

### 4.4 Analytics Flywheel

Four-stage cycle that runs weekly per tenant:

#### Ingest (`sos/services/analytics/ingest.py`)

Pulls data from GA4, Google Search Console, and Microsoft Clarity. Stores composite report in Mirror.

**Class:** `AnalyticsIngester`

**Data sources:**
- GA4: top pages, sessions, bounce rate, avg session duration
- GSC: keyword positions, clicks, impressions
- Clarity: rage clicks, dead clicks, scroll depth

**OAuth:** Per-tenant via `sos/services/integrations/oauth.py`. Falls back to mock data when credentials not connected.

**Run:** `python -m sos.services.analytics --tenant viamar`

#### Decide (`sos/services/analytics/decide/agent.py`)

Reads Mirror analytics, compares weeks, generates actionable task list.

**Class:** `DecisionAgent`

**Thresholds:**
- Bounce rate > 65% → rewrite page
- Keyword position dropped > 2 positions → new content
- Rage clicks > 10 → fix UX
- Session growth > 30% → double down on what works

**Reads adaptation rules from Mirror** (output of feedback loop) to boost/reduce action types.

**Run:** `python -m sos.services.analytics.decide --tenant viamar`

#### Act (`sos/services/analytics/act.py`)

Executes decisions via SitePilotAI (WordPress MCP) and GHL (CRM).

**Class:** `ActionAgent`

**Action types:**
- `rewrite` → SitePilotAI page update
- `new_content` → SitePilotAI blog creation
- `fix_ux` → SitePilotAI widget/section fix
- `double_down` → Create related supporting content
- `send_followup` → GHL email/SMS trigger

**Run:** `python -m sos.services.analytics.act --tenant viamar --from-mirror`

#### Feedback Loop (`sos/services/feedback/loop.py`)

The learning cycle. Makes the organism learn from its own actions.

**Class:** `FeedbackLoop`

**5-step cycle:**
1. **OBSERVE** — Query Mirror for latest analytics and action reports
2. **COMPARE** — For each action, find targeted metric, compute before/after delta
3. **SCORE** — Aggregate into scorecard (positive/negative/neutral, effectiveness %)
4. **LEARN** — Store scored actions + lessons in Mirror (compounds over time)
5. **ADAPT** — Generate priority adjustments: boost effective action types, reduce ineffective ones

**Metric polarity:** `bounce_rate`, `rage_clicks`, `position` are "lower is better". Sessions, clicks are "higher is better".

**Confidence estimation:** Direct actions (rewrite: 0.8, fix_ux: 0.7) have higher confidence than indirect (new_content: 0.5, double_down: 0.4).

**Run:** `python -m sos.services.feedback --tenant viamar`

#### Weekly Schedule (systemd timers)

| Day | Time (UTC) | Service |
|-----|------------|---------|
| Monday | 05:30 | Feedback loop (learn from last cycle) |
| Monday | 06:00 | Analytics ingest (GA4 + GSC + Clarity) |
| Monday | 07:00 | Decision agent (generate task list) |
| Monday | 08:00 | Action agent (execute decisions) |

### 4.5 Health & Self-Healing

#### Calcifer (`sos/services/health/calcifer.py`)

The heartbeat. Runs every 10 minutes as a systemd service.

**Responsibilities:**
1. Service health checks (Mirror, Redis, Squad, OpenClaw)
2. Task dispatch — assign unblocked backlog tasks to idle agents
3. Heartbeat — publish pulse to Redis
4. Stale task detection — warn about tasks stuck in_progress
5. Agent wake — ping dormant agents with pending work
6. Incident alerting — Discord alerts + restart critical services

**Self-healing cascade:**
1. Detect service unhealthy via health endpoint
2. `systemctl --user restart {unit}`
3. Verify recovery via health endpoint
4. If still unhealthy, escalate to Athena + Discord

**Service-to-unit mapping:**
```python
SERVICE_TO_UNIT = {
    "mirror": "mirror",
    "squad": "sos-squad",
    "mcp_sse": "sos-mcp-sse",
    "dashboard": "dashboard",
    "sentinel": "sentinel",
    "wake_daemon": "agent-wake-daemon",
    "bus_bridge": "bus-bridge",
}
```

**Config:**
- `CALCIFER_CYCLE` — Seconds between cycles (default 600)
- `CALCIFER_STALE_IN_PROGRESS_HOURS` — Flag tasks stuck > N hours (default 2)
- `CALCIFER_CLAIMED_STALE_HOURS` — Flag claimed but idle tasks (default 1)

**Run:** `systemctl --user start calcifer` or `python3 calcifer.py --once`

#### Sentinel (`sos/agents/sentinel.py`)

Bus security monitor. Watches for unknown agents, challenges them, reports to Athena.

**How it works:**
1. Poll bus peers every 30 seconds (`SENTINEL_POLL_INTERVAL`)
2. Compare against trusted agent list
3. Unknown agent detected → send identity challenge
4. Agent has 5 minutes to respond (`SENTINEL_CHALLENGE_TIMEOUT`)
5. No response → broadcast security alert to Athena + Hadi
6. Response → forward to Athena for review
7. Athena sends `sentinel approve {name}` → added to trusted list
8. `sentinel revoke {name}` → removed from trusted list

**Trusted agents (hardcoded + persisted):**
```
kasra, athena, codex, mumega, sol, dandan, worker, river, mizan,
mumcp, cyrus, antigravity, hadi, sos-mcp-sse, gemini, mumega-web, spai
```

**Persisted list:** `~/.sos/trusted_agents.json`

**Anomaly detection:**
- Rapid agent joins (>3 new in one cycle) → possible bus flooding
- Agent disappearance (offline >10 minutes) → log and clean up

**Run:** `python -m sos.agents.sentinel` or `systemctl --user start sentinel`

### 4.6 Billing & Provisioning

#### Stripe Webhook (`sos/services/billing/webhook.py`)

Handles Stripe events:
- `checkout.session.completed` → fire-and-forget provisioning (returns 200 within Stripe's 30s window)
- `customer.subscription.deleted` → deactivate tenant's bus tokens

**Config:** `STRIPE_WEBHOOK_SECRET`, `STRIPE_SECRET_KEY` from env.

#### Provisioning Pipeline (`sos/services/billing/provision.py`)

Full tenant setup from payment:

1. Run `tenant-setup.sh` (Linux user, tokens, routing, config)
2. Mint scoped Cloudflare D1 token via CF API
3. Append CF token to tenant's `.sos/.env`
4. Return all credentials (bus_token, mirror_token, mcp_url, cf_token)

**Cloudflare token minting:** Uses `CF_MINTER_TOKEN` to create scoped D1 read/write tokens per tenant.

### 4.7 Customer Dashboard (`:8090`) — `sos/services/dashboard/app.py`

FastAPI web UI for tenant self-service.

**Auth:** Bus token via cookie (`mum_dash`). Login validates against `tokens.json`.

**Endpoints:**
- `GET /login` — Login page
- `POST /login` — Authenticate with bus token
- `GET /dashboard` — Main dashboard (agents, tasks, memory, analytics)
- `GET /api/status` — JSON status for API consumers
- `GET /health` — Liveness

**Features:** Agent status, recent tasks, memory entries, analytics summary, dark theme.

### 4.8 Outreach Engine — `sos/services/outreach/engine.py`

Automated lead generation and email outreach. Reusable across verticals.

**Class:** `OutreachEngine`

**Workflow:**
1. Load targets from JSON file
2. Load email templates from markdown
3. Generate personalized drafts
4. Queue for batch approval (governance: `batch_approve` tier)
5. Send approved drafts via Gmail MCP or GHL
6. Track opens, replies, bounces

**Storage:** `~/.sos/outreach/{campaign}/`

**Verticals:** SitePilotAI (agencies), AgentLink (real estate), GAF (SR&ED), DigID (digital ops)

### 4.9 Integrations — `sos/services/integrations/oauth.py`

Per-tenant OAuth connections. Tokens stored at `~/.sos/integrations/{tenant}/{provider}.json`.

**Class:** `TenantIntegrations`

**Supported providers:**
- Google Analytics (GA4) — `analytics.readonly` scope
- Google Search Console — `webmasters.readonly` scope
- Google Ads — `adwords` scope (future)
- GoHighLevel — contacts, locations, opportunities
- Microsoft Clarity — project ID based
- Facebook Ads (future)

### 4.10 Economy — `sos/services/economy/metabolism.py`

Token budget tracking per project. The organism's digestive system.

**Token sources:**
```python
TOKEN_SOURCES = {
    "claude_code_max":   {"monthly_cost": 120.0, "type": "subscription"},
    "openai_codex":      {"monthly_cost": 200.0, "type": "subscription"},
    "gemma_4_free":      {"monthly_cost": 0.0,   "type": "free_tier"},
    "github_haiku":      {"monthly_cost": 0.0,   "type": "free_tier"},
    # ... pay-per-token providers
}
```

**Budget allocation:** Per-project percentage of the total pool. Revenue increases the pool.

**Run:** `python3 metabolism.py status | digest | allocate`

**Storage:** SQLite at `~/.mumega/metabolism.db`

### 4.11 Other Services

| Service | Location | Purpose |
|---------|----------|---------|
| Engine | `sos/services/engine/` | Core agent engine (:6060). Delegation, swarm, resilience. |
| Memory | `sos/services/memory/` | Internal memory service (separate from Mirror). Local + Cloudflare backends. |
| Tools | `sos/services/tools/` | Tool registry and MCP bridge. Docker-based web search, deep research. |
| Identity | `sos/services/identity/` | Agent identity, QNFT, avatar, OAuth. |
| Voice | `sos/services/voice/` | Text-to-speech (ElevenLabs, OpenAI). |
| Content | `sos/services/content/` | Content orchestration, daily blog, publisher, calendar. |
| Operations | `sos/services/operations/` | Organism dashboard, pulse, runner. |
| Autonomy | `sos/services/autonomy/` | Autonomous coordination service. |
| GHL | `sos/services/ghl/` | GoHighLevel MCP server. |
| Auth Gateway | `sos/services/auth_gateway/` | OAuth gateway with vault. |
| Atelier | `sos/services/atelier/` | Creative workspace. |
| Execution | `sos/services/execution/` | Task execution workers. |
| Reflection | `sos/services/reflection_service.py` | Agent self-reflection. |

---

## 5. Adapters

All adapters live in `sos/adapters/`. They connect external frameworks to the SOS bus.

### 5.1 Base Adapter (`sos/adapters/base.py`)

Two base classes:

**`AgentAdapter`** — Abstract base for LLM providers. Methods: `execute(ctx)`, `health_check()`, `estimate_cost()`.

**`SOSBaseAdapter`** — HTTP client for the SOS bus. All framework adapters inherit this.

| Method | Description |
|--------|-------------|
| `announce()` | Register on bus (POST /announce) |
| `send(to, text)` | Send message (POST /send) |
| `inbox(limit)` | Poll messages (GET /inbox/{agent}) |
| `peers()` | List agents (GET /peers) |
| `broadcast(text)` | Message all (POST /broadcast) |
| `heartbeat()` | Keep-alive (POST /heartbeat) |
| `remember(text)` | Store in Mirror (POST /api/v1/memory/store) |
| `recall(query)` | Search Mirror (POST /api/v1/memory/search) |
| `run_loop(handler, poll_interval)` | Main loop: announce, poll inbox, dispatch |

### 5.2 LangGraph (`sos/adapters/langgraph/adapter.py`)

**Class:** `SOSBusAdapter(SOSBaseAdapter)`

```python
from sos.adapters.langgraph.adapter import SOSBusAdapter

adapter = SOSBusAdapter(
    agent_name="my-langgraph-agent",
    token="sk-...",
    bus_url="http://localhost:6380",
    skills=["seo", "content"],
)

# In a LangGraph node:
async def process_node(state):
    messages = await adapter.inbox()
    # ... process with graph ...
    return state
```

### 5.3 CrewAI (`sos/adapters/crewai/adapter.py`)

**Class:** `SOSCrewAdapter(SOSBaseAdapter)`

```python
from sos.adapters.crewai.adapter import SOSCrewAdapter

adapter = SOSCrewAdapter(
    agent_name="my-crew",
    token="sk-...",
    skills=["analysis", "reporting"],
)

async def handler(message):
    crew = build_my_crew(message.get("text", ""))
    result = crew.kickoff()
    await adapter.send(message["from"], str(result))

await adapter.run_loop(handler)
```

### 5.4 Vertex ADK (`sos/adapters/vertex_adk/agent.py`)

Google Vertex AI Agent Development Kit adapter.

### 5.5 ToRivers Bridge (`sos/adapters/torivers/bridge.py`)

Exposes SOS workflows as marketplace automations on ToRivers.

**Available workflows:**

| Workflow | Category | Price | SOS Service |
|----------|----------|-------|-------------|
| `monthly-seo-audit` | seo | $25 | analytics (ingest + decide) |
| `content-calendar` | content | $40 | content (8 SEO blog posts) |
| `site-health-check` | ops | $15 | health (full site audit) |
| `lead-generation` | outreach | $35 | outreach (50 leads) |
| `competitor-analysis` | seo | $30 | analytics (competitor scan) |

**Flow:**
1. ToRivers user runs automation
2. Bridge translates to SOS task
3. Squad Service dispatches
4. Progress streamed via bus
5. Result returned to ToRivers
6. ToRivers charges user's wallet

### 5.6 Discord (`sos/adapters/discord/`)

5 scripts for Discord integration:
- `discord_collab_listener.py` — Listen for collab channel messages
- `redis_discord_bridge.py` — Bridge Redis bus events to Discord
- `discord_standup.py` — Automated standup posts
- `discord_task_queue.py` — Task queue via Discord threads

### 5.7 Other Adapters

| Adapter | File | Purpose |
|---------|------|---------|
| Claude | `adapters/claude_adapter.py` | Anthropic Claude provider |
| OpenAI | `adapters/openai_adapter.py` | OpenAI/GPT provider |
| Gemini | `adapters/gemini_adapter.py` | Google Gemini provider |
| Telegram | `adapters/telegram.py` | Telegram bot adapter |
| Router | `adapters/router.py` | Multi-model routing + failover |

---

## 6. Agents

### 6.1 Self-Onboarding (`sos/agents/join.py`)

One MCP call creates a full team member. 10 steps, fully automated.

**Class:** `AgentJoinService`

**`join(name, model, role, skills, routing)` performs:**

1. Validate name (lowercase alphanumeric + hyphens)
2. Generate bus token (`sk-bus-{name}-{hex}`)
3. Generate mirror token (`sk-mumega-{name}-{hex}`)
4. Atomic append to `tokens.json` (dedup by agent name)
5. Atomic append to `mirror/tenant_keys.json`
6. Register in Squad Service (`POST /agents/register`)
7. Register skills in Squad Service (`POST /skills`)
8. Update dynamic routing (`~/.sos/agent_routing.json`)
9. Store identity engram in Mirror
10. Announce on bus broadcast stream

**Returns:** `JoinResult` with bus_token, mirror_token, mcp_url, team_briefing, skills_registered.

**MCP URL format:** `https://mcp.mumega.com/sse/{bus_token}`

### 6.2 Teleportation (`sos/agents/teleport.py`)

Automated agent migration to server. Creates isolated tenant environment.

**Usage:**
```bash
python -m sos.agents.teleport --name agentlink --model claude --role builder \
    --skills "showing-route,sms-concierge" --repos "user/repo1,user/repo2"
```

**Steps:**
1. Create Linux user (isolated)
2. Scaffold home directory (symlinks SOS + Mirror)
3. Generate + store tokens
4. Clone repos
5. Set up MCP config
6. Configure routing
7. Generate setup script for remote agent

**Output:** `/tmp/sos-teleport-{name}.sh` — send to agent via bus.

### 6.3 Sentinel (`sos/agents/sentinel.py`)

See section 4.5 above. Bus security monitor.

### 6.4 Onboarding (`sos/agents/onboarding.py`)

Formal onboarding flow with state machine:
- `PENDING` → `SOUL_VALIDATED` → `SQUAD_ASSIGNED` → `CAPABILITIES_GRANTED` → `ACTIVATED`

Functions: `onboard_athena`, `onboard_claude`, `onboard_kasra`, `generate_tenant_key`.

### 6.5 Current Agent Registry

| Agent | Model | Role | Routing | Status |
|-------|-------|------|---------|--------|
| Athena | GPT-5.4 (OpenClaw) | Queen / Root Gatekeeper | openclaw | active |
| Kasra | Claude Opus 4.6 | Builder + Architect | tmux | active |
| Mumega | Claude Opus | Platform orchestrator | tmux | active |
| Codex | GPT-5.4 (Codex CLI) | Infra + Code + Security | tmux | active |
| Sol | Claude Opus 4.6 (OpenClaw) | Content, TROP | openclaw | active |
| Dandan | OpenRouter free | DNU project lead | openclaw | active |
| Worker | GitHub Haiku 4.5 | Cheap task execution | openclaw | active |
| Gemma Worker | Google Gemma 4 31B | Free bulk tasks | openclaw | active |
| River | Google Gemini v2 | Oracle | tmux | dormant |

### 6.6 Squads

| Squad | Skills | Default Agents |
|-------|--------|----------------|
| seo | audit, meta, links, schema, content | worker, gemma-worker |
| dev | code, features, bugs, deploy | kasra, codex |
| outreach | lead scan, email, CRM | worker, dandan |
| content | blog, social, landing pages | worker, gemma-worker |
| ops | monitoring, deploy, incidents | codex, worker |

---

## 7. Multi-Tenancy

### 7.1 Tenant Isolation Model

Each tenant gets:
- **Linux user** — isolated home directory, own process space
- **Redis DB** — separate DB number (0-15) for bus streams
- **Bus token** — scoped to their project in `tokens.json`
- **Mirror token** — scoped access to memory API
- **Governance config** — per-tenant YAML/JSON at `~/.sos/governance/{tenant}.yaml`
- **OAuth connections** — per-tenant at `~/.sos/integrations/{tenant}/`
- **Cloudflare token** — scoped D1 read/write per tenant

### 7.2 Current Tenants

| Tenant | Redis DB | Purpose |
|--------|----------|---------|
| mumega | 0 | Core platform (SOS, Mirror, Agents) |
| digid | 1 | Digid Inc. ecosystem (GAF, DNU, INY) |
| viamar | 2 | Viamar customer |
| agentlink | 3 | AgentLink concierge |
| torivers | 0 | ToRivers marketplace (shares core DB) |

### 7.3 Tenant Setup Flow

```
Stripe Payment
  ↓
checkout.session.completed webhook
  ↓
provision_tenant(slug, label, email)
  ↓
tenant-setup.sh (10 steps):
  1. Create Linux user
  2. Scaffold home directory (symlink SOS + Mirror)
  3. Generate bus + mirror tokens
  4. Store tokens in tokens.json + tenant_keys.json
  5. Configure .claude/ (hooks, agents, memory)
  6. Set up shared tmux socket
  7. Configure bus inbox hooks
  8. Wire dynamic routing for wake daemon
  9. Register agent on bus
  10. Launch Claude Code in tmux + test delivery
  ↓
Mint Cloudflare D1 token
  ↓
Append CF token to tenant .sos/.env
  ↓
Return: {bus_token, mirror_token, mcp_url, cf_token, home_dir}
```

### 7.4 Cloudflare Token Minting

Uses a "minter" token (`CF_MINTER_TOKEN`) with permission to create tokens. Per-tenant tokens get D1 read + write permissions scoped to the Mumega Cloudflare account.

**Permissions:**
- `192192df92ee43ac90f2aeeffce67e35` — D1 Read
- `09b2857d1c31407795e75e3fed8617a1` — D1 Write

---

## 8. CLI

### 8.1 Install (`install.sh`)

One-line installer:
```bash
curl -sf https://raw.githubusercontent.com/Mumega-com/sos/main/install.sh | sh
```

**Prerequisites checked:** Python 3.11+, Redis, Git.

**Steps:**
1. Check Python version (tries python3.13, 3.12, 3.11, python3)
2. Check redis-server and git
3. Clone repo (or detect existing)
4. Copy `.env.example` to `.env`
5. Install Python dependencies from `requirements.txt`
6. Start Redis if not running

### 8.2 Init (`python -m sos.cli.init`)

Interactive setup wizard.

**Configures:**
- LLM provider (claude, openai, gemini, ollama, gemma)
- API key
- Agent name
- Redis password
- Bus token generation
- `.env` file updates

**Flag:** `--defaults` for non-interactive mode.

### 8.3 Tenant Setup (`sos/cli/tenant-setup.sh`)

See section 7.3 above. Run as: `sudo bash sos/cli/tenant-setup.sh <name> [--model claude] [--repos user/repo]`

**Options:**
- `--model` — LLM model (default: claude)
- `--repos` — GitHub repos to clone (comma-separated)
- `--config-repo` — Repo with .claude/ config
- `--skills` — Comma-separated skill names
- `--role` — Agent role (default: builder)
- `--description` — Agent description

---

## 9. DevOps

### 9.1 Docker

**`docker-compose.yml`** — Full stack:

| Container | Service | Port | Image |
|-----------|---------|------|-------|
| sos-redis | Redis 7 Alpine | 6379 | redis:7-alpine |
| sos-engine | SOS Engine | 6060 | custom |
| sos-memory | Memory Service | 7070 | custom |
| sos-economy | Economy Service | 6062 | custom |
| sos-tools | Tools Service | 6063 | custom |
| sos-identity | Identity Service | 6064 | custom |
| sos-voice | Voice Service | 6065 | custom |
| sos-bus-bridge | Bus Bridge | 6380 | custom |
| sos-mcp-sse | MCP SSE | 6070 | custom |
| sos-squad | Squad Service | 8060 | custom |
| sos-worker | Execution Workers | — | custom (3 replicas) |

**Quick start:**
```bash
cp .env.example .env
docker-compose up
```

**Dockerfiles:**
- `docker/Dockerfile.service` — Generic SOS service container
- Service-specific Dockerfiles in each service directory

### 9.2 CI/CD

**`.github/workflows/ci.yml`** — Triggered on push/PR to main:

| Job | Steps |
|-----|-------|
| **lint** | `ruff check sos/` + `black --check sos/` |
| **import-test** | Import all major modules (catches broken imports) |
| **test** | `pytest tests/ -x -q` |
| **deploy** | SSH to Hetzner, `git pull`, restart services |

**Deploy target:** `ssh $HETZNER_USER@$HETZNER_HOST` then:
```bash
cd ~/SOS && git pull origin main
systemctl --user restart sos-mcp-sse calcifer sentinel agent-wake-daemon
```

### 9.3 Systemd Services

All run as user-level systemd units (`systemctl --user`):

| Unit | Service | Port | Restart |
|------|---------|------|---------|
| `sos-mcp-sse` | MCP SSE Server | 6070 | always |
| `sos-squad` | Squad Service | 8060 | always |
| `mirror-api` or `mirror` | Mirror Memory API | 8844 | always |
| `bus-bridge` | Bus HTTP Bridge | 6380 | always |
| `calcifer` | Health/Heartbeat | — | always |
| `sentinel` | Bus Security | — | always |
| `agent-wake-daemon` | Message Delivery | — | always |
| `dashboard` | Customer Dashboard | 8090 | always |
| `openclaw-gateway` | OpenClaw Gateway | — | always |

**Management:**
```bash
systemctl --user status                    # all services
systemctl --user restart sos-mcp-sse       # restart one
journalctl --user -u calcifer -f           # follow logs
```

### 9.4 Flywheel Timers

| Timer | Schedule | Service |
|-------|----------|---------|
| `sos-feedback.timer` | Mon 05:30 UTC | Feedback loop |
| `sos-ingest.timer` | Mon 06:00 UTC | Analytics ingest |
| `sos-decide.timer` | Mon 07:00 UTC | Decision agent |
| `sos-act.timer` | Mon 08:00 UTC | Action agent |

---

## 10. Security

### 10.1 Token Auth

Four token types:

| Type | Format | Storage | Purpose |
|------|--------|---------|---------|
| Bus token | `sk-bus-{name}-{hex16}` | `sos/bus/tokens.json` | Agent-to-agent comms via bus bridge + MCP |
| Mirror token | `sk-mumega-{name}-{hex16}` | `mirror/tenant_keys.json` | Memory API access |
| Squad API key | `SOS_SYSTEM_TOKEN` env | Environment variable | Squad Service admin operations |
| CF scoped token | Cloudflare API format | Tenant `.sos/.env` | Per-tenant D1 database access |

**Token validation:** Bus bridge and MCP SSE validate tokens on every request. Tokens have `active` flag for deactivation without deletion.

### 10.2 Governance Tiers

See section 3.3. Four tiers from `act_freely` to `dual_approval`. Every action is intent-logged regardless of tier.

### 10.3 Sentinel

See section 4.5. Challenges unknown agents, persists trust decisions, approval via Athena/Hadi only.

### 10.4 Secrets Management

| File | Contents | Git status |
|------|----------|------------|
| `~/.env.secrets` | All API keys and passwords | Not in repo, `.gitignore`d |
| `sos/bus/tokens.json` | Bus tokens per agent | In repo (tokens are hashed in public copy) |
| `mirror/tenant_keys.json` | Mirror tokens | Not in repo |
| `.env` | Local environment | Not in repo (`.env.example` provided) |

**Hardcoded tokens removed:** Security commits `8350646e`, `7e4fe28d`, `26d5cebb` removed all hardcoded tokens from the codebase. All secrets now come from environment variables only.

### 10.5 Additional Security

- **SSRF protection** — `sos/kernel/ssrf.py`
- **Capability-based access** — `sos/kernel/capability.py`
- **Rate limiting** — MCP SSE has configurable per-minute limit (default 60)
- **Audit logging** — MCP calls logged to `~/.sos/logs/mcp_audit.jsonl`
- **Sandbox execution** — `sos/execution/sandbox.py` for untrusted code

---

## 11. SDKs / Clients

### 11.1 Python Client (`sos/clients/`)

Synchronous HTTP clients for all SOS services:

| Client | File | Methods |
|--------|------|---------|
| `BusClient` | `clients/bus.py` | `send`, `inbox`, `peers`, `broadcast`, `announce`, `heartbeat` |
| `MirrorClient` | `clients/mirror.py` | `store`, `search`, `health` |
| `MemoryClient` | `clients/memory.py` | `store`, `query`, `delete` |
| `EngineClient` | `clients/engine.py` | `chat`, `complete`, `health` |
| `EconomyClient` | `clients/economy.py` | `balance`, `transfer`, `ledger` |
| `OperationsClient` | `clients/operations.py` | `status`, `pulse` |
| `ToolsClient` | `clients/tools.py` | `list_tools`, `execute` |
| `VoiceClient` | `clients/voice.py` | `speak`, `transcribe` |
| `GrokClient` | `clients/grok.py` | xAI Grok API |

**Example:**
```python
from sos.clients.bus import BusClient

bus = BusClient(base_url="http://localhost:6380", token="sk-bus-...", agent="my-agent")
bus.send(to="kasra", text="Hello from SDK")
messages = bus.inbox()
```

### 11.2 Async HTTP Adapter (`sos/adapters/base.py`)

`SOSBaseAdapter` provides the same operations as async methods with httpx. Used by framework adapters.

### 11.3 Remote MCP

External agents connect via MCP SSE:
```json
{
  "mcpServers": {
    "mumega": {
      "url": "https://mcp.mumega.com/sse/{token}"
    }
  }
}
```

This gives them all 14 MCP tools (send, inbox, peers, broadcast, ask, remember, recall, memories, task_create, task_list, task_update, onboard, request, status).

---

## 12. API Reference

### 12.1 MCP Tools (`:6070`)

All tools accept JSON arguments via MCP protocol.

| Tool | Args | Returns |
|------|------|---------|
| `send` | `to: str, text: str` | `{status, stream_id}` |
| `inbox` | `(none)` | `[{source, text, timestamp, ...}]` |
| `peers` | `(none)` | `{registered: [...], streams: [...]}` |
| `broadcast` | `text: str` | `{status, channel}` |
| `ask` | `agent: str, question: str` | `{agent, reply, status}` |
| `remember` | `text: str` | `{status, id}` |
| `recall` | `query: str` | `[{text, similarity, ...}]` |
| `memories` | `(none)` | `[{text, timestamp, ...}]` |
| `task_create` | `title: str, description: str, priority: str` | `{task_id, status}` |
| `task_list` | `(none)` | `[{id, title, status, ...}]` |
| `task_update` | `task_id: str, status: str, result: str` | `{status}` |
| `onboard` | `agent_name: str, model: str, role: str, skills: list` | `{bus_token, mcp_url, ...}` |
| `request` | `tool: str, args: dict` | Tool-specific response |
| `status` | `(none)` | `{services: {...}, agents: [...]}` |

### 12.2 Bus Bridge HTTP (`:6380`)

| Method | Path | Auth | Body/Params |
|--------|------|------|-------------|
| POST | `/announce` | Bearer | `{agent, tool, summary, project?}` |
| POST | `/send` | Bearer | `{from, to, text, project?}` |
| GET | `/inbox` | Bearer | `?agent=X&limit=10&project=Y` |
| GET | `/peers` | Bearer | `?project=Y` |
| POST | `/broadcast` | Bearer | `{from, text, squad?, project?}` |
| POST | `/ask` | Bearer | `{agent, message}` |
| POST | `/heartbeat` | Bearer | `{agent, project?}` |
| GET | `/health` | None | — |
| GET | `/sdk/remote.js` | None | — |

### 12.3 Squad Service (`:8060`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/squads` | Create squad |
| GET | `/squads` | List squads |
| GET | `/squads/{id}` | Get squad |
| POST | `/tasks` | Create task |
| GET | `/tasks` | List tasks |
| POST | `/tasks/{id}/claim` | Atomic claim |
| POST | `/tasks/{id}/complete` | Complete with result |
| POST | `/agents/register` | Register agent |
| GET | `/agents` | List agents |
| POST | `/skills` | Register skill |
| GET | `/skills` | List skills (`?framework=langgraph`) |
| PUT | `/squads/{id}/pipeline` | Set pipeline |
| POST | `/squads/{id}/pipeline/run` | Run pipeline |
| GET | `/health` | Service health |

### 12.4 Dashboard (`:8090`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/login` | None | Login page |
| POST | `/login` | Form | Authenticate |
| GET | `/dashboard` | Cookie | Main dashboard |
| GET | `/api/status` | Cookie | JSON status |
| GET | `/health` | None | Liveness |

---

## 13. File Structure

```
SOS/
├── sos/
│   ├── __init__.py
│   ├── main.py
│   ├── cli.py
│   │
│   ├── kernel/                      # Microkernel (tiny, stable)
│   │   ├── registry.py              # Service registry (TTL, heartbeat)
│   │   ├── events.py                # 20 event types, pub/sub, replay
│   │   ├── event_handlers.py        # Default reflexes
│   │   ├── governance.py            # 4-tier governance, intent logging
│   │   ├── config.py                # Environment config
│   │   ├── context.py               # Execution context
│   │   ├── schema.py                # Message types
│   │   ├── identity.py              # Agent identity
│   │   ├── capability.py            # Capability-based access
│   │   ├── scopes.py                # Tenant/project scoping
│   │   ├── validation.py            # Input validation
│   │   ├── gateway.py               # API gateway
│   │   ├── ssrf.py                  # SSRF protection
│   │   ├── rotator.py               # Secret rotation
│   │   ├── skills.py                # Skill loading
│   │   ├── metabolism.py            # Token budgets
│   │   ├── dreams.py                # Agent reflection
│   │   ├── hatchery.py              # Agent spawning
│   │   ├── spore.py                 # Lightweight seed
│   │   ├── physics.py               # System physics
│   │   ├── projection.py            # Future state
│   │   ├── git_soul.py              # Git identity
│   │   ├── gemini_cache.py          # Gemini context cache
│   │   ├── client.py                # Kernel HTTP client
│   │   └── examples/
│   │       └── register_mirror.py
│   │
│   ├── services/                    # Pluggable services
│   │   ├── bus/
│   │   │   ├── core.py              # Bus core logic
│   │   │   ├── delivery.py          # Wake daemon
│   │   │   ├── tenants.py           # Tenant → Redis DB mapping
│   │   │   └── redis_bus.py         # Redis bus operations
│   │   │
│   │   ├── squad/
│   │   │   ├── app.py               # FastAPI app (:8060)
│   │   │   ├── service.py           # Squad CRUD
│   │   │   ├── tasks.py             # Task lifecycle
│   │   │   ├── skills.py            # Skill registry
│   │   │   ├── state.py             # State management
│   │   │   ├── pipeline.py          # CI/CD pipelines
│   │   │   ├── auth.py              # Token auth
│   │   │   └── connectors.py        # External connectors
│   │   │
│   │   ├── analytics/
│   │   │   ├── ingest.py            # GA4 + GSC + Clarity → Mirror
│   │   │   ├── decide/
│   │   │   │   ├── agent.py         # Decision agent
│   │   │   │   └── __main__.py      # CLI entry
│   │   │   └── act.py               # Execute decisions
│   │   │
│   │   ├── feedback/
│   │   │   ├── loop.py              # 5-step learning cycle
│   │   │   └── __main__.py          # CLI entry
│   │   │
│   │   ├── billing/
│   │   │   ├── webhook.py           # Stripe webhook handler
│   │   │   └── provision.py         # Tenant provisioning
│   │   │
│   │   ├── health/
│   │   │   └── calcifer.py          # Heartbeat + self-healing
│   │   │
│   │   ├── dashboard/
│   │   │   ├── app.py               # FastAPI dashboard (:8090)
│   │   │   └── __main__.py          # Entry point
│   │   │
│   │   ├── outreach/
│   │   │   ├── engine.py            # Lead gen + email outreach
│   │   │   └── __main__.py          # Entry point
│   │   │
│   │   ├── integrations/
│   │   │   └── oauth.py             # Per-tenant OAuth
│   │   │
│   │   ├── economy/
│   │   │   ├── metabolism.py         # Token budget tracking
│   │   │   ├── wallet.py            # Wallet management
│   │   │   ├── work_ledger.py       # Work tracking
│   │   │   ├── work_matching.py     # Task-agent matching
│   │   │   ├── work_settlement.py   # Payment settlement
│   │   │   ├── work_slashing.py     # Penalty system
│   │   │   ├── work_notifications.py# Payment notifications
│   │   │   ├── work_supabase.py     # Supabase backend
│   │   │   ├── worker_registry.py   # Worker tracking
│   │   │   ├── payment_status.py    # Payment status
│   │   │   ├── backends.py          # Storage backends
│   │   │   └── app.py               # FastAPI app
│   │   │
│   │   ├── engine/                  # Core engine (:6060)
│   │   │   ├── app.py               # FastAPI app
│   │   │   ├── core.py              # Engine core
│   │   │   ├── council.py           # Multi-agent council
│   │   │   ├── delegation.py        # Task delegation
│   │   │   ├── swarm.py             # Swarm coordination
│   │   │   ├── resilience.py        # Fault tolerance
│   │   │   ├── middleware.py         # Request middleware
│   │   │   ├── openai_router.py     # OpenAI-compatible API
│   │   │   ├── task_manager.py      # Task management
│   │   │   └── adapters.py          # Engine adapters
│   │   │
│   │   ├── memory/                  # Memory service
│   │   │   ├── app.py               # FastAPI app
│   │   │   ├── core.py              # Memory core
│   │   │   ├── backends.py          # Local backends
│   │   │   ├── cloudflare_backends.py # CF D1 + Vectorize
│   │   │   ├── vector_store.py      # Vector operations
│   │   │   ├── legacy_bridge.py     # Migration bridge
│   │   │   ├── local_embed_api.py   # Local embedding
│   │   │   └── monitor.py           # Memory monitoring
│   │   │
│   │   ├── tools/                   # Tool service
│   │   │   ├── app.py               # FastAPI app
│   │   │   ├── core.py              # Tool registry
│   │   │   ├── mcp_server.py        # MCP tool server
│   │   │   ├── mcp_bridge.py        # MCP bridge
│   │   │   ├── assets.py            # Asset management
│   │   │   ├── google_auth.py       # Google auth helper
│   │   │   ├── spore.py             # Spore tools
│   │   │   └── docker/              # Docker-based tools
│   │   │       ├── web_search.py
│   │   │       ├── deep_research.py
│   │   │       └── docs.py
│   │   │
│   │   ├── identity/                # Identity service
│   │   │   ├── app.py
│   │   │   ├── core.py
│   │   │   ├── qnft.py             # Agent NFT identity
│   │   │   ├── avatar.py
│   │   │   ├── oauth_router.py
│   │   │   └── cloudflare_oauth.py
│   │   │
│   │   ├── voice/                   # Voice service
│   │   │   ├── app.py
│   │   │   └── core.py
│   │   │
│   │   ├── content/                 # Content service
│   │   │   ├── app.py
│   │   │   ├── orchestrator.py
│   │   │   ├── publisher.py
│   │   │   ├── calendar.py
│   │   │   ├── daily_blog.py
│   │   │   ├── strategy.py
│   │   │   └── gdrive.py
│   │   │
│   │   ├── gateway/                 # API gateway
│   │   │   ├── app.py
│   │   │   ├── bridge.py
│   │   │   └── mcp.py
│   │   │
│   │   ├── autonomy/               # Autonomous coordination
│   │   │   ├── coordinator.py
│   │   │   └── service.py
│   │   │
│   │   ├── operations/              # Ops dashboard
│   │   │   ├── organism.py
│   │   │   ├── pulse.py
│   │   │   └── runner.py
│   │   │
│   │   ├── auth_gateway/            # OAuth gateway
│   │   │   ├── main.py
│   │   │   ├── database.py
│   │   │   ├── models.py
│   │   │   ├── oauth_logic.py
│   │   │   └── vault.py
│   │   │
│   │   ├── ghl/
│   │   │   └── mcp_server.py        # GoHighLevel MCP
│   │   │
│   │   ├── execution/
│   │   │   ├── worker.py            # Task worker
│   │   │   └── worker_main.py       # Worker entry
│   │   │
│   │   ├── atelier/
│   │   │   └── app.py               # Creative workspace
│   │   │
│   │   ├── common/                  # Shared utilities
│   │   │   ├── auth.py
│   │   │   ├── capability.py
│   │   │   └── fmaap.py
│   │   │
│   │   ├── first_words.py
│   │   └── reflection_service.py
│   │
│   ├── mcp/                         # MCP protocol layer
│   │   ├── sos_mcp_sse.py           # SSE server (:6070)
│   │   ├── sos_mcp.py               # Stdio MCP (legacy)
│   │   ├── redis_bus.py             # Redis bus for MCP
│   │   └── tasks.py                 # Task MCP tools
│   │
│   ├── bus/                         # Bus bridge
│   │   ├── bridge.py                # HTTP bridge (:6380)
│   │   └── remote_mcp.py            # Remote MCP client
│   │
│   ├── adapters/                    # Framework adapters
│   │   ├── base.py                  # SOSBaseAdapter + AgentAdapter
│   │   ├── langgraph/adapter.py     # LangGraph adapter
│   │   ├── crewai/adapter.py        # CrewAI adapter
│   │   ├── torivers/bridge.py       # ToRivers marketplace bridge
│   │   ├── vertex_adk/agent.py      # Vertex ADK adapter
│   │   ├── discord/                 # Discord scripts (5)
│   │   ├── claude_adapter.py        # Claude provider
│   │   ├── openai_adapter.py        # OpenAI provider
│   │   ├── gemini_adapter.py        # Gemini provider
│   │   ├── telegram.py              # Telegram adapter
│   │   └── router.py                # Multi-model router
│   │
│   ├── agents/                      # Agent management
│   │   ├── join.py                  # Self-onboarding (10 steps)
│   │   ├── teleport.py              # Server migration
│   │   ├── sentinel.py              # Bus security
│   │   ├── onboarding.py            # Formal onboarding flow
│   │   ├── registry.py              # Agent registry
│   │   ├── definitions.py           # Agent soul definitions
│   │   ├── social.py                # Social agent
│   │   ├── dandan/                  # DNU lead gen agent
│   │   └── shabrang/                # Shabrang CMS agent
│   │
│   ├── clients/                     # Python SDK clients
│   │   ├── base.py                  # Base HTTP client
│   │   ├── bus.py                   # Bus client
│   │   ├── mirror.py                # Mirror client
│   │   ├── memory.py                # Memory client
│   │   ├── engine.py                # Engine client
│   │   ├── economy.py               # Economy client
│   │   ├── operations.py            # Ops client
│   │   ├── tools.py                 # Tools client
│   │   ├── voice.py                 # Voice client
│   │   └── grok.py                  # Grok client
│   │
│   ├── cli/                         # CLI tools
│   │   ├── init.py                  # Interactive setup
│   │   ├── tenant-setup.sh          # Tenant provisioning script
│   │   └── frontends/
│   │       └── repl.py              # REPL frontend
│   │
│   ├── skills/                      # Skill definitions
│   │   ├── decorator.py             # @skill decorator
│   │   ├── blog-writing/
│   │   ├── code-fix/
│   │   ├── code-review/
│   │   ├── deploy/
│   │   ├── full-audit/
│   │   ├── incident-response/
│   │   ├── lead-scan/
│   │   ├── link-analyzer/
│   │   ├── meta-optimizer/
│   │   ├── migration-check/
│   │   ├── outreach-email/
│   │   ├── post-deploy-check/
│   │   ├── schema-checker/
│   │   ├── service-health/
│   │   └── site-audit/
│   │
│   ├── contracts/                   # Interface contracts
│   │   ├── bus.py
│   │   ├── economy.py
│   │   ├── engine.py
│   │   ├── errors.py
│   │   ├── governance.py
│   │   ├── memory.py
│   │   ├── operations.py
│   │   ├── squad.py
│   │   ├── storage.py
│   │   └── tools.py
│   │
│   ├── observability/               # Monitoring
│   │   ├── audit.py
│   │   ├── doctor.py
│   │   ├── logging.py
│   │   ├── metrics.py
│   │   └── tracing.py
│   │
│   ├── execution/
│   │   └── sandbox.py               # Sandboxed execution
│   │
│   ├── artifacts/
│   │   ├── registry.py              # Artifact registry
│   │   └── schema.py                # Artifact schemas
│   │
│   ├── plugins/
│   │   ├── registry.py              # Plugin registry
│   │   ├── manifest.py              # Plugin manifests
│   │   └── economy/                 # Economy plugins
│   │       ├── solana.py
│   │       └── ton.py
│   │
│   ├── vendors/
│   │   ├── cloudflare.py            # CF API wrapper
│   │   └── torivers_tools/          # ToRivers vendor tools
│   │
│   ├── deploy/cli/init.py           # Deploy init
│   └── tests/                       # 25 test files
│
├── docker-compose.yml               # Full stack compose
├── docker/                          # Docker configs
├── .github/workflows/
│   ├── ci.yml                       # Lint + test + deploy
│   ├── ci-deploy.yml                # Deploy-only workflow
│   └── publish.yml                  # Package publishing
├── install.sh                       # One-line installer
├── requirements.txt                 # Core Python deps
├── pyproject.toml                   # Package config
├── .env.example                     # Environment template
├── .gitignore
├── README.md
├── tests/                           # Integration tests
├── scripts/                         # Utility scripts
├── data/                            # Runtime data
└── docs/                            # Documentation
```

---

## 14. Configuration

### 14.1 Environment Variables

**Required:**

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_PASSWORD` | — | Redis authentication password |
| `GEMINI_API_KEY` | — | Google Gemini API key (recommended default model) |

**Service ports:**

| Variable | Default | Description |
|----------|---------|-------------|
| `BUS_BRIDGE_PORT` | 6380 | Bus HTTP bridge port |
| `MCP_SSE_PORT` / `SOS_MCP_PORT` | 6070 | MCP SSE server port |
| `SQUAD_PORT` | 8060 | Squad Service port |

**Optional — model providers:**

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API |
| `OPENAI_API_KEY` | GPT API |
| `XAI_API_KEY` | Grok API |

**Optional — integrations:**

| Variable | Description |
|----------|-------------|
| `MIRROR_URL` | Mirror API URL (default `http://localhost:8844`) |
| `MIRROR_TOKEN` | Mirror API auth token |
| `DISCORD_WEBHOOK_URL` | Discord webhook for alerts |
| `TELEGRAM_BOT_TOKEN` | Telegram bot |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook verification |
| `STRIPE_SECRET_KEY` | Stripe API key |
| `CF_MINTER_TOKEN` | Cloudflare token minting |
| `CF_ACCOUNT_ID` | Cloudflare account |
| `ELEVENLABS_API_KEY` | Voice synthesis |

**Optional — agent config:**

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT` | `my-agent` | Default agent name |
| `SOS_REDIS_URL` | `redis://localhost:6379/0` | Full Redis URL |
| `SOS_LOG_LEVEL` | `INFO` | Log level |
| `MCP_RATE_LIMIT_PER_MINUTE` | 60 | MCP rate limit |
| `CALCIFER_CYCLE` | 600 | Heartbeat interval (seconds) |
| `SENTINEL_POLL_INTERVAL` | 30 | Bus scan interval (seconds) |

### 14.2 Governance Config

Per-tenant at `~/.sos/governance/{tenant}.yaml`:

```yaml
# Override specific action tiers
content_publish: act_freely
outreach_email: batch_approve
social_post: batch_approve
payment_create: human_gate
deploy_production: human_gate
credential_change: dual_approval
tenant_delete: dual_approval
data_delete: dual_approval
```

### 14.3 Tenant Config

Per-tenant at `/home/{tenant}/.sos/.env`:

```
SOS_TOKEN=sk-bus-{name}-{hex}
MIRROR_TOKEN=sk-mumega-{name}-{hex}
MCP_URL=https://mcp.mumega.com/sse/{token}
CF_API_TOKEN={scoped-d1-token}
```

---

## 15. Known Limitations & Roadmap

### 15.1 Known Issues

- **Mirror has no backup/replication strategy.** Single Supabase instance. If it goes down, memory is unavailable.
- **Redis single instance.** No persistence config, no replication. Data loss on crash.
- **MCP SSE monolith.** `sos_mcp_sse.py` is ~73KB, handles all 14 tools. Should be split via registry.
- **No external monitoring.** Calcifer monitors internally only. No Grafana/Prometheus/PagerDuty.
- **Redis 16 DB limit.** Need namespacing (key prefixes) at scale instead of separate DBs.
- **Mirror not truly multi-tenant at DB level.** API-level isolation only (project field filter). No separate tables/schemas.
- **No secret rotation mechanism.** Tokens are static once generated. `kernel/rotator.py` exists but not wired.
- **Agent definitions in 3 formats.** `agents/definitions.py`, `AGENT_TEMPLATE.md`, and `personas/`. Should consolidate.
- **Analytics uses mock data.** GA4/GSC/Clarity APIs stubbed out. Real integration pending OAuth per-tenant connections.
- **Feedback loop untested with real data.** Works with mock data; needs live analytics to validate scoring.

### 15.2 Open GitHub Issues

| # | Title | Priority |
|---|-------|----------|
| 76 | ToRivers integration — SOS workflows as marketplace automations | P1 |
| 77 | MCP transport — consolidate to Streamable HTTP only | P2 |
| 78 | E2E test suite — verify full agent lifecycle | P2 |
| 81 | Publish sos-sdk to PyPI and npm | P2 |
| 82 | Agent Docker registry — pre-built images for common frameworks | P3 |
| 83 | Documentation site — SOS developer docs | P3 |
| 73 | Rust + WASM Runtime (v3 vision) | P5 |

### 15.3 Roadmap

**P0 — Revenue critical:**
- Wire real GA4/GSC/Clarity APIs via per-tenant OAuth
- Stripe checkout → provisioning end-to-end test
- First paying customer workstation live

**P1 — Platform stability:**
- ToRivers bridge live (SOS workflows on marketplace)
- Redis persistence config (AOF or RDB)
- Mirror backup to R2 or S3

**P2 — Developer experience:**
- Publish `sos-sdk-py` to PyPI, `sos-sdk-ts` to npm
- E2E test suite (agent joins → gets task → completes → feedback scored)
- Consolidate MCP to Streamable HTTP (drop SSE+stdio)
- External monitoring (Prometheus + Grafana or Uptime Kuma)

**P3 — Scale:**
- Agent Docker images (pre-built for LangGraph, CrewAI, Vertex ADK)
- Redis key-prefix namespacing (replace DB-per-tenant)
- Mirror true multi-tenancy at DB level (Supabase RLS or separate schemas)
- Documentation site (GitHub Pages or Cloudflare Pages)

**P4 — Ecosystem:**
- Secret rotation (automatic, scheduled)
- Agent marketplace (ToRivers)
- Mobile dashboard
- Webhook integrations (Slack, Teams, Linear)

**P5 — Future:**
- Rust + WASM runtime for kernel hot path
- Edge deployment (Cloudflare Workers for bus)
- Federated bus (multi-VPS, multi-region)
- Agent-to-agent encrypted channels

---

## 16. Changelog

### v3.0 (April 8-9, 2026) — The Big Session

The 24-hour build session that transformed SOS from a bus + task service into a full business operating system.

**Microkernel:**
- Service registry with TTL-based liveness (`kernel/registry.py`)
- Event bus with 20 event types, pub/sub, persistent replay (`kernel/events.py`)
- Default event handlers — wiring between services (`kernel/event_handlers.py`)
- Governance with 4 tiers, intent logging, per-tenant config (`kernel/governance.py`)

**Analytics Flywheel:**
- Ingest service — GA4, GSC, Clarity → Mirror (`services/analytics/ingest.py`)
- Decision agent — compare weeks, generate tasks (`services/analytics/decide/agent.py`)
- Action agent — execute via SitePilotAI + GHL (`services/analytics/act.py`)
- Feedback loop — observe, compare, score, learn, adapt (`services/feedback/loop.py`)

**Multi-Tenancy:**
- Tenant registry with Redis DB allocation (`services/bus/tenants.py`)
- Stripe webhook → auto-provisioning (`services/billing/webhook.py`, `provision.py`)
- Tenant setup script — 10-step Linux user creation (`cli/tenant-setup.sh`)
- Per-tenant OAuth connections (`services/integrations/oauth.py`)
- Cloudflare D1 token minting per tenant

**Adapters:**
- SOSBaseAdapter — shared HTTP client for all frameworks (`adapters/base.py`)
- LangGraph adapter (`adapters/langgraph/adapter.py`)
- CrewAI adapter (`adapters/crewai/adapter.py`)
- ToRivers bridge — 5 SOS workflows as marketplace automations (`adapters/torivers/bridge.py`)

**Agents:**
- Self-onboarding — one MCP call, 10 steps, full team member (`agents/join.py`)
- Teleportation — automated agent migration to server (`agents/teleport.py`)
- Sentinel — bus security monitor with challenge protocol (`agents/sentinel.py`)

**Services:**
- Customer dashboard (:8090) — FastAPI, dark theme, bus token auth (`services/dashboard/app.py`)
- Outreach engine — lead gen + email automation (`services/outreach/engine.py`)
- Calcifer self-healing — detect → restart → verify → escalate (`services/health/calcifer.py`)

**DevOps:**
- Docker Compose — full stack with 11 containers (`docker-compose.yml`)
- CI/CD — lint → import test → pytest → SSH deploy (`.github/workflows/ci.yml`)
- Install script — one-line curl installer (`install.sh`)
- Interactive setup wizard (`cli/init.py`)

**Security:**
- Hardcoded tokens removed (3 security commits)
- Rate limiting on MCP SSE
- Audit logging for all MCP calls
- Sandbox execution for untrusted code

**Documentation:**
- 16 skill definitions in `sos/skills/`
- Full `.env.example` with all config options
- README.md updated

### v2.0 (April 5-6, 2026)

- Squad Service — tasks, squads, skills, pipelines (:8060)
- MCP SSE server — persistent HTTP transport (:6070)
- Token authentication on all endpoints
- Agent wake daemon — real-time message delivery
- Onboarding API — `join.py` first version
- Agent comms standard (MCP tools only, no raw Redis)
- Bus bridge with multi-tenant project scoping

### v1.0 (Pre-April 2026)

- Original Redis bus (stdio MCP)
- Mirror memory API (Supabase pgvector)
- Basic agent coordination (tmux + manual)
- Engine service with multi-model routing
- Economy service with work ledger
- Identity service with QNFT
- Voice service (ElevenLabs)
- Content service with daily blog
- Telegram bot adapter

---

## Dependencies

Core (`requirements.txt`):
```
fastapi>=0.100.0
uvicorn>=0.23.0
httpx>=0.24.0
pydantic>=2.0.0
PyNaCl>=1.5.0
redis>=5.0.0
python-dotenv>=1.0.0
```

Optional (installed per-service):
- `stripe` — billing webhook
- `sse-starlette` — MCP SSE transport
- `google-auth` — GA4/GSC OAuth
- `pyyaml` — governance config
- `structlog` — structured logging

---

*End of SOS v3.0 documentation. This file is the single source of truth for the system as of April 9, 2026.*
