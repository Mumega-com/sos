# Changelog

All notable changes to SOS (Sovereign Operating System) will be documented here.

## [0.5.2] - 2026-04-18 — Arbitration: intent → proposal → ratification

**Release theme: "The kernel enforces the blueprint — step 3 of 3."**

v0.5.0 gave us the floor + audit spine. v0.5.1 gave us the unified gate.
v0.5.2 closes the triptych: when two agents call the gate independently,
arbitration looks across proposers and picks exactly one winner. The
audit spine is the storage layer — proposals ARE `AuditEventKind.INTENT`
events, arbitration is a read-over-audit + decision function. No new
durability layer.

### Arbitration (`sos.kernel.arbitration`)

- **`sos/kernel/arbitration.py`** — three public coroutines:
  - `propose_intent(agent, action, resource, tenant, priority, metadata)` →
    writes an INTENT event tagged `metadata.arbitration=True` with
    `metadata.priority=N`. Returns the proposal id (audit event id).
  - `arbitrate(resource, tenant, window_ms=500, strategy="priority+coherence+recency")` →
    `ArbitrationDecision`. Reads proposals in window, sorts, emits one
    `ARBITRATION` audit event, returns a frozen decision. **Never raises**
    — arbitration failures fall through to a no-winner decision so the
    caller's denial path stays clean.
  - `read_proposals(tenant, resource, window_ms=500)` — observability helper.
- **Rank rule** — `priority → coherence → recency`:
  1. `metadata.priority` (int, higher wins).
  2. Conductance sum `sum(G[agent][skill])` from `sos.kernel.conductance`
     — the agent's proven-flow signal; absent agents score 0.
  3. Later `timestamp` wins.
  Strategy name is recorded in `ArbitrationDecision`, so future strategies
  (`fmaap-weighted`, `governance-tier-aware`) ship as new string values
  without schema churn.
- **`sos/contracts/arbitration.py`** — `ArbitrationDecision` + `LoserRecord`
  frozen Pydantic v2 models. 4 required + 7 optional fields on the decision.
  Additive-only. Baseline locked in `test_arbitration_schema_stable.py` (7 tests).

### Gate integration (opt-in)

- **`sos/kernel/policy/gate.py`** — `can_execute()` grows three kwargs:
  `propose_first: bool = False`, `priority: int = 0`, `window_ms: int = 500`.
  When `propose_first=True`: gate calls `propose_intent` + `arbitrate`;
  winners add `"arbitration"` to `pillars_passed` and flow through normal
  signals; losers short-circuit with a denial whose reason names the winner.
  **Default `propose_first=False` preserves every existing caller unchanged.**

### Legacy shim removed

- **`sos/kernel/governance.py`** — the `~/.sos/governance/intents/{tenant}/`
  file-writing block is gone, per the v0.5.0 CHANGELOG plan. Audit has been
  authoritative for one full minor version. `get_intent_log()` now reads
  from `sos.kernel.audit.read_events` so its return shape is unchanged for
  callers.

### Tests (new, all green)

- **`tests/contracts/test_arbitration_schema_stable.py`** — 7 tests
  (frozen, required/optional fields, no-removal, additive-only).
- **`tests/kernel/test_arbitration.py`** — 7 tests (single-proposer wins,
  higher priority wins, coherence tie-break, recency tie-break, empty
  window no-winner, ARBITRATION event persisted, multi-tenant isolation).
- **`tests/kernel/test_policy_gate_propose.py`** — 3 tests (winner allowed,
  loser denied, `propose_first=False` path unchanged).

### Docs

- **`docs/kernel/arbitration.md`** — public contract doc matching
  `audit.md` / `policy.md` style: why arbitration, architecture insight
  (read-over-audit), surface, rank rule, contracts, gate integration,
  durability, failure modes, end-to-end example.

### Deferred to v0.5.3+

- Per-squad arbitration windows (dynamic, based on coherence).
- Arbitration replay tooling (`sos.cli arbitration replay`).
- Migrating the remaining ~10 service-side permission checks to
  `can_execute` (same mop-up deferred from v0.5.1).

## [0.5.1] - 2026-04-18 — Unified policy gate

**Release theme: "The kernel enforces the blueprint — step 2 of 3."**

v0.5.0 gave us the floor (R0 lock) and the audit spine. v0.5.1 adds the
single gate every HTTP route, every agent action, every kernel-governed
decision asks: *may this agent perform this action on this resource?*
One question, one call, one audited answer. No service reinvents it.

### The gate (`sos.kernel.policy.gate`)

- **`sos/kernel/policy/gate.py`** — new async function
  `can_execute(agent, action, resource, tenant, authorization, capability, context)`.
  Composes five signals in one place:
  1. Bearer verification (`sos.kernel.auth.verify_bearer`)
  2. Tenant scope enforcement (system/admin bypass or exact-match)
  3. Capability (if `SOS_REQUIRE_CAPABILITIES=1`)
  4. FMAAP 5-pillar validation when squad context is present
  5. Governance tier lookup
  Writes exactly one `AuditEventKind.POLICY_DECISION` event per call.
  **Fail-open for availability** (FMAAP DB down → warn + allow),
  **fail-closed for security** (missing/invalid bearer, scope mismatch).
- **`sos/contracts/policy.py`** — `PolicyDecision` frozen Pydantic v2
  model. 5 required + 7 optional fields. Additive-only — new signals
  land as new list members or metadata keys, never schema churn.
  Snapshot baseline locked in `test_policy_schema_stable.py`.
- **`sos/kernel/governance.py::before_action`** now consults the gate
  first. FMAAP pillar failures become authoritative denials without
  governance having to re-implement the checks. Fail-open on gate
  error preserves governance availability.

### Proof-of-concept migration

- **`sos/services/integrations/app.py`** — all 3 authenticated routes
  (`GET /oauth/credentials/{tenant}/{provider}`,
  `POST /oauth/ghl/callback/{tenant}`,
  `POST /oauth/google/callback/{tenant}`) now make one `can_execute()`
  call + `_raise_on_deny()` handoff. The inline `_verify_bearer`,
  `_check_tenant_scope`, and `_require_system_or_admin` helpers are
  gone. Behaviour preserved (same 401/403/200 semantics); every call
  now writes an audit event.

### Tests (new, all green)

- **`tests/contracts/test_policy_schema_stable.py`** — 5 tests snapshotting
  the `PolicyDecision` baseline (frozen, required fields, optional fields,
  no-removal, instance-immutability).
- **`tests/kernel/test_policy_gate.py`** — 7 tests covering system-token
  cross-tenant allow, scoped-token own-tenant allow, scoped-token
  cross-tenant deny, no-scope deny, invalid-bearer deny,
  kernel-internal (no auth) allow, audit event persisted.
- **`tests/services/test_integrations_gate.py`** — FastAPI TestClient
  tests proving the migration preserves 401/403/200/404/400 behaviour.

### Docs

- **`docs/kernel/policy.md`** — public contract doc matching
  `docs/kernel/audit.md` style: what the gate is, what it isn't,
  surface, signals, fail-open/fail-closed, durability, migration guide
  for sibling services.

### Deferred

- **v0.5.1.1+ mop-up:** migrate the ~10 other permission sites across
  `economy`, `registry`, `squad`, `mcp`, `tools` to the gate. Each is
  a ~15-line commit — they do not block v0.5.1.
- **v0.5.2:** `sos/kernel/arbitration.py` conflict-resolution layer
  (needs design work before code). Remove the
  `~/.sos/governance/intents/` legacy compat shim.

## [0.5.0] - 2026-04-18 — Kernel floor lock + unified audit stream

**Release theme: "The kernel enforces the blueprint — step 1 of 3."**

This is the release that makes the kernel provably the floor. Every
`sos.services.*` import has been expunged from `sos/kernel/`, and a
permanent AST sweep test prevents regression. A new unified audit
stream (`sos.kernel.audit`) lands as the canonical sink for every
governed decision; it's designed so v0.5.1 policy and v0.5.2
arbitration can plug in without reopening the kernel.

### R0 floor lock — the last kernel→services leak, closed

- **`sos/kernel/governance.py`** no longer imports
  `sos.services.economy.metabolism`. Budget checks now flow through
  `sos.clients.economy.AsyncEconomyClient.can_spend()` over HTTP.
  Fail-open on any error (connection refused, timeout, 5xx) — economy
  downtime can never block governance.
- **`sos/services/economy/app.py`** gains `GET /budget/can-spend`
  (Bearer-auth, tenant-scoped) that delegates unchanged to
  `metabolism.can_spend(project, cost)`.
- **`sos/clients/economy.py`** gains sync + new `AsyncEconomyClient`
  (subclasses `AsyncBaseHTTPClient`) with matching `can_spend()`
  methods. The async variant is what the kernel uses.
- **Stale R2 ignore removed.** The
  `sos.adapters.telegram → sos.services.economy.metabolism` entry was
  already obsolete (no such import existed) and has been dropped from
  `pyproject.toml`. `lint-imports`: **4 kept, 0 broken.**

### Unified audit stream (`sos.kernel.audit`)

- **`sos/contracts/audit.py`** — new frozen Pydantic model:
  `AuditEvent`, `AuditEventKind`
  (`intent`, `policy_decision`, `action_completed`, `action_failed`,
  `arbitration`), and `AuditDecision` (`allow`, `deny`,
  `require_approval`, `n/a`). Shape designed to accommodate v0.5.1
  policy and v0.5.2 arbitration writers **without schema changes**.
- **`sos/kernel/audit.py`** — three public functions
  (`new_event`, `append_event`, `read_events`) and nothing else. Disk
  write at `~/.sos/audit/{tenant}/{YYYY-MM-DD}.jsonl` is authoritative
  and fsync'd; bus emit to `sos:audit:{tenant}` Redis stream is
  observational and best-effort. Disk never blocks on Redis.
- **`sos/kernel/governance.py`** now writes every intent (and every
  budget denial) through `audit.append_event`. The legacy
  `~/.sos/governance/intents/{tenant}/{date}.jsonl` file is still
  populated as a read-side compat shim — planned removal in v0.5.2.
- **`docs/kernel/audit.md`** — public contract documentation.

### Tests (18 new, all green on first run)

- **`tests/contracts/test_kernel_no_service_imports.py`** — AST sweep
  walking every `sos/kernel/**/*.py` file, asserting **zero**
  `sos.services.*` imports. `_ALLOWED_EXCEPTIONS` is an empty
  `frozenset`; any future addition requires an explicit test edit.
- **`tests/kernel/test_governance_budget.py`** — 3 tests covering
  the three paths of budget consultation: allowed, blocked, fail-open
  on HTTP error.
- **`tests/kernel/test_audit.py`** — 7 tests covering roundtrip
  append+read, filter-by-kind, Pydantic immutability (`frozen=True`),
  disk durability when Redis is down (the whole availability
  guarantee), corrupted-line tolerance, default-today date.
- **`tests/contracts/test_audit_schema_stable.py`** — 5 tests
  snapshotting the v0.5.0 schema baseline. Field removals, rename,
  required-status changes, and missing enum values all fail loudly.
  Additive changes are allowed.

### Durability guarantees

- **Kernel is now provably service-free.** Not by convention; by CI.
- **AuditEvent schema is frozen.** v0.5.1 and v0.5.2 add code, not
  schema churn.
- **Policy gate (`kernel/policy.py`) and arbitration
  (`kernel/arbitration.py`) will land in new files** without
  modifying audit or governance. The kernel reopens to *add* modules,
  not to *modify* them. That is the durability property v0.5.0 buys.

### Deferred (intentional)

- `kernel/policy.py` unified `can_execute()` gate — v0.5.1.
- `kernel/arbitration.py` conflict resolution — v0.5.2 (needs a
  design brainstorm first; "who wins when Loom and Athena disagree"
  is a 488 question, not a coding question).
- `sos/cli` split to drop the three
  `sos.cli → sos.services.*.__main__` dispatcher ignores — pending.
- Removal of the legacy `~/.sos/governance/intents/` shim — v0.5.2.

## [0.4.6] - 2026-04-18 — R2 sweep (clients/adapters/cli/agents)

Closes the P1 micro-kernel violations where non-service code reached into
service internals. Every fix routes through an HTTP client (or inlined env
read). `lint-imports`: **4 kept, 0 broken.** R2 `ignore_imports` shrinks
from 10 entries to 5; the remaining 5 are a documented R2 backlog
(P1-01 MCP gateway, `sos.adapters.telegram` → economy metabolism, and
three `sos.cli` → service `__main__` dispatcher entries — legitimate
packaging pattern pending the v0.5 CLI split).

### P1 closures

- **P1-07 — `clients/operations` → runner over HTTP.** Was importing
  `sos.services.operations.runner` in-process. Now routes through
  `BaseHTTPClient` against the ops runner.
- **P1-02 — `adapters/router` → `clients/economy`.** `ModelRouter` no
  longer imports `sos.services.economy.wallet.SovereignWallet`; it debits
  via `EconomyClient` (HTTP). Concrete LLM adapters (`ClaudeAdapter`,
  `GeminiAdapter`, `OpenAIAdapter`) are now lazy-loaded through a module
  `__getattr__` so optional extras (`gemini`, `openai`) don't fail at
  import time.
- **P1-04 — `agents/shabrang` standalone mining daemon.** Dropped the
  `SOSEngine` base class (never used its chat capability). Now a plain
  `ShabrangAgent` built on `CoherencePhysics` + `MirrorClient` + `Config`.
- **P1-05 — `agents/join` drives journeys via HTTP.** New FastAPI app at
  `sos/services/journeys/app.py` (port 6070) and matching
  `sos.clients.journeys.{JourneysClient, AsyncJourneysClient}`.
  `agents/join` no longer imports `JourneyTracker` or `SYSTEM_TOKEN` from
  squad — admin token resolves from `SOS_ADMIN_TOKEN` /
  `SOS_SYSTEM_TOKEN` env.
- **P1-06 — `cli/onboard` uses `SaasClient`.** Tenant CRUD now flows
  through `sos.clients.saas.SaasClient` +
  `sos.contracts.tenant.TenantCreate` (`model_dump(mode="json")` for
  enum serialization). Drops the two in-process imports of
  `sos.services.saas.registry` and `.models`.

### Tests

- `tests/clients/test_no_service_imports.py` — AST sweep asserting
  no module under `sos/clients/` imports `sos.services.*`.
- `tests/adapters/test_router_economy_client.py` — 6 tests covering
  `_record_cost` debit flow + default wallet factory.
- `tests/agents/shabrang/test_shabrang_decoupled.py` — 3 tests: no engine
  leak, no `SOSEngine` base, constructs without LLM SDKs.
- `tests/clients/test_journeys_client.py` — 7 tests for sync + async
  client endpoint mapping and token resolution.
- `tests/agents/test_join_decoupled.py` — 4 tests for no squad/journeys
  leak + env-driven admin token.
- `tests/cli/test_onboard_decoupled.py` — 4 tests: AST sweep, SaasClient
  import, singleton cache, end-to-end mock of `create_tenant` +
  `activate_tenant`.

### Side fixes

- `SOSLogger` has no `.warning` method — fixed 4 pre-existing
  `log.warning(...)` calls in `sos/adapters/router.py` to use `.warn`.
- `sos/services/journeys/app.py` registers via
  `sos.services.bus.discovery.register_service` on startup (R1 carve-out
  for `services→bus` is the documented P1-10 theme).

## [0.4.7] - 2026-04-18 — MCP R2 sweep (P1-01 closure)

Closes the P1-01 backlog: `sos.mcp.sos_mcp_sse` no longer imports any
`sos.services.*` module. Every cross-namespace reach-in is now replaced
with an HTTP client call. Shipped in four phases so each half-commit is
independently revertable. `lint-imports`: **4 kept, 0 broken.** R2
`ignore_imports` shrinks from 5 to 4 (all four MCP→services entries
dropped; the remaining ignores are `sos.adapters.telegram` → economy
metabolism and three `sos.cli` → service `__main__` dispatcher entries
— out of scope, pending v0.5 CLI split).

### Phase closures

- **Phase 1 — MCP ↔ squad via `SquadClient`.** Dropped in-process
  imports of squad `auth`/`api_keys`. MCP's `/auth/verify` and
  `/api-keys` routes now proxy to the squad service (`:8060`) via
  `sos.clients.squad.SquadClient`. Kernel auth (`sos.kernel.auth`)
  replaces squad's token lookup.
- **Phase 2 — MCP ↔ saas via `SaasClient`.** New endpoints on
  `sos.services.saas.app`: `POST /rate-limit/check`, `POST
  /audit/tool-call`, `GET/POST /marketplace/*`, notification prefs.
  MCP's hot path now awaits `_async_saas_client.check_rate_limit(...)`
  (with fail-open on exception) and fire-and-forgets
  `log_tool_call(...)` via `loop.create_task` so audit never blocks
  the request.
- **Phase 3 — MCP ↔ billing via `AsyncBillingClient`.** New wrapper at
  `sos.services.billing.app` (`:8077`) exposing `/webhook/stripe`.
  MCP proxies the raw Stripe request (bytes + `stripe-signature`
  header) unchanged via `AsyncBillingClient.forward_stripe_webhook`;
  HMAC verification still runs inside the billing handler. MCP no
  longer imports `sos.services.billing.webhook`.
- **Phase 4 — MCP ↔ integrations via `AsyncIntegrationsClient`.** New
  POST endpoints on `sos.services.integrations.app`:
  `/oauth/ghl/callback/{tenant}`, `/oauth/google/callback/{tenant}`.
  MCP's `/oauth/ghl/callback` and `/oauth/google/callback` routes
  now proxy via `AsyncIntegrationsClient`; inline
  `TenantIntegrations` imports removed.

### Client additions

- `sos/clients/billing.py` — new. `BillingClient` (sync, health only)
  + `AsyncBillingClient` with `forward_stripe_webhook(raw_body,
  headers)` that preserves byte-exact payload for HMAC verification.
- `sos/clients/integrations.py` — extends existing client with
  `handle_ghl_callback(tenant, code)` and
  `handle_google_callback(tenant, code, service)` on both sync and
  async variants.
- `sos/clients/saas.py` — extends with `check_rate_limit`,
  `log_tool_call`, `browse_marketplace`, `subscribe_marketplace`,
  `my_subscriptions`, `create_listing`, `my_earnings`, notification
  preferences. 9 new methods × 2 (sync + async).
- `sos/clients/base.py` — `_request()` now accepts `params=` and
  `content=` on both sync and async paths (needed for saas query
  params and billing raw-body forwarding).

### Service additions

- `sos/services/billing/app.py` — new. Minimal FastAPI wrapper around
  the existing `sos.services.billing.webhook.stripe_webhook_handler`.
  Runs on port 8077.
- `sos/services/saas/app.py` — adds rate-limit, audit, marketplace
  (browse/subscribe/my_subscriptions/create_listing/my_earnings), and
  notification preference endpoints. All admin-auth'd.
- `sos/services/integrations/app.py` — adds
  `POST /oauth/{ghl,google}/callback/{tenant}` with
  `_require_system_or_admin` (callbacks require system scope; no
  tenant-scoped caller can complete another tenant's OAuth).

### Tests

- `tests/contracts/test_mcp_no_service_imports.py` — AST sweep on
  `sos/mcp/sos_mcp_sse.py` asserting zero `sos.services.*` imports
  (top-level or inline) with `sos.services.bus.discovery` whitelisted
  as the sole exempt infra shim. Any future MCP→services leak fails
  this test in CI.

## [0.4.8] - 2026-04-18 — Repo hygiene

Pure `git mv` + path updates. Zero behavior change. `lint-imports`:
**4 kept, 0 broken.** Package tree separated from data + docs +
ops utilities before the v0.4.6 / v0.4.7 / v0.5.0 refactors.

### Moves

- **Marketing data out of the package.** `sos/squads/marketing/`
  (Remotion video pipeline, 532MB of assets, skill-graph docs) →
  `data/squads/marketing/`. `sos/squads/trop/` stays — it's still
  a live Python module imported as `sos.squads.trop`. Refs in
  `setup.sh`, `CHANGELOG.md`, `.mkt-lead-claude.md` updated.
- **Top-level docs to `docs/`.** Moved 7 files: `AGENT_TEMPLATE`,
  `ARCHITECTURE`, `PORTING_GEMINI_V1`, `SOVEREIGN_MEMORY`, `TASKS`,
  `TECH-RADAR`, `WHITEPAPER`. Root keeps tool-expected files
  (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `CHANGELOG.md`,
  `CONTRIBUTING.md`, `README.md`).
- **Root utilities to `scripts/`.** `create_agent.py`,
  `organ_daemon.py` — doc refs in `organs/README.md` and
  `docs/AGENT_TEMPLATE.md` updated.
- **Agent-seed scripts to `sos/agents/seeds/`.** `bootstrap_river`,
  `onboard_athena`, `onboard_claude_final`, `kasra_onboard`.
- **Dispatcher archive to `.archive/`.** `sos/services/dispatcher.archive/`
  → `.archive/sos-services-dispatcher/`. Keeps `sos/services/`
  siblings list to live services only.
- **Examples dir.** `scripts/demo_ai_to_ai_commerce.py` →
  `examples/demo_ai_to_ai_commerce.py`.

### Hygiene

- **Deprecation banners** on `sos/deprecated/redis_bus.py` and
  `sos/deprecated/sos_mcp.py` (top of file, above module docstring).
  No live imports — R5 stays KEPT.
- **`.gitignore`** adds `graphify-out/`, `graphify-out-*/`,
  `.wrangler/`, `.remember/`, `.code-review-graph/`.

### Acceptance

- `sos/squads/trop` still imports cleanly (`python3 -c 'import sos.squads.trop'`)
- `lint-imports` 4 kept, 0 broken
- Pre-existing failures unrelated to moves (missing `base58` dep,
  schema-count drift test) — none introduced by this sprint

---

## [0.4.5] - 2026-04-18 — P0 decoupling sweep

Closes every P0 R1 violation tracked by `pyproject.toml` import-linter
`ignore_imports`. Services no longer reach across the boundary into
each other's internals; reads go through `sos.clients.*` (HTTP) and
writes go through bus events. `lint-imports`: **4 kept, 0 broken.**

### Waves

- **Wave 1 — `squad → health+journeys`** (P0-04, P0-05)
  squad now emits `task.completed`; `health.bus_consumer` +
  `journeys.bus_consumer` subscribe with the 5-invariant pattern.
- **Wave 2 — `content → engine`** (P0-03)
  `SwarmCouncil` byte-moved to `sos/kernel/council.py`; content calls
  engine through `AsyncEngineClient` (`ChatRequest`).
- **Wave 3 — `analytics → integrations`** (P0-06)
  New `sos/services/integrations/` FastAPI service on port 6066.
  `sos/clients/integrations.py` — sync + async client with tenant-scoped
  bearer auth. `analytics/ingest.py` converted to async.
- **Wave 4 — `autonomy → identity`** (P0-07)
  `UV16D` moved to `sos/contracts/identity.py`.
  `sos/clients/identity.py` — `generate_avatar`, `on_alpha_drift`.
  identity service gains `POST /avatar/generate` + social endpoints.
- **Wave 5 — `brain → registry`** (P0-09)
  New `sos/services/registry/` FastAPI service on port 6067.
  `sos/clients/registry.py` returns `AgentIdentity` (reconstructed from
  HTTP payloads). brain no longer imports the registry module.
- **Wave 6 — `dashboard → economy + registry`** (P0-12)
  `EconomyClient.list_usage` deserializes into typed `UsageEvent`.
  dashboard `tenants.py` + `routes/sos_operator.py` routed through
  `EconomyClient` / `RegistryClient`.
- **Wave 7 — `engine → tools`** (P0-02)
  Engine held a redundant in-proc `ToolsCore` alongside `ToolsClient`.
  Removed; chat tool-exec loop routes through the HTTP client.
- **Wave 8 — conductance → kernel** (P0-10, P0-11)
  `conductance_*` helpers + `CONDUCTANCE_FILE/ALPHA/GAMMA` moved to
  `sos/kernel/conductance.py`. `feedback.loop` + `journeys.tracker`
  import from kernel; `health.calcifer` re-exports for BC.
- **Wave 9 — `billing → saas`** (P0-01)
  `sos/clients/saas.py` — sync + async `SaasClient` (admin-key
  bearer). `billing.webhook` calls `create_tenant` /
  `activate_tenant` / `cancel_tenant` over HTTP. `TenantCreate` /
  `TenantUpdate` remain as the type contracts; `.model_dump()` at
  the HTTP boundary.

### Added

- `sos/kernel/council.py` — `SwarmCouncil` shared between engine + content.
- `sos/kernel/conductance.py` — conductance matrix, shared kernel primitive.
- `sos/contracts/identity.py` — `UV16D` as a pure dataclass.
- `sos/services/integrations/` — oauth-credentials HTTP service.
- `sos/services/registry/` — agent-roster HTTP service.
- `sos/clients/integrations.py`, `sos/clients/identity.py`,
  `sos/clients/registry.py`, `sos/clients/saas.py` — new HTTP clients
  using the same `BaseHTTPClient` / `AsyncBaseHTTPClient` pattern.
- Bus consumers: `health.bus_consumer`, `journeys.bus_consumer` — five-
  invariant Redis consumer pattern.

### Removed

- `sos/services/engine/council.py` — replaced by `kernel/council.py`.

### Structural enforcement

R1 ignore list shrunk from 15 entries (all P0 + P1-10) to just the
P1-10 `bus.discovery` carve-outs pending kernel move. Every sprint
from here is P1 cleanup or v0.5 kernel consolidation.

Refs: `docs/plans/2026-04-17-sos-sprint-roadmap.md`

## [0.4.4] - 2026-04-17 — Structural foundation

### Added
- `sos/kernel/bus.py`, `sos/kernel/auth.py`, `sos/kernel/health.py`,
  `sos/kernel/config.py`, `sos/kernel/policy/` — shared infrastructure
  extracted from `services/` into kernel/ where it belongs.
- `sos/contracts/tenant.py` + `sos/contracts/schemas/tenant_v1.json` —
  first-class Tenant contract (was scattered across saas, billing, cli).
- `sos/contracts/errors.py` — `BusValidationError`,
  `MessageValidationError` live here; `tests/contracts/` stops importing
  service internals (R6).
- `UsageEvent` moved to `sos/contracts/economy.py`.
- `docs/sos-method.md` — one-page method, machine-enforced.
- `import-linter` contracts in `pyproject.toml` enforcing R1/R2/R5/R6.
- `.pre-commit-config.yaml` runs `lint-imports` + `tests/contracts/` on
  every commit.

### Removed
- `sos/services/bus/core.py`, `sos/services/auth/`,
  `sos/services/_health.py`, `sos/services/common/` — all relocated to
  `kernel/` or `kernel/policy/`.
- `sos/services/saas/models.py` — `Tenant*` moved to `contracts/`.

### Refactored
- FMAAP (`kernel/policy/fmaap.py`) no longer imports `squad.service`
  internals; reads `DB_PATH` from `kernel/config.py`.
- `tests/contracts/test_messages_unified.py` moved to
  `tests/services/bus/test_messages_unified.py` (imports
  `bus.enforcement`, which is a service — violated R6).
- `tests/contracts/test_errors.py` — 4 enforcement-backcompat tests
  relocated to `tests/services/bus/test_enforcement_errors.py` for the
  same reason.

### Structural enforcement
- `lint-imports` passes all four contracts at v0.4.4 tag. Known v0.4.5+
  P0s listed in `[tool.importlinter.contracts].ignore_imports` — each
  sprint shrinks that list.

Refs: `docs/plans/2026-04-17-sos-structural-audit.md`,
      `docs/plans/2026-04-17-sos-sprint-roadmap.md`

## [0.4.3] - 2026-04-17 — "Dispatcher + Brain + Code Mode"

Plan: [`docs/plans/2026-04-17-v0.4.3-squad-sprint.md`](docs/plans/2026-04-17-v0.4.3-squad-sprint.md)

### Added
- **Brain service (`sos/services/brain/`)** — bus consumer that obeys the 5 invariants (idempotency, per-stream checkpoints, fail-open, SCAN discovery, replay tolerance). Now scores every `task.created`, emits `task.scored`, maintains a priority queue, dispatches to the best-matching agent via `ProviderMatrix`, and emits `task.routed`. Priority queue is FIFO-stable via `(−score, counter, task_id)` heap.
- **Scoring** — `score_task(impact, urgency, unblock_count, cost)` formula in `sos/services/brain/scoring.py`. URGENCY_WEIGHTS: critical=4.0, high=2.0, medium=1.0, low=0.5. Defaults when payload is thin: impact=5, urgency=payload.priority or "medium", unblock=0, cost=1.0.
- **Matrix** — `sos/services/brain/matrix.py::select_agent` — picks largest skill overlap, breaks ties by lowest agent load then lex name. Returns `None` on zero overlap.
- **`task.scored` contract** — JSON Schema (`sos/contracts/schemas/messages/task.scored_v1.json`) + Pydantic binding (`TaskScoredMessage` / `TaskScoredPayload` in `sos/contracts/messages.py`) + enforcement registration in `sos/services/bus/enforcement.py`.
- **BrainSnapshot contract** — HTTP response shape for the dashboard (not a bus type). Schema at `sos/contracts/schemas/brain_snapshot_v1.json`, Pydantic at `sos/contracts/brain_snapshot.py`. Fields: `queue_size`, `in_flight`, `recent_routes` (≤50), `events_by_type`, `events_seen`, `last_update_ts`, `service_started_at`.
- **`GET /sos/brain`** — operator dashboard endpoint (`sos/services/dashboard/routes/brain.py`). Reads the Brain's snapshot from Redis key `sos:state:brain:snapshot` (TTL 30s, written at end of every tick) — avoids cross-process imports. Bearer-protected, `503` on cache miss.
- **`GET /sos/pairing/nonce` + `POST /sos/pairing`** — agent pairing endpoints (`sos/services/saas/pairing.py`). ed25519 pubkey + nonce challenge + signed response → bearer token registered (hash-only) in `tokens.json` under `scope=agent`. Agent ID deterministic: `<Name>_sos_NNN`. Signature verified via `cryptography.hazmat.primitives.asymmetric.ed25519`.
- **Pairing contract** — schema at `sos/contracts/schemas/pairing_v1.json` (`$defs.PairingRequest` + `$defs.PairingResponse`) + Pydantic at `sos/contracts/pairing.py`.
- **`sos/cli/pair-agent.sh`** — one-shot agent provisioning script. Generates ed25519 keypair, fetches nonce, signs, pairs, writes token to `~/.sos/token` (0600), smoke-tests host. Turns "onboard a fresh agent" into one command.
- **Code Mode MCP** — `sos/mcp/code_mode.py`. Tool calls become Python snippets executed in a restricted `exec` sandbox; only the final value returns to the model. Stdout/stderr captured via `contextlib.redirect_stdout/stderr`; timeout via `asyncio.wait_for(asyncio.to_thread(...))`; trailing expression auto-rewritten to `_last = <expr>`. Targets Cloudflare's ~99.9% token reduction on tool-heavy flows. Wired into `sos_mcp_sse` gateway as the `code_mode` tool, exposing a narrow safe-tool namespace (`status, peers, memories, recall, search_code, task_board, task_list`).
- **OpenAPI** — `sos/contracts/openapi/dashboard.yaml` (adds `/sos/brain` + BrainSnapshot/RoutingDecision) and `sos/contracts/openapi/saas.yaml` (adds `/sos/pairing/*` + PairingRequest/PairingResponse). Both validated with `openapi-spec-validator`.
- **`sos-brain-wire` specialist** — new Sonnet stateless subagent (`.claude/agents/sos-brain-wire.md`). One-shot Brain deliverable: touch ≤2 files, write one unit test, must obey the 5-invariant bus-consumer pattern. Joins the reused v0.4.0 squad (schema-author, pydantic-author, openapi-author, contract-tester, connectivity-medic).

### Changed
- **`BrainService` emits cross-service state via Redis, not Python imports.** At end of every `_tick()`, writes `sos:state:brain:snapshot` (30s TTL). Dashboard reads from this key instead of importing BrainService — clean inter-process boundary.
- **`sos/services/brain/state.py`** gained `priority_queue`, `_queue_counter`, `enqueue`, `pop_highest`, `queue_size`, `task_skills`.
- **`agent_joined` handling** — the payload lacks skills/capabilities, so dispatch looks them up via `sos.services.registry.read_all()` called through `asyncio.to_thread` from the async consumer.

### Fixed
- **Heap FIFO stability** — tie-breaking `(−score, counter, task_id)` ensures equal-score tasks pop in insertion order, not arbitrary string order.

### Architectural invariants
- Brain obeys the 5 bus-consumer invariants. Every new handler must too — violations are blockers.
- Cross-service state hand-off goes through Redis keys with TTL, never through in-process imports. The `sos:state:<service>:snapshot` convention is established.
- Pairing tokens are stored **hash-only** in `tokens.json`; plaintext is returned to the caller exactly once and never persisted.
- Schema catalog is the source of truth — `enforcement.py::_V1_TYPES` mirrors `messages.py::MessageType` mirrors `schemas/messages/<type>_v1.json` filenames.

### Tests
- 103 new green tests across brain / contracts / services / mcp: priority queue (6), scoring integration (4), matrix (7), dispatch (5), task.scored contract (14), brain_snapshot contract (14), pairing contract (20), dashboard brain route (5), pairing endpoint (9), code_mode unit (12), code_mode integration (6), brain E2E (1 — full scoring → dispatch → dashboard round-trip against fakeredis).

### Commits
- Single squad sprint: 28 steps across 6 sprints, 5 reused specialists + 1 new (`sos-brain-wire`), parallel dispatch where independent.

## [0.4.1] - 2026-04-18 — "Moat + Coherence"

### Added
- **Skill Registry with provenance** — `SkillCard v1` (JSON Schema Draft 2020-12 + Pydantic + 67 contract tests). Provenance fields: `author_agent`, `authored_by_ai`, `lineage[]`, `earnings{}`, `verification{}`, `commerce{}`, `runtime{}`. Mumega's moat primitive. Competes with ClawHub / Vercel skills.sh on **provenance, not volume**.
- **Public skill marketplace** at `https://app.mumega.com/marketplace` — card grid of every `marketplace_listed: true` skill with earnings proof line, verification badge, price, author. Detail pages at `/marketplace/skill/{id}` (incl. `?format=json`). Unauthenticated.
- **Operator dashboard** at `https://app.mumega.com/sos` — Phase 0 (flow map) + Phase 1 (Overview + Agents) + Phase 2 (Money pulse + Skill moat). Admin-scoped. Now renders a 30-node / 38-edge service map with license-split badges (Community / Proprietary / External).
- **Customer dashboard** tenant moat panels — "Your Skills", "Your Earnings", "Recent Usage" on `/dashboard`.
- **Agent OS product page** at `mumega.com/products/agent-os` — pricing, install one-liner, marketplace link, pitch.
- **SOS lab page** at `mumega.com/labs/sos` — engineering surface (contracts, changelog, architecture).
- **`mumega.com/install`** shell script — 30-second Mac onboarding. Signup + `.mcp.json` + `~/.claude.json` patching, per-tool snippets for Cursor / Codex / Gemini CLI / Windsurf.
- **Internal SkillCards** (8 so far) — Mumega **dogfoods its own skill registry** for its own cleanup work. Every maintenance commit is driven by a SkillCard invocation with provenance + earnings tracking.
- **Economy UsageLog** (`POST /usage`, trop issue #98) — currency-agnostic (`cost_micros`), tenant-scoped, append-only JSONL. Enables edge tenants (CF Workers) to ingest model-call telemetry.
- **AI-to-AI commerce demo** — `scripts/demo_ai_to_ai_commerce.py`. Cross-squad purchase with $MIND settlement, full UsageLog trace, earnings bump on author skill.
- **Provider Matrix simplified** — `sos/providers/matrix.py` with `ProviderCard`, `CircuitBreakerConfig`, `CircuitBreaker` 3-state machine (closed / open / half_open), `load_matrix()`, `select_provider()`. YAML config at `sos/providers/providers.yaml`. Health-probe CLI scaffold at `sos/providers/health_probe.py`. **25 new contract tests** on ProviderCard JSON Schema (v0.4.1 same-day add).
- **SquadTask v1** — schema + Pydantic binding + 57 tests. Squad Service has a typed contract for the first time. Sole source of truth for task state.
- **UsageEvent v1** — JSON Schema Draft 2020-12 matching existing Pydantic + 25 roundtrip tests. Completes the contracts set for v0.4.1.

### Changed
- **Mirror subscribes to the bus.** New `mirror_bus_consumer.service` (systemd --user) tails `sos:stream:global:*`, auto-writes engrams with embeddings on every v1 send / task_completed / announce. Retires 5+ synchronous `mirror_post("/store", ...)` call sites from `sos_mcp_sse.py`. **Kills 3 debt items** (Mirror-no-bus-consumption, UsageLog-not-mirrored, write-amplification).
- **Squad Service is the single source of truth for tasks.** Mirror `/tasks` endpoints retired → HTTP 410 Gone. SOS `sos/mcp/tasks.py` callers repointed to `http://localhost:8060`. **Ends the double task system.**
- **Single auth module.** `sos/services/auth/__init__.py` ships `verify_bearer(authorization) -> AuthContext | None`. 4 call sites migrated (dashboard `_verify_token`, economy `_verify_bearer`, MCP SSE token path, Mirror `resolve_token`). Env-var fallback (`SOS_SYSTEM_TOKEN`, `MIRROR_TOKEN`) preserved. 30-second TTL + mtime cache.
- **Dispatcher scope-trim.** `sos/services/dispatcher/` + `workers/sos-dispatcher/` archived (`.archive/` suffix, `ARCHIVED.md` explains retirement). Post-competitive-scan pivot: runtime is commoditized by OpenAI/Anthropic/Google/MS; Mumega's moat is economy + provenance + coordination.
- **Deprecated code consolidated.** `sos/mcp/redis_bus.py` + `sos/mcp/sos_mcp.py` stdio moved to `sos/deprecated/` via git mv. `remote.js` kept — actively served as `/sdk/remote.js` by bridge.

### Fixed
- **Trop #97** — adapter pricing tables stale. Refreshed Gemini / Anthropic / OpenAI catalogs with 2026-04-17 data. Added `PricingEntry` with flat-per-call support (Imagen 4 / DALL-E 3). Source-linked every non-zero entry.
- **Trop #98** — edge tenants had no way to report usage to the platform ledger. Shipped `POST /usage` + UsageLog.

### Architectural invariants
- Contract coverage extended: **Agent Card v1 + Messages v1 (8 types) + SkillCard v1 + UsageEvent v1 + ProviderCard v1 + SquadTask v1**.
- Bus strict enforcement: unknown message types reject with `SOS-4004`.
- Every non-zero pricing entry carries a `source:` audit tag.
- `app.mumega.com/sos` is the single operator glass: flow map, agents, bus pulse, money pulse, skill moat, incidents.

### Shipped sprint pattern
- **Dogfood loop:** every cleanup commit references a SkillCard; each SkillCard's `earnings.invocations_by_tenant.mumega` increments by 1. Pattern proves the platform maintains itself with its own primitives — strongest possible moat proof.

### Tests
- 403 passed at tag (was 185 at v0.4.0). Contract suite alone: 230 tests (was 46 at v0.4.0).

### Commits
- 18 commits from `v0.4.0` through `v0.4.1` on branch `codex/sos-runtime-validation`.

## [0.1.0] - 2026-02-03

### Added
- **CLI**: `mumega` command with doctor, chat, start, status, version
- **Engine**: Multi-model support (Gemini, Claude, GPT, Grok, Ollama)
- **Resilience**: Circuit breakers, rate limiting, failover router
- **Autonomy**: Dream synthesis, pulse scheduling, coordinator
- **Memory**: Tiered storage with Cloudflare backends
- **Identity**: QNFT system, capability-based access control
- **Errors**: Protocol-level error codes (1xxx-8xxx ranges)
- **Config**: Validation system with `.env.example`
- **Tests**: Unit tests for resilience, CLI, autonomy, dreams
- **Security**: Hardened docker-compose, secret scanning
- **Docs**: OpenClaw learnings, plugin model

### Infrastructure
- PyPI package name: `mumega`
- Python 3.10+ required
- Optional dependencies: gemini, openai, local, full

## [0.1.1] - 2026-02-03

### Added
- **Security**: SSRF protection for external API calls (#47)
- **Security**: Scope-based authorization system (#50)
- **Security**: Ed25519 capability signature verification (#1)
- **Observability**: Prometheus metrics for circuit breakers, rate limiters, dreams, autonomy (#18)
- **Reliability**: Gateway failover with circuit breaker persistence (#15)
- **Testing**: Load tests for rate limiter and circuit breaker (#20)
- **Ops**: Prometheus alerting rules for SOS services (#23)

## [0.4.0] - 2026-04-17

### Added
- **All legacy producers migrated to v1 types**:
  - `sos/bus/bridge.py` `sos_msg()` — builds via `SendMessage`/`AnnounceMessage` Pydantic models. Legacy `msg_type="chat"` is mapped to `"send"`; legacy target `"broadcast"` is normalized to `"sos:channel:global"`.
  - `sos/mcp/sos_mcp.py` `sos_msg()` — same migration. The deprecated MCP stdio entry-point emits v1 on the wire.
  - `sos/mcp/sos_mcp_sse.py` broadcast handler — uses `SendMessage` directly with channel target (parallel to the send handler shipped in beta.1).
  - `sos/mcp/redis_bus.py` `sos_message()` — same v1 mapping, kept for backwards parameter compatibility with older MCP installs.
- **Strict enforcement** at `sos/services/bus/enforcement.py`. `enforce()` now rejects unknown types with `SOS-4004` (legacy-tolerance window closed). `enforce_or_log()` preserved for gradual rollout call sites that may still need it.

### Changed
- The legacy `{"type": "chat", ...}` and `{"type": "broadcast", ...}` shapes no longer exist anywhere in the SOS codebase as a producer. Consumers that read legacy entries from historical Redis streams continue to work because `from_redis_fields()` on `BusMessage` is tolerant of both shapes.
- Contract tests: 56/56 passing (8 schema files × canonical target pattern × round-trip symmetry).

### Sprint delivery notes
- Sprint 3 was a ~30-minute integration pass (no subagent dispatch; each migration is 30–50 lines and reads best done in-process).
- Running services picked up the migrations on next restart. Existing long-lived bus messages in historical streams (pre-0.4.0 legacy shape) are read unchanged by `from_redis_fields()`.

## [0.4.0-beta.1] - 2026-04-17

### Added
- **Contracts: v1 "send" type in production** — primary MCP SSE gateway send handler now builds via `SendMessage` Pydantic model. Source, target, timestamp, message_id validated on construction. Legacy "chat" type retired from this call path.
- **Structured payloads** on the bus: `payload: {"text": "...", "content_type": "text/plain"}` (was: JSON-stringified single-level object).

### Changed
- `sos/mcp/sos_mcp_sse.py` send handler produces v1 messages; inbox + wake-daemon continue to parse identically (payload is still JSON-encoded in Redis hash field).

### Known remaining (closed in 0.4.0)
- `sos/bus/bridge.py`, `sos/mcp/redis_bus.py`, `sos/mcp/sos_mcp.py` still produce legacy "chat"/"broadcast" types. They flow through `enforcement.enforce()` legacy-tolerant path. Migration scheduled for Sprint 3+.

## [0.4.0-alpha.2] - 2026-04-17

### Added
- **Message schema registry v1** — 8 JSON Schema files under `sos/contracts/schemas/messages/` (announce, send, wake, ask, task_created, task_claimed, task_completed, agent_joined). Draft 2020-12. All 8 share a normalized target pattern supporting multi-level channels (`sos:channel:private:agent:athena` etc.).
- **Pydantic bindings** at `sos/contracts/messages.py`. `BusMessage` base + 8 typed subclasses with nested `*Payload` models. `parse_message()` dispatcher. `to_redis_fields()` + `from_redis_fields()` symmetric round-trip.
- **Enforcement module** at `sos/services/bus/enforcement.py`. `enforce()` and `enforce_or_log()`. Legacy-tolerant (known v1 types validated strictly; unknown types pass through for migration window). Error codes SOS-4001/4002/4003.
- **46 new contract tests** across `tests/contracts/test_messages_*.py`. 56 total passing (Agent Card + Messages).
- **Claude.ai sos-claude connector identity fixed** — `tokens.json` entry "Claude.ai — Hadi browser agent" now maps to `agent: hadi` (was incorrectly `agent: kasra`, self-contradicting with its own label). Flat identity resolved for Hadi's primary claude.ai surface.

### Changed
- Nothing under `sos/kernel/` or public service interfaces changed. Contracts layer is additive.

### Architectural
- Sprint 1 delivery pattern validated: 12 Sonnet subagent dispatches in parallel/sequential, 1 Opus architectural gate review (Athena found 4 real drift issues, all fixed), ~45min wall time, ~$4.50 total subagent spend.

## [0.3.0] - undated

Bulk of v0.3.x work predates this changelog's consistent maintenance. See
git log between tags `v0.2.0-beta2` and `v0.4.0-alpha.2` for the chronology,
including: SaaS service + Stripe + Resend, Inkwell v4/v5, mumega-edge worker,
multi-seat tokens, build queue, audit logging, rate limiting, RBAC,
notification router + webhooks, MSG-002/003, SEC-001/002/004/005, PIP-001/002/003,
customer tool gating, signup → build pipeline, ToRivers marketplace.

## [Unreleased]

### Pending
- **Service restart** to pick up the 0.4.0 producers (sos_mcp_sse, sos_mcp, bridge, redis_bus). Running processes import `sos_msg()` at boot and continue to use the legacy builder until restart.
- Per-agent token migration for mumega-hosted tmux agents (kasra deferred per +500k context decision). Only sos-medic + trop provisioned with per-agent tokens so far.
- v0.4.1 Provider Matrix (deterministic LLM routing, circuit breakers, FMAAP Metabolism extension).
- v0.4.2 External observability plane (mumega-watch on a second VPS, not Cloudflare).
- Additional model provider integrations.
