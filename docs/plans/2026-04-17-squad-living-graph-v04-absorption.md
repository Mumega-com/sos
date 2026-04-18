# Squad-as-Living-Graph — v0.4 absorption plan

**Date:** 2026-04-17
**Author:** sos-dev
**Status:** Recommendation
**Depends on:** Kasra's 2026-04-15 plan (`docs/plans/2026-04-15-squad-as-living-graph.md`)
**Relates to:** v0.4.0 Contracts scope, v0.4.1 Provider Matrix, $MIND status plan

## The question

Kasra's 2026-04-15 plan specs squad becoming a "living entity": 16D DNA vector, wallet with MIND balance, goals with coherence thresholds, conductance routing, FMAAP gate on every action. Schema-level work already landed (columns + tables exist in `squads.db`) but the data isn't populated consistently and the FMAAP gate is a skeleton.

Does v0.4 absorb this work, or does it stay a parallel track?

## Current state

Inspecting the live DB schema:

```sql
-- already exists in squads.db
squads:           id, tenant_id, name, project, objective, tier, status,
                  roles_json, members_json, kpis_json, budget_cents_monthly,
                  created_at, updated_at,
                  dna_vector (NEW, text/json), coherence (NEW, real),
                  receptivity (NEW, real), conductance_json (NEW, json)

squad_wallets:    squad_id, tenant_id, balance_cents, total_earned_cents,
                  total_spent_cents, fuel_budget_json, updated_at

squad_transactions: id, squad_id, tenant_id, type, amount_cents,
                    counterparty, reason, task_id, created_at

squad_goals:      id, squad_id, tenant_id, target, markers_json,
                  coherence_threshold, deadline, status, progress,
                  created_at, updated_at
```

All four tables exist. The schema landed via the commits leading up to today.

What's incomplete:
- **DNA vectors** — column exists, rows empty. No code generates the 16D vector yet.
- **Coherence / receptivity** — defaults (0.5, 0.7) set, never updated by any service.
- **Conductance** — `conductance_json` defaults `{}`, no accumulation on task completion, no decay cron.
- **Wallets** — populated for `trop` (today's onboard, 3000¢ sonnet fuel). Others missing.
- **Goals** — table empty.
- **FMAAP gate** — skeleton in `sos/services/common/fmaap.py`, not invoked on hot paths (TODO comment I read earlier today).

## Three options

### Option A — Full absorption into v0.4.x

Kasra's schema becomes the reference. v0.4.0 Contracts writes Card schemas for each squad entity (Squad Card, Wallet Card, Goal Card, Conductance Card). v0.4.1 Provider Matrix writes FMAAP gate integration. v0.5+ fills in DNA generation, conductance routing.

- **Pro:** single coherent roadmap, contracts cover everything
- **Con:** expands v0.4.x scope by 30-50%; delays ship

### Option B — Minimal absorption

v0.4.0 Contracts adds ONLY Squad Card + Wallet Card schemas (the two most-used entities). FMAAP gate + conductance stay in Kasra's own track. DNA + goals defer to v0.5-v0.7.

- **Pro:** bounded scope, v0.4.0 ships on time
- **Con:** parallel tracks risk drift; Kasra's work may land before or after contracts

### Option C — Reference plan only, track in v0.4.1+

v0.4.0 doesn't touch squad-living-graph. Kasra ships his schema work; v0.4.1 Provider Matrix retroactively adds FMAAP gate integration (Metabolism pillar) against whatever state exists. Conductance + DNA land when explicitly triggered (a squad needs them).

- **Pro:** don't expand v0.4.0 scope at all
- **Con:** FMAAP + Provider Matrix integration becomes a "discovery" exercise during v0.4.1, higher risk of mid-release surprises

## Recommendation: **Option B (minimal absorption)**

Why this split works:

### What v0.4.0 adds from Kasra's plan (3 new Cards)

1. **Squad Card v1** — `sos/contracts/schemas/squad_card_v1.json` + `sos/contracts/squad_card.py` + tests
   - Captures: id, tenant_id, name, objective, tier, status, roles, members, kpis, budget_cents_monthly, coherence, receptivity, conductance_json, dna_vector
   - Round-trip to `squads` table row
   
2. **Wallet Card v1** — `sos/contracts/schemas/wallet_card_v1.json` + ...
   - Captures: squad_id, tenant_id, balance_cents, total_earned_cents, total_spent_cents, fuel_budget_json
   - Round-trip to `squad_wallets` table row
   
3. **Transaction (LedgerEntry) Card v1** — shared with economy layer (see `2026-04-17-mind-status-and-integration.md`)
   - Captures squad_transactions + economy/work_ledger rows in a single schema

These three, plus the existing Agent Card v1, give us full squad observability at the contract layer without building any new behavior. A `/squads/{id}` endpoint can return a typed `SquadCard` response; v0.4.3 dispatcher can rate-limit based on `wallet_card.balance_cents`.

### What v0.4.1 adds (FMAAP wiring, not DNA)

Provider Matrix needs FMAAP Metabolism pillar to enforce "squad has a healthy provider in its required tier." That integration requires reading:
- Squad Card (for required tier based on squad.tier)
- Wallet Card (for balance check)
- Provider matrix health (for "at least one provider exists")

So v0.4.1 wires Metabolism against existing Squad+Wallet data. **No new DNA work** — FMAAP Physics pillar ("coherence") reads whatever's in the coherence column (default 0.5). If it's wrong, squads just get approved/blocked on the default value — no one dies.

### What defers to v0.5+

- DNA generation (16D vectors from squad attributes)
- Conductance accumulation + decay cron
- Goals table usage (populate, track progress, enforce)
- Full FMAAP Flow + Alignment + Physics pillar logic
- Per-squad knowledge graph (code-review-graph integration)

Each of those gets its own deliverable when a specific agent triggers the need. If nobody needs squad DNA for 6 months, it doesn't ship for 6 months. That's correct prioritization.

## Sprint scope delta

v0.4.0 Contracts grows by **3 new Cards + tests**. Using Haiku+Opus squad:

- Squad Card: ~3 Haiku calls (schema, pydantic, tests) = ~$0.15
- Wallet Card: same = ~$0.15
- LedgerEntry Card: same = ~$0.15
- Opus review of all 3 at contract gate = ~$0.50

Total added cost: **~$1.00**. Time: 1-2 days of dispatch. Acceptable expansion.

## Integration with Kasra's ongoing work

Kasra's plan is living at `docs/plans/2026-04-15-squad-as-living-graph.md`. His original work assignment lists:
- Schema migration (SQLite) — him, Opus fuel
- Wallet endpoints + transactions — sos-dev subagent, Sonnet fuel
- Goal endpoints + progress tracking — sos-dev subagent, Sonnet fuel
- DNA generation for squads — him, Opus fuel
- Conductance routing logic — him, Opus fuel
- FMAAP real implementation — Gemini research → Kasra build, Opus fuel
- Tests — Haiku subagent

**His plan assigned me the wallet + goal endpoint work.** I haven't done those yet. When I do (v0.4.0 Week 3 OpenAPI, v0.4.1 FMAAP wiring):

1. OpenAPI for squad service endpoints → covers the wallet + goal endpoints he assigned me
2. Pydantic models for Wallet Card + Goal Card → I write; he validates
3. FMAAP Metabolism gate → wires his skeleton to real checks

So absorption isn't conflict — it's alignment. Our schemas, our contracts, same data model.

## What to communicate to Kasra

**Send him a heads-up** when v0.4.0 Week 2 starts (message schemas) that I'm adding Squad Card + Wallet Card + LedgerEntry Card as sibling deliverables. He should validate the schemas match his DB columns exactly; if not we reconcile before contract tests land.

Draft bus message:
```
sos-dev here. Pulling your 2026-04-15 squad-as-living-graph schema into v0.4.0 Contracts
week 2 as three Agent-Card-pattern schemas (Squad, Wallet, LedgerEntry). Spec:
docs/plans/2026-04-17-squad-living-graph-v04-absorption.md

Checking with you before I publish: do the columns in squads.db still match your plan?
Any schema drift since Apr 15 I should know about? FMAAP wiring lands in v0.4.1 Metabolism
pillar against whatever coherence/receptivity values exist — no DNA generation required
for v0.4.x.

Your original task assignment (wallet + goal endpoints → sos-dev subagent) slots into
week 3 OpenAPI work. Writing against the frozen Card schemas.
```

## Decisions open

| # | Question | Default |
|---|---|---|
| SLG1 | Option B (minimal absorption)? Or A/C? | B |
| SLG2 | Add Squad + Wallet + LedgerEntry Cards to v0.4.0 Week 2? | Yes |
| SLG3 | FMAAP Metabolism wiring in v0.4.1 (as planned) OR in v0.4.2? | v0.4.1 |
| SLG4 | DNA generation — timeline? | Trigger-driven, no timeline commitment |

## One-line summary

Absorb just three new Cards (Squad, Wallet, LedgerEntry) into v0.4.0 Contracts Week 2 — one day of squad work, ~$1 added cost. FMAAP Metabolism gate lands in v0.4.1 as planned. DNA + conductance + goals stay trigger-driven beyond v0.5. Send Kasra a heads-up so his schema and mine don't drift.
