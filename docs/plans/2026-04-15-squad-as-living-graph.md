# Squad as Living Graph — Architecture Plan

**Date:** 2026-04-15
**Author:** Kasra
**Goal:** Transform Squad Service from task dispatcher into living entity with profile, wallet, goal physics, and knowledge graph. Scale target: 1M squads.

## Current State

Squad Service (`:8060`) has:
- ✅ Squads with tiers (Nomad/Fortress/Construct), roles, members
- ✅ Tasks with priority, claiming, completion
- ✅ Skills with fuel grades
- ✅ Events log
- ✅ Tenant isolation (tenant_id on all tables)
- ❌ No DNA profile (16D vector)
- ❌ No wallet/treasury
- ❌ No goal physics (G = T, M, C*, τ)
- ❌ No conductance routing
- ❌ No knowledge graph per squad
- ❌ No FMAAP validation on actions

## What to Add

### 1. Squad Profile (16D DNA)
Add to `squads` table:
```sql
ALTER TABLE squads ADD COLUMN dna_vector TEXT;  -- JSON array of 16 floats
ALTER TABLE squads ADD COLUMN coherence REAL DEFAULT 0.5;
ALTER TABLE squads ADD COLUMN receptivity REAL DEFAULT 0.5;
ALTER TABLE squads ADD COLUMN conductance_json TEXT DEFAULT '{}';  -- skill→G mapping
```

The DNA vector is generated from squad attributes (industry, communication style, values) using the same formula from `mirror/agent_dna.py`.

### 2. Squad Wallet
```sql
CREATE TABLE IF NOT EXISTS squad_wallets (
    squad_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    balance_cents INTEGER NOT NULL DEFAULT 0,
    total_earned_cents INTEGER NOT NULL DEFAULT 0,
    total_spent_cents INTEGER NOT NULL DEFAULT 0,
    fuel_budget_json TEXT NOT NULL DEFAULT '{}',  -- {diesel: X, regular: Y, ...}
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS squad_transactions (
    id TEXT PRIMARY KEY,
    squad_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    type TEXT NOT NULL,  -- earn|spend|transfer|mint
    amount_cents INTEGER NOT NULL,
    counterparty TEXT,  -- other squad_id or 'system'
    reason TEXT NOT NULL,
    task_id TEXT,
    created_at TEXT NOT NULL
);
```

### 3. Squad Goals (FRC-Agentic)
```sql
CREATE TABLE IF NOT EXISTS squad_goals (
    id TEXT PRIMARY KEY,
    squad_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    target TEXT NOT NULL,  -- what "done" looks like
    markers_json TEXT NOT NULL DEFAULT '[]',  -- measurable checkpoints
    coherence_threshold REAL NOT NULL DEFAULT 0.6,
    deadline TEXT,
    status TEXT NOT NULL DEFAULT 'active',  -- active|achieved|abandoned
    progress REAL NOT NULL DEFAULT 0.0,  -- 0.0 to 1.0
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 4. Conductance Routing
```python
# In squad service, when routing a task:
def route_task(task):
    # Find squads with matching skills
    candidates = find_squads_with_skill(task.skill)
    
    # Score by conductance: G = accumulated flow for this skill
    for squad in candidates:
        G = squad.conductance.get(task.skill, 0.1)
        squad.score = G * squad.coherence
    
    # Route to highest-scoring squad
    return max(candidates, key=lambda s: s.score)

# After task completion, update conductance:
def on_task_complete(squad, task):
    G = squad.conductance.get(task.skill, 0.1)
    G_new = G + abs(task.value) ** gamma  # dG/dt = |F|^γ - αG
    squad.conductance[task.skill] = G_new

# Periodic decay (every hour):
def decay_conductance(squad):
    for skill, G in squad.conductance.items():
        squad.conductance[skill] = G * (1 - alpha)  # decay
```

### 5. FMAAP Gate
Every action through the Squad Service passes through FMAAP:
```python
def validate_action(agent_id, action, resource, squad):
    return FMAAPPolicyEngine().validate(
        flow=squad.coherence,
        metabolism=squad.wallet.balance > 0,
        alignment=agent_dna_distance(agent, squad) < threshold,
        autonomy=agent in squad.members,
        physics=squad.coherence > squad.goal.coherence_threshold,
    )
```

### 6. Peers Scoping (already done)
Non-system tokens only see agents in their project scope.

## Migration Plan

1. **Phase 1** — Schema: Add columns + new tables (backward compatible)
2. **Phase 2** — Endpoints: POST /squads/{id}/wallet, GET /squads/{id}/profile, POST /squads/{id}/goals
3. **Phase 3** — Routing: Conductance-based task dispatch
4. **Phase 4** — FMAAP: Validate all actions
5. **Phase 5** — Graph: Each squad's knowledge graph stored in code-review-graph

## Work Assignment

| Task | Agent | Fuel |
|------|-------|------|
| Schema migration (SQLite) | Kasra | opus |
| Wallet endpoints + transactions | sos-dev subagent | sonnet |
| Goal endpoints + progress tracking | sos-dev subagent | sonnet |
| DNA generation for squads | Kasra | opus |
| Conductance routing logic | Kasra | opus |
| FMAAP real implementation | Gemini research → Kasra build | opus |
| Tests | haiku subagent | haiku |
