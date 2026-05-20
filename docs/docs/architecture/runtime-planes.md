---
title: Runtime Planes
---

# Runtime Planes

SOS has six runtime planes:

| Plane | Public status | Notes |
|---|---|---|
| MCP tool plane | Core | Agent-facing tools over MCP. |
| Bus postal plane | Core | Redis streams for send, inbox, broadcast, and wake-compatible delivery. |
| Squad labor plane | Core | Task primitives and agent coordination. |
| Gateway/ingress plane | Public but hardening | S080 owns public-edge review before broad exposure. |
| Mirror memory plane | Optional | Mirror is a separate repo. SOS should degrade cleanly without it. |
| Engine inference plane | Optional | Multi-model routing is available but not required for bus/task use. |

Host overlays can add product-specific services without changing public SOS.

