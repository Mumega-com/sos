# Public Edge Map

Status: S080 baseline  
Last updated: 2026-05-20

This map describes the intended public edge for a self-hosted SOS deployment.
The public OSS repo ships services; operators choose hostnames and reverse
proxies. Treat every route not listed as private until explicitly reviewed.

| Hostname pattern | Path | Upstream | Auth | Exposure | Notes |
|---|---|---|---|---|---|
| `mcp.<host>` | `GET /health` | `sos.mcp.sos_mcp_sse` | none | public minimal | Liveness only. Do not include token, tenant, queue, or secret details. |
| `mcp.<host>` | `POST /mcp` | `sos.mcp.sos_mcp_sse` | bearer token | public authenticated | JSON-RPC MCP tool calls. Rate limit and audit at the edge. |
| `mcp.<host>` | `GET /sse/{token}` | `sos.mcp.sos_mcp_sse` | bearer-equivalent path token | public authenticated | Prefer header bearer tokens where client support allows it. Path tokens must not be logged. |
| `mcp.<host>` | `GET /bridge/inbox` | `sos.mcp.sos_mcp_sse` | bearer token | public authenticated | Remote SDK inbox compatibility. Scope must come from token, not query params alone. |
| `bus.<host>` | `GET /health` | `sos.bus.bridge` | none | optional public minimal | Useful for local/dev. Hosted deployments may keep bus bridge private behind MCP. |
| `bus.<host>` | `/send`, `/inbox`, `/peers`, `/broadcast`, `/watch`, `/announce`, `/heartbeat` | `sos.bus.bridge` | bearer token | private or authenticated public | Public exposure requires TLS, rate limits, and token-scoped project isolation. |
| `squad.<host>` | `GET /health` | `sos.services.squad.app` | none | optional public minimal | Liveness and DB reachability only. |
| `squad.<host>` | `/tasks`, `/squads`, `/skills`, `/projects`, `/contacts`, `/partners`, `/opportunities`, `/referrals` | `sos.services.squad.app` | bearer token | private or authenticated public | Contains work, identity, and relationship data. Default posture is private service network. |
| `squad.<host>` | `POST /webhooks/ghl/lead` | `sos.services.squad.app` | shared webhook secret | optional public webhook | Must fail closed when `GHL_WEBHOOK_SECRET` is unset. |
| `registry.<host>` | `GET /health` | `sos.services.registry.app` | none | private or minimal public | Registry health only. |
| `registry.<host>` | `/agents`, `/agents/cards`, `/mesh/*` | `sos.services.registry.app` | bearer token or signed mesh challenge | private or authenticated public | Agent inventory and enrollment are sensitive. |
| `engine.<host>` | `GET /health` | `sos.services.engine.app` | none | private or minimal public | Engine health only. |
| `engine.<host>` | `/chat`, `/identity/mint`, `/delegate`, `/governance/*`, `/policy/*` | `sos.services.engine.app` | bearer token/capability gate | private | Do not expose prototype token minting or governance mutation routes without an edge review. |
| `memory.<host>` / Mirror | `/health` | Mirror repo | none | optional public minimal | Mirror is optional and separate from public SOS. |
| `memory.<host>` / Mirror | memory read/write routes | Mirror repo | bearer token | authenticated public or private | Memory content is sensitive by default. |
| `inkwell.<host>` | site routes | Inkwell repo | app-specific | public | Inkwell is a separate publishing surface. |

## Required Edge Controls

- TLS terminates before any bearer token reaches the service.
- Reverse proxy logs must redact `Authorization`, `token`, path-token segments,
  and webhook secret headers.
- Public unauthenticated routes are limited to minimal health and static docs.
- Authenticated public routes require rate limits and request size limits.
- Webhook routes require per-provider secrets and replay/idempotency handling.
- Services that are not intentionally public bind to loopback or a private
  network, not `0.0.0.0` on an internet-reachable host.

## Unknowns To Resolve Before Broad Exposure

- Which deployment owns public MCP edge rate limiting: Cloudflare, nginx, or a
  dispatcher service.
- Whether bus bridge should ever be internet-facing, or always private behind
  MCP.
- Which host overlays expose additional routes outside the public SOS repo.
