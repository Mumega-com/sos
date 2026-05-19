# Mumega Edge — Canonical API Topology

**Status:** Phase 4 / v0.9.3 (2026-04-19)
**Owner:** `mumega-edge` (Cloudflare Worker, separate repo)
**Consumers:** every SOS service, every Inkwell instance, api.mumega.com callers

---

## Why this exists

Before Phase 4 every SOS service was reachable directly on its VPS port
(bus:8071, saas:8075, economy:6062, …). Inkwell instances hit SOS at
`${VPS}:port`, browsers hit `api.mumega.com`, the surface was fragmented,
and per-service CORS / auth was copy-pasted across Workers.

Phase 4 collapses the surface to a **single Cloudflare Worker** —
`api.mumega.com` — that:

1. Terminates TLS / CORS / rate-limit at the edge.
2. Authenticates Bearer tokens against `TOKENS` KV.
3. Proxies to the appropriate SOS service on the VPS.
4. Owns Inkwell content directly (D1) so Inkwell instances don't need
   per-tenant Workers.

---

## URL shape

```
https://api.mumega.com/
├── /health                     → edge health (no auth)
├── /auth/*                     → session / supabase flows
├── /signup/*                   → tenant creation
├── /billing/*                  → Stripe webhooks
├── /dashboard/*                → dashboard queries
├── /seats/*                    → seat management
├── /tenants/*                  → tenant CRUD
├── /sync-tenant                → VPS → edge tenant sync
├── /sos/<service>/<path>       → Bearer-gated proxy to SOS
│   ├── /sos/bus/*              → VPS:8071
│   ├── /sos/saas/*             → VPS:8075
│   ├── /sos/economy/*          → VPS:6062
│   ├── /sos/registry/*         → VPS:6067
│   ├── /sos/mesh/*             → VPS:6067 (mesh lives in registry)
│   ├── /sos/objectives/*       → VPS:6068
│   ├── /sos/integrations/*     → VPS:6066
│   ├── /sos/glass/*            → VPS:8092
│   └── /sos/operations/*       → VPS:6068
└── /inkwell/
    ├── /content/<tenant>           → D1 on edge (list public content)
    ├── /content/<tenant>/<slug>    → D1 (get/put/delete, paywall-aware)
    ├── /glass/*                    → proxy to /sos/glass
    └── /shelf/*                    → proxy to /sos/economy/shelf
```

---

## Env var matrix (mumega-edge `wrangler.toml` / secrets)

| Binding                    | Kind   | Purpose                               |
| -------------------------- | ------ | ------------------------------------- |
| `DB`                       | D1     | tenants, inkwell_content              |
| `TOKENS`                   | KV     | Bearer token → `{slug, plan, scope}`  |
| `SESSIONS`                 | KV     | browser session store                 |
| `VPS_URL`                  | var    | fallback origin, e.g. `http://vps:`   |
| `SOS_BUS_URL`              | var    | override for bus (full URL)           |
| `SOS_SAAS_URL`             | var    | override for saas                     |
| `SOS_ECONOMY_URL`          | var    | override for economy                  |
| `SOS_REGISTRY_URL`         | var    | override for registry/mesh            |
| `SOS_OBJECTIVES_URL`       | var    | override for objectives/operations    |
| `SOS_INTEGRATIONS_URL`     | var    | override for integrations             |
| `SOS_GLASS_URL`            | var    | override for glass                    |
| `SOS_OPERATIONS_URL`       | var    | override for operations               |
| `VPS_SYNC_SECRET`          | secret | shared secret for `/sync-tenant`      |
| `SUPABASE_*`, `STRIPE_*`   | secret | see `src/types.ts`                    |

Resolution order for each SOS service:
1. `SOS_<NAME>_URL` if set (full URL, any host/port).
2. `VPS_URL` + canonical port (see below) otherwise.

---

## Canonical SOS port registry

Keep in sync with `sos/services/<name>/app.py::DEFAULT_PORT`:

| Service       | Port  | Notes                                    |
| ------------- | ----- | ---------------------------------------- |
| bus           | 8071  | message bus                              |
| saas          | 8075  | tenant / billing API                     |
| economy       | 6062  | $MIND + shelf                            |
| registry      | 6067  | agent registry + mesh                    |
| integrations  | 6066  | dossier, connectors                      |
| objectives    | 6068  | objective graph                          |
| operations    | 6068  | organism/pulse (shares port w/ objects)  |
| glass         | 8092  | operator glass                           |

---

## Auth flow

```
browser / service          edge                              SOS (VPS)
─────────────────          ────                              ─────────
                                                                     
Bearer <token>   ─────►  TOKENS KV lookup                            
                         ↓                                           
                         { slug, plan, scope, active }               
                         ↓                                           
                         strip hop-by-hop headers                    
                         add x-sos-edge: 1                           
                         ─────────────────────────────►  handle      
                                                         Bearer      
                         ◄─────────────────────────────  response    
◄──────  response                                                    
```

- `requireBusToken(c)` lives in `src/lib/proxy.ts`.
- On 401 the Worker returns `{error: "missing_bearer"}` or `{error: "invalid_bearer"}` — never passes the request upstream.
- The tenant-owned bearer (`scope: "customer"`) is issued by `/sync-tenant`
  when SOS creates a tenant.
- Admin tokens (`scope: "system"`) are minted manually and should be scoped
  to the service they write to.

---

## Adding a new SOS service

1. Give it a canonical port in `sos/services/<name>/app.py` and in the
   table above.
2. In `mumega-edge/src/routes/sos.ts`, add an entry to `SERVICES`:
   ```ts
   myservice: { envKey: 'SOS_MYSERVICE_URL', port: 6099 }
   ```
3. Add the binding to `src/types.ts` (`SOS_MYSERVICE_URL?: string`).
4. If it has a non-default override, add `SOS_MYSERVICE_URL` to
   `wrangler.toml [vars]`.
5. Deploy: `npx wrangler deploy` from the mumega-edge repo.
6. Smoke-test: `curl -H 'Authorization: Bearer <token>' https://api.mumega.com/sos/myservice/health`.

Callers then hit `https://api.mumega.com/sos/myservice/<path>` — no CORS
fiddling, no per-service Worker.

---

## Inkwell content on the edge

`/inkwell/content/*` is **not** a proxy. It reads and writes the D1
`inkwell_content` table directly:

- `GET /inkwell/content/:tenant` — public content list (no auth).
- `GET /inkwell/content/:tenant/:slug` — single page. Respects
  `visibility` (`public` / `members` / `private`) and `grant_id`
  (paywall: returns 402 unless the caller owns the grant; until the
  grant store ships, only the tenant owner reads paid content).
- `PUT /inkwell/content/:tenant/:slug` — upsert (ON CONFLICT DO UPDATE).
  Bearer must match tenant.
- `DELETE /inkwell/content/:tenant/:slug` — same write gate.

`/inkwell/glass/*` and `/inkwell/shelf/*` are thin convenience proxies so
an Inkwell instance can stay on one origin without CORS or per-instance
Workers.

---

## Not covered here

- Rate limiting per tenant (SEC-004 — already in place on the Worker).
- Audit logging of tool calls (SEC-002 — SOS-side).
- Per-instance Inkwell forks (Phase 4 Step 4.5): they will stop shipping
  a `workers/inkwell-api/` Worker and point directly at
  `api.mumega.com/inkwell`.

See also: `ARCHITECTURE.md` (top-level), `docs/architecture/SERVICE_MAP.md`
(SOS services & ports), `docs/architecture/AGENT_WIRING.md` (agent-side).
