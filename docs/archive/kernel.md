# Kernel

The kernel is the smallest possible core: bus + auth + registry. Everything else is a service.

## Service Registry

Services self-register on startup. The registry is backed by Redis with TTL-based liveness.

### How it works

1. Service starts and calls `registry.register()` with its name, tools, and health endpoint
2. Redis stores the registration with a TTL (default 60 seconds)
3. Service heartbeats every 30 seconds to refresh the TTL
4. If the service dies, the TTL expires and the registration disappears
5. Kernel discovers available tools by scanning the registry

### API

```python
from sos.kernel.registry import ServiceRegistry

registry = ServiceRegistry()

# Register
await registry.register(
    name="mirror",
    tools=[
        {"name": "remember", "description": "Store an engram in memory"},
        {"name": "recall", "description": "Search memory by query"},
        {"name": "memories", "description": "List recent memories"},
    ],
    health_endpoint="http://localhost:8844/health",
    tenant_scope="global",  # or a specific tenant ID
    ttl=60,
)

# Heartbeat (call every 30s)
await registry.heartbeat("mirror", ttl=60)

# List all registered services
services = await registry.list_services()
# [{"name": "mirror", "tools": [...], "health_endpoint": "...", ...}]

# Deregister manually
await registry.deregister("mirror")
```

### Redis keys

Registrations live at `sos:kernel:services:{name}` with a TTL. No cleanup needed.

## Event Bus

The event bus is Redis pub/sub with persistence to a stream for audit replay.

### Predefined event types

**Tenant lifecycle:** `tenant.created`, `tenant.deleted`, `payment.received`, `payment.failed`

**Agent lifecycle:** `agent.joined`, `agent.left`, `agent.challenged`

**Task lifecycle:** `task.created`, `task.claimed`, `task.completed`, `task.failed`

**Content:** `content.published`, `content.updated`

**Analytics:** `analytics.ingested`, `decision.made`, `action.executed`, `feedback.scored`

**Health:** `health.degraded`, `health.recovered`, `service.registered`, `service.down`

### Emitting events

```python
from sos.kernel.events import EventBus, TASK_COMPLETED

bus = EventBus()

event_id = await bus.emit(
    event_type=TASK_COMPLETED,
    data={"task_id": "t-123", "result": "deployed successfully"},
    source="squad",
)
```

### Subscribing to events

```python
async def on_task_completed(event: dict) -> None:
    task_id = event["data"]["task_id"]
    # Score the result, update analytics, etc.

bus = EventBus()
await bus.subscribe("task.completed", on_task_completed)
await bus.listen()  # Blocks, processing events as they arrive
```

### Replay

Events persist to a Redis stream at `sos:events:log`. Replay events for debugging or catch-up:

```python
events = await bus.replay(
    event_type="task.completed",
    since="2026-01-01T00:00:00Z",
    limit=100,
)
```

## Adding a new service to the kernel

1. Create your service in `sos/services/yourservice/`
2. On startup: register tools with `ServiceRegistry`
3. Subscribe to events you care about via `EventBus`
4. Heartbeat every 30 seconds
5. Emit events when meaningful things happen in your service

The kernel does not need to know about your service in advance. Registration is dynamic.
