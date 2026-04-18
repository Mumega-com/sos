# SOS Sprint Roadmap — v0.4.4 → v0.5.0

Six sprints, ordered. Each one is independently shippable and tagged. Built
directly on the findings in `docs/plans/2026-04-17-sos-structural-audit.md`.

## The arc

```
v0.4.3 [shipped]  Dispatcher + Brain + Code Mode
   │
   ▼
v0.4.4            Shared infrastructure extraction        ← structural foundation
   │
   ▼
v0.4.5            Event-driven service decoupling         ← services deploy independently
   │
   ▼
v0.4.6            Client refactor                         ← clients/ actually used
   │
   ▼
v0.4.7            MCP gateway refactor                    ← MCP is a proxy, not an embedder
   │
   ▼
v0.4.8            Repo hygiene                            ← data vs code, docs vs root
   │
   ▼
v0.5.0            Kernel consolidation                    ← policy + audit + arbitration
```

`v0.4.8` is low-risk and can ship in parallel with any other sprint. Everything
else is sequential — later sprints assume earlier ones have landed.

## Squad

Same squad as v0.4.3. No new specialists.

- **Loom (coordinator, opus)** — briefs, reviews, commits
- **sos-schema-author** (sonnet, stateless) — JSON Schemas
- **sos-pydantic-author** (sonnet, stateless) — Pydantic bindings
- **sos-openapi-author** (sonnet, stateless) — OpenAPI docs
- **sos-contract-tester** (sonnet, stateless) — pytest + fakeredis tests
- **sos-brain-wire** (sonnet, stateless) — bus consumer handlers
- **sos-connectivity-medic** (stateful) — wakes only if a pipe breaks

Parallel dispatch where work is independent. Sequential where one step's output
feeds the next.

---

## Sprint v0.4.4 — Shared infrastructure extraction

**Theme:** extract shared libraries trapped under `services/`; add the contracts
that should exist; wire structural enforcement so boundary violations fail at
commit time.

**Goal:** after this sprint, import-linter blocks every P1-10 / V3-14..20 class
of violation at pre-commit.

### Steps

1. **Extract bus.** `services/bus/core.py` → `kernel/bus.py`. Update 6 callers (content, engine, execution, identity, memory + `adapters/telegram`, `adapters/discord`). Pure move + rename.
2. **Extract auth.** `services/auth/__init__.py` → `kernel/auth.py` (with `AuthContext` + `verify_bearer`). Update 4 callers (dashboard.auth, dashboard.routes.brain, economy.app, mcp.sos_mcp_sse).
3. **Extract health-response helper.** `services/_health.py` → `kernel/health.py`. Update 4 callers.
4. **Stub policy module.** `services/common/capability.py` + `common/fmaap.py` → `kernel/policy/capability.py` + `kernel/policy/fmaap.py`. Fills out in v0.5.0; v0.4.4 is just the move.
5. **Tenant contract.** Dispatch sos-schema-author + sos-pydantic-author in parallel: `contracts/schemas/tenant_v1.json` + `contracts/tenant.py` (Tenant, TenantCreate, TenantUpdate, TenantPlan, TenantStatus). Update 3 callers (saas.app, billing.webhook, cli.onboard) to import from contracts.
6. **Move test-leaking types to contracts.**
   - `BusValidationError`, `MessageValidationError` → `contracts/errors.py`
   - `UsageEvent` → `contracts/economy.py` (file exists)
   - Fix 3 contract tests.
7. **Add `import-linter` config.** `pyproject.toml::[tool.importlinter]` encoding R1, R2, R5, R6. Violations fail `lint-imports`.
8. **Pre-commit hook.** `.pre-commit-config.yaml` runs `lint-imports` + `pytest tests/contracts/`. Blocks commits that violate boundaries.
9. **Method doc.** `docs/sos-method.md` — one page, hard rules only. The six rules above + pointer to the audit + sprint roadmap.
10. **Wire method into agents.** Each `.claude/agents/*.md` gets one line: "Before editing, read `docs/sos-method.md`." Seven subagent definitions touched.
11. **CHANGELOG entry** + tag **v0.4.4**.

### Out of scope

- Event-driven service fixes (that's v0.4.5)
- MCP refactor (v0.4.7)
- Repo-root cleanup (v0.4.8)
- Policy engine logic — we only move the skeleton in v0.4.4; logic fills in v0.5.0

### Risk

Low. Mostly mechanical moves. Test suite must stay green — each move + update
is a single commit with its own test run.

---

## Sprint v0.4.5 — Event-driven service decoupling

**Status: SHIPPED 2026-04-18** — tagged `v0.4.5` @ `8db3d45c`.
Wave-by-wave log in `CHANGELOG.md` under `[0.4.5]`.

> **Roadmap → reality deltas.** Three steps played out differently than planned:
>
> - **Step 11 (FMAAP → squad, P0-08)** was already closed in v0.4.4 when
>   `kernel/policy/fmaap.py` started reading `DB_PATH` from `kernel.config`.
>   No squad import remained to remove. The P0-08 ignore entry was stale.
> - **Steps 9 + 12 (billing → saas via bus events, new schemas).** Step 9
>   originally called for `billing.payment.confirmed` with saas subscribing.
>   Shipped as Wave 9 via HTTP through a new `sos/clients/saas.py` instead —
>   same decoupling result, no new schema/pydantic/enforcement wiring needed,
>   shipped in ~3 min. The bus path stays available for when billing gains a
>   second downstream that can't block the Stripe webhook.
> - **Steps 1 + 6 (health decoupling).** Step 1 Wave 1 closed P0-04 (squad→health)
>   via bus events. Step 6 (feedback→health, P0-10) and P0-11 (journeys→health)
>   shipped in Wave 8 as a *kernel extraction* — conductance helpers are pure
>   file-I/O with no service deps, so `sos/kernel/conductance.py` is a cleaner
>   fix than a bus subscription. `health.calcifer` re-exports for BC.

**Theme:** every P0 service→service in-process import becomes a bus event, an
HTTP call, or a kernel primitive. Services can restart independently.

### Steps (one per P0, grouped by target service)

1. **Squad → health decoupling** (P0-04, P0-10, P0-11).
   - Squad emits `task.completed` via `TaskCompletedMessage` (contract already exists).
   - Health subscribes to `sos:stream:global:squad:*`, owns `conductance_update` + `conductance_decay`.
   - Journeys reads health's conductance from Redis key `sos:state:health:conductance:<agent>` (follows the snapshot pattern established in v0.4.3).
   - Add `tests/services/test_squad_health_decoupling.py`.
2. **Squad → journeys decoupling** (P0-05). Journeys subscribes to the same `task.completed` event, owns journey-progress logic.
3. **Content → engine decoupling** (P0-03). Two fixes:
   - Rehydrate `content.publish.requested` as a bus event OR
   - `clients/engine.py` HTTP call (choose based on whether content is synchronous — brief to subagent).
4. **Analytics → integrations decoupling** (P0-06). Integrations exposes HTTP `/oauth/credentials`; analytics calls it. New OpenAPI entry.
5. **Autonomy → identity decoupling** (P0-07). Identity exposes HTTP `/avatar/generate` + `/avatar/uv16d`; autonomy calls them.
6. **Feedback → health decoupling** (P0-10). Feedback emits `feedback.signal`; health subscribes.
7. **Brain → registry decoupling** (P0-09). Registry exposes HTTP `/agents`; brain calls it via `asyncio.to_thread(httpx.get, ...)`.
8. **Dashboard → economy decoupling** (P0-12). Economy already has HTTP `/usage` endpoint; dashboard calls it.
9. **Billing → saas decoupling** (P0-01, P0-02). Billing emits `billing.payment.confirmed`; saas subscribes and mutates tenant state. Tenant contract already moved in v0.4.4.
10. **Engine → tools cleanup** (P0-02). Remove redundant in-process import; `clients/tools.py` already exists and is used alongside it.
11. **FMAAP → squad decoupling** (P0-08). FMAAP calls squad HTTP; `DB_PATH` constant removed from cross-service reach.
12. **New bus message types** for any event above that doesn't have a v1 schema yet (e.g. `billing.payment.confirmed`, `feedback.signal`). Dispatch schema-author + pydantic-author + enforcement-wire in parallel per type.
13. **CHANGELOG entry** + tag **v0.4.5**.

### Enforcement

After this sprint, `import-linter` R1 config graduates from "warn" to "fail" on
any new service→service import.

---

## Sprint v0.4.6 — Client refactor

**Theme:** every P1 client-class violation fixes by actually using `clients/*`.

### Steps

1. **Audit `clients/`.** Verify each of the 9 existing clients (bus, economy, engine, memory, mirror, operations, tools, voice, grok) has a working HTTP implementation. One known violation: `clients/operations.py` imports service internals.
2. **Fix `clients/operations.py`** (P1-07). Rewrite as HTTP client.
3. **Adapter migration** (P1-02, P1-03). `adapters/router.py` → `clients/economy`; `adapters/telegram.py` + `adapters/discord.py` → `clients/bus`.
4. **Agent seed migration** (P1-04, P1-05). `agents/shabrang/agent.py` → `clients/engine`; `agents/join.py` → HTTP + config-loaded token.
5. **CLI onboard split** (P1-06). Split `cli/onboard.py`:
   - User-facing entry stays in `cli/onboard.py` (chat flow, prompts)
   - Provisioning logic moves to `clients/saas.py::onboard_tenant()` using HTTP
   - `Tenant*` imports switch to `contracts/tenant.py` (already moved in v0.4.4)
6. **`pair-agent.sh` relocation.** Moves to `sos/cli/pair-agent.sh` is correct per v0.4.3 decision — no change needed here unless we introduce `clients/sh/` (hold until v0.4.8 hygiene decision).
7. **Test clients.** Each `clients/<name>.py` gets a unit test with fakeredis/respx verifying it never imports `sos.services.*`.
8. **CHANGELOG entry** + tag **v0.4.6**.

---

## Sprint v0.4.7 — MCP gateway refactor

**Theme:** `sos/mcp/sos_mcp_sse.py` rewritten as a thin HTTP proxy. Biggest
single refactor in the roadmap — earns its own sprint.

### Steps

1. **Inventory current MCP tool handlers.** ~40 tools, each currently imports a service internal. Group by target service.
2. **For each tool, replace direct import with `clients/*` call.**
   - `squad.*` tools → `clients/squad.py` (new client — add in this sprint)
   - `saas.*` tools → `clients/saas.py` (new client)
   - Memory/mirror tools → `clients/mirror.py` (exists)
   - Engine/chat tools → `clients/engine.py` (exists)
3. **Audit middleware.** MCP keeps its own auth layer (bearer verification, rate-limiting) but delegates business logic to services via HTTP.
4. **New clients**: `clients/squad.py` + `clients/saas.py` authored in parallel by sos-openapi-author (reads OpenAPI doc, generates client).
5. **Integration test.** `tests/mcp/test_mcp_proxy.py` — every tool handler hits a mocked HTTP endpoint, never a service import.
6. **Restart test.** Kill squad service mid-session; MCP tool calls get 503, not ImportError. Proves independence.
7. **CHANGELOG entry** + tag **v0.4.7**.

### Risk

Medium. 40+ tool handlers touched. Plan staged rollout: convert 5 tools,
verify, convert next 5, etc. `import-linter` blocks regressions.

---

## Sprint v0.4.8 — Repo hygiene (can ship in parallel)

**Theme:** pure moves. Data out of package. Docs out of root. Cosmetic clarity.

### Steps

1. **`sos/squads/` → `data/squads/`.** 341 files of project-specific data, not code. Update any loader in `services/squad/` to read from the new path.
2. **Top-level docs → `docs/`.** Move: GEMINI.md, PORTING_GEMINI_V1.md, WHITEPAPER.md, TECH-RADAR.md, SOVEREIGN_MEMORY.md.
3. **Root-level strays → `data/`.** `artifacts/`, `athena/`, `personas/`, `souls/`, `organs/`.
4. **Generated output → `.gitignore`.** `graphify-out/`, `dist/`, `mumega.egg-info/`.
5. **Root utilities → `scripts/`.** `create_agent.py`, `organ_daemon.py`.
6. **`scripts/` → `sos/agents/`.** Agent-seed scripts move: `bootstrap_river.py`, `onboard_athena.py`, `onboard_claude_final.py`, `kasra_onboard.py`.
7. **`scripts/demo_ai_to_ai_commerce.py` → `examples/`** (new top-level dir).
8. **`sos/cli/tenant-setup.sh` → `scripts/`.** Ops provisioning, not user-facing.
9. **Archive dispatcher.** `sos/services/dispatcher.archive/` → `.archive/sos-services-dispatcher/`.
10. **`sos/docs/` decision** — empty directory either populated with package-internal docs or removed.
11. **`sos/deprecated/` final sweep.** Verify no live imports (audit confirmed clean). Add `# DEPRECATED — do not import` banner to each file.
12. **CHANGELOG entry** + tag **v0.4.8**.

### Why low-risk

Nothing in this sprint changes behavior. It's git mv + path updates in loaders.
Can parallel with any other sprint except v0.4.4 (which also touches config).

---

## Sprint v0.5.0 — Kernel consolidation

**Theme:** the kernel becomes what ChatGPT described and we've been pointing at
— a minimal trusted core that **enforces** the blueprint. Three sub-themes,
each producing a first-class kernel service.

### Theme A: Policy engine

**Current state:** permission logic scattered across `auth.verify_bearer`,
`common.capability`, `common.fmaap`, `SkillCard.trust_tier`, `tokens.json`
scopes.

**Target state:** `sos/kernel/policy/engine.py` exposes one function:

```python
def check(
    agent: AgentIdentity,
    action: str,             # "send_email", "spend_mind", "publish_content"
    resource: str,           # "customer:viamar" or "channel:trop"
    context: dict,           # coherence, wallet balance, recent_actions
) -> Decision  # allow | deny | require_approval
```

Every service calls `policy.check(...)` at its boundary. Deny response includes
reason. `require_approval` enqueues an approval request on `sos:stream:policy:approvals`.

### Theme B: Unified audit stream

**Current state:** `saas/audit.py`, implicit brain event trail, tool-call logs
in MCP, billing events — each service logs its own way.

**Target state:** `sos/kernel/audit/stream.py` writes every policy-relevant
event to `sos:stream:audit` (immutable, append-only, retained 90d hot / 1y
cold). One format (`AuditEvent v1` in `contracts/`):

```
{actor, action, resource, decision, timestamp, request_id, meta{}}
```

Dashboard gets `/sos/audit` endpoint — filter by actor / action / resource /
time-range. First real forensic surface.

**Write path is async with backpressure.** Service callsites `await audit.log(...)`
which enqueues to an in-process bounded `asyncio.Queue` (per-process, size 1024).
A single background writer drains the queue to Redis `XADD sos:stream:audit`.
If the queue fills (Redis slow or down), `audit.log` returns a
`AuditBackpressure` decision the service can act on — drop, deny the underlying
action, or retry — rather than silently blocking the request path. This keeps
audit non-blocking on the happy path and observable on the sad path. Without it,
the policy boundary becomes a latency cliff at every service call.

### Theme C: Arbitration protocol

**Current state:** conflicts resolved by Hadi ad-hoc (task.claimed idempotency
handles dual-claim races; everything else is human-arbitrated).

**Target state:** `sos/kernel/arbitration.py` formalizes three conflict types
and their resolution:

- **Dual-claim** — two agents claim same task. Current: first-claim-wins. Keep.
- **Value disagreement** — two agents produce conflicting outputs for same goal. Protocol: coherence-weighted vote (each agent's output scored; higher coherence wins; tie escalates).
- **Contract breach** — agent violates a contract (malformed message, unsigned tool call, budget overrun). Protocol: circuit-break that agent for N seconds; third breach in an hour → human escalation.

Escalation path: `sos:stream:arbitration:escalations` with a rate-limited
Discord notification to Hadi.

### Steps

1. **Schema + Pydantic** for `AuditEvent v1`, `PolicyDecision v1`, `ArbitrationEscalation v1`. Parallel dispatch schema-author + pydantic-author.
2. **`sos/kernel/policy/engine.py`** — merge auth.verify_bearer + capability + fmaap + trust_tier checks into one function. Subagent: sos-brain-wire.
3. **`sos/kernel/audit/stream.py`** — single-writer audit log; every service calls `audit.log(event)` instead of its own logger for policy-relevant events.
4. **`sos/kernel/arbitration.py`** — the three conflict types + resolution functions + escalation bus emit.
5. **Migration: every service boundary calls policy.check().** One service per step (brain, saas, dashboard, economy, squad, ...). Each service migration = one subagent dispatch with a unit test.
6. **Migration: every audit-worthy event writes to `sos:stream:audit`.** Same per-service cadence.
7. **Dashboard route** `/sos/audit` + `/sos/policy` + `/sos/arbitration` — operator surface for the three kernel services. OpenAPI doc updated.
8. **Integration test.** Scenario: agent tries to spend more $MIND than wallet holds → policy denies → audit event written → agent's circuit breaker increments. Proves all three themes interlock.
9. **CHANGELOG entry** + tag **v0.5.0**.

### Why this is the v0.5.0 line

After v0.4.4 through v0.4.8, SOS structurally matches the blueprint. v0.5.0 is
when the kernel starts **enforcing** the blueprint. That's the semantic shift
worth a minor-version bump rather than a patch.

---

## Budget + timing

| Sprint | Size | Risk | Depends on |
|---|---|---|---|
| v0.4.4 | ~11 steps, mostly mechanical | low | v0.4.3 (done) |
| v0.4.5 | ~13 steps, event wiring | medium | v0.4.4 |
| v0.4.6 | ~8 steps, client updates | low | v0.4.4 |
| v0.4.7 | ~7 steps + staged migration | medium-high | v0.4.6 |
| v0.4.8 | ~12 steps, pure moves | low | parallel |
| v0.5.0 | ~9 steps, 3 themes | high | v0.4.4–7 |

v0.4.4 is doable in one session with parallel subagent dispatch (similar size
to v0.4.3). The rest range up to v0.5.0 which is multi-session.

## Order of approval

Per the agreed protocol: commit the v0.4.3 tree + tag v0.4.3 first (held
separately — not mixed with this roadmap). Then start v0.4.4.

No code moves until you approve the structural audit + this roadmap. When you
approve v0.4.4 scope, I brief the squad for parallel dispatch and begin.
