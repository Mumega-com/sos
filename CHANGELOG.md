# Changelog

All notable changes to SOS (Sovereign Operating System) will be documented here.

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
