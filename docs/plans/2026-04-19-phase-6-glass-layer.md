# Phase 6 — Glass Layer (v0.10.0)

**Status:** planned, 2026-04-19
**Tracked by:** #211
**Depends on:** Phase 5 (`sos init`, v0.9.4, shipped `b693c13b`)
**Parent:** `docs/plans/2026-04-19-mumega-mothership.md` §Phase 6
**Target ship:** v0.10.0

## Gate

Every tenant's `/dashboard` is self-writing. SQL/bus-query → rendered
JSON → Inkwell static page. **No LLM in the render path.**

Verification: Point a browser at `<inkwell-instance>/dashboard`, see a
rendered dashboard composed of tiles. Each tile's data originates in a
SOS service, not in an LLM call.

## Why this phase exists

After Phase 5, every tenant has a provisioned Inkwell instance with a
squad and an open pulse loop. But the `/dashboard` route on each
instance is static — it shows what the template shipped with, not the
tenant's actual operating state (wallet balance, heartbeat, objective
progress, decisions log, GA metrics).

Phase 6 closes the feedback loop: make the dashboard a mirror of the
running organism. Each **tile** declares a query (SQL on a service
table, or a bus stream tail, or a static JSON endpoint). The SOS
`/glass` service resolves queries server-side and hands Inkwell a
typed payload. Inkwell renders with a `template` component and caches
in KV. No LLM in the hot path — rendering is fast, cheap, and
deterministic.

The LLM role moves to the *authoring* side: Growth Intelligence
(Phase 7) will propose new tiles by writing `glass.json` entries.
Phase 6 just builds the substrate.

---

## Approaches considered

**Option A: "Tiles as declarative JSON, SOS resolves server-side"** — the shipping choice.
- What: tile declares `{id, title, query, template, refresh_interval}`;
  a `/glass/tiles/{tenant}` endpoint runs the query; Inkwell adapter
  fetches rendered payloads and caches in KV.
- Tradeoffs: clean separation (query = SOS concern, render = Inkwell
  concern). Cache strategy is obvious (KV TTL = `refresh_interval`).
  Tile definitions are git-tracked JSON — diffable, reviewable.
  **Con:** if a tenant wants a bespoke query, they have to add a new
  tile kind (server-side code change).

**Option B: "Client-side queries with a SOS GraphQL endpoint"**
- What: Inkwell calls a GraphQL endpoint; tile is defined in-page as a
  GraphQL query string.
- Tradeoffs: maximum flexibility, but puts query logic in the client,
  bloats the edge bundle, and forces us to ship a GraphQL schema for
  every service. Over-engineered for the current need.

**Option C: "SSR HTML per dashboard route"**
- What: SOS renders the full dashboard HTML; Inkwell just iframes it.
- Tradeoffs: fastest to ship, but breaks the Inkwell-is-the-face
  contract, hard to style, hard to cache at the edge.

**Going with A.** It matches the existing port-and-client pattern
(contract → service route → client → inkwell adapter), and it's the
same shape the Phase 5 `standing_workflows.json` established.

---

## Step-by-step

### Step 6.1 — Define the `glass` port + Tile contract

- File: `sos/contracts/ports/glass.py` (new)
- Change: pydantic v2 models:
  - `TileQuery` — one of `SqlQuery`, `BusTailQuery`, `HttpQuery` (tagged union via `discriminator="kind"`).
  - `TileTemplate` (enum): `number`, `sparkline`, `progress_bar`, `event_log`, `status_light`, `chart`.
  - `Tile` — `{id: str, title: str, query: TileQuery, template: TileTemplate, refresh_interval_s: int, tenant: str}`.
  - `TilePayload` — `{tile_id, rendered_at, data: dict, cache_ttl_s: int}` (shape the Inkwell adapter consumes).
- Contract test: `tests/contracts/test_glass.py` — pydantic round-trip for each `TileQuery` variant, enum completeness, `extra="forbid"` on all models.
- Outcome: `python -m pytest tests/contracts/test_glass.py -q` green.

### Step 6.2 — Tile registry (Redis-backed)

- File: `sos/services/glass/_tile_store.py` (new)
- Change: Redis-backed store for per-tenant tile lists.
  - Key: `sos:glass:tiles:{tenant}` → JSON array of `Tile` dicts (1y TTL).
  - Functions: `list_tiles(tenant) -> list[Tile]`, `upsert_tile(tenant, tile)`, `delete_tile(tenant, tile_id)`.
  - Injectable Redis client, mirrors the `_qnft_store.py` pattern from Phase 5.
- Outcome: 4 unit tests (empty list, upsert, upsert-overwrite, delete) under `tests/services/glass/`.

### Step 6.3 — `glass` service FastAPI app

- File: `sos/services/glass/app.py` (new)
- Change: FastAPI app on port `8092` (next free port per `sos/contracts/ports/registry`).
  - `POST /glass/tiles/{tenant}` — upsert a tile. `require_system=True` + `Idempotency-Key` header.
  - `GET /glass/tiles/{tenant}` — list tiles for tenant. Tenant-or-system scope.
  - `DELETE /glass/tiles/{tenant}/{tile_id}` — remove tile. System-only.
  - `GET /glass/payload/{tenant}/{tile_id}` — **the hot path.** Loads tile, runs `query`, returns `TilePayload`. Sets `Cache-Control: max-age=<refresh_interval_s>`.
- Query resolvers (dispatched on `tile.query.kind`):
  - `sql`: runs against the tile's target service DB (start with `economy`, `registry`, `objectives`).
  - `bus_tail`: `XREVRANGE <stream> + -` limited to N entries.
  - `http`: GET another service endpoint, proxy the JSON.
- Outcome: 8 route tests under `tests/services/glass/test_app.py` (happy + 401/403 + 404 + cache headers).

### Step 6.4 — Port registration

- File: `sos/contracts/ports/registry.py`
- Change: add `"glass": 8092` to the port map. Update env-token scope map so `SOS_GLASS_SYSTEM_TOKEN` is recognized.
- Outcome: `tests/contracts/test_ports_registry.py` still green.

### Step 6.5 — Python client

- File: `sos/clients/glass.py` (new)
- Change: `GlassClient` + `AsyncGlassClient` mirroring the Phase-5 client pattern.
  - Methods: `upsert_tile(tenant, tile, *, idempotency_key)`, `list_tiles(tenant)`, `delete_tile(tenant, tile_id)`, `get_payload(tenant, tile_id)`.
- Outcome: `tests/clients/test_glass.py` — 4 cases with `httpx.MockTransport`.

### Step 6.6 — Inkwell adapter

- File: `/home/mumega/inkwell/kernel/adapters/glass/sos.ts` (new, Inkwell repo)
- Change: TypeScript adapter in Inkwell that hits `api.mumega.com/glass/payload/<tenant>/<tile_id>`, caches in Cloudflare KV with TTL = `cache_ttl_s` from the payload, returns the data to the Astro template.
- Interface: `fetchTile(tenant: string, tileId: string): Promise<TilePayload>`.
- Outcome: vitest test in `/home/mumega/inkwell/tests/kernel/adapters/glass.test.ts` with a MSW fetch mock.

### Step 6.7 — `/dashboard` Astro route in Inkwell template

- File: `/home/mumega/inkwell/instances/_template/pages/dashboard.astro` (new)
- Change: Astro page that:
  - Reads `instances/<slug>/glass.json` at build time.
  - For each tile, calls `fetchTile(tenant, tile.id)` at request time (server-rendered).
  - Renders the right component based on `tile.template` (one `<NumberTile/>`, `<SparklineTile/>`, etc. per template kind).
- File: `/home/mumega/inkwell/instances/_template/glass.json` (new) — empty array placeholder with a commented example.
- Outcome: `wrangler pages dev` locally renders `/dashboard` with dummy tiles. Add a `vitest` smoke test that renders the route with a fake fetch.

### Step 6.8 — Default tile set

- File: `sos/services/glass/_default_tiles.py` (new)
- Change: function `default_tiles(tenant: str) -> list[Tile]` returning 5 tiles — these are the Phase 6 "Health, Metabolism, Objectives, Decisions, Metrics" from the mothership plan:
  1. **Health** — `status_light`, `http` query to `/registry/squad/<tenant>/status`.
  2. **Metabolism** — `sparkline`, `sql` query `SELECT ts, balance FROM wallet_ledger WHERE tenant=? ORDER BY ts DESC LIMIT 30`.
  3. **Objectives** — `progress_bar`, `http` query to `/objectives/roots/<tenant>`.
  4. **Decisions** — `event_log`, `bus_tail` on `audit:decisions:<tenant>` last 20 entries.
  5. **Metrics** — `chart`, `http` query to `/integrations/ga4/<tenant>` (falls back to empty data until Phase 7 ships).
- Wire into Phase 5: add a new Step F to `sos/cli/init.py` — `step_f_seed_glass_tiles(cfg, tenant)` — that calls `GlassClient.upsert_tile()` for each default tile. Also update `instances/<slug>/glass.json` with the tile ID list so Inkwell renders them. This is the only Phase-5 touchpoint; it happens after Step D (workflows) and before Step E (pulse).
- Outcome: an end-to-end `sos init <tenant>` run produces a tenant where `<inkwell>/dashboard` renders 5 working tiles.

### Step 6.9 — E2E test

- File: `tests/e2e/test_glass_dashboard_round_trip.py` (new)
- Change: spins up the glass service in-process, seeds 3 default tiles, hits `GET /glass/payload/<tenant>/<tile_id>` for each, asserts shape matches `TilePayload` and `Cache-Control` header is set. Mocks the downstream service HTTP calls.
- Outcome: `python -m pytest tests/e2e/test_glass_dashboard_round_trip.py -q` green.

### Step 6.10 — lint-imports contract

- File: `pyproject.toml` (`[tool.importlinter]` section)
- Change: add a layers contract: `sos.services.glass` can import `sos.contracts` and `sos.clients.*` but not `sos.services.*`. Same R2 rule as the rest of the services.
- Outcome: `lint-imports` still green.

### Step 6.11 — Ship v0.10.0

- Files: `pyproject.toml`, `CHANGELOG.md`.
- Change: bump version to `0.10.0`; promote `[Unreleased]` → `[0.10.0] — <date>`; commit + tag `v0.10.0`; push main + tag.
- Outcome: GitHub tag `v0.10.0` exists on `main`; task #211 closed.

---

## Non-goals

- **No LLM in the render path.** Phase 6 is pure fetch + render.
- **No custom tile authoring UI.** Tiles are seeded by Step 6.8 and
  (later) by Growth Intelligence. A human-facing "add a tile" form is
  Phase 7+ scope.
- **No cross-tenant dashboards.** Every call is tenant-scoped.
- **No GraphQL.** Ruled out in the approach comparison above.
- **No real-time push.** Tiles pull on TTL. WebSockets are out of scope.

## Open questions (to resolve during implementation)

- **Which services have direct SQL?** If `economy` / `objectives` /
  `registry` don't all expose queryable tables today, Step 6.3 falls
  back to `http` queries against their existing FastAPI routes. Will
  confirm in the recon at the start of Step 6.3.
- **Port 8092 collision?** Verify against the port registry during
  Step 6.4 — may need to pick a different port if `8092` is already
  claimed.
- **Inkwell repo branching.** Phase 5 committed `_template/` straight
  to Inkwell main. Phase 6 adds a `dashboard.astro` + `glass.json` — keep
  to main unless the Inkwell team wants a branch.

## Exit criteria

1. `sos init <fake-tenant>` end-to-end produces a `/dashboard` that
   renders 5 tiles (Step 6.9 asserts this in CI with mocks; manual
   verify in dev for the actual browser render).
2. No LLM call in `sos/services/glass/**`.
3. 3-way test counts green: contracts (glass port), service (glass app),
   e2e (round-trip).
4. `lint-imports` green with the new R2 glass contract.
5. `v0.10.0` tag on `main`, CHANGELOG entry dated, task #211 closed.
