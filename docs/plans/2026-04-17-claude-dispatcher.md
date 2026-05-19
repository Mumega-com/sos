# claude-dispatcher — SOS front door on Cloudflare Worker (v0.4.3)

**Date:** 2026-04-17
**Author:** sos-dev
**Status:** Proposed, Hadi approved Cloudflare Worker use for SOS
**Depends on:** v0.4.0 "Contracts" (Agent Card + message schema + OpenAPI for bus gateway endpoint shape)
**Follows:** v0.4.2 "Observability Plane" (shares CF Workers infrastructure)

## Why this exists

Today `mcp.mumega.com` is an **nginx dumb proxy** that forwards `/sse/<token>` directly to `localhost:6070` on the VPS. Three structural problems:

1. **No edge validation.** Every bad token reaches the VPS before being rejected. DDoS risk, burn cycles, leak information about valid token formats.
2. **Flat identity via shared token.** The `sk-claudeai-*` family of tokens — used by claude.ai remote connectors that every internal agent inherits from `~/.claude.json` — collapses to `agent:kasra`. Whole bug class (#20, #21, #22) lives here.
3. **Customers can't connect to `localhost:6070` directly.** Their Claude Code runs on their laptop. They need a public URL. We give them `mcp.mumega.com/sse/<their-token>` but there's nothing between that URL and the VPS's MCP gateway. No rate limit, no revocation check, no per-tenant quota.

The dispatcher is a **Cloudflare Worker that owns `mcp.mumega.com`** and does at the edge what the VPS is forced to do today.

## Rename while we're at it

| Current (misleading) | Proposed |
|---|---|
| `claude.ai sos-claude` connector | `sos` (plain) or `sos-dispatcher` |
| Label `Hadi — Claude.ai MCP` | `Hadi — SOS` |
| Label `Kasra — Claude.ai MCP` | `Kasra — SOS` |
| Token `sk-claudeai-*` | Retire entirely. Per-user `sk-<agent>-<hex>`. |

Reason: the dispatcher isn't "for claude.ai." It serves any MCP-capable client — Claude Code local, claude.ai web, Cursor, Codex, ChatGPT custom GPTs. The "claude.ai" label is a product-coupled accident from the first setup. Drop it.

## Architecture

### Today (dumb proxy)

```
Customer Claude ──HTTPS──> mcp.mumega.com (nginx)
                                │
                                ▼
                           localhost:6070 (SOS MCP SSE gateway)
                                │
                                ▼
                           tokens.json + Redis streams
```

### With dispatcher

```
Any MCP client ──HTTPS──> mcp.mumega.com (CF Worker = dispatcher)
                                │
                                │    ┌─ CF D1: tenant/agent registry (mirror of tokens.json)
                                │    ├─ CF KV: token hashes + status (revocation, plan tier)
                                │    ├─ CF Durable Objects: per-tenant rate-limit state
                                │    └─ CF R2: (optional) request log archive
                                │
                                │ [1. validate token hash against KV]
                                │ [2. resolve identity → tenant/agent/scope]
                                │ [3. check plan-tier rate limit via DO]
                                │ [4. add X-SOS-Identity header to upstream req]
                                │ [5. stream SSE response back to client]
                                │
                                ▼
                           localhost:6070 (VPS, firewall: CF IPs only)
                                │
                                ▼
                           Redis streams
```

### What dispatcher does per request

1. **Extract token** from URL path (`/sse/<token>` or `/mcp/<token>`) or Bearer header
2. **Hash + KV lookup** — `sha256(token)` → KV `token:<hash>` returns `{tenant_id, agent, scope, plan, active}` or nothing
3. **Revocation check** — if KV returns inactive, 401 with `SOS-1001` error code
4. **Rate limit check** — Durable Object `rateLimit:<tenant_id>` atomically bumps a window counter against `plan.limit_per_minute`. Exceeded → 429 with `SOS-9003`.
5. **Route** — add headers:
   - `X-SOS-Identity: agent:<name>`
   - `X-SOS-Tenant-Id: <slug>`
   - `X-SOS-Scope: <scope>`
   - `X-SOS-Plan: <plan>`
6. **Proxy** to `http://<VPS-internal-IP>:6070/sse/<original-token>` (via CF Tunnel or direct HTTPS). VPS's SSE gateway reads the identity headers and trusts them (because nothing else can reach :6070).
7. **Stream** SSE back to client. Log request metadata to D1 async (don't block response).

## What doesn't change

- **MCP gateway code** on VPS stays as-is for the SSE part. It starts trusting `X-SOS-Identity` headers when they're present (set by dispatcher only; header is a reserved name).
- **Bus, squad, mirror, dashboard, saas** — no code changes. All identity resolution happens at the edge.
- **tokens.json** stays as the canonical on-VPS token registry; CF KV is a cached read-only mirror of it. Source-of-truth is still the VPS; a sync job pushes changes to KV.

## Phasing — 4 shippable increments

### v0.4.3-alpha (foundation)

Ship a **transparent CF Worker proxy** that validates tokens against KV and forwards to VPS. No rate limiting, no logging. Proves the shape works without changing behavior.

- Worker scaffold: `workers/sos-dispatcher/` in this repo
- `wrangler.toml` with `mcp.mumega.com` route, KV binding, D1 binding
- Script that exports `tokens.json` → KV entries (idempotent sync job)
- Rollout strategy: CF Worker runs in parallel with nginx for one week, traffic split 10%→50%→100%

### v0.4.3-beta (rate limiting)

Durable Object per tenant. Plan-tier config in KV. Customer-visible 429 with `SOS-9003` error code.

- `RateLimitDO` class — one instance per `tenant_id`
- Plan tiers: starter=10 rpm, growth=100 rpm, scale=1000 rpm, enterprise=unlimited
- 429 response includes `Retry-After` header

### v0.4.3 (analytics + revocation)

- D1 request log: `ts, tenant_id, agent, tool, outcome, latency_ms`
- Revocation endpoint: `DELETE /admin/tokens/<hash>` on Worker → KV delete → effective immediately
- Status page data feed: per-tenant request rates, error rates, P99 latency

### v0.4.3-rc (VPS firewall lockdown)

- `iptables` rule on VPS: inbound `:6070` restricted to Cloudflare IP ranges only
- Remove nginx `mcp.mumega.com` block (dispatcher owns TLS + routing now)
- Canary: sos-dev traffic via dispatcher for 72h before full cutover

## Migration from tokens.json to CF KV

Source of truth stays on VPS in `tokens.json`. KV is a read-cache.

Sync job:

```python
# scripts/sync-tokens-to-kv.py
# 1. Read tokens.json
# 2. For each active entry, compute token_hash (sha256 of raw, or use stored hash)
# 3. Upload to KV as token:<hash> → {tenant_id, agent, scope, plan, active, role}
# 4. Mark inactive entries as deleted in KV
# Runs on every tokens.json change + periodic drift check (5 min)
```

Triggered by:
- Cron (every 5 minutes — catches any out-of-band edits)
- File watcher (instantaneous for `sos.cli.onboard` and provisioner runs)

## Breakables integration

`mumega-watch` (v0.4.2) probes the dispatcher:

```yaml
# breakables.yaml
- id: claude-dispatcher
  type: cf-worker
  endpoint: https://mcp.mumega.com/health
  probe: { interval_s: 60, timeout_s: 3, expect_http: 200 }
  severity: critical
  affects: [every agent with MCP access, every tenant, every squad]
  dependencies: [vps-port-6070, cf-kv-tokens, cf-d1-log, cf-tunnel]

- id: cf-kv-tokens
  type: cf-kv
  probe: { check: kv_get, key: "token:<known-hash>", expect_nonempty: true }
  severity: critical

- id: tokens-sync-lag
  type: cron-last-run
  probe: { check: sos:state:last_token_sync_at, max_age_s: 600 }
  severity: high
```

Status page surfaces dispatcher health to customers.

## What the dispatcher does NOT do

- **Not a model-provider router.** That's OpenClaw's layer (third-party, see boundary doc). Dispatcher routes BUS/TOOL calls, not LLM prompts.
- **Not a semantic memory proxy.** Mirror has its own auth; dispatcher doesn't understand semantic search, just token gating + routing.
- **Not a content/HTML server.** `app.mumega.com` (customer portal) is a sibling Worker, not part of dispatcher.
- **Not an MCP server implementation.** Dispatcher is a gatekeeper and proxy. The actual MCP tool bodies stay at `:6070`. This matters: as new MCP tools ship, dispatcher automatically covers them because it's a pass-through with auth.

## What the dispatcher enables downstream

- **Per-tenant SLA.** Plan-tier rate limits become a real product feature instead of a marketing claim.
- **Token revocation in production.** If a customer's key leaks, `wrangler kv key delete token:<hash>` and it's dead within 60s.
- **Analytics for billing.** D1 request log is the metering source. Plans based on usage become possible.
- **Multi-region expansion.** Worker runs at CF edges globally. VPS is the origin but customers get local latency.
- **Auth upgrade path.** Dispatcher can add OAuth, JWT, webauthn later without touching VPS.

## Security shape

| Attack vector | Before | After |
|---|---|---|
| Token brute force on public URL | VPS burns cycles per attempt | CF rejects at edge, free |
| DDoS on `mcp.mumega.com` | VPS saturates, every agent dies | CF absorbs, VPS unaffected |
| Leaked token in logs | Valid until someone notices + edits `tokens.json` + restarts MCP | `wrangler kv key delete`, < 60s to neutralize |
| Insider access to VPS (compromises tokens.json raw) | Raw tokens are already hash-only (SEC-001) | Same — no regression |
| Bypass attempt (skip dispatcher, hit VPS direct) | Possible if attacker knows IP | Firewall blocks all non-CF IPs |

## Decisions needed from Hadi

| # | Question |
|---|---|
| D1 | Cloudflare account — same account as `mumega-edge` + `mumega-site`? (assume yes unless told otherwise) |
| D2 | Domain — keep `mcp.mumega.com` as the dispatcher's hostname, or give it a distinct one? |
| D3 | Rollout risk tolerance — can we run dispatcher in parallel with nginx for a week, or cut over directly? |
| D4 | Plan tiers — confirm rate limits (starter=10 rpm, growth=100 rpm, scale=1000 rpm, enterprise=unlimited)? |
| D5 | Revocation workflow — who can revoke tokens? Just ops (Hadi, sos-dev) via CLI, or do customers self-revoke from `/dashboard/settings`? |

## Cost estimate (CF-side)

CF Workers free tier: 100,000 req/day. Paid tier: $5/month baseline + $0.50 per million req.

Expected traffic early: low thousands of MCP requests/day. **$0 for first N months.**

D1: free tier 5 GB + 25M rows read/day. Logs are small (< 1KB per request). No cost.

KV: free tier 100K reads/day + 1K writes/day. Reads are per MCP call; writes are per-token-change. Well inside free tier.

DO: billed per duration × requests. For rate-limiter use, micro-durations. Negligible.

**Expected first-year dispatcher cost: $0–$20/month total.** Pays for itself in the first DDoS-defense event.

## Ship criteria for v0.4.3

1. Every production agent connects through `mcp.mumega.com` served by CF Worker, not nginx
2. Token revocation works end-to-end in < 60s
3. Rate limits fire correctly for a synthetic test tenant (429 with `SOS-9003`)
4. D1 request log captures every request with tenant attribution
5. VPS `:6070` blocked from non-CF IPs (nmap from an unrelated host shows port closed)
6. `mumega-watch` reports dispatcher as a live breakable with green status
7. Zero customer-visible breakage during cutover

## One-line summary

v0.4.3 replaces `mcp.mumega.com`'s nginx dumb proxy with a Cloudflare Worker dispatcher that validates tokens at the edge, enforces per-tenant rate limits, logs every request, and makes token revocation a 60-second operation instead of a VPS rollout. Retires the "claude.ai" naming that was always a misnomer. Establishes the pattern every future SOS public-edge surface follows.
