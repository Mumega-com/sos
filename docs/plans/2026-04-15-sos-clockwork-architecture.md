# SOS Clockwork Architecture — The Deterministic Business OS

**Date:** 2026-04-15
**Author:** Kasra
**Approach:** Option C — Hormonal Clock + Inkwell Self-Documentation + Enterprise Security

## The Three Layers

```
┌──────────────────────────────────────────────────────┐
│                   INKWELL (Glass)                      │
│  Self-documenting dashboard — charts, metrics, logs   │
│  Every action → page. Every metric → chart.           │
│  Humans, agents, and businesses read the same truth.  │
└──────────────────────────┬───────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────┐
│                   SOS (Clock)                         │
│  Deterministic scaffolding — cron, FSM, FMAAP gates  │
│  LLMs fire in windows. Conductance routes work.      │
│  Budget caps. Audit trail. State machine transitions. │
└──────────────────────────┬───────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────┐
│                   MIRROR (Memory)                     │
│  Encrypted engrams. Tenant-isolated pgvector.         │
│  Agent DNA. Budget enforcement. Audit log.            │
└──────────────────────────────────────────────────────┘
```

## Layer 1: The Clock (SOS Runtime)

### Rhythms — deterministic, no LLM needed

| Rhythm | Interval | What runs | LLM? |
|--------|----------|-----------|------|
| **Heartbeat** | 60s | Lifecycle check: alive/dead/stuck/parked | No |
| **Pulse** | 5min | Metrics snapshot: CPU, Redis, disk, queue depth | No |
| **Breath** | 1hr | Conductance decay: G *= (1-α) for all skills | No |
| **Circadian** | 24hr (6am) | Flywheel: ingest GSC/GA4 → store → score → report | No (data pull) |
| **Weekly** | Mon 5:30am | Brain cycle: PERCEIVE → THINK → ACT → REMEMBER → REPORT | Yes (planning only) |
| **Monthly** | 1st of month | Treasury reconciliation, goal review, DNA drift check | Yes (review only) |

### State Machine per Squad

```
ONBOARDING → ACTIVE → OPERATING → REVIEW
     ↑                    │           │
     └────────────────────┘           │
              PAUSED ←────────────────┘
```

Each state has:
- **Allowed actions** (whitelist, not blacklist)
- **Budget ceiling** (max spend per state)
- **FMAAP gate** (all 5 pillars must pass)
- **Transition guards** (what triggers state change)

### FMAAP — Real Implementation

```python
def validate(agent_id, action, squad):
    # 1. FLOW — is the system in harmony?
    flow = squad.coherence >= 0.4  # below 0.4 = system chaos, halt
    flow_score = squad.coherence

    # 2. METABOLISM — can we afford this?
    cost = estimate_cost(action)  # deterministic lookup table
    metabolism = squad.wallet.balance >= cost
    metabolism_score = min(1.0, squad.wallet.balance / max(cost, 1))

    # 3. ALIGNMENT — does this match the squad's purpose?
    alignment = action.skill in squad.conductance  # agent can only do what squad knows
    alignment_score = squad.conductance.get(action.skill, 0.0)

    # 4. AUTONOMY — is the agent authorized?
    autonomy = agent_id in [m.agent_id for m in squad.members]
    autonomy_score = 1.0 if autonomy else 0.0

    # 5. PHYSICS — is coherence stable?
    physics = squad.coherence >= squad.goal.coherence_threshold
    physics_score = squad.coherence / squad.goal.coherence_threshold

    return FMAAPResult(
        valid=all([flow, metabolism, alignment, autonomy, physics]),
        scores=[flow_score, metabolism_score, alignment_score, autonomy_score, physics_score],
    )
```

### Cost Table — Deterministic, No Surprises

| Fuel Grade | Cost/1M tokens | Models | When used |
|------------|---------------|--------|-----------|
| **Diesel** | $0 | Gemma 4 31B, Gemini Flash, GPT-4o-mini | Routine: reports, content, analysis |
| **Regular** | $0.35 | Haiku 4.5, Flash | Standard tasks |
| **Premium** | $5 | Sonnet 4.6 | Complex code, integrations |
| **Aviation** | $15 | Opus 4.6 | Architecture, critical decisions |

Budget per squad per month = wallet balance. When empty, only diesel runs. Deterministic.

### Conductance Routing — No Dispatcher Needed

```
Task arrives → extract skill label
  → query all squads with that skill
    → score = G[skill] × coherence × (1 if budget > 0 else 0)
      → route to highest score
        → on completion: G[skill] += |value|^γ
          → on decay (hourly): G[skill] *= (1 - α)
```

No LLM in the routing loop. Pure math. O(n) where n = squads with that skill.

## Layer 2: The Glass (Inkwell Self-Documentation)

Every squad gets an Inkwell instance that documents everything automatically.

### What Gets Published (by cron, not LLM)

| Page | Source | Update frequency | Charts |
|------|--------|-----------------|--------|
| `/dashboard` | D1 marketing_snapshots | Daily 6am | Line: clicks/impressions WoW |
| `/health` | Lifecycle state + Redis | Every 5min | Status badges: green/yellow/red |
| `/transactions` | squad_transactions table | On event | Table: earn/spend/transfer log |
| `/goals` | squad_goals table | On update | Progress bars per goal |
| `/team` | squad members + DNA | On change | Radar chart: 16D DNA vector |
| `/metrics` | GSC + GA4 snapshots | Daily | Bar: top queries. Line: traffic trend |
| `/decisions` | squad_events log | On event | Timeline: what happened when |
| `/costs` | cost_events table | On event | Donut: spend by fuel grade |

### How It Works

```
Cron (deterministic) → SQL query → JSON → Inkwell template → static page
```

No LLM generates the dashboard. It's a SQL query rendered to a template. Gemini CLI (free) writes the narrative summary once per week — that's the only LLM touch.

### Who Reads It

| Reader | What they see | Access |
|--------|--------------|--------|
| **Business owner** | Dashboard, metrics, costs, goals | Web (their Inkwell domain) |
| **Agent** | Tasks, health, team, decisions | MCP tools (get_dashboard) |
| **Mumega team** | All squads aggregated | Internal Inkwell at mumega.com |
| **Auditor** | Transactions, decisions, FMAAP logs | Read-only API with auth |

## Layer 3: Enterprise Security

### Tenant Isolation (already partially built)

| Boundary | Implementation | Status |
|----------|---------------|--------|
| **Network** | Each customer = separate Linux user (like /home/viamar/) | ✅ Done |
| **Database** | tenant_id on every table, enforced at query level | ✅ Done |
| **Redis** | Separate Redis DB per tenant (DB 0=system, DB 1+=tenants) | 🔲 Designed, not enforced |
| **Bus** | Project-scoped tokens, peers filtered by scope | ✅ Fixed today |
| **Memory** | Tenant-scoped engrams in Mirror, separate embedding namespaces | ✅ Done |
| **Files** | Linux user permissions (chmod 700 home dirs) | ✅ Done |

### Auth & Access Control

| Component | Method | Notes |
|-----------|--------|-------|
| **MCP SSE** | Bearer token per agent | tokens.json with agent identity |
| **Squad API** | Bearer token per tenant | api_keys table with tenant_id |
| **Mirror API** | Bearer token per agent | tenant_keys.json with agent_slug |
| **Inkwell API** | Per-customer publish token | INKWELL_PUBLISH_TOKEN in wrangler.toml |
| **WordPress** | SitePilotAI API key per site | Role-scoped (reader/designer/admin) |

### Encryption

| Data | At rest | In transit |
|------|---------|------------|
| **Engrams** | PostgreSQL encryption (pgcrypto) | HTTPS to Mirror API |
| **Secrets** | Linux file permissions (600) | Never in Redis, never in logs |
| **Bus messages** | Redis AUTH + requirepass | Localhost only (no external Redis) |
| **Customer data** | D1 encryption (Cloudflare managed) | HTTPS + Cloudflare SSL |
| **Agent DNA** | Stored as vector in pgvector | Internal API only |
| **Wallet** | SQLite with file permissions | Squad Service localhost only |

### Audit Trail

Every action logged to three places:
1. **squad_events** table — structured events with actor, type, payload
2. **Mirror engrams** — semantic, searchable memory
3. **Inkwell /decisions** page — human-readable timeline

```
Agent does thing → FMAAP validates → squad_events records →
  Mirror stores engram → Inkwell page updates → auditor can query all three
```

### FMAAP as Security Layer

FMAAP isn't just governance — it's **security**:
- **Autonomy** pillar = authorization check (is this agent in this squad?)
- **Metabolism** pillar = budget check (can this tenant afford this?)
- **Alignment** pillar = scope check (is this action within squad's declared skills?)
- **Flow** pillar = health check (is the system stable enough for this action?)
- **Physics** pillar = coherence check (is this action consistent with squad identity?)

An attacker who compromises one agent still can't:
- Access another tenant (tenant_id isolation)
- Spend another squad's budget (wallet isolation)
- Execute skills the squad doesn't have (conductance = 0)
- Act if coherence is broken (physics gate blocks)

### Compliance Readiness

| Standard | How SOS addresses it |
|----------|---------------------|
| **SOC 2 Type II** | Audit trail (squad_events), access control (tokens), encryption, monitoring (lifecycle) |
| **ISO 42001** | FMAAP = AI governance framework, coherence threshold = human oversight, human_gate governance |
| **GDPR/PIPEDA** | Tenant isolation, data residency (customer chooses VPS location), right to delete (trash squad) |
| **PCI DSS** | Stripe handles payments, no card data in SOS, wallet is internal tokens only |

## Implementation Phases

### Phase 1: Wire the Clock (Week 1)
- Real FMAAP engine (replace skeleton)
- Cost lookup table (deterministic pricing)
- Conductance routing in Squad Service
- Hourly decay cron

### Phase 2: Wire the Glass (Week 2)
- Inkwell dashboard templates (SQL → static pages)
- Cron job: daily metric snapshot → Inkwell publish
- Health page with status badges
- Transaction log page

### Phase 3: Wire the Security (Week 3)
- Redis DB isolation per tenant
- Encrypt secrets at rest (not just file permissions)
- FMAAP audit logging to all three sinks
- Rate limiting per tenant on MCP SSE

### Phase 4: First Customer Package (Week 4)
- VPS snapshot script (capture golden image)
- First-boot initializer (inject customer config)
- Viamar as test deployment
- Document the whole thing on Inkwell

### Phase 5: Scale Test (Week 5+)
- 10 squads on one VPS (multi-tenant)
- 100 squads across 10 VPS (federated)
- Conductance routing under load
- FMAAP gate latency benchmarks

## The Promise

When this is done, onboarding a customer looks like:

```bash
# 1. Spin up instance from snapshot
gcloud compute instances create viamar-squad \
  --source-machine-image=sos-golden-v1 \
  --zone=northamerica-northeast1-a

# 2. Inject customer config
ssh viamar-squad 'sos init --config customer.json'

# 3. Done. The clock starts ticking.
# - Heartbeat: 60s
# - Flywheel: daily 6am
# - Brain: weekly Monday
# - Dashboard: auto-updating
# - Budget: capped
# - Security: enterprise-grade
# - FMAAP: gating every action
```

Total human intervention: zero after onboarding.
Cost: $5-25/month infrastructure + diesel fuel (free models).
Revenue: $500-1,500/month per customer.
Margin: 95%+.
