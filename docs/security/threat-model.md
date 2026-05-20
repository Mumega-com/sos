# Threat Model

Status: S080 baseline  
Last updated: 2026-05-20

SOS is a coordination kernel for agents, humans, tasks, and optional memory.
The highest-risk public assets are bearer tokens, task/customer data, bus
messages, webhook ingress, and Redis-backed state.

## Assets

- Agent and system bearer tokens.
- Redis streams, pub/sub channels, idempotency keys, and task events.
- Squad tasks, contacts, partners, opportunities, referrals, and project state.
- Optional Mirror memory content.
- Webhook payloads from external systems.
- Audit and health telemetry.

## Trust Boundaries

- Browser or external agent to public edge.
- Public edge to SOS services.
- SOS services to Redis and SQLite/Postgres.
- SOS services to optional host overlays such as Mirror, Inkwell, OpenClaw,
  Hermes, and Mumega private add-ons.
- Third-party webhook provider to SOS webhook ingress.

## Health Policy

Unauthenticated health endpoints may report only:

- service name or generic status
- version when already public
- boolean dependency reachability
- coarse degraded/healthy state

Unauthenticated health endpoints must not report:

- bearer tokens or token hashes
- tenant/customer/project names
- queue contents or message samples
- filesystem paths that reveal private deployment layout
- raw exception traces or database URLs

Full health, flow health, debug health, metrics, service maps, queue depth,
and dependency detail are private/admin surfaces. They require bearer auth or
a private network boundary.

## Webhook Ingress Policy

- Webhook endpoints must fail closed when their shared secret or signing key is
  missing.
- Missing secret is a server configuration failure, not a permissive dev mode
  on public hosts.
- Invalid signature or shared secret returns `401` or `403`.
- Malformed payload returns `422`.
- Providers with replay identifiers must store idempotency keys before side
  effects.
- Provider secrets must not be accepted in query strings.
- Webhook logs must redact signatures, secrets, and raw payloads containing PII.

Current public SOS webhook baseline:

- `POST /webhooks/ghl/lead` in Squad requires `X-GHL-Secret` and returns
  `503 ghl_webhook_not_configured` when `GHL_WEBHOOK_SECRET` is unset.
- GitHub webhook replay/idempotency is a follow-up item tracked under #145.

## CORS Policy

Default production posture is an explicit allowlist. Wildcard CORS is allowed
only for local development or public static artifacts that do not use
credentials.

Recommended `SOS_CORS_ALLOW_ORIGINS` values:

- local dev: `http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000`
- hosted MCP browser clients: add `https://claude.ai`, `https://chatgpt.com`,
  or another exact origin only when that client is intentionally supported
- production: exact HTTPS origins only

Routes using bearer tokens should not combine `Access-Control-Allow-Origin: *`
with credentialed browser access.

## Redis Policy

Local development may use unauthenticated localhost Redis.

Production must use:

- non-localhost Redis unless Redis is private to the service host
- authentication through `REDIS_PASSWORD` or a password-bearing `REDIS_URL`
- TLS (`rediss://`) or a private network transport for remote Redis
- no public internet exposure for Redis ports

`sos doctor` now reports Redis safety findings for declared production
environments.

## Primary Threats

| Threat | Risk | Mitigation |
|---|---|---|
| Token leakage through logs or path tokens | account or project takeover | redact edge logs, prefer header bearer tokens, rotate exposed tokens |
| Cross-project inbox read | data exfiltration | token-scoped project checks on bus/MCP inbox |
| Public debug health | topology and secret disclosure | minimal public health, auth-only full health |
| Missing webhook secret | unauthenticated task/customer injection | fail closed when secret is absent |
| Wildcard credentialed CORS | browser-origin abuse | explicit origin allowlist |
| Public Redis | total bus/task compromise | bind private, require auth, use TLS/private network |
| Replay webhook | duplicate tasks or state transitions | provider event id idempotency |
| Prototype token minting exposed | unauthorized identity creation | keep mint/register routes private until reviewed |

## Residual Risks

- Several legacy services still configure wildcard CORS in code. S080 documents
  the target posture; a later hardening PR should convert services to a shared
  allowlist helper.
- Some service health routes still expose dependency details useful for
  operators. Public reverse proxies should expose only the minimal route until
  code-level split routes are complete.
- Public bus bridge exposure remains optional and should stay private unless an
  operator has rate limits, TLS, and token-scoped isolation in place.
