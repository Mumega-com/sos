# SovereignOS (SOS) Architecture Agreement

Status: Draft v0.1
Owner: Codex
Last Updated: 2026-01-10

## Purpose
This document defines the architecture agreement for SovereignOS (SOS). We use it to align all agents on scope, boundaries, and contracts before implementation. Changes to core architecture require updating this document first.

## Vision
Build a sovereign, modular operating system for agents and humans where:
- The core remains small, fast, and auditable.
- Interfaces are clients, not the brain.
- Memory, economy, and tools are pluggable services.
- New agents can safely onboard and join squads or guilds.
- River is the trusted gatekeeper across editions (business, education, kids, art/music).

## Principles
- Microkernel: keep the core minimal and stable.
- Strict boundaries: adapters never import heavy engine internals.
- Lazy loading: heavy deps load only inside their service.
- Capability-based access: tools and actions require explicit grants.
- Offline-first, sovereign-by-default.
- Reproducible behavior over cleverness.

## System Overview (Conceptual)

  [Adapters] --HTTP/IPC--> [Engine Service]
      |                        |
      |                        |---> [Memory Service]
      |                        |---> [Economy Service]
      |                        |---> [Tool/MCP Service]
      |
      +---> [Onboarding + Identity Service]

## Core Modules

### 1) Kernel
Responsibilities:
- Message/response schema
- Identity primitives
- Config and runtime paths
- Capability and policy definitions

Constraints:
- No external network calls
- No heavy ML or vector deps

### 2) Engine Service
Responsibilities:
- Orchestration and reasoning
- Model selection and failover
- Routing to tools/memory/economy

Constraints:
- Must boot without heavy deps if services are disabled
- No direct disk writes outside runtime paths

### 3) Memory Service
Responsibilities:
- Vector memory, retrieval, ranking
- Mirror or swarm memory bridges
- Long-term memory lifecycle

Constraints:
- Owns heavy deps (chroma, sentence-transformers)
- Provides API to Engine, not imported by Engine

### 4) Economy Service
Responsibilities:
- Work ledger, payouts, slashing
- Wallet adapters (Solana now, plug-in for others)
- Treasury policies and limits

Constraints:
- Wallets are plugins, not required for client-only mode

### 5) Adapter Clients
Responsibilities:
- CLI, Telegram, Discord, Web, API gateways
- Thin clients that call Engine Service

Constraints:
- No heavy deps
- No direct access to memory/economy

### 6) Onboarding + Identity Service
Responsibilities:
- River-led onboarding flow
- Squad/guild assignment
- Policy enforcement for editions

Constraints:
- Must be auditable and rule-driven

### 7) Tool/MCP Service
Responsibilities:
- Tool registry
- MCP discovery and session handling
- Capability gating

Constraints:
- Tool execution is isolated and auditable

## Runtime Modes
- Pure Client: Adapters only, Engine remote.
- Local Sovereign: Engine + Memory + Economy on same host.
- Swarm: Engine local, memory/economy remote or shared.

## Data and Storage
- Kernel state: small local config, versioned.
- Engine state: conversation cache, runtime logs.
- Memory state: vector store + mirror integration.
- Economy state: ledger + wallet history.

## Interfaces and Contracts
- HTTP+JSON for v0.1 (IPC upgrade later).
- Explicit versioned API contracts.
- All cross-service calls must be versioned and documented.

## Security Model
- Capability tokens for dangerous actions.
- Explicit sandboxing and safe tool allowlist.
- River as root gatekeeper; editions enforce policy layers.

## Plugin Architecture
- Plugins declared via manifest:
  - name, version, capabilities, dependencies
  - entrypoints for tools, adapters, or wallets
- Engine loads plugins only through the Tool/MCP Service.

## Governance and Change Control
- Architecture changes require updating this document first.
- Services must include a health endpoint and version endpoint.
- Breaking changes require a migration plan and approval.

## Migration Strategy (Draft)
- Keep current repo stable; create a new SOS workspace.
- Extract kernel and interfaces first, then move services.
- Use feature flags to switch adapters to SOS Engine.

## Open Decisions
- IPC protocol choice (HTTP vs gRPC vs NATS).
- Primary datastore for memory service.
- Plugin signature and trust model.
- Default edition policy sets (business, education, kids, art).

---

## Claude Code Review & Recommendations

**Reviewer:** Claude Code (Opus 4.5)
**Date:** 2026-01-10
**Status:** Approved with recommendations

### Resolved Decisions

#### IPC Protocol: HTTP+JSON → Unix Domain Sockets
**Recommendation:** Start with HTTP+JSON for v0.1 (pragmatic), but design for Unix domain socket upgrade in v0.2.

Rationale:
- HTTP+JSON: Easy debugging, curl-friendly, works across network
- Unix sockets: 10x lower latency for local services, no TCP overhead
- Avoid gRPC unless streaming is critical—protobuf adds compilation step

**Implementation:** Services should accept `SOS_IPC_MODE=http|unix` env var.

#### Plugin Trust Model: Signed Manifests + Sandbox
**Recommendation:** Ed25519 signed manifests with tiered trust levels.

```
Trust Levels:
├── core      - Ships with SOS, no signature check
├── verified  - Signed by Mumega key, auto-approved
├── community - Signed by author, requires user approval
└── unsigned  - Development only, blocked in production
```

Plugins execute in subprocess sandbox with:
- Filesystem: read-only except designated plugin data dir
- Network: allowlist only (no arbitrary outbound)
- Capabilities: explicit grants via manifest

#### Memory Service Datastore: SQLite + Pluggable Embeddings
**Recommendation:** SQLite for metadata, pluggable embedding backend.

```
Embedding Backends:
├── local     - sentence-transformers (heavy, offline)
├── openai    - text-embedding-3-small (light, online)
├── voyage    - voyage-3 (high quality, online)
└── ollama    - nomic-embed-text (local, moderate)
```

This allows "light mode" without 2GB+ model downloads.

### Additional Requirements

#### 1. Observability Standard
All services MUST implement:

```python
# Health endpoint
GET /health → {"status": "ok|degraded|unhealthy", "version": "0.1.0"}

# Metrics endpoint (Prometheus format)
GET /metrics → # TYPE sos_requests_total counter\nsos_requests_total{service="engine"} 1234

# Structured logging
{"ts": "2026-01-10T12:00:00Z", "level": "info", "service": "engine", "trace_id": "abc123", "msg": "..."}
```

See: `docs/OBSERVABILITY.md`

#### 2. Rate Limiting & Backpressure
Adapters calling Engine must respect:
- Per-adapter rate limits (configurable)
- Queue depth limits with 429 responses
- Circuit breaker pattern for downstream failures

#### 3. State Machine Definitions
Complex flows must have explicit state machines:

```
Task Lifecycle:
  pending → claimed → in_progress → review → completed
                                         ↘ rejected → pending
                    ↘ abandoned → pending

Onboarding Flow:
  anonymous → identified → verified → squad_assigned → active
```

See: `docs/STATE_MACHINES.md`

#### 4. Testing Strategy
Each service must have:
- Unit tests (pytest, >80% coverage on business logic)
- Contract tests (validate API schema)
- Integration tests (docker-compose test harness)

See: `docs/TESTING_STRATEGY.md`

### Migration Timeline Recommendation
Set hard deadlines to prevent parallel codebase rot:

| Phase | Deadline | Kill Old Path |
|-------|----------|---------------|
| Phase 1: Kernel | +2 weeks | - |
| Phase 2: Services | +4 weeks | - |
| Phase 3: Adapters | +6 weeks | Week 8 |
| Phase 4: Onboarding | +8 weeks | Week 10 |

After Week 10: Delete feature flags, old code paths removed.

### Files Created by Claude Code
- `docs/OBSERVABILITY.md` - Logging, metrics, tracing standards
- `docs/SECURITY_MODEL.md` - Plugin trust, capability tokens
- `docs/STATE_MACHINES.md` - Task and onboarding lifecycles
- `docs/TESTING_STRATEGY.md` - Testing requirements
- `sos/kernel/` - Core schema and types
- `sos/contracts/` - Service interface definitions

---

## Next Steps
- Review by Gemini, Claude, River, and Mumega.
- Capture feedback here, then generate task breakdown.

