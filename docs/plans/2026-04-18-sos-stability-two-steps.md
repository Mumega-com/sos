# SOS Stability — Two Steps to Fully Stable, Then CF Layer

**Author:** Loom
**Date:** 2026-04-18
**Authorized by:** Hadi — "Go for option C... make or use sprints and subagents as needed, if you make skills for agents maybe you get better result. Go on and dont stop"
**Scope:** Option C (combined hardening sprint) — Step 1 Stability MVP → Step 2 Stability Polish → CF layer on top.
**Storage:** handled by Hadi separately (2 HDDs). Disk-pressure work is out of scope here.

---

## Goal

Turn the SOS microkernel from "works when I watch it" into "works when I don't." Ship the two stability increments the platform needs before the CF edge layer can safely multiplex it.

**Success criteria for Step 1 (Stability MVP):**
1. All `pytest -q` passes — no errors, no pre-existing failures, no `auth_migration` flakes.
2. The 12 most-called write endpoints accept and honour an `Idempotency-Key` header (replay returns the stored response instead of double-executing).
3. Every bus envelope carries a `trace_id`; downstream emissions propagate it; `AuditEvent` records it.
4. Pre-gate audit regression tests exist for **both** integrations and identity (closing the #138 gap permanently).
5. `AsyncRegistryClient` has unit tests covering list/get/not-found/network-error.

**Success criteria for Step 2 (Stability Polish):**
1. OpenTelemetry spans emitted from every FastAPI service + the bus consumer loop; correlated via `trace_id`.
2. Traces linkable to Mirror engrams (one-way index on `trace_id`).
3. Minimal traces viewer at `/sos/traces` on the dashboard.
4. `alembic` managing `squads.db` + `sos_pgvector` schemas (no more ad-hoc `CREATE TABLE` at import time).
5. Typed config loader replacing the `.env.secrets` + `tokens.json` + `agent_registry.py` sprawl.

**After both steps:** begin CF layer — `workers/sos-dispatcher/`, Code Mode MCP wrapper, Mesh enrollment (pending Hadi action #33).

---

## Squad

Three specialist subagent skills, built once, reused across the sprint. Skills are written to `.claude/agents/`:

| Skill | Spawn-as | Purpose |
|---|---|---|
| `sos-test-closer` | `general-purpose` | Writes regression pytests from a pattern in an existing `app.py`. Sonnet-tier work. |
| `sos-idempotency-author` | `general-purpose` | Wraps a write endpoint with an idempotency-key check + response cache, using the canonical helper. Sonnet-tier work. |
| `sos-trace-threader` | `general-purpose` | Threads `trace_id` through a service's envelope construction + audit calls. Sonnet-tier work. |

Stateful follow-up work (OTEL, alembic, viewer) is Loom's own direct work — too cross-cutting to delegate cheaply.

Fuel discipline: the three subagents are regular-grade (Sonnet). Loom stays on Opus for cross-cutting judgment and plan revision.

---

## Step 1 — Stability MVP (~1.5 weeks)

### 1.1 Close testing debt (day 0)

**Step 1.1.a: Integrations pre-gate regression test**
File: `tests/services/test_integrations_pregate_audit.py` (new)
Change: three `pytest.mark.asyncio` tests — missing-bearer to each of the 3 integrations endpoints, asserting exactly one `AuditEvent` with `kind=POLICY_DECISION`, `decision=DENY`, `policy_tier="integrations_pregate"`, `agent="anonymous"`.
Outcome: `pytest tests/services/test_integrations_pregate_audit.py -q` → 3 passed.

**Step 1.1.b: Identity pre-gate regression test**
File: `tests/services/test_identity_pregate_audit.py` (new)
Change: mirror 1.1.a for the 2 avatar endpoints — missing bearer + invalid bearer + scopeless token, asserting `policy_tier="identity_pregate"` for each.
Outcome: `pytest tests/services/test_identity_pregate_audit.py -q` → 5+ passed.

**Step 1.1.c: AsyncRegistryClient unit tests**
File: `tests/clients/test_registry_client.py` (new)
Change: cover `list_agents()` success, `get_agent()` 200 + 404, and network timeout (→ empty list / raise). Use `respx` or `httpx.MockTransport`.
Outcome: 4+ passed; graph shows the client now has `tests_for`.

**Step 1.1.d: Triage `test_auth_migration` errors**
File: `tests/services/test_auth_migration.py` (existing)
Change: run it, read the 5 errors, classify each as (fix / skip / delete). Fix the fixable ones; mark obsolete cases with `@pytest.mark.skip(reason="...")` + issue link; delete cases testing code that no longer exists.
Outcome: `pytest tests/services/test_auth_migration.py -q` → 0 errors (skips allowed with recorded reasons).

### 1.2 Bus trace_id propagation (days 1-2)

**Step 1.2.a: Add `trace_id` to `BusMessage` envelope**
File: `sos/contracts/messages.py`
Change: add `trace_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{32}$")` to `BusMessage`. Update `to_redis_fields()` to include it if set.
Outcome: `BusMessage(...).trace_id` round-trips via `to_redis_fields()` / `parse_message()`.

**Step 1.2.b: Update JSON schemas to accept `trace_id`**
Files: `sos/contracts/schemas/messages/*.json`
Change: add optional `trace_id` to the base envelope in every v1 schema.
Outcome: schema stability snapshot test still passes (or is regenerated with the expected diff).

**Step 1.2.c: Thread `trace_id` through `new_event()` audit helper**
File: `sos/kernel/audit.py`
Change: accept `trace_id` kwarg on `new_event()`; store on `AuditEvent`.
Outcome: audit records produced inside a bus-consumer context carry the bus envelope's `trace_id`.

**Step 1.2.d: Bus consumer — extract inbound + inject outbound**
Files: `sos/services/bus/delivery.py`, `sos/kernel/bus.py` (the consumer core)
Change: on each message received, pull `trace_id` (or mint one if absent) and stash in a `contextvars.ContextVar`; all `new_event()` + outgoing emissions read from that var.
Outcome: a task.created consumed → emits task.scored with the same `trace_id`; one `AuditEvent` row ties them.

**Step 1.2.e: Update canonical E2E test to assert trace continuity**
File: `tests/brain/test_e2e_brain.py`
Change: seed `trace_id` on the input envelope; assert task.scored and task.routed carry the same `trace_id`; assert `recent_routing_decisions` entry has it too.
Outcome: one extra assertion per routed step.

### 1.3 Idempotency keys on write endpoints (days 3-6)

**Step 1.3.a: Canonical helper**
File: `sos/kernel/idempotency.py` (new)
Change: `async def with_idempotency(key: str | None, fn, ttl_s=86400, redis=...) -> Any` — checks Redis `sos:idem:<key>`, returns cached response or runs `fn()` and caches its result. Same-key-different-payload yields a 409.
Outcome: doctested helper; used by 12 endpoints below.

**Step 1.3.b–m: Wrap 12 write endpoints**

For each of these, the change is: accept `Idempotency-Key: Optional[str] = Header(None)`; wrap the handler body with `with_idempotency(key, ...)`. Subagent `sos-idempotency-author` handles them in parallel.

| # | File | Endpoint |
|---|---|---|
| b | `sos/services/saas/app.py` | POST `/signup` |
| c | `sos/services/saas/app.py` | POST `/onboard` |
| d | `sos/services/saas/app.py` | POST `/tenants` |
| e | `sos/services/saas/app.py` | POST `/tenants/{slug}/seats` |
| f | `sos/services/saas/app.py` | POST `/my/tasks` |
| g | `sos/services/saas/app.py` | POST `/builds/enqueue/{slug}` |
| h | `sos/services/squad/app.py` | POST `/tasks` |
| i | `sos/services/squad/app.py` | POST `/tasks/{task_id}/complete` |
| j | `sos/services/engine/app.py` | POST `/tasks/create` |
| k | `sos/services/economy/app.py` | POST `/credit` |
| l | `sos/services/economy/app.py` | POST `/debit` |
| m | `sos/services/tools/app.py` | POST `/execute` |

Outcome per: replaying the same request with same `Idempotency-Key` → identical response, no side effect (verified by a 2-request test that asserts only 1 ledger row).

**Step 1.3.n: Idempotency helper tests**
File: `tests/kernel/test_idempotency.py` (new)
Change: unit tests for hit/miss/mismatch-409/ttl-expiry.
Outcome: 4+ passed.

### 1.4 Tag v0.5.7 (day 7)

**Step 1.4.a: CHANGELOG + version bump**
Files: `CHANGELOG.md`, `pyproject.toml`
Change: cut a v0.5.7 entry — `trace_id` propagation, idempotency on 12 endpoints, pre-gate regression tests, registry-client tests.
Outcome: `git tag v0.5.7` pushed.

---

## Step 2 — Stability Polish (~2-3 weeks)

### 2.1 OpenTelemetry through the spine

**Step 2.1.a: opentelemetry deps + bootstrap**
Files: `pyproject.toml`, `sos/observability/tracing.py` (new)
Change: add `opentelemetry-api/sdk/instrumentation-fastapi/exporter-otlp`; bootstrap a `TracerProvider` with OTLP exporter pointing at local collector; attach resource `service.name` per service.
Outcome: `GET /health` on any service produces a span visible via `otel-cli list`.

**Step 2.1.b: Instrument every FastAPI service**
Files: every `*/app.py` under `sos/services/`
Change: wire `FastAPIInstrumentor.instrument_app(app)` in startup; inject current `trace_id` (already on the envelope after Step 1) as the span's `trace_id`.
Outcome: dashboard → tools chain produces one connected trace.

**Step 2.1.c: Instrument the bus consumer loop**
File: `sos/kernel/bus.py`
Change: wrap each message-handling iteration in `tracer.start_as_current_span("bus.consume")`; link parent context from the envelope `trace_id`.
Outcome: bus hop appears as a child span in the overall trace.

**Step 2.1.d: Minimal `/sos/traces` viewer**
Files: `sos/services/dashboard/routes/traces.py` (new), `sos/services/dashboard/templates/traces.html` (new)
Change: page reads the OTLP backend (jaeger or tempo) for recent traces by `trace_id`; renders span tree.
Outcome: browse to `app.mumega.com/sos/traces` → last 50 traces listed, click → span tree.

**Step 2.1.e: Trace↔engram link in Mirror**
Files: `sos/services/mirror/mirror_api.py`, `sos/services/mirror/schema.sql`
Change: add `trace_id TEXT NULL` + index on `engrams`; backfill on ingest from envelope field.
Outcome: querying Mirror by `trace_id` returns all engrams produced during that flow.

### 2.2 Alembic for durable schemas

**Step 2.2.a: Alembic for squads.db**
Files: `alembic/`, `alembic.ini` (new); `sos/services/squad/db.py` (touched)
Change: baseline migration from current schema; remove ad-hoc `CREATE TABLE IF NOT EXISTS`.
Outcome: `alembic upgrade head` produces the exact current schema; test sweep green.

**Step 2.2.b: Alembic for pgvector/mirror**
Files: same `alembic/` tree, separate `[database]` section
Change: baseline current mirror schema.
Outcome: fresh clone can bring up Mirror with `alembic upgrade head`.

### 2.3 Typed config consolidation

**Step 2.3.a: `sos/kernel/config.py`**
Files: `sos/kernel/config.py` (new)
Change: pydantic-settings `Settings` class loading `.env.secrets` + the parts of `tokens.json` / `agent_registry.py` that are really config. Expose `get_settings()` singleton. Fails loud on missing required fields.
Outcome: importing `get_settings()` anywhere returns the same typed object.

**Step 2.3.b: Migrate services off ad-hoc `os.getenv`**
Files: every `*/app.py` doing `os.getenv`
Change: replace with `settings.xxx` access; keep env-var names stable.
Outcome: `grep -r "os.getenv" sos/services/` returns only tolerable leftovers (flags, feature toggles).

### 2.4 Tag v0.6.0 (week 4)

**Step 2.4.a: CHANGELOG + version bump**
Files: `CHANGELOG.md`, `pyproject.toml`
Outcome: `git tag v0.6.0` pushed. Stability phase complete.

---

## Step 3 — CF layer (after v0.6.0)

Out of scope for this plan beyond a reminder of what's queued:

- `workers/sos-dispatcher/` — edge CF Worker: bus-token validation, per-tenant rate limits, KV revocation list.
- Code Mode MCP wrapper → 99.9% token reduction for cross-agent MCP calls.
- Mesh enrollment — BLOCKED on #33 (Hadi action).

---

## Risk register

| Risk | Mitigation |
|---|---|
| `trace_id` schema change breaks in-flight messages | Field is optional with default `None`; existing messages without it still parse. |
| Idempotency cache hits across tenant boundaries | Key is namespaced as `sos:idem:<tenant>:<key>`; helper refuses keyless writes at tenant-scoped endpoints. |
| OTEL exporter pressure on :8080 | Run OTLP collector on :4317 locally; buffer-drop on backpressure (OTEL default). |
| Alembic baseline mismatch vs live DB | `alembic stamp head` after verifying schemas match byte-for-byte; never `upgrade` blindly on prod. |
| Pre-existing `auth_migration` failures turn out to be real regressions | Triaged one-by-one in 1.1.d; may extend Step 1 by ~1 day. |

---

## What I am not doing

- **Storage / disk pressure** — Hadi handling via 2 HDDs.
- **Horizontal scaling** — one VPS, no Kubernetes, no consul.
- **New features** — strict hardening sprint; nothing ships that isn't in service of "works unwatched."
- **Full Mirror rebuild** — engram link is a one-way index addition, not a schema refactor.
