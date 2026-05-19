# Mumega / Agent OS — Service Dependency Map

SVG source: [`/sos/services/dashboard/service_map.svg`](../../sos/services/dashboard/service_map.svg)
Rendered at: `https://app.mumega.com/sos`
Generated: 2026-04-17

---

## Nodes

| ID  | Name                     | Layer                    | License       | Notes                                       |
|-----|--------------------------|--------------------------|---------------|---------------------------------------------|
| n1  | Customer Edge            | Incoming / Clients       | External      | CF Pages + Workers; tenants trop, gaf, dnu  |
| n2  | MCP Clients              | Incoming / Clients       | External      | Claude Code, Cursor, Codex, Gemini CLI      |
| n3  | External Agents          | Incoming / Clients       | External      | remote.js SDK, third-party MCP              |
| n4  | Installer                | Incoming / Clients       | External      | curl installer → posts to /signup           |
| n5  | sos_mcp_sse              | MCP / HTTP Gateways      | Community     | MCP SSE :6070; v1 SendMessage enforcement   |
| n6  | sos_mcp (stdio)          | MCP / HTTP Gateways      | Community     | Deprecated stdio path; migration complete   |
| n7  | bus-bridge               | MCP / HTTP Gateways      | Community     | HTTP bridge :6380; bearer token auth        |
| n8  | SaaS / Signup            | MCP / HTTP Gateways      | Proprietary   | :8075; tenant lifecycle + token issuance    |
| n9  | Auth                     | Kernel Trio              | Community     | tokens.json (SHA-256 hashed); SEC-001       |
| n10 | Redis Bus                | Kernel Trio              | Community     | Streams + pubsub :6379; global stream key   |
| n11 | Registry                 | Kernel Trio              | Community     | Agent Card v1; Redis hashes with TTL        |
| n12 | Contracts v1             | Kernel Trio              | Community     | 8 message types + SkillCard v1 JSON Schema  |
| n13 | Wake Daemon              | Kernel Trio              | Community     | pubsub subscriber; tmux send-keys delivery  |
| n14 | Adapters                 | Adapters                 | Community     | Claude, Gemini, OpenAI; PricingEntry 2026-04|
| n15 | Provider Matrix          | Adapters                 | Proprietary   | Config + circuit breakers; providers.yaml TBD|
| n16 | Agents (tmux)            | Adapters                 | External      | hadi, kasra, codex, sos-dev; Claude Code    |
| n17 | Calcifer                 | Adapters                 | Proprietary   | Autonomous heartbeat; alerts → Discord      |
| n18 | Squad Service            | Product Layer            | Proprietary   | :8060; bounties, claims, settlement         |
| n19 | Mirror                   | Product Layer            | Proprietary   | pgvector engrams :8844; 20k+ memories       |
| n20 | Economy                  | Product Layer            | Proprietary   | wallet + UsageLog + $MIND; /usage POST      |
| n21 | SkillCard Registry       | Product Layer            | Proprietary   | Filesystem JSON + Pydantic; provenance+earn |
| n22 | Operator Dashboard       | Product Layer            | Proprietary   | :8090; /sos/overview · /money · /skills     |
| n23 | Stripe                   | External Egress          | External      | USD → $MIND webhook via saas                |
| n24 | Anthropic/Google/OpenAI  | External Egress          | External      | Model API providers; PricingEntry per model |
| n25 | OpenClaw Gateway         | External Egress          | External      | :18789; 6 agents; DEGRADED — expired OAuth  |
| n26 | Solana devnet            | External Egress          | External      | $MIND on-chain settlement; wallet.py        |
| n27 | Mumega Edge              | External Egress          | External      | CF Worker; billing/signup proxy             |
| n28 | Inkwell                  | Sibling Products         | Community     | Astro framework; powers mumega-site + tenants|
| n29 | Customer Dashboard       | Sibling Products         | Proprietary   | /dashboard, /marketplace; tenant-scoped     |
| n30 | Mumega Site              | Sibling Products         | External      | mumega.com CF Pages; /products/agent-os     |

---

## Edges

| Source | Target | Transport          | Color         | Notes                                            |
|--------|--------|--------------------|---------------|--------------------------------------------------|
| n1     | n8     | HTTP               | Slate         | Tenant signup flow                               |
| n2     | n5     | HTTP/MCP           | Slate         | MCP SSE connect                                  |
| n3     | n5     | HTTP/MCP           | Slate         | External agent MCP connection                    |
| n4     | n8     | HTTP               | Slate         | Installer POST to /signup                        |
| n27    | n8     | HTTP               | Slate         | Edge worker sync to SaaS (dashed — async)        |
| n8     | n9     | HTTP (VPS local)   | Slate         | SaaS writes tokens.json via internal call        |
| n16    | n5     | HTTP               | Slate         | Agents connect via MCP SSE                       |
| n16    | n6     | stdio              | Slate         | Legacy agents via deprecated stdio               |
| n15    | n14    | In-process         | Slate         | Provider Matrix selects Adapter                  |
| n14    | n24    | HTTPS              | Slate         | Adapters call model APIs                         |
| n16    | n14    | In-process         | Slate         | Agents invoke model via Adapters (optional path) |
| n29    | n8     | HTTP               | Slate         | Customer Dashboard reads tenant state            |
| n28    | n30    | Build pipeline     | Slate         | Inkwell generates Mumega Site                    |
| n22    | n8+    | HTTP               | Slate         | Operator Dashboard reads all services (+many)    |
| n5     | n10    | Redis XADD         | Indigo (bus)  | MCP SSE publishes inbound messages               |
| n10    | n5     | Redis XREVRANGE    | Indigo (bus)  | Bus delivers to MCP SSE inbox                   |
| n7     | n10    | Redis XADD         | Indigo (bus)  | Bridge publishes to bus                          |
| n10    | n7     | Redis XREVRANGE    | Indigo (bus)  | Bus delivers to bridge inbox                     |
| n6     | n10    | Redis XADD         | Indigo (bus)  | Legacy stdio to bus (dashed — deprecated)        |
| n10    | n11    | Redis read         | Indigo (bus)  | Registry reads agent presence from bus           |
| n10    | n13    | Redis pubsub       | Indigo (bus)  | Wake Daemon subscribes to agent events           |
| n13    | n16    | tmux send-keys     | Indigo (bus)  | Wake Daemon delivers wake to idle agents         |
| n10    | n17    | Redis pubsub       | Indigo (bus)  | Calcifer subscribes for heartbeat monitoring     |
| n17    | n10    | Redis publish      | Indigo (bus)  | Calcifer publishes alerts back to bus            |
| n10    | n18    | Redis stream       | Amber (squad) | Bus delivers task events to Squad                |
| n18    | n10    | Redis XADD         | Amber (squad) | Squad publishes task_created/claimed/completed   |
| n18    | n21    | HTTP/in-process    | Amber (squad) | Squad reads SkillCard for invocation             |
| n21    | n20    | HTTP/in-process    | Amber (squad) | Skill invocation triggers UsageLog + earnings    |
| n5     | n19    | HTTP POST          | Purple (mem)  | MCP SSE mirror_post /store on send + remember    |
| n19    | n5     | HTTP GET           | Purple (mem)  | Mirror /recent/{agent} recall (dashed)           |
| n5     | n20    | HTTP POST          | Purple (mem)  | MCP SSE POST /usage to Economy on each call      |
| n18    | n20    | HTTP POST          | Purple (mem)  | Squad posts usage events                         |
| n29    | n20    | HTTP GET           | Purple (mem)  | Customer Dashboard reads UsageLog                |
| n22    | n20    | HTTP GET           | Purple (mem)  | Operator Dashboard reads UsageLog                |
| n22    | n19    | HTTP GET           | Purple (mem)  | Operator Dashboard reads Mirror memories         |
| n23    | n20    | HTTP webhook       | Green (money) | Stripe webhook credits wallet                    |
| n20    | n26    | RPC (wallet.py)    | Green (money) | Economy transmutes $MIND to Solana               |
| n20    | n18    | HTTP/callback      | Green (money) | Economy settles bounty credits to Squad          |
| n18    | n19    | HTTP               | Red (debt)    | DEBT: double task system (see below)             |
| n19    | n10    | —                  | Red (debt)    | MISSING: Mirror has no XREVRANGE consumer        |
| n20    | n19    | —                  | Red (debt)    | MISSING: UsageLog not replicated to Mirror       |

---

## Architectural Debt

1. **Double task system** — Squad Service (`/tasks`) and Mirror (`/tasks`) maintain independent task stores with no reconciliation. Tasks can diverge; no source of truth defined.

2. **OpenClaw Gateway degraded** — `:18789` OAuth token expired. All 6 registered agents behind that gateway are in degraded state. Manual re-auth required; no auto-refresh flow exists.

3. **Mirror does not consume Redis bus** — The Mirror memory store has no `XREVRANGE` consumer. It receives engrams only via direct HTTP `POST /store` from the MCP SSE gateway. Bus events (task completions, squad actions) are invisible to Mirror.

4. **UsageLog not replicated to Mirror** — Economy events posted to `/usage` never propagate to the semantic memory layer. Operators cannot query "what did this agent cost?" via Mirror's semantic search.

5. **Provider Matrix `providers.yaml` path TBD** — Circuit breaker configuration is designed but not wired to the Adapters layer. Failover between model providers is not operational.

6. **stdio MCP deprecated but not removed** — `sos_mcp` stdio path is still referenced in agent configs and the graph. Cleanup is pending; the legacy path adds surface area for bugs.

---

## License Split Rationale

### Community (Apache 2.0) — `sos-community`

Components that form the protocol substrate: the bus, registry, auth schema, contracts, wake daemon, and all adapters. These are open because:
- Third-party integrations (CrewAI, LangGraph, Discord bots) need to implement the same wire format.
- The MCP gateway and bridge are reference implementations that partners fork.
- Openness grows the addressable agent ecosystem, which drives SaaS revenue indirectly.
- Inkwell is publicly forkable as a publishing framework — openness is its value proposition.

### Proprietary Core (Mumega commercial) — `sos`

Components that encode Mumega's competitive moat:
- **SaaS / Signup** — tenant lifecycle, billing hooks, token issuance.
- **Provider Matrix** — cost optimisation, circuit breaker config, routing logic.
- **Calcifer** — autonomous heartbeat and alerting operational layer.
- **Squad Service** — bounty economy, skill invocation, settlement.
- **Mirror** — the 20k+ engram store is a proprietary data asset.
- **Economy** — $MIND wallet, UsageLog, Stripe integration, Solana transmute.
- **SkillCard Registry** — provenance, author earnings, commerce layer.
- **Dashboards** — operator and customer UX are product differentiators.

### External / Third-Party

All model providers, Stripe, Solana, CF infrastructure, and user-operated agent processes. These are outside the license boundary by definition.
