# Session log — 2026-04-18 — Kernel durability arc (v0.5.0 → v0.5.3)

**Branch:** `codex/sos-runtime-validation`
**Start tag:** v0.4.8
**End tag:** v0.5.3
**Commits:** 5 (v0.5.0, v0.5.1, v0.5.2, v0.5.3, plan docs)

## What shipped

| Tag | Theme | What it locks in |
|-----|-------|------------------|
| `v0.5.0` | Floor + audit | R0 contract: kernel cannot import `sos.services.*`. Disk-authoritative audit stream in `sos/kernel/audit.py`. `AuditEvent` frozen. |
| `v0.5.1` | Unified gate | `sos/kernel/policy/gate.py::can_execute()` composes bearer + scope + capability + FMAAP + governance in one call. `PolicyDecision` frozen. `integrations/app.py` migrated as POC. |
| `v0.5.2` | Arbitration | `sos/kernel/arbitration.py::propose_intent/arbitrate`. Proposals ARE audit INTENT events — read-over-audit + decision function, no new storage. `ArbitrationDecision` + `LoserRecord` frozen. `propose_first=True` added to `can_execute`. Legacy `~/.sos/governance/intents/` shim removed. |
| `v0.5.3` | Gate wave 1 | Generalized `can_execute()` across 5 more services: economy (4), registry (2), identity (2), journeys (4), operations (3) = 15 routes. All parallelized via sonnet subagents. |

## The architectural spine

```
┌─ sos.kernel ────────────────────────────────────────────────┐
│                                                             │
│  audit (v0.5.0)    — disk spine. append_event / read_events │
│    │                                                        │
│    ├─► policy.gate (v0.5.1) — can_execute() → PolicyDecision│
│    │       │                                                │
│    │       ├─ bearer / scope / capability / FMAAP / tier    │
│    │       │                                                │
│    │       └─ propose_first=True (v0.5.2) ─┐                │
│    │                                        │                │
│    ├─► arbitration (v0.5.2) ◄──────────────┘                │
│    │       propose_intent / arbitrate                        │
│    │                                                         │
│    └─► governance — consults gate first (v0.5.1)             │
│                                                              │
│  contracts.audit / contracts.policy / contracts.arbitration  │
│    — all frozen Pydantic v2, additive-only forever.          │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Proof

- **Kernel + contracts**: 442 tests green, 0 failures across v0.5.0 → v0.5.3.
- **`lint-imports`**: 4 contracts kept, 0 broken (R1 service independence, R2 clients/adapters, R5 deprecated, R6 contract tests).
- **Schema stability tests**: `AuditEvent`, `PolicyDecision`, `ArbitrationDecision` locked via snapshot tests. Breaking adds fails CI.

## Gate reach — 6 / 14 services

| Migrated | Routes | Note |
|----------|--------|------|
| integrations (v0.5.1) | 3 | POC |
| economy (v0.5.3) | 4 | _verify_bearer + _resolve_tenant removed |
| registry (v0.5.3) | 2 | _resolve_project_scope retained for sub-tenant |
| identity (v0.5.3) | 2 | Scopeless-but-verified 403 short-circuit added |
| journeys (v0.5.3) | 4 | All admin-only — require_system=True |
| operations (v0.5.3) | 3 | All admin-only |

## Deferred to v0.5.4 → v0.5.6

- **v0.5.4** — `saas/app.py` (39 routes). Custom auth: `MUMEGA_MASTER_KEY` admin + tokens.json slug lookup. Not a straight `can_execute` swap.
- **v0.5.5** — `squad/` (28 routes + `auth.py` with capability/role model). Uses `require_capability(capability, roles)` — pass `capability=` to the existing gate signature.
- **v0.5.6** — `gateway/bridge.py` (~8 routes). External-agent API keys, independent tenant registry — again not a straight swap.

Real scope for these three: most have auth patterns the kernel gate wasn't designed for. The realistic migration is *audit-wrapper* (keep native auth, add `POLICY_DECISION` emit per route) except for squad, which genuinely fits the gate's `capability` parameter.

## Session protocol that worked

- **Plan doc per version** in `docs/plans/YYYY-MM-DD-<slug>.md` — keeps each sprint scoped + reviewable.
- **Parallel sonnet subagents for mechanical work** — v0.5.3 dispatched 5 migrations concurrently.
- **Schema-stability snapshots locked on day one** — every frozen contract has a `test_<name>_schema_stable.py` enforcing additive-only.
- **`propose_first=False` default** on the gate — new capabilities are opt-in; existing callers never break.

## Still on the working tree (not this arc's work)

`sos/services/saas/*`, `sos/services/bus/delivery.py`, `sos/kernel/agent_registry.py`,
`sos/services/squad/tasks.py`, plus many untracked agents/adapters/docs. Pre-existing
codex-branch work; belongs to a separate sprint.
