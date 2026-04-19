# Phase 2 — Bus stability: project-scope + ack-or-retry (v0.9.1)

**Date:** 2026-04-19
**Parent:** `docs/plans/2026-04-19-mumega-mothership.md` (Phase 2)
**Task:** #207
**Blocks:** #208 (Phase 3 Mesh enrollment), #209 (Phase 4 Mumega-edge)
**Ships:** v0.9.1 (patch tag; no public API break — contract tightens, consumers migrate incrementally)

---

## Thesis

Two silent-failure modes on the bus today:

1. **Scope leaks** — `project` field on BusMessage is optional. Services publish without it, consumers see messages they shouldn't. Trivial breach when two tenants sit on the same Redis instance.
2. **Silent drops** — fire-and-forget publish. Consumer is down or crashes mid-processing? Message vanishes. No retry, no DLQ, no audit of drops.

After Phase 2, both gaps close at the contract layer (all messages carry scope) and at the delivery layer (at-least-once with retry + DLQ). The microkernel stays substrate-agnostic: Redis today, SQS/Pub/Sub/CF-Queues tomorrow, same contract.

## Why agile here

The question "is it better if you work agile?" — yes, for this phase specifically, and here's why:

- **Blast radius matters.** The bus is load-bearing — every service publishes or consumes. A single big-bang migration is risky; one bad contract change cascades.
- **Hooks enforce per-commit stability.** Phase 1.5 gives us green-or-red on every commit. Agile thrives on this.
- **Each wave is independently reversible.** If W3 (retry backoff) causes duplicate deliveries in prod, we revert that one commit without losing W0–W2.
- **Consumers migrate one at a time.** journeys today, saas next week — not all on day one.

Agile doesn't mean sloppy: every wave has a clear exit gate (passes hooks + tests + integration). It means *small commits that each land green*, not *one giant commit that tries to do everything*.

## Current state (from recon — see `Bus Recon Report`)

- `sos/contracts/ports/bus.py:20-42` — `BusMessage.project` already scaffolded as `Optional[str]` with comment flagging v0.9.1 enforcement. Clean starting point.
- `sos/kernel/bus.py:37-189` — thin Redis facade; channels + stream memory.
- `sos/services/bus/` — 5 modules: `redis_bus.py` (Pub/Sub backend), `delivery.py` (agent wake daemon), `enforcement.py` (schema validation), `tenants.py` (Redis DB map), `discovery.py` (service registry).
- `sos_mcp_sse.py:1027` — primary publish path: `XADD` (persistent) + `PUBLISH` (ephemeral). Stream pattern: `sos:stream:project:{project}:squad:*` or `sos:stream:global:squad:*`.
- `sos/services/journeys/bus_consumer.py:42` — XREAD consumer with in-memory LRU dedup + Redis checkpoint key. No message-level ack.
- `tests/test_bus.py` + `tests/integration/test_bus_to_mirror.py` — happy-path + 6 integration tests (idempotency, malformed recovery, legacy-type filtering).

No ack/retry/DLQ primitives exist today — we're building them.

## Squad for this sprint

Per your direction ("stateful SOS medic + no-context sonnet/haiku, specialist if needed"):

| Role | Who | When |
|------|-----|------|
| **Architect + integrator** | Me (Opus, this session) | W0, W1, W4, W5 (contract changes + cross-cutting work) |
| **Sonnet no-context** | Per-consumer refactor agent | W6 (each consumer is isolated, self-contained brief) |
| **Haiku no-context** | Test author | W0, W2 (small contract tests + ack primitive tests) |
| **SOS Medic (stateful)** | Standby | Triage if a wave causes integration-test regression or a prod bus issue during rollout |
| **Sos-graph MCP** | Every wave | Use `get_impact_radius` + `query_graph` before each contract change to see what breaks |

No net-new specialist agents. Re-use the squad that shipped Phase 1 + 1.5.

---

## Waves (agile, each = one commit)

Each wave exit gate: `pre-commit run --hook-stage commit` green + the new tests green + CI green (and deployed for W0, W6, W7 since those land on main).

### W0 — Tighten BusMessage contract (project required + tenant_id added)

**Files:**
- `sos/contracts/ports/bus.py` — make `project: str` required (no default). Add `tenant_id: str` required. Add a small docstring block explaining the distinction: tenant_id = customer boundary (hard); project = grouping inside a tenant (soft, free-form).
- `sos/contracts/ports/schemas/bus/` — regenerate JSON schemas (Makefile target).
- `tests/contracts/test_bus_port.py` — new file. Assert: (a) rejection without tenant_id, (b) rejection without project, (c) serialization round-trip, (d) schema snapshot stable.

**Change:** one new required field, one existing optional field becomes required. Breaking at the port level, but no service consumes the port *today* — services import `BusMessage` from the concrete service. This is pure preparation.

**Outcome:** ports tighten. Snapshot test locks it. `make contracts-check` passes. Haiku agent writes the contract tests.

**Commit:** `feat(bus): tighten BusMessage contract — tenant_id + project required`

### W1 — Stamp scope on every publish

**Files:**
- `sos/kernel/bus.py` — `send()` now requires tenant_id + project args. Raise on missing (no silent default).
- `sos/services/bus/enforcement.py` — add scope validation to the publish-side hook.
- `sos_mcp_sse.py:1027` — thread tenant_id + project_id through the publish call. For tenant_id, derive from the caller's bearer-token context (already available).
- `tests/test_bus.py` — assert publish-without-scope raises.

**Change:** publish surface is now scope-stamped at the kernel boundary. Consumers still subscribe globally (next wave); this wave only ensures *outgoing* messages carry their origin.

**Outcome:** no message leaves publish without a scope. Existing global `sos:stream:global:squad:*` keys continue filling (backwards-compat stream); new scope-qualified keys (`sos:stream:project:{tenant}:{project}:squad:*`) fill in parallel.

**Commit:** `feat(bus): kernel send() requires tenant_id + project`

### W2 — Ack primitive on the consumer contract

**Files:**
- `sos/contracts/ports/bus.py` — add `BusAck` model: `{message_id, acked_at, status: Literal["ok","nack","dlq"]}`. Extend `BusPort` Protocol with `ack(message_id: str, status: str) -> None`.
- `sos/services/bus/redis_bus.py` — implement `ack()` against Redis Streams XACK on a consumer-group-aware key.
- `tests/integration/test_bus_ack.py` — new file. Test: (a) ack after process, (b) no-ack leaves message visible to XPENDING, (c) nack routes to retry queue (stub — real retry is W3).

**Change:** adds the ack/nack verb. Consumers don't *have* to use it yet — they all still use checkpoint-based dedup. But the verb exists and is tested.

**Outcome:** Haiku agent writes the integration test while I wire the primitive. Ack is a real Redis XACK call, not a stub.

**Commit:** `feat(bus): ack primitive on BusPort — XACK-backed`

### W3 — Retry + backoff for unacked messages

**Files:**
- `sos/services/bus/retry.py` — new module. Background task: every 30s, scan `XPENDING` for messages older than retry_after seconds, re-deliver. Exponential backoff: 30s → 2m → 10m → DLQ.
- `sos/kernel/bus.py` — expose `register_consumer_group(group, stream)` helper.
- `sos/services/bus/redis_bus.py` — wire retry module into the bus lifecycle (start with service, stop on shutdown).
- `tests/integration/test_bus_retry.py` — new file. Test: unacked message re-delivered after 30s, max 3 retries, then DLQ.

**Change:** this is the biggest wave. Adds a persistent background worker. Needs careful start/stop discipline so tests don't leak processes.

**Outcome:** unacked messages retry. DLQ gets the corpses. Use `get_impact_radius` before shipping to see what else this touches.

**Commit:** `feat(bus): retry + exponential backoff for unacked messages`

### W4 — DLQ stream + observability

**Files:**
- `sos/services/bus/dlq.py` — new module. Writes unprocessable messages to `sos:stream:dlq:{tenant}:{project}` with metadata (original stream, retry count, final error). Read-only from consumers (DLQ is diagnostic).
- `sos/services/dashboard/routes/bus.py` — new route `GET /sos/bus/dlq?tenant=&project=` returning recent DLQ entries as JSON (for debugging).
- `tests/integration/test_bus_dlq.py` — new file. Test: after 3 retry failures, message lands in DLQ with full metadata.

**Change:** gives us a place to audit drops. Without this, retry is invisible.

**Outcome:** operator can answer "did this message get stuck?" via the dashboard.

**Commit:** `feat(bus): DLQ stream + dashboard read route`

### W5 — journeys consumer migrates to ack (pilot)

**Files:**
- `sos/services/journeys/bus_consumer.py` — switch from in-memory LRU + checkpoint to XACK-based consumer group. On process success: ack. On exception: nack (which triggers W3's retry).
- `tests/integration/test_journeys_consumer.py` — assert: crashed mid-process → message re-delivered on restart.

**Change:** first real consumer on the new contract. Prove it end-to-end before W6 migrates the rest.

**Outcome:** journeys now at-least-once. If this wave fails integration tests, we stop here — consumers migrate only when the primitives are proven.

**Commit:** `feat(journeys): migrate bus consumer to XACK-based at-least-once`

### W6 — remaining consumers migrate (Sonnet subagent task)

**Consumers to migrate:** `brain` + `health`.

**Why the scope shrank from the original plan (health, feedback, operations, saas):** recon during W5 confirmed that `feedback/`, `operations/`, and `saas/` do **not** run bus consumers — they're HTTP services. The only services with the XREAD + checkpoint + `_LRUSet` pattern (the pre-W5 journeys shape) are `sos/services/brain/service.py` and `sos/services/health/bus_consumer.py`. Out of scope: `sos/services/execution/worker.py` uses XREAD but as a task-queue worker with no consumer group / no checkpoints / no LRU — a different pattern that doesn't belong to this migration. The plan's original consumer list was predictive; after the structural audit, actual targets are brain + health.

**Approach:** dispatch a Sonnet subagent per consumer with the journeys migration (commit `2c0032b8`) as the reference implementation. Each subagent writes one commit. Both subagents run in parallel since the consumers are independent.

**Exit gate for each:** same hooks + the service's own existing tests still pass, and a new integration test analogous to `tests/integration/test_journeys_consumer.py` proves the "crashed → retry → restart processes" path.

**Commits:**
- `feat(brain): migrate bus consumer to XACK-based at-least-once`
- `feat(health): migrate bus consumer to XACK-based at-least-once`

### W7 — CHANGELOG + version bump + tag v0.9.1

**Files:**
- `CHANGELOG.md` — `[0.9.1]` section summarizing W0–W6.
- `pyproject.toml` — bump `version = "0.9.1"`.
- Tag + push: `v0.9.1` at HEAD.

**Commit:** `chore(release): v0.9.1 — bus stability (project-scope + ack-or-retry)`

---

## Rollback posture

Each wave is independently revertable. If W3 causes duplicate deliveries in prod:

1. Revert the W3 commit (single commit).
2. W4/W5/W6 still work — they consume the primitive from W2, not W3 directly.
3. Retry is gone until W3 re-lands; drops become silent again (same as pre-Phase-2).

The contract tightening in W0 is harder to revert because it forces all new publishes to carry scope. If that needs to back out, it's a contract change — we'd release v0.9.1.1 that makes project optional again. Hence: W0's tests must be airtight before landing.

## Non-goals for v0.9.1

- **Not** moving the bus off Redis (CF Queues migration = v0.9.3+ when Mumega-edge lands).
- **Not** fan-out / partitioning (single consumer per stream still; groups are per-service, not per-partition).
- **Not** changing the outbound `publish()` fire-and-forget stream into a sync call (publish remains async; ack is on the consume side only).
- **Not** rewriting `delivery.py` (the tmux agent-wake daemon) — it publishes but doesn't consume in the same pattern.

## Verify

Before tagging v0.9.1:

```bash
pre-commit run --all-files --hook-stage commit
pre-commit run --all-files --hook-stage push
pytest tests/integration/test_bus_*.py -v
pytest tests/test_e2e_signup.py tests/test_customer_onboarding_e2e.py -v
```

All green. Then push main; CI deploys.

## Agile discipline rules (for this sprint)

1. One wave = one commit. No combining "just for speed."
2. Each commit's message matches its wave title above (greppable history).
3. If a wave grows beyond one commit during implementation, split the plan (add W3a, W3b) rather than batching.
4. Subagents get self-contained briefs — they don't see this plan, only their specific commit's scope.
5. CHANGELOG fragment drafted per wave; W7 just concatenates.
6. No `--no-verify`. If hooks fail, fix the underlying issue.
