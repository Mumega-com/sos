# sos-dispatcher — Cloudflare Worker

SOS Dispatcher, Cloudflare Worker implementation.

Same contract as `sos/services/dispatcher/` (Python impl). Protocol spec:
`docs/plans/2026-04-17-dispatcher-protocol.md`.

## Pre-deploy setup (one-time)

Needs CF_API_TOKEN + CF_ACCOUNT_ID in env. Already available in `~/.env.secrets`.

```bash
source ~/.env.secrets
cd workers/sos-dispatcher
npm install
```

### Create the KV namespace

```bash
wrangler kv namespace create SOS_TOKENS
# copy the returned id into wrangler.toml (replace the placeholder)
```

### Create the D1 database

```bash
wrangler d1 create sos-dispatcher-log
# copy the returned database_id into wrangler.toml (replace the placeholder)
```

### Sync tokens

```bash
cd /mnt/HC_Volume_104325311/SOS
python3 scripts/sync-tokens-to-kv.py
```

## Deploy

```bash
cd workers/sos-dispatcher
npm run deploy
```

First deploy lands on `sos-dispatcher.<account>.workers.dev`. Route to `mcp.mumega.com`
only after canary validation.

## Local development

```bash
npm run dev
# listens on :6071, proxies to UPSTREAM_HOST:UPSTREAM_PORT (167.235.31.213:6070)
```

## Endpoints

- `GET /health` — no auth, returns service status
- `GET /sse/<token>` — SSE stream, token-authenticated
- `POST /messages` — MCP messages transport
- `POST /mcp/<token>` — streamable-HTTP transport

## Contract tests

`tests/contracts/test_dispatcher_protocol.py` — run against deployed URL with
known-good + known-revoked + rate-limit-known tokens. Same tests validate
the Python impl and any future impl.
