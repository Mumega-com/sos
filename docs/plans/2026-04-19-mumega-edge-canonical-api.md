# Phase 4 — Mumega-edge as canonical API (v0.9.3)

**Status:** in progress (audit done, routes not yet migrated)
**Task:** #209
**Gate:** Every Inkwell instance points `workerUrl` at `api.mumega.com`. No per-instance Workers.

---

## Why this matters

Today every per-instance Inkwell fork (`digid-inkwell`, `shabrang-inkwell`,
`mumega-internal-inkwell`, …) ships its own `workers/inkwell-api` CF Worker.
That pattern multiplies deploy surface, duplicates auth middleware, and makes
it impossible to roll a single API change across all tenants. Phase 4
collapses this to **one Worker** — `mumega-edge` at `api.mumega.com` — that
proxies `/sos/*` to SOS services (saas, economy, registry, objectives,
integrations, glass, mesh) and serves `/inkwell/*` routes directly for the
Inkwell CMS.

## Scope boundary

This phase spans **three repos**:

| Repo | Change |
|------|--------|
| `/home/mumega/mumega-edge/` (this Worker) | Add `/sos/*` + `/inkwell/*` route groups, keep existing auth |
| `/home/mumega/inkwell/` (template) | `workerUrl` default = `https://api.mumega.com` |
| Per-instance inkwell forks (`digid-inkwell`, `shabrang-inkwell`, …) | Remove `workers/inkwell-api`; import shared config |
| SOS repo (this one) | Docs only — nothing SOS-server-side changes |

**Non-reversible actions** (paused for user approval):
- `wrangler deploy` of the new mumega-edge (live cutover of `api.mumega.com`)
- Deleting `workers/inkwell-api` from per-instance forks

---

## Step 4.1 — Audit (✅ done 2026-04-19)

All 15 existing routes classify as **(a) proxy-to-sos** in the Phase 4
architecture. None need retiring; all need re-homing under `/sos/*` or
`/inkwell/*` prefixes.

| Method + Path | File | Target (proxy) | Notes |
|---|---|---|---|
| GET `/health` | index.ts | — | keep at root |
| POST `/auth/login` | auth.ts | — | local magic-link; generates JWT in `SESSIONS` KV; uses Resend |
| GET `/auth/verify?token=…` | auth.ts | — | consumes magic token; sets session cookie |
| GET `/auth/me` | auth.ts | — | returns authenticated user + tenant from SESSIONS KV |
| POST `/auth/logout` | auth.ts | — | invalidates session cookie |
| POST `/billing/webhook` | billing.ts | VPS:8075/billing/webhook | Stripe webhook passthrough |
| GET `/billing/portal` | billing.ts | — | stub — Stripe portal redirect |
| GET `/dashboard/overview` | dashboard.ts | VPS:8075/tenants/{slug}/usage | usage stats proxy |
| GET `/dashboard/billing` | dashboard.ts | VPS:8075/tenants/{slug}/invoice | billing summary proxy |
| GET `/dashboard/seats` | dashboard.ts | VPS:8075/tenants/{slug}/seats | seat list proxy |
| POST `/seats` | seats.ts | VPS:8075/tenants/{slug}/seats | create team seat token |
| DELETE `/seats/:tokenId` | seats.ts | VPS:8075/tenants/{slug}/seats/{tokenId} | revoke seat |
| POST `/signup` | signup.ts | — | local D1 insert + async VPS sync; returns MCP SSE config |
| GET `/tenants/me` | tenants.ts | — | authenticated tenant record (sensitive fields stripped) |
| POST `/sync-tenant` | index.ts | — | VPS→Edge internal sync, protected by `x-sync-secret` header |

**Bindings today**: D1 `mumega-edge`, KV `TOKENS`, KV `SESSIONS`, secrets
(STRIPE_SECRET_KEY, JWT_SECRET, RESEND_API_KEY, VPS_SYNC_SECRET),
env (VPS_URL, MCP_BASE_URL).

**Gap vs design**: routes sit at root, not under `/sos/*` or `/inkwell/*`.
VPS URL is hardcoded rather than routed through a SOS service map.

---

## Steps 4.2 – 4.7 — plan of record

### Step 4.2 — Add SOS-proxy route groups

Add `src/routes/sos.ts` with Hono sub-routers mounted at:

| Prefix | Target SOS service | Port | Current equivalent |
|--------|--------------------|------|--------------------|
| `/sos/bus/*` | `bus` | 8071 | — (new) |
| `/sos/economy/*` | `economy` | 6062 | — (new) |
| `/sos/registry/*` | `registry` | 8077 | — (new) |
| `/sos/objectives/*` | `objectives` | 8078 | — (new) |
| `/sos/mesh/*` | `registry` (same process as `/mesh/*`) | 8077 | — (new) |
| `/sos/integrations/*` | `integrations` | 8079 | — (new) |
| `/sos/glass/*` | `glass` | 8080 | — (new) |
| `/sos/operations/*` | `operations` | 8081 | — (new) |

Implementation pattern: shared `proxyTo(serviceBase)` helper that
forwards method, headers (stripping cookies), body; returns upstream
response with `x-sos-edge: 1` header appended. Auth middleware:
require `Authorization: Bearer <token>` and look the token up in the
`TOKENS` KV (no cookie session for `/sos/*` — those are token-only).

**Service base URLs** come from env vars `SOS_<UPPER>_URL` (fallback to
`VPS_URL + ":port"`). All 8 service URLs go in `wrangler.toml [vars]`.

**Files to add:**
- `src/routes/sos.ts` (new)
- `src/lib/proxy.ts` (new — helper)

**File to edit:**
- `src/index.ts` — `app.route('/sos', sosRouter)`
- `wrangler.toml` — add `SOS_*_URL` vars

### Step 4.3 — Add Inkwell route group

Add `src/routes/inkwell.ts` with three sub-mounts:

| Prefix | Purpose | Backing store |
|--------|---------|---------------|
| `/inkwell/content/*` | CMS content (pages, posts, dossiers rendered as markdown) | D1 `inkwell_content` + R2 for media |
| `/inkwell/glass/*` | Glass tile registry passthrough | proxy to SOS glass service |
| `/inkwell/shelf/*` | Shelf product listing passthrough | proxy to SOS economy shelf routes |

`/inkwell/content/*` is **not** a proxy — it reads/writes the edge's own D1
tables (no per-instance Workers needed because one D1 database per tenant
is keyed on the bus token → slug lookup). `/inkwell/glass/*` and
`/inkwell/shelf/*` are thin proxies; they exist to give Inkwell a single
origin (`api.mumega.com`) rather than forcing the browser to call the
VPS directly.

**Files to add:**
- `src/routes/inkwell.ts` (new)
- `migrations/0002_inkwell_content.sql` (new — content table)

### Step 4.4 — Update Inkwell config default

Edit `/home/mumega/inkwell/inkwell.config.ts`:

```diff
-  workerUrl: process.env.INKWELL_WORKER_URL ?? 'https://{{slug}}-inkwell.workers.dev',
+  workerUrl: process.env.INKWELL_WORKER_URL ?? 'https://api.mumega.com',
```

Per-instance forks override via `INKWELL_WORKER_URL` only if they still run
their own Worker (transitional).

### Step 4.5 — Remove per-instance Workers

For each of: `digid-inkwell`, `shabrang-inkwell`, `mumega-internal-inkwell`
(and whatever other forks exist):
- Delete `workers/inkwell-api/`
- Bump `inkwell.config.ts` to remove `workerUrl` override (inherit from template).
- Re-deploy the Pages site (rebuild triggers fresh config).

**This is the non-reversible step.** User approval required before each
per-instance cutover.

### Step 4.6 — Document

`docs/architecture/mumega-edge.md` — single-ingress topology diagram, env
var matrix, how to add a new SOS service to the proxy, how auth flows
through the edge.

### Step 4.7 — Ship v0.9.3

- Bump `pyproject.toml` (SOS side) version to 0.9.3
- Update CHANGELOG on SOS side with the cross-repo summary
- `wrangler deploy` on mumega-edge
- Tag `v0.9.3` in SOS repo
- Update the 3 per-instance forks (cut over one at a time, start with `mumega-internal`)

---

## What can be done autonomously

- ✅ Step 4.1 audit (done)
- ✅ Step 4.2 — write `sos.ts` + `proxy.ts` + update `index.ts`, local only
- ✅ Step 4.3 — write `inkwell.ts` + D1 migration, local only
- ✅ Step 4.6 — write `docs/architecture/mumega-edge.md`
- ⏸ Step 4.4 — needs coordination with `inkwell` repo default (local edit fine,
  but it has live consequences once next Inkwell build ships)
- ⏸ Step 4.5 — per-instance Worker deletion (destructive, user-gated)
- ⏸ Step 4.7 — `wrangler deploy` on mumega-edge (live cutover, user-gated)

---

## Risks

- **DNS / TLS**: `api.mumega.com` must have the CF route set up. Verify in
  CF dashboard before deploy.
- **CORS**: every Inkwell site (`digid.com`, `shabrang.ai`, etc.) must be in
  the allow-list — a regression here breaks `/dashboard` immediately.
- **Token KV sync**: `TOKENS` KV on the edge must match `sos/bus/tokens.json`
  on the VPS. Currently kept in sync by `scripts/sync-tokens-to-kv.py`
  (task #38, shipped). Confirm the sync cron is still running before
  cutover.
- **Legacy fallback**: keep VPS:8075 routes live for 2 weeks after cutover
  so anyone with cached client config doesn't break. Retire in v0.9.4.
