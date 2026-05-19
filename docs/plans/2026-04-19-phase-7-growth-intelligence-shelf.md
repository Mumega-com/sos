# Phase 7 — Growth Intelligence squad + Shelf (v0.10.1)

**Status:** planned, 2026-04-19
**Tracked by:** #212
**Depends on:** Phase 6 (Glass layer, v0.10.0, shipped `0c414bb1`)
**Parent:** `docs/plans/2026-04-19-mumega-mothership.md` §Phase 7
**Target ship:** v0.10.1

## Gate

A new tenant signs up and, within 10 minutes, gets:

1. A **brand-vector dossier** (auto-synthesized from scraped signals).
2. A wallet credited with a starter $MIND balance.
3. One **Shelf product** listed (Mumega Playbook) and buyable via Stripe.

Verification: `sos init <fake-tenant>` end-to-end (Phase 5 + 6 + 7) produces
a tenant whose `/dashboard/growth` tile shows a non-empty dossier AND whose
`/shelf` route lists at least one product with a working Stripe checkout link.

## Why this phase exists

Phase 6 gave every tenant a dashboard that *mirrors* their organism. Phase 7
adds two things the organism couldn't do before:

- **Intelligence on the input side** — a standing "growth-intel" squad
  pulls GA/GSC/Ads + BrightData/Apify signals into a synthesized brand
  vector the dashboard can show.
- **Revenue on the output side** — a Shelf that sells digital products
  (courses, books, playbooks) through Stripe, feeding captured amounts
  back into `/economy/credit` so $MIND accrues from real sales.

This is the phase where Mumega stops being "an agent framework with a
dashboard" and starts being "a tenant-owned revenue-generating
organism". v1.0.0 (Phase 8) is just onboarding — all the new
*capability* lands here.

## Pre-flight check — external dependencies

Phase 7 has **real external dependencies** that cannot be stubbed
end-to-end. Breaking them out so we know what's ship-autonomous vs.
blocked on credentials:

| Dependency | Needed for | Who provides | Blocking? |
|---|---|---|---|
| GA/GSC OAuth client_id+secret | 7.1 | Hadi (Google Cloud console) | **YES** for live test |
| Google Ads developer token | 7.1 | Hadi (Ads API application) | YES for live test |
| BrightData API token | 7.2 | Hadi (BrightData dashboard) | YES for live test |
| Apify API token | 7.2 | Hadi (Apify account) | YES for live test |
| Stripe publishable + secret | 7.6 | Hadi (Stripe dashboard) | **YES** for checkout |
| Stripe webhook signing secret | 7.6 | Hadi (webhook endpoint config) | YES |
| D1 database binding (shelf) | 7.6 | Hadi (wrangler) | YES for live test |
| Mumega Playbook content | 7.7 | Hadi (authoring) | can ship placeholder |

**Autonomous ship plan (no external creds needed):**
- All code + contracts + tests with `httpx.MockTransport` mocks
- A `--provider=fake` adapter for dev/CI so the squad loop runs end-to-end
- An empty-shelf fallback so `/shelf` renders even before products land
- Migration + D1 schema committed (binding applied later by wrangler)

**Live test (requires Hadi):** end-to-end smoke against real GA property +
BrightData scrape + Stripe test checkout. Can be deferred to v0.10.1.1
hotfix once creds land.

## Inkwell v8.3 primitives we inherit (landed 2026-04-19, commit `54db2fe`)

Three things Inkwell just shipped that change the Phase 7 surface area —
less new code on the SOS side, more reuse of Inkwell's CF-native rails:

- **CF Access middleware** (`workers/inkwell-api/src/middleware/cf-access.ts`)
  — service tokens (machine-to-machine) + JWT RS256 via JWKS, KV-cached
  1hr. **Use it for:** Stripe webhook auth on the Inkwell side, and any
  Inkwell → SOS call that needs to prove identity without a user session.
  Replaces the ad-hoc bearer pattern we'd otherwise have to build.
- **CF Workflows** (`workers/inkwell-api/src/workflows/generic.ts`) —
  `GenericWorkflow` with `fetch`/`sleep`/`db_query` steps, durable
  across restarts. **Use it for:** BrightData/Apify poll loops (Step 7.2)
  instead of custom async-poll code; and the growth-intel daily cron
  (Step 7.4) as a native alternative to SOS pulse.
- **5-provider automation chain** (`plugins/automation/mcp-tools.ts`)
  detection order: **CF Workflows > ToRivers > n8n > Zapier > webhook**.
  **Use it for:** Step 7.4 — tenant picks a provider via env; the same
  growth-intel workflow runs against whichever is configured.

**Net effect:** Step 7.2 poll loops can be 20 lines of Workflow YAML
instead of a custom async poller. Step 7.4 doesn't need a new trigger
system — it hooks into the existing automation chain. Step 7.6 Stripe
webhook auth reuses the CF Access service-token pattern.

## Approaches considered

**Option A: "Ship contracts + mockable adapters, defer live wiring"** — the shipping choice.
- What: land all pydantic contracts, provider-agnostic adapter interface,
  growth-intel squad + Glass tile + Shelf code, with `fake` adapters that
  produce canned data. Real credentials flip a single env flag to activate.
- Tradeoffs: v0.10.1 ships on time + mergeable. Exit criteria "live
  tenant sees real GA data" pushes to v0.10.1.1. **Pro:** no blocked
  PRs. **Con:** the gate ("10-minute tenant → real dossier") is only
  provable once Hadi provisions the creds.

**Option B: "Block v0.10.1 on live credentials"**
- What: don't ship until every real integration works.
- Tradeoffs: gate is fully provable when shipped. **Con:** Hadi-side
  blocker of unknown duration stalls Phase 8 (#213). Not aligned with
  the "merge fast, iterate" directive.

**Option C: "Ship Shelf only, defer Growth Intelligence"**
- What: split Phase 7 into 7a (Shelf) and 7b (Growth). 7a unblocks
  revenue; 7b waits for scraping creds.
- Tradeoffs: cleanest scope, but splits the dashboard story (Growth tile
  empty until 7b). The /dashboard/growth tile is half the gate.

**Going with A.** It matches the Phase 6 pattern (ship the substrate,
let real data backfill). The growth-intel squad uses the same
contract-first approach — an `IntelligenceProvider` protocol with
`GA4Provider`, `GSCProvider`, `BrightDataProvider`, `ApifyProvider`,
`FakeProvider` implementations behind it.

---

## Step-by-step

### Step 7.1 — GA/GSC/Ads OAuth providers

- Files: `sos/services/integrations/oauth.py` (extend), `sos/contracts/ports/integrations.py` (new)
- Change: split the existing Google OAuth stub into per-service providers.
  - `GA4Provider.fetch_metrics(tenant, property_id, range)` → 30d sessions + top pages
  - `GSCProvider.fetch_queries(tenant, site, range)` → top 50 search queries + impressions
  - `GoogleAdsProvider.fetch_campaigns(tenant, customer_id)` → active campaigns + CPC
  - All three implement the `IntelligenceProvider` protocol.
  - Real OAuth token exchange replaces the #222 stubs (see the TODO blocks in `oauth.py`).
- Contract: `IntelligenceProvider` protocol with a single `pull(tenant, params)` method returning a `ProviderSnapshot`.
- Outcome: `pytest tests/services/integrations/test_providers.py -q` green with httpx.MockTransport mocks.

### Step 7.2 — BrightData + Apify adapters

- Files: `sos/services/integrations/adapters/brightdata.py` (new), `sos/services/integrations/adapters/apify.py` (new)
- Change: connector-style pull. Each adapter takes a `dataset_id` + tenant, triggers the provider, waits for completion, stores the result in a new `snapshots` table.
  - BrightData: `POST https://api.brightdata.com/dca/trigger` → wait for `/dca/dataset/<id>` ready → store.
  - Apify: `POST https://api.apify.com/v2/acts/<id>/runs` → wait for `/v2/actor-runs/<runId>` finished → store.
  - Both emit `snapshot.created` on the bus for the narrative-synth agent to pick up.
- **Poll loop strategy:** the adapter exposes `trigger()` + `fetch_result(run_id)` as two separate async methods. The SOS-side implementation has a simple `asyncio.sleep` poll for dev/CI. In production, the Inkwell-side `GenericWorkflow` orchestrates the trigger → sleep → fetch chain as durable Workflow steps (survives Worker restarts, no in-memory state). One interface, two execution modes.
- Fake provider: `FakeSnapshotProvider` returns canned data so growth-intel squad loop runs in dev.
- Outcome: 6 unit tests under `tests/services/integrations/test_adapters.py` covering trigger / fetch / error / timeout for each.

### Step 7.3 — Growth-intel squad (3 agents)

- Files: `sos/agents/growth_intel/__init__.py`, `trend_finder.py`, `narrative_synth.py`, `dossier_writer.py` (new)
- Change: three agents, each subscribed to a bus stream:
  - **trend-finder** — listens to daily cron, triggers BrightData/Apify snapshots, emits `snapshot.created`.
  - **narrative-synth** — on `snapshot.created`, clusters the signals into a `BrandVector` (pydantic model with `tone`, `audience`, `opportunity_vector: list[str]`, `threat_vector: list[str]`).
  - **dossier-writer** — on `brand_vector.updated`, renders a markdown dossier to `sos:memory:{tenant}:dossier:latest` (Redis) and writes a digest to `sos:memory:{tenant}:dossier:history:{date}`.
- Contract: `sos/contracts/brand_vector.py` — `BrandVector`, `Dossier`, `ProviderSnapshot`.
- Outcome: 8 tests under `tests/agents/test_growth_intel.py` covering each agent + the full chain with fake provider.

### Step 7.4 — Wire squad to pulse

- File: `sos/cli/_default_standing_workflows.py` (new)
- Change: add a `growth-intel` workflow to the standing workflows template so every new tenant gets a daily growth run:
  ```json
  {"name": "{{SLUG}}-growth-intel", "schedule": "0 10 * * *",
   "description": "Daily growth intelligence pull + dossier",
   "steps": ["trend-finder", "narrative-synth", "dossier-writer"],
   "bounty_mind": 50,
   "trigger": "auto"}
  ```
- **Trigger provider (auto-detect via Inkwell v8.3 automation chain):**
  `trigger: "auto"` lets the Inkwell automation plugin pick the best
  available: `CF Workflows > ToRivers > n8n > Zapier > generic webhook`.
  No per-tenant wiring code — each instance config declares which
  provider is configured, and the chain resolves at call time.
- Wire: `sos/cli/init.py::step_d_write_workflows` picks this up automatically (it iterates over `data["workflows"]`).
- Outcome: the e2e `sos init` test gets a fourth workflow entry asserted.

### Step 7.5 — "Brand Vector" Glass tile

- File: `sos/cli/_default_tiles.py` (extend)
- Change: add a sixth default tile:
  ```python
  Tile(
      id="brand-vector",
      title="Brand Vector",
      query=HttpQuery(kind="http", service="integrations",
                      path=f"/integrations/dossier/{tenant}/latest"),
      template=TileTemplate.EVENT_LOG,
      refresh_interval_s=3600,
      tenant=tenant,
  )
  ```
- New route: `GET /integrations/dossier/{tenant}/latest` on the integrations service — reads from Redis `sos:memory:{tenant}:dossier:latest`, returns `{date, summary, opportunities, threats}`.
- Inkwell: `/dashboard/growth` → subset route that only shows the growth-related tiles (brand-vector, metrics, objectives).
- Outcome: e2e round-trip test (§6.9-style) hits `/glass/payload/<tenant>/brand-vector` and asserts it pulls through the new route.

### Step 7.6 — Shelf commerce (Inkwell + SOS)

- Files:
  - `inkwell/kernel/adapters/commerce/sos.ts` (new)
  - `sos/services/economy/shelf.py` (new routes on existing economy service)
  - `sos/contracts/shelf.py` (new) — `ShelfProduct`, `ShelfCapture`
  - `migrations/0003_shelf.sql` (new D1 schema — `shelf_products`, `shelf_captures`)
- Change:
  - `GET /economy/shelf/{tenant}` → list products for a tenant.
  - `POST /economy/shelf/{tenant}` (system-only) → add a product.
  - `POST /economy/shelf/checkout/{tenant}/{product_id}` → creates a Stripe Checkout Session, returns the `url` + `session_id`.
  - `POST /economy/shelf/capture` → Stripe webhook (signed with webhook secret). On `checkout.session.completed`, credits the tenant's $MIND wallet with the captured amount × exchange rate, writes a row to `shelf_captures`, grants access (the Inkwell adapter reads this to unlock content).
  - Inkwell adapter: `createSosCommerceAdapter({baseUrl, tenant})` with `listProducts()`, `createCheckout(productId)`, `grantedProducts(userId)`.
  - **Auth path:** the Inkwell `/shelf` routes call SOS via the CF Access middleware (v8.3) — service-token JWT over JWKS, KV-cached. No per-request bearer wiring on the SOS side; SOS verifies the token against the shared issuer. The Stripe webhook itself verifies via `stripe.webhooks.construct_event` (signing secret, not CF Access).
- Outcome: 10 tests covering happy/error paths + webhook signature verification.

### Step 7.7 — Mumega Playbook dogfood

- Files: `inkwell/instances/mumega/shelf/mumega-playbook.md` (new, placeholder), seed script in `sos/cli/init.py::step_g_seed_shelf`
- Change: `step_g_seed_shelf` runs after Step F, posts a default "Mumega Playbook" product to the shelf (price = 29.00 USD, access = content ID "mumega-playbook"). Only runs for tenant `mumega-internal` (checked by slug) — other tenants get an empty shelf.
- Outcome: `sos init mumega-internal` produces a `/shelf` that lists one product.

### Step 7.8 — E2E growth + shelf round-trip

- File: `tests/e2e/test_growth_shelf_round_trip.py` (new)
- Change: spins up integrations + economy + glass services in-process, seeds one tenant, runs the growth squad against the fake provider, asserts dossier is written to Redis, then hits `/economy/shelf/<tenant>/checkout/mumega-playbook` and asserts a mocked Stripe session URL comes back.
- Outcome: green.

### Step 7.9 — lint-imports contracts

- File: `pyproject.toml` (`[tool.importlinter]`)
- Change: add R2 contracts for the new modules (`sos.agents.growth_intel` stays leaf; `sos.services.economy.shelf` can only import `sos.contracts` / `sos.kernel.*`; Inkwell commerce adapter mirrors glass adapter).
- Outcome: `lint-imports` stays green.

### Step 7.10 — Ship v0.10.1

- Files: `pyproject.toml`, `CHANGELOG.md`
- Change: bump to `0.10.1`; promote `[Unreleased]` → `[0.10.1] — <date>`; commit + tag + push.
- Outcome: `v0.10.1` on `main`; task #212 closed.

---

## Non-goals

- **No live credential provisioning.** Real GA property / BrightData
  dataset / Apify actor / Stripe dashboard setup is Hadi's job.
  Phase 7 ships with `fake` providers wired by default; live providers
  activate when `SOS_INTEGRATIONS_PROVIDER=live` + creds are present.
- **No Shelf UI customization.** Inkwell renders a default list;
  Phase 7+ (customization per instance) is Phase 8-onward scope.
- **No recurring subscriptions.** Shelf ships with one-time purchases
  only. Subscriptions require Stripe Billing + different webhook
  handling, deferred to post-v1.0.
- **No brand-vector versioning.** Latest dossier wins; no diff view,
  no rollback. History is append-only in Redis.
- **No multi-currency.** USD only. Currency conversion lands post-v1.0.

## Open questions (to resolve during implementation)

- **Stripe Connect vs. platform-account.** For now we use one
  platform Stripe account + `application_fee_amount` to skim a
  Mumega cut. Per-tenant Stripe Connect accounts would be cleaner
  but require KYC flow we can't build in Phase 7. Platform-account
  is the ship path.
- **D1 vs. Redis for shelf_captures.** D1 is more durable but
  requires wrangler bindings + migration story. Redis is already
  wired. **Defaulting to Redis for v0.10.1**, migrate to D1 in
  Phase 8 if durability is needed. The migration lives in the
  migration file but isn't applied until Phase 8.
- **BrandVector embeddings.** Narrative-synth currently clusters by
  simple keyword overlap. Real embeddings (Voyage-3 or similar)
  would be better — flagged as post-v1.0 cleanup.

## Exit criteria

1. `sos init <fake-tenant>` end-to-end (Phase 5 + 6 + 7) produces a
   tenant whose `/dashboard/growth` shows a non-empty dossier AND
   whose `/shelf` lists the Mumega Playbook (for `mumega-internal`
   only) or empty (for all others).
2. Fake providers produce canned data; live providers exist behind
   the env flag but are not exercised in CI.
3. No real Stripe charges made in tests — webhook signatures verified
   against the `STRIPE_WEBHOOK_SECRET_TEST` key.
4. `lint-imports` green.
5. 3 new test count: contracts (brand_vector, shelf) + services
   (providers, adapters, shelf routes) + e2e (growth + shelf
   round-trip). All green.
6. `v0.10.1` tag on `main`, CHANGELOG dated, task #212 closed.

## What Hadi needs to do for the **live** gate (separate from merge)

Not blocking v0.10.1 ship — these land post-merge and flip the env
flag from `fake` to `live`:

1. Google Cloud project with GA4 Data API + Search Console API
   enabled; OAuth consent screen published; download
   `client_id` + `client_secret` → store via `wrangler secret put
   GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`.
2. Google Ads developer token application approved (takes 3-5 days).
3. BrightData account → API token → `BRIGHTDATA_API_TOKEN` secret.
4. Apify account → API token → `APIFY_API_TOKEN` secret.
5. Stripe account in test mode → `STRIPE_SECRET_KEY`,
   `STRIPE_PUBLISHABLE_KEY`; webhook endpoint configured →
   `STRIPE_WEBHOOK_SECRET`.
6. D1 database created → binding in `wrangler.toml` for the
   economy worker → migration 0003 applied.

When 1-6 are done, set `SOS_INTEGRATIONS_PROVIDER=live` on the
integrations worker and run a manual smoke test of the dogfood flow.
