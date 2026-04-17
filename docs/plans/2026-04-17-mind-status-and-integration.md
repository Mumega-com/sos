# $MIND / Economy status + SOS integration plan

**Date:** 2026-04-17
**Author:** sos-dev
**Status:** Reference (describes current state; defers activation decisions to Hadi)
**Related:** WHITEPAPER.md, `sos/services/economy/`, Mumega product roadmap Phase 6

## What exists today (actual file inventory)

`sos/services/economy/` directory contains:

```
app.py                — FastAPI service
backends.py           — storage backends (DB, Solana)
Dockerfile            — container spec
metabolism.py         — cron-driven digest (runs every 4h)
payment_status.py     — payment state machine
wallet.py             — wallet abstraction with SolanaWallet integration
worker_registry.py    — worker (agent) registration for bounties
work_ledger.py        — transaction log (MIND-denominated)
work_matching.py      — bounty → worker matching
work_notifications.py — notify bounty events
work_settlement.py    — settle completed work (MIND payout via treasury)
work_slashing.py      — slash workers who fail
work_supabase.py      — Supabase backend for ledger
```

Plus `sos/plugins/economy/solana.py` (referenced) for actual Solana chain integration.

**This is not a skeleton — it's a partial implementation of the $MIND protocol described in `WHITEPAPER.md`.** Key things that work:

- Work units carry a `payout_currency` (default `MIND`)
- Wallets sync with real Solana balances (devnet)
- Minting is logged as proof on Solana
- Slashing is implemented
- Supabase ledger is the persistent backend

**Mumega-docs `vision/roadmap.md` confirms** Phase 2 current list includes: **"Economy service — decide: start it or archive it"**.

Phase 6 (far future) confirms: "Economy service live ($MIND tokens, bounty board, Solana settlement)".

So economy is **in flight, but not running in production yet**.

## Cron already wired

`crontab -l` (mumega user) shows:

```
0 */4 * * * /usr/bin/python3 /home/mumega/SOS/sos/services/economy/metabolism.py digest
```

Every 4 hours. Already running. This is the "metabolism" that aggregates work ledger entries into settlement digests.

So economy is **partially live** — the metabolism digest runs on cron, but the full settlement pipeline + bounty marketplace + worker registration aren't exercising production flows yet.

## Three possible statuses

### (1) Active — operate it now

Treat economy as v0.4-ready infrastructure. Flip the switch in the SaaS service to record real work units in the ledger. Phase 2 item gets ticked as "active."

- **Pro:** captures operational data from day one. $MIND issuance starts (devnet).
- **Con:** unexplored edge cases — slashing logic, workers who never get paid, accounting reconciliation

### (2) Dormant — code stays, not connected

Code remains in the repo. Cron still runs metabolism digest. No new code written on economy until Phase 6. Mumega product runs without economy-layer involvement.

- **Pro:** zero risk, zero maintenance
- **Con:** code rot — every SOS refactor may break economy/ without us noticing

### (3) Archived — move out of the critical path

Git-move `sos/services/economy/` → `sos/archive/economy/` (or to a separate `Mumega-com/mind-protocol` repo). Signal: "this is the $MIND future vision, not current SOS core."

- **Pro:** cleanest
- **Con:** painful re-import when Phase 6 starts

## Recommendation: **Option 1 (Active) with measured activation**

### Why

- WHITEPAPER.md is already public and commits Mumega to the protocol. Walking back would be awkward.
- The code is there. Not flipping it on is leaving operational data on the table.
- Metabolism cron is already running; going from 4h digest → full ledger is one commit, not a rewrite.
- Provider Matrix (v0.4.1) produces cost data that economy can consume natively.

### How — staged activation across v0.4 releases

**v0.4.0 (current) — no economy changes**

Ship Contracts. Don't touch economy. Prepare:
- Economy endpoint OpenAPI spec added to `sos/contracts/openapi/economy.yaml`
- Economy error codes added to SOS-XXXX taxonomy (reserve 9xxx range)

**v0.4.1 Provider Matrix — economy consumes provider cost data**

- Provider Cards include cost_per_mtok; work_ledger reads these and records real cost per tool call
- First-time integration between economy and the core SOS services (squad → economy ledger on task completion)
- Metabolism digest gains "tokens spent this 4-hour window per tenant" column

**v0.4.2 Observability — economy health is a breakable**

- `mumega-watch` probes economy service `/health` and metabolism cron last-run
- Economy failures don't cascade (ledger drift is less critical than routing failure)

**v0.4.3 Dispatcher — economy enforced on the hot path**

- Dispatcher writes ledger entries for every authenticated request (tenant, agent, cost estimate)
- If a tenant's budget is exhausted: dispatcher returns SOS-4001 (Metabolism gate) — don't invoke LLM at all

**v0.5+ — economy grows into the bounty system per WHITEPAPER.md**

- `work_matching.py` becomes the routing brain for non-squad external work
- Devnet → mainnet evaluation
- $MIND minting gate (coherence score ≥ 0.5) enforced on settlement

### Activation risk minimization

- **Start with recording, not paying.** Ledger captures data; actual $MIND minting stays devnet only until Phase 6.
- **Shadow-mode FMAAP Metabolism pillar first.** The gate logs "would-have-blocked" events but still lets actions through. When false-positive rate is near zero, flip to enforce.
- **Per-tenant ledger isolation is already in economy/ code** (verify; if not, add it).

## Integration with v0.4 contracts

Economy needs its own contracts in v0.4.0 scope:

1. **WorkUnit Card** — JSON Schema for a work unit (task + cost + worker + timestamps)
2. **LedgerEntry Card** — schema for ledger rows (earn, spend, mint, burn, slash)
3. **Wallet Card** — squad/agent wallet state (MIND balance, reserved, available)

Same pattern as Agent Card v1. Same subagent squad (sos-schema-author + pydantic + tester) can produce these in Week 2 of v0.4.0 alongside the bus message schemas.

That gets economy schemas frozen early, so Provider Matrix (v0.4.1) has a stable target to write ledger entries against.

## Decisions open for Hadi

| # | Question | Default |
|---|---|---|
| MI1 | Economy status — Active (recommended), Dormant, or Archived? | Active (staged activation) |
| MI2 | Add Economy schemas (WorkUnit, LedgerEntry, Wallet) to v0.4.0 scope? | Yes (1-2 days extra, worth it for Provider Matrix integration) |
| MI3 | Devnet → mainnet timing — keep devnet-only until Phase 6, or activate earlier? | Devnet-only until Phase 6 per WHITEPAPER timeline |
| MI4 | WHITEPAPER.md update cadence — reflect v0.4 state before v1.0 launch? | Yes, pre-v0.9 — whitepaper needs to match actual code when Go Viral happens |

## Sources

- WHITEPAPER.md (this repo) — $MIND protocol spec, already public
- `sos/services/economy/*.py` — current partial implementation
- `mumega-docs:vision/roadmap.md` Phase 2 + Phase 6

## One-line summary

Economy/$MIND is already partially wired with cron + Solana integration. Activate it in staged fashion across v0.4.1-v0.4.3, starting with cost-recording (not paying). Add 3 new Cards (WorkUnit, LedgerEntry, Wallet) to v0.4.0 Contracts scope so Provider Matrix has stable schemas to write against. Devnet-only until Phase 6 per whitepaper.
