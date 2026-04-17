# Bus consumer pattern — the Mirror template

**Date:** 2026-04-18
**Status:** Canonical pattern for services that need to react to bus events.
**Reference impl:** `/home/mumega/mirror/mirror_bus_consumer.py` (separate Mirror repo; see also `systemctl --user status mirror_bus_consumer.service`).

---

## Why this exists

Before Mirror-on-bus (island #12, shipped 2026-04-17), any service that wanted to react to a bus event (e.g., store an engram for every `send`) had two options:

1. Be called synchronously from `sos_mcp_sse.py` via an HTTP side-effect (`mirror_post("/store", ...)`) — tight coupling, write amplification, latency stacking.
2. Poll redis manually — no standard, every service reinvents.

Mirror was refactored onto the bus in island #12 and now subscribes directly to `sos:stream:*`. The pattern it uses is what every future kernel-adjacent service should follow.

---

## The pattern

```python
# pseudocode — see /home/mumega/mirror/mirror_bus_consumer.py for reference
import asyncio
from redis.asyncio import Redis

class MyServiceBusConsumer:
    def __init__(self, stream_patterns: list[str], consumer_name: str):
        self.patterns = stream_patterns           # e.g. ["sos:stream:global:agent:*"]
        self.consumer = consumer_name             # e.g. "mirror-bus-consumer"
        self.redis = Redis(...)
        self.checkpoints: dict[str, str] = {}     # stream → last_id_seen

    async def run(self):
        await self._load_checkpoints()
        while True:
            streams = await self._discover_streams()      # SCAN on redis with pattern
            if not streams:
                await asyncio.sleep(0.5)
                continue
            read_spec = {s: self.checkpoints.get(s, "$") for s in streams}
            results = await self.redis.xread(read_spec, block=1000)
            for stream_name, entries in results:
                for entry_id, data in entries:
                    try:
                        await self._handle(stream_name, entry_id, data)
                        self.checkpoints[stream_name] = entry_id
                        await self._persist_checkpoint(stream_name, entry_id)
                    except Exception:
                        logging.exception("handler failed; skipping event")
                        # DO NOT advance checkpoint on handler error

    async def _handle(self, stream: str, entry_id: str, data: dict):
        """Override in subclass. Idempotent. Must tolerate duplicate delivery."""
        raise NotImplementedError
```

Five invariants every bus consumer must obey:

### 1. Idempotency on message_id

Every v1 bus message carries a unique `message_id` (UUID). The handler must treat duplicate deliveries (same `message_id`) as no-ops. Mirror's consumer, for example, checks whether an engram with `context_id == message_id` already exists before storing.

**Why:** XREAD semantics + redis clustering + consumer restarts can deliver the same entry more than once.

### 2. Checkpoint per stream, not per consumer

The consumer stores `{stream_name: last_entry_id_seen}` in redis at `sos:consumer:<consumer_name>:checkpoint:<stream>`. On restart, it resumes from the checkpoint. It does NOT use redis consumer groups (`XGROUP`) because we want simplicity + one consumer per service — no sharding yet.

**Why:** services come and go during deployment; checkpoints survive restarts; no message lost when the service briefly dies.

### 3. Fail-open, never fail-blocking

If the handler raises on a message, the consumer **logs and skips** — does NOT advance the checkpoint. The message will be re-delivered on next restart. The consumer keeps running — does not crash on handler exceptions.

**Why:** one malformed message should not take down the entire consumer. Poison-pill protection.

### 4. Stream discovery via SCAN, not hardcoded list

The consumer `SCAN`s for matching stream names every iteration. New streams (new agents / new tenants) are picked up automatically. No restart needed to add a new agent.

**Why:** the bus topology grows with new tenants; a static list would require redeploys.

### 5. Backfill + replay CLI

Every bus consumer ships a `python -m <module> replay --stream <name> --from <id> --to <id>` helper that re-runs the handler over historical entries. Useful for:
- Recovering from handler bugs that dropped messages
- Bootstrapping a new service against historical data
- Debugging individual events

Mirror's replay handles ~10k entries/sec (bottleneck is embedding calls, not bus reads).

---

## When to build a new bus consumer vs call the service directly

**Build a bus consumer when:**
- Your service needs to react to agent actions in near-real-time (< 2s)
- The reaction is a side effect (storing, logging, alerting) — not in the agent's critical path
- You want decoupling: the agent publisher doesn't need to know you exist
- Your service runs independently of the publisher (different process, restart independently)

**Call the service directly via HTTP or MCP tool instead when:**
- The agent's workflow must block until your service responds (e.g., `recall` returning a search result)
- You need request-scoped auth context (who asked, what scope)
- Latency < 100ms is required

---

## Services that should follow this pattern next

Per the coherence plan, these are planned or likely:

- **Brain (Stage 2 of coherence plan):** subscribes to `task.created`, `task.done`, `agent.woke` — scores + dispatches. Will be the largest bus consumer.
- **Witness worker:** subscribes to `witness.cast` events (once added to the message types) — applies CoherencePhysics + emits economy transactions for witness rewards.
- **Lineage indexer:** subscribes to `skill.executed` + `skill.registered` — maintains a graph of skill lineage for marketplace queries.
- **Compliance auditor (enterprise on-prem):** subscribes to every event — maintains per-tenant append-only audit log.
- **Calcifer (existing):** already consumes `sos:channel:system:events` — should be re-examined for alignment with this pattern.

---

## Gotchas the Mirror consumer learned

1. **Redis `SCAN` with pattern `*` on a large keyspace returns empty batches.** Use a sensible batch size (`count=100`) + non-match iterations.
2. **`XREAD` with `block=1000` + no data returns an empty list, not None.** Handle both cases.
3. **Serialized entries can have non-UTF-8 bytes** (legacy cruft). Validate + skip; don't crash.
4. **Checkpoints in redis can drift if the service crashes between handler success and checkpoint write.** Atomic option: use a Lua script to combine handler-success-ack + checkpoint-bump. For v1 it's fine to accept rare re-deliveries (idempotency covers it).
5. **Memory growth:** Mirror's consumer embeds every message via Gemini API. On burst traffic, use a bounded in-memory queue to control the concurrency. Failing to do so OOMs the service.

---

## Cross-references

- `/home/mumega/mirror/mirror_bus_consumer.py` — reference implementation (separate Mirror repo)
- `/home/mumega/mirror/mirror_bus_consumer.service` — systemd --user unit (reference)
- `sos/contracts/messages.py` — v1 message types the consumer parses
- `sos/services/bus/enforcement.py` — strict validation (SOS-4001/4002/4003/4004)
- `docs/docs/architecture/MESSAGE_BUS.md` — bus architecture overview
- `docs/plans/2026-04-18-coherence-plus-us-market.md` island #12 — where this pattern is canonicalized
