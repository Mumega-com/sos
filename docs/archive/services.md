# Services

Every service registers its tools with the kernel on startup. If a service dies, its tools are auto-removed (TTL expiry). No manual cleanup.

## Service table

| Service | Port | Purpose | Status |
|---------|------|---------|--------|
| **Bus Bridge** | 6380 | HTTP API for agent bus (send, inbox, peers, broadcast) | Production |
| **MCP SSE** | 6070 | MCP-standard SSE endpoint for external agents | Production |
| **Squad** | 8060 | Task queue, teams, skills, pipelines | Production |
| **Mirror** | 8844 | Memory API (engrams, vector search via pgvector) | Production |
| **Dashboard** | 8090 | Web UI for monitoring agents, tasks, services | Production |
| **Calcifer** | -- | Health monitoring, service liveness, escalation | Production |
| **Sentinel** | -- | Security: token validation, capability enforcement | Production |
| **Analytics** | -- | Three-phase: ingest data, decide what to do, act on it | Production |
| **Feedback** | -- | Score results, generate adaptation rules | Production |
| **Billing** | -- | Stripe webhook handler, auto-provisioning | Production |
| **Outreach** | -- | Email pipeline (lead scan, nurture, CRM sync) | Production |
| **Economy** | -- | Work matching, wallets, metabolism (cost tracking) | Production |
| **Identity** | -- | Agent identity, avatars, OAuth, Cloudflare tokens | Active |
| **Auth Gateway** | -- | OAuth flows, vault, database-backed auth | Active |
| **Content** | -- | Blog generation, social posts | Active |
| **Voice** | -- | Voice interface adapter | Active |

## How services run

Each service can run as:

- **systemd unit** -- `systemctl --user start sos-squad`
- **Direct Python** -- `python -m sos.services.squad.app`
- **Docker** -- `docker-compose up squad`

## Adding a new service

1. Create `sos/services/myservice/` with `__init__.py` and `app.py`
2. Register tools with the kernel on startup:

```python
from sos.kernel.registry import ServiceRegistry

registry = ServiceRegistry()
await registry.register(
    name="myservice",
    tools=[
        {"name": "myservice_do_thing", "description": "Does the thing"},
    ],
    health_endpoint="http://localhost:9000/health",
    ttl=60,
)
```

3. Heartbeat every 30 seconds to stay registered:

```python
import asyncio

async def heartbeat_loop():
    while True:
        await registry.heartbeat("myservice", ttl=60)
        await asyncio.sleep(30)
```

4. Subscribe to events your service cares about:

```python
from sos.kernel.events import EventBus

bus = EventBus()
await bus.subscribe("task.completed", handle_task_completed)
await bus.listen()
```

5. Add a systemd unit file in `SOS/systemd/` if it should run permanently.
