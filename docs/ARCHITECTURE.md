# Architecture

SOS is a microkernel for AI agent teams. The kernel is small. Everything else is a service.

## The organism

```
                         ┌─────────────────────────────┐
                         │        MCP SSE :6070         │
                         │   (external agent gateway)   │
                         └──────────┬──────────────────┘
                                    │
┌───────────┐    ┌──────────────────▼──────────────────┐    ┌───────────┐
│  Agent A   │◄──►│             KERNEL                  │◄──►│  Agent B   │
│ (Claude)   │    │                                     │    │ (LangGraph)│
└───────────┘    │  ┌─────────┐ ┌──────┐ ┌──────────┐ │    └───────────┘
                  │  │Registry │ │ Bus  │ │  Auth    │ │
                  │  └─────────┘ └──────┘ └──────────┘ │
                  └──────────────────┬──────────────────┘
                                     │
              ┌──────────┬───────────┼───────────┬──────────┐
              ▼          ▼           ▼           ▼          ▼
         ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
         │ Squad  │ │ Mirror │ │Feedback│ │Calcifer│ │Economy │
         │ :8060  │ │ :8844  │ │        │ │(health)│ │        │
         └────────┘ └────────┘ └────────┘ └────────┘ └────────┘
```

## Microkernel design

The kernel has exactly three responsibilities:

1. **Bus** -- message passing between agents and services (Redis pub/sub)
2. **Auth** -- token validation, tenant scoping, capability checks
3. **Registry** -- services self-register their tools, heartbeat to stay alive, auto-deregister on death (TTL expiry)

Everything else is a service that registers with the kernel.

## Data flow

```
Agent sends message
  → MCP SSE server receives it
    → Kernel validates token + tenant scope
      → Bus routes to target agent or service
        → Service processes and responds
          → Response flows back through bus
```

For tasks:

```
Agent creates task (Squad Service)
  → Task stored with priority + labels
    → Another agent claims task (atomic, no double-dispatch)
      → Agent executes work
        → Agent completes task with result
          → Feedback loop scores the result
            → Score feeds into adaptation rules
```

## Tenant isolation

Each tenant gets:

- Dedicated Redis DB (DB 0 = system, DB 1+ = tenants)
- Scoped tokens that limit which tools and data an agent can access
- Isolated task queues, memory namespaces, and analytics
- Separate Cloudflare DNS and worker bindings

Agents from tenant A cannot see tenant B's messages, tasks, or memory.

## Event system

Services communicate through Redis pub/sub events:

| Event | Trigger | Subscribers |
|-------|---------|-------------|
| `task.completed` | Agent finishes work | Feedback loop |
| `tenant.created` | Stripe payment | Provisioning |
| `agent.joined` | Agent announces | Sentinel |
| `health.degraded` | Service unresponsive | Calcifer |
| `analytics.ingested` | New data arrives | Decision agent |
| `content.published` | Blog/page goes live | Analytics |
| `payment.received` | Stripe webhook | Economy |
| `feedback.scored` | Result evaluated | Adaptation |

Events are fire-and-forget via pub/sub, but also persisted to a Redis stream for audit replay.

## Key principles

1. **Local-first** -- works offline, no cloud dependency
2. **Multi-model** -- failover across providers (free Gemma -> Haiku -> Opus)
3. **Event-driven** -- services react to events, not polling
4. **Agents don't negotiate** -- they coordinate through shared signals (tasks, events, bus)
5. **Feedback compounds** -- every result gets scored, scores shape future behavior
