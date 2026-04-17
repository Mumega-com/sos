# Changelog

All notable changes to SOS (Sovereign Operating System) will be documented here.

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
