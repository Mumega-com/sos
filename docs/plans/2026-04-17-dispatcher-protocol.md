# Dispatcher Protocol — portable contract across deployment targets

**Date:** 2026-04-17
**Author:** sos-dev
**Status:** Canonical (tonight's work implements against this)
**Constraint:** SOS is a microkernel. Dispatcher is a service. Kernel must run on Raspberry Pi, Cloudflare Workers, Google Cloud Run, AWS Lambda — all on the same kernel code.

## The one rule

**SOS kernel has zero imports from any specific deployment target.** Dispatcher is an optional external service that speaks a well-defined HTTP protocol. Any implementation that satisfies this contract is valid. The kernel doesn't care whether requests arrived via CF Worker, nginx, Python FastAPI on an RPi, or a C program.

## What a dispatcher is

The dispatcher is the **public edge** of an SOS deployment. It sits between external MCP clients (Claude Code, claude.ai, Cursor, Codex, etc.) and the SOS MCP SSE gateway at `:6070`. Its job:

1. Authenticate the incoming token
2. Resolve identity (tenant, agent, scope, plan)
3. Enforce per-tenant rate limits
4. Add identity headers to the upstream request
5. Proxy to `:6070` and stream the SSE/HTTP response back
6. Log the request asynchronously
7. Return typed errors (`SOS-XXXX`) on rejection

Everything else — semantic search, squad routing, bus delivery — happens **inside** the kernel at `:6070`. The dispatcher is thin.

## The protocol (what any impl must do)

### Inbound endpoints

```
GET  /sse/<token>                           → SSE stream for MCP
POST /messages?session_id=<id>              → MCP messages transport
POST /mcp/<token>                           → streamable-HTTP transport (newer clients)
GET  /health                                → 200 JSON, no auth required
```

### Token validation

Input: `<token>` from URL path.

Output: `AuthContext { tenant_id, agent, scope, plan, role }` OR an error.

Implementation is free but must:
- Look up `sha256(<token>)` in a token store
- Return `AuthContext` only if the entry is `active=true`
- On miss: return 401 with body `{"code": "SOS-1001", "message": "invalid token"}`
- On revoked: return 401 with body `{"code": "SOS-1002", "message": "token revoked"}`

Token store backends:
- **Python dispatcher (RPi, VPS):** reads `sos/bus/tokens.json` directly from filesystem
- **CF Worker:** reads CF KV namespace `sos-tokens` (synced from `tokens.json` on VPS)
- **Cloud Run (GC):** reads Firestore collection `tokens` (synced)
- **Lambda (AWS):** reads DynamoDB table `sos-tokens` (synced)

All four share the SAME token hashing (sha256), so the hash is portable across backends.

### Rate limiting

Input: `tenant_id` + `plan` from AuthContext.

Output: `{ allowed: bool, remaining: int, retry_after_s: int }`.

Thresholds (per-minute, configurable):
- `starter`: 10 rpm
- `growth`: 100 rpm
- `scale`: 1000 rpm
- `enterprise`: unlimited

On rejection: return 429 with body `{"code": "SOS-9003", "message": "rate limit exceeded", "retry_after": N}` + `Retry-After` header.

Implementation backends:
- **Python dispatcher:** Redis `INCR` with TTL per tenant minute-window
- **CF Worker:** Durable Object per tenant, atomic counter
- **Cloud Run:** Memorystore (managed Redis)
- **Lambda:** DynamoDB atomic counter

### Identity headers

On successful auth + rate check, dispatcher adds to upstream request:

```
X-SOS-Identity:  agent:<name>
X-SOS-Tenant-Id: <slug>
X-SOS-Scope:     agent|customer|admin
X-SOS-Plan:      starter|growth|scale|enterprise|null
X-SOS-Role:      admin|operator|viewer
X-SOS-Source:    dispatcher-cf|dispatcher-py|dispatcher-gc|dispatcher-aws
```

The kernel at `:6070` trusts these headers **only** if they arrive from the configured dispatcher IP range. Bare VPS deploys can validate via `X-Forwarded-For` + allowlist. CF deploys restrict `:6070` to CF IP ranges.

### Request logging

Every request is logged async with shape:

```json
{
  "ts": "ISO8601",
  "tenant_id": "string",
  "agent": "string",
  "scope": "agent|customer|admin",
  "endpoint": "/sse|/messages|/mcp|/health",
  "method": "GET|POST",
  "status": 200-599,
  "latency_ms": integer,
  "bytes_out": integer,
  "error_code": "SOS-XXXX|null"
}
```

Storage backends:
- **Python dispatcher:** SQLite at `~/.sos/data/dispatcher.db`
- **CF Worker:** D1 database `sos-dispatcher-log`
- **Cloud Run:** BigQuery table `mumega.dispatcher.requests`
- **Lambda:** DynamoDB + optional S3 archive

Logs are **not** on the hot path — fire-and-forget. If logging is down, requests still succeed.

### Revocation

`DELETE /admin/tokens/<hash>` (admin auth required):
- Python: rewrite `tokens.json`, mark `active=false`
- CF: `wrangler kv key delete --namespace-id=<SOS_TOKENS> token:<hash>`
- Cloud Run: Firestore update
- Lambda: DynamoDB update

Revocation must take effect within **60 seconds** across all running dispatcher instances.

## Portability guarantees

### What's the same across all deployment targets

- Token format (`sk-<type>-<name>-<hex>`)
- Token hashing (sha256)
- Error codes (`SOS-XXXX`)
- HTTP endpoints (`/sse/<token>`, `/mcp/<token>`, `/messages`, `/health`)
- AuthContext fields (tenant_id, agent, scope, plan, role)
- Identity headers on upstream request
- Rate-limit thresholds per plan
- Request log shape

### What's deployment-specific

| Concern | Python (RPi/VPS) | CF Worker | GC Cloud Run | AWS Lambda |
|---|---|---|---|---|
| Token storage | `tokens.json` file | KV namespace | Firestore | DynamoDB |
| Rate-limit state | Redis | Durable Object | Memorystore | DynamoDB |
| Request log | SQLite | D1 | BigQuery | DynamoDB + S3 |
| TLS termination | nginx or Caddy in front | CF edge | CF or GC load balancer | API Gateway |
| Deployment | `systemctl` unit | `wrangler deploy` | `gcloud run deploy` | `sam deploy` |
| Scale | vertical on one box | global edge auto-scale | regional auto-scale | global auto-scale |
| Cost model | flat (VPS rental) | per-request | per-request | per-request + gateway fees |
| Cold start | none | ~1ms | ~500ms | ~100ms |

### The SOS kernel contract

The kernel at `:6070` (or wherever SOS MCP SSE gateway runs) accepts:
- Any request with valid `X-SOS-*` headers from an allowlisted IP range
- OR any request with a full raw token in the URL (self-validating, for bare deploys without dispatcher)

That second mode means **the kernel runs without a dispatcher**. RPi deployments can run SOS naked — no CF, no Cloud Run, no Python dispatcher. The dispatcher is optional. Missing it means:
- No edge token validation (every bad token reaches the kernel)
- No rate limiting
- No revocation without kernel restart
- No request analytics

For a single-user deploy on an RPi, that's fine. For production, a dispatcher of some flavor is required.

## Choosing a dispatcher for your deployment

### Decision tree

```
Are you running on a Raspberry Pi / home server / single VPS?
  → Use Python dispatcher (`sos/services/dispatcher/`). No cloud needed.

Do you want global edge distribution + DDoS absorption + managed scaling?
  → Use CF Worker dispatcher (`workers/sos-dispatcher/`). Cheapest at scale.

Do you want GC-native + IAM integration + BigQuery analytics?
  → Use Cloud Run dispatcher (future — not written yet, protocol covers it).

Do you want AWS-native + CloudWatch + IAM + API Gateway?
  → Use Lambda dispatcher (future — not written yet, protocol covers it).

Do you want none at all?
  → Run SOS kernel naked. OK for development, not for production.
```

### Hybrid / multi-region

A deployment can run multiple dispatcher impls in parallel:
- CF Worker at `mcp.mumega.com` for global users
- Python dispatcher on the RPi at home for local-network-only agents
- Same kernel accepts both as long as identity headers are signed by an allowlisted dispatcher

## Today's status (2026-04-17)

| Impl | Scaffolded | Deployable | Tested |
|---|---|---|---|
| Python (`sos/services/dispatcher/`) | tonight | tonight | next session |
| CF Worker (`workers/sos-dispatcher/`) | tonight | tonight (creds exist) | next session |
| GC Cloud Run | not yet | n/a | n/a |
| AWS Lambda | not yet | n/a | n/a |

## Ship criteria (per dispatcher impl)

1. Endpoints return correct HTTP codes for: valid token, invalid token, revoked token, rate-limit exceeded, upstream timeout
2. Identity headers correctly added to upstream
3. Revocation takes effect within 60s
4. Request log captures all 4 dimensions (tenant, agent, endpoint, outcome)
5. `/health` returns 200 with backend-specific status fields
6. Load test: sustains 100 rps baseline, 429s fire correctly at plan thresholds
7. Chaos test: upstream (`:6070`) down → dispatcher returns 503, does NOT cascade to other tenants

## Contract tests (the portability proof)

`tests/contracts/test_dispatcher_protocol.py` — runs against any URL. Given a dispatcher URL + a known-good token + a known-revoked token + a rate-limit-known tenant, asserts:

1. Valid token → 200
2. Invalid token → 401 + SOS-1001
3. Revoked token → 401 + SOS-1002
4. Rate-limit exceeded → 429 + SOS-9003 + `Retry-After` header
5. `/health` → 200 without auth

If a new dispatcher impl (GC Cloud Run, AWS Lambda) passes this test, it's compliant. That's how we enforce portability.

## What this means for SOS tonight

- **`sos/services/dispatcher/`** — Python impl, FastAPI, ~300 LOC. Runs on VPS. Will run on RPi without modification. Uses `tokens.json` directly. Uses Redis for rate-limit. Uses SQLite for logs.
- **`workers/sos-dispatcher/`** — CF Worker impl, Hono TypeScript, ~300 LOC. Uses KV for tokens (synced from `tokens.json`). Uses Durable Object for rate-limit. Uses D1 for logs.
- **Both deploy-ready by end of tonight.** Production cutover is a separate step.
- **SOS kernel stays untouched** — no new imports, no CF-specific types, no GC-specific types. Kernel is still just Python services on `:6070`.

## Related

- Dispatcher plan (CF-specific details): `docs/plans/2026-04-17-claude-dispatcher.md`
- Master roadmap: `docs/plans/2026-04-17-sos-roadmap-v0.4-to-v1.0.md`
- CF Agents Week context: `docs/plans/2026-04-17-cloudflare-agents-week-context.md`

## One-line summary

SOS is a microkernel. Dispatcher is a protocol. Four reference impls (Python, CF, GC, AWS) share the same HTTP contract and token hash. Deploy on anything from a Raspberry Pi to Cloudflare's global edge without changing a line of kernel code.
