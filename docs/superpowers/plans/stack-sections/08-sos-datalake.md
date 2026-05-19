# Section 8 — SOS Datalake (Service Module)

**Author:** Loom
**Date:** 2026-04-24
**Phase:** 4 — `sos-datalake` as a service module (NOT a kernel primitive)
**Depends on:** Section 1 (role registry, engram tiers), Section 6 (plugin.yaml contract), Section 7 (fractal node primitive)
**Gate:** Athena
**Owner:** Kasra

---

## 1. Background & Mandate

### Why this is a service module, not kernel

SOS is a **microkernel**. The kernel stays minimal: auth, bus, Mirror interface, role registry, plugin loader, schema/events. That's it. If the datalake were kernel, **every kernel upgrade would carry data-source adapter risk — wrong blast radius**. A GSC API schema change would force a kernel patch; a Stripe webhook update would ripple into every plugin at once.

Instead, `sos-datalake` is a **service module**, peer to `squad-service`, `mirror-service`, and `dispatcher`. It registers via the plugin contract (Section 6), owns its own DB namespace, emits bus events, and exposes MCP tools. Kernel provides the substrate (bus, auth, role registry, plugin loader); `sos-datalake` provides credential management, ingest orchestration, and the query surface. Each source adapter is itself a plugin living under `sos-datalake` — `datalake-adapter-gsc`, `datalake-adapter-stripe`, etc.

"Every plugin consumes it" does not mean "it's kernel." It means "it's a well-placed service with a clean contract." Kernel upgrades stay small and safe; datalake upgrades stay scoped to the datalake service and its adapters.

### Why datalake-first beats query-on-demand

Every customer-facing plugin (GAF, AgentLink, DentalNearYou, TROP, future forks) needs business signal: organic traffic, revenue, CRM state, R&D labor attribution. Query-on-demand — GSC API on page load, Stripe on dashboard render, QBO on monthly report — does not scale past one customer per plugin. Cold API latency on every render, quota exhaustion under load, no cross-source joins without application-code assembly, credential storage and rate-limit logic duplicated across plugin directories.

The datalake flips this. Data is pulled once on a schedule, normalized into a typed events table, and every plugin reads from the lake — fast, pre-joined, no API call on the hot path. Trade-off is explicit: **lake is eventual, not live**. A GSC query is 6h stale; a Stripe webhook is near-real-time via reconciliation. Plugins that need live signal call the API directly; plugins that need historical analysis and cross-source joins hit the lake. Both coexist. `mcp__sos_datalake__latest_ingest` returns staleness so callers choose.

### What we commit to doing once, in one place

Per-workspace credential encryption, consent tracking (PIPEDA), ingest audit trail, cross-tenant isolation, rate-limit circuit breakers, backfill semantics — these are solved in `sos-datalake` once and every plugin inherits them for free. GAF declares `datalake_sources: [gsc, ga4, qbo, github]` in its manifest; the service provisions the adapters, credentials, and MCP tools scoped to GAF's workspace. AgentLink declares `[ghl, stripe]`; gets a different subset. Adding a tenth source in six months = write one adapter plugin, zero changes to existing customer plugins.

---

## 2. The Adapter Contract

Every data source is its own plugin adapter, living at `SOS/plugins/datalake-{source}/`. The kernel treats each adapter as a first-class plugin with its own `plugin.yaml`. Plugins that consume a source declare it in their manifest; the kernel kernel-loads the adapter on first use.

New field in `plugin.yaml` for consuming plugins:

```yaml
datalake_sources:
  - gsc
  - bing
  - ga4
  - ads
  - ghl
  - stripe
  - qbo
  - github
  - boast   # placeholder — adapter shape defined, no live API until partnership signs
```

Each adapter's `plugin.yaml` declares:

```yaml
# plugins/datalake-gsc/plugin.yaml
name: datalake-gsc
version: 1.0.0
display_name: Google Search Console Adapter
adapter_for: datalake
source: gsc
api_base_url: https://searchconsole.googleapis.com/webmasters/v3
auth:
  type: oauth2
  scopes:
    - https://www.googleapis.com/auth/webmasters.readonly
  token_url: https://oauth2.googleapis.com/token
ingest_schedule: "0 */6 * * *"       # every 6 hours
backfill_window_days: 487             # 16 months (GSC API max)
raw_table: datalake_gsc_raw
normalized_tables:
  - datalake_gsc_metrics
retention_raw_days: 548               # 18 months
retention_normalized: forever
rate_limit:
  strategy: exponential_backoff
  base_delay_ms: 1000
  max_delay_ms: 60000
  max_retries: 5
  circuit_breaker_threshold: 3        # consecutive failures before open
  quota_exceeded_status: quota_exceeded
```

Adapters expose no HTTP routes of their own. They are ingest workers only — one Cloudflare Worker per source, triggered by Cron Triggers. The kernel's MCP query surface is the read layer.

---

## 3. Storage Schema (SQL DDL)

All tables below live in the `sos-datalake` service's DB namespace (prefix `datalake_`). The service owns them; the kernel does not.

### 3.1 Envelope encryption — per-workspace DEK

Single-KMS encryption is a single point of total compromise (one leak exposes every workspace). Instead: envelope encryption. One master key in Cloudflare KMS. One Data Encryption Key (DEK) per workspace, generated at workspace creation, wrapped by the master, stored as a row. Purge a workspace → delete the DEK row → all that workspace's credentials and encrypted data are irrecoverable without touching any other workspace.

```sql
CREATE TABLE workspace_keys (
  workspace_id   INTEGER PRIMARY KEY,
  encrypted_dek  BYTEA NOT NULL,         -- DEK encrypted by master (from CF KMS)
  master_key_id  TEXT NOT NULL,          -- which master-key version wrapped this DEK
  created_at     TIMESTAMPTZ DEFAULT now(),
  rotated_at     TIMESTAMPTZ,
  purged_at      TIMESTAMPTZ             -- set on workspace teardown; DEK then nulled
);
```

DEK is unwrapped at use time only, never cached in plaintext, never stored in Supabase unencrypted. PIPEDA right-to-erasure = one `DELETE FROM workspace_keys WHERE workspace_id = X` followed by a cascade purge job (Section 3.4).

### 3.2 Consent as its own paper trail

PIPEDA requires a durable audit record. The consent table survives purge — metadata only, no secret content.

```sql
CREATE TABLE datalake_consents (
  id             SERIAL PRIMARY KEY,
  workspace_id   INTEGER NOT NULL,
  source         TEXT NOT NULL,
  scope          JSONB NOT NULL,          -- OAuth scopes + purpose strings + version
  consented_at   TIMESTAMPTZ NOT NULL,
  revoked_at     TIMESTAMPTZ,
  purged_at      TIMESTAMPTZ,
  purge_job_id   INTEGER,                 -- reference to background purge worker run
  purge_on_revoke BOOLEAN NOT NULL DEFAULT TRUE,  -- per-source config; default true for OAuth
  UNIQUE (workspace_id, source, consented_at)
);
```

Two distinct purge paths, both represented:

- **Credential purge** (admin disconnects source): immediate — revoke OAuth, mark `revoked_at`, enqueue credential-blob delete. DEK stays because other sources may still use it.
- **End-user data purge** (future, v2): scoped via `entity_id` on `datalake_events`. Schema supports it today; tooling deferred to v2.

### 3.3 Credentials (encrypted at rest via workspace DEK)

```sql
CREATE TABLE datalake_source_credentials (
  id                SERIAL PRIMARY KEY,
  workspace_id      INTEGER NOT NULL REFERENCES workspace_keys(workspace_id),
  customer_id       TEXT NOT NULL,
  source            TEXT NOT NULL,
  credential_type   TEXT NOT NULL,     -- oauth_token | api_key | service_account
  encrypted_blob    BYTEA NOT NULL,    -- encrypted with workspace's DEK (envelope)
  scope             TEXT[],
  refresh_token_ref TEXT,              -- pointer to KV for short-lived refresh cache
  consent_id        INTEGER NOT NULL REFERENCES datalake_consents(id),
  created_at        TIMESTAMPTZ DEFAULT now(),
  revoked_at        TIMESTAMPTZ,
  UNIQUE (workspace_id, customer_id, source)
);
```

### 3.4 Unified events table (NOT per-source normalized)

Per-source normalized tables (one per source) explicitly rejected as an architecture. Reasons:

1. Cross-source joins are where the value is. Per-source tables force N-way joins in application code; unified table is one query.
2. Adding a source = new adapter, not new table + new join logic.
3. Schema drift is absorbed in normalization, not propagated to consumers.

```sql
-- The single table every source normalizes INTO
CREATE TABLE datalake_events (
  id            BIGSERIAL PRIMARY KEY,
  workspace_id  INTEGER NOT NULL,
  customer_id   TEXT NOT NULL,
  source        TEXT NOT NULL,           -- gsc | bing | ga4 | ads | ghl | stripe | qbo | github | boast
  entity_type   TEXT NOT NULL,           -- query | page | session | event | contact | opportunity | payment | transaction | commit | ...
  entity_id     TEXT,                    -- source-native identifier where applicable
  metric        TEXT NOT NULL,           -- clicks | impressions | revenue | commits | ...
  value         NUMERIC,
  value_json    JSONB,                   -- multi-dim values (e.g. {device, country, amount})
  ts            TIMESTAMPTZ NOT NULL,    -- when the event actually happened in source
  ingested_at   TIMESTAMPTZ DEFAULT now(),
  ingest_run_id INTEGER REFERENCES datalake_ingest_runs(id)
);

CREATE INDEX datalake_events_workspace_customer_ts
  ON datalake_events(workspace_id, customer_id, ts DESC);
CREATE INDEX datalake_events_source_metric_ts
  ON datalake_events(source, metric, ts DESC);
CREATE INDEX datalake_events_entity
  ON datalake_events(entity_type, entity_id);
```

### 3.5 Raw storage (lossless JSONB, per-source for debugging)

```sql
CREATE TABLE datalake_raw (
  id             BIGSERIAL PRIMARY KEY,
  workspace_id   INTEGER NOT NULL,
  customer_id    TEXT NOT NULL,
  source         TEXT NOT NULL,
  endpoint       TEXT NOT NULL,
  payload        JSONB NOT NULL,
  fetched_at     TIMESTAMPTZ DEFAULT now(),
  ingest_run_id  INTEGER REFERENCES datalake_ingest_runs(id)
);

CREATE INDEX datalake_raw_workspace_source_fetched
  ON datalake_raw(workspace_id, source, fetched_at DESC);
```

Retention: 18 months raw (configurable per adapter in its `plugin.yaml`), forever on `datalake_events` (or until PIPEDA purge).

### 3.6 Scheduled summaries (materialized aggregates)

Dashboards and partner digests do not query `datalake_events` directly — too hot for live traffic. Scheduled job runs daily at 03:00 UTC producing per-customer per-source aggregates.

```sql
CREATE TABLE datalake_summaries (
  id              BIGSERIAL PRIMARY KEY,
  workspace_id    INTEGER NOT NULL,
  customer_id     TEXT NOT NULL,
  source          TEXT NOT NULL,
  summary_type    TEXT NOT NULL,        -- daily_rollup | weekly_rollup | funnel | trend
  summary_date    DATE NOT NULL,
  metrics_json    JSONB NOT NULL,
  computed_at     TIMESTAMPTZ DEFAULT now(),
  UNIQUE (workspace_id, customer_id, source, summary_type, summary_date)
);
```

### 3.7 Ingest audit

```sql
CREATE TABLE datalake_ingest_runs (
  id            SERIAL PRIMARY KEY,
  workspace_id  INTEGER NOT NULL,
  customer_id   TEXT NOT NULL,
  source        TEXT NOT NULL,
  started_at    TIMESTAMPTZ DEFAULT now(),
  completed_at  TIMESTAMPTZ,
  rows_inserted INTEGER,
  status        TEXT NOT NULL,  -- success | partial | failed | quota_exceeded | skipped
  error_message TEXT,
  scheduler     TEXT NOT NULL,  -- cron | manual | backfill | webhook_reconcile
  window_start  TIMESTAMPTZ,
  window_end    TIMESTAMPTZ
);
```

> **Gate feedback applied 2026-04-24 (Athena, positions 1+2+3):** envelope encryption with per-workspace DEK replaces single-KMS pattern; unified `datalake_events` table replaces per-source normalized tables; `datalake_consents` as its own PIPEDA paper trail that survives purges. Original subagent draft had per-source `datalake_{source}_*` normalized tables — that would have forced cross-source joins into application code and duplicated schema drift. Single events table is the correct pattern; raw JSONB per-source preserved for debugging, but normalized data lives in one place.

### 3.8 Source-to-events mapping reference

Each adapter's normalization step converts raw JSONB into `datalake_events` rows. Canonical `entity_type` and `metric` vocabulary per source (adapter is responsible for honoring this mapping; the events table is otherwise source-agnostic):

| Source | entity_type values | metric values (examples) | value_json keys |
|---|---|---|---|
| `gsc` | `query`, `page` | `clicks`, `impressions`, `ctr`, `avg_position` | `{device, country, date}` |
| `bing` | `query`, `page` | `clicks`, `impressions`, `ctr`, `avg_position` | `{device, country, date}` |
| `ga4` | `session`, `event`, `page` | `sessions`, `users`, `conversions`, `event_count` | `{event_name, source, medium, campaign, country, device_category}` |
| `ads` | `campaign`, `ad_group`, `keyword` | `impressions`, `clicks`, `cost_micros`, `conversions`, `conversion_value` | `{campaign_id, ad_group_id, keyword_text}` |
| `ghl` | `contact`, `opportunity` | `created`, `updated`, `stage_change`, `value_change` | `{pipeline_id, stage_id, status, email, phone, tags}` |
| `stripe` | `payment`, `subscription`, `invoice`, `refund` | `amount`, `status_change` | `{currency, payment_method, stripe_customer_id}` |
| `qbo` | `transaction` | `amount` | `{txn_type, account_name, class_name, vendor_name, customer_name}` |
| `github` | `commit`, `pull_request`, `issue` | `additions`, `deletions`, `created`, `merged`, `closed` | `{repo_full_name, sha, author_login, author_email, message_summary}` |
| `boast` | `engagement`, `claim` | `status_change`, `filed`, `recovered` | `{external_id, record_type, raw_payload}` (schema TBD until partnership signs) |

`entity_id` on `datalake_events` is the source-native identifier (GSC query string, GA4 session_id, Stripe payment_id, GitHub commit SHA, etc.). Same entity can appear multiple times with different metrics.

A single fact from any source becomes one or more `datalake_events` rows. A Stripe payment produces one row per metric the adapter cares about (amount, status_change). A GSC daily snapshot for a page produces one row per metric (clicks, impressions, ctr, avg_position). The unified shape composes cleanly in queries; per-source schema drift is absorbed in the adapter's normalization code, never in consumer queries.

---

## 4. Ingestion Layer

One Cloudflare Worker per source, triggered by Cron Triggers. Workers are stateless; all coordination happens through `datalake_source_credentials` and `datalake_ingest_runs`.

| Worker | Schedule | Mode | Notes |
|---|---|---|---|
| `ingest-gsc-worker` | `0 */6 * * *` | poll | Backfills 487 days on first connect |
| `ingest-bing-worker` | `0 * * * *` | poll | Bing API allows hourly; no webhook |
| `ingest-ga4-worker` | `0 */4 * * *` | poll | GA4 data processing lag ~2–4h |
| `ingest-ads-worker` | `0 */4 * * *` | poll | Ads data finalized at 4h mark |
| `ingest-ghl-worker` | webhook + `0 */6 * * *` | hybrid | Webhook-first; cron reconciles missed events |
| `ingest-stripe-worker` | webhook + `0 */6 * * *` | hybrid | Webhook-first; cron catches gaps |
| `ingest-qbo-worker` | `0 2 * * *` | poll | QBO API is slower; daily sufficient |
| `ingest-github-worker` | `0 * * * *` | poll | Commits, PRs, issues hourly |
| `ingest-boast-worker` | `0 0 * * *` | placeholder | No-op until partnership signs |

**Per-worker execution loop:**

1. Query `datalake_source_credentials WHERE source = {source} AND revoked_at IS NULL`
2. For each credential, INSERT a `datalake_ingest_runs` row (`status = 'running'`)
3. Decrypt credential from `encrypted_blob` using workspace key from KV
4. Pull data from source API for the configured window (`window_start`, `window_end`)
5. INSERT raw payload into `datalake_{source}_raw` (JSONB, lossless)
6. UPSERT normalized rows into `datalake_{source}_*` tables by their unique key
7. UPDATE `datalake_ingest_runs` → `status = 'success'`, `rows_inserted`, `completed_at`
8. Emit `sos:event:datalake:{source}:ingested` on the SOS bus with `{workspace_id, customer_id, rows_inserted}`
9. On API rate limit: exponential backoff (base 1s, max 60s, 5 retries); after circuit opens, UPDATE run to `quota_exceeded`, return — retry next schedule

**Backfill behavior:** on first credential, `scheduler = 'backfill'`, window is pulled in 7-day chunks backward to `backfill_window_days`. Subsequent runs use `scheduler = 'cron'` with a 25h look-back window to catch any lag.

---

## 5. Query Surface (MCP Tools)

Tools on `mcp__sos_datalake__*`, exposed on the SOS MCP bus (:6070). All tools enforce cross-tenant isolation at the SQL level via `workspace_id + customer_id` scope derived from the caller's token.

| Tool | Purpose |
|---|---|
| `query(customer_id, sql)` | Read-only SQL against the caller's scoped lake; rejects any write or cross-tenant ref |
| `compare_periods(customer_id, source, metric, period_a, period_b)` | Structured period-over-period comparison (returns delta, %, direction) |
| `trend(customer_id, source, metric, window)` | Time series for a metric over a rolling window |
| `anomalies(customer_id, source, threshold)` | Statistical anomaly detection (z-score) across a source's metrics |
| `funnel(customer_id, steps[])` | Cross-source funnel: e.g. GSC impression → GA4 session → Stripe payment |
| `latest_ingest(customer_id, source)` | Returns `{last_run_at, status, rows_inserted}` — staleness check before using lake |

**Role gating:** token's `roles` claim is checked against a permission matrix.

| Role | `query` | `compare_periods` / `trend` / `anomalies` / `funnel` | `latest_ingest` |
|---|---|---|---|
| `observer` | read | read | read |
| `worker` | read | read | read |
| `knight` | read | read | read |
| `coordinator` | read | read | read |
| `principal` / `admin` | all workspaces | all workspaces | all workspaces |

`query` is additionally row-level-gated: the SQL is parsed for a `customer_id` literal; if it references a customer outside the caller's workspace, the kernel rejects it before execution.

---

## 6. Consent + PIPEDA

At customer onboarding, an OAuth flow is initiated per declared source. The scope list is shown to the customer verbatim. On authorization, the kernel writes a `datalake_source_credentials` row with `consent_signed_at` and `consent_scope` (JSONB: `{scopes, purpose, version, ip}`). This is the PIPEDA consent record.

**Revoke:** `POST /datalake/credentials/{id}/revoke` — immediately sets `revoked_at = now()`, nulls the token in KV. Ingest workers check `revoked_at IS NULL` before processing; a revoked credential is never fetched again. Response is synchronous: 200 confirms revocation before returning.

**Right to erasure (PIPEDA):** `POST /datalake/credentials/{id}/purge` initiates a 30-day grace period. After 30 days, a purge worker DELETEs all `datalake_*_raw` and `datalake_*_metrics/events/...` rows for `(workspace_id, customer_id)`. The `datalake_ingest_runs` rows are retained (zero PII) with `status = 'purged'` as the audit trail of the purge itself.

**Encryption:** credentials encrypted with per-workspace symmetric keys via pgcrypto `pgp_sym_encrypt`. Keys stored in Cloudflare KV (`datalake:key:{workspace_id}`), never in Supabase. Blast radius of a compromised key is one workspace.

---

## 7. Integration with Existing Primitives

**Engrams (Section 1C):** when an ingest worker detects an anomaly (metric drops >30% day-over-day), it writes an engram with `tier = 'entity'`, `entity_id = customer_id`, `tags = ['datalake', 'anomaly', source]`. Knights observing the bus pick these up for investigation. Engrams attach to Phase 3 fractal nodes via `node_id` in `properties`.

**Inkwell pages (Section 1D / Inkwell RBAC):** customer-facing dashboards and partner digest pages are Astro server components that read directly from datalake normalized tables. No API call on render. The "last 28 days traffic" widget hits `datalake_gsc_metrics` with a date range filter. Partner digests (PDF or page) pull from `datalake_stripe_payments` + `datalake_ghl_opportunities` for pipeline-to-revenue attribution.

**Business graph (Section 1E / Section 7):** datalake facts become `outcome` nodes in `business_nodes`. A Stripe payment UPSERTS a node with `entity_type = 'deal'`, `properties.amount`, `properties.source = 'stripe'`. Cross-source correlations (GSC impression surge followed by GHL contact spike) become `business_edges` with `edge_type = 'influenced'` and `weight` derived from temporal proximity. This makes the graph self-populating from real data rather than manually curated.

**Coherence metrics (Section 5):** instead of ad-hoc queries, the observability layer reads from datalake. Knight coherence scores can incorporate real customer outcomes: a knight that triggered an SR&ED claim (QBO + GitHub evidence) that resulted in a filing gets a coherence point in the structured record.

**37-CDAP upsell:** at customer consent for GSC + QBO, a background job pre-scans their datalake slice to generate a personalized CDAP scan preview — "your site has 3 months of GSC data, your QBO shows $180k in eligible spend" — before the formal engagement starts.

---

## 8. Plugin Contract

Updated `plugin.yaml` field:

```yaml
datalake_sources:
  - gsc       # required for SEO intelligence
  - ga4       # required for conversion tracking
  - ghl       # required for CRM events
  - stripe    # required for revenue tracking
  - qbo       # required for SR&ED eligibility (GAF-specific)
  - github    # required for R&D labor attribution (GAF-specific)
```

**GAF** declares all six above (plus `bing`, `ads` for full search coverage = 8 total).
**AgentLink** declares `[ghl, stripe]` — no QBO or GitHub needed for showing management.
**DentalNearYou** declares `[ghl, stripe, ga4, gsc]` — practice CRM + revenue + web.
**Future plugins** declare exactly what they need; the kernel provisions exactly that — no more, no less.

The kernel reads `datalake_sources` at plugin registration and creates the credential OAuth flows for each declared source. If a source adapter isn't installed, registration fails with a clear error: `datalake adapter datalake-{source} not found`.

---

## 9. Test Plan

| Test | Pass Condition |
|---|---|
| Adapter contract | New adapter added at `plugins/datalake-newco/` — existing plugins unaffected, kernel loads adapter without changes |
| Credential isolation | workspace-A token: `SELECT * FROM datalake_source_credentials WHERE workspace_id != A` returns 0 rows (RLS) |
| Ingest idempotency | Run ingest worker twice for same window — normalized table row count unchanged (UPSERT on unique key) |
| Rate limit handling | Mock API returns 429 → worker backs off, marks run `quota_exceeded`, retries on next cron schedule |
| Consent revoke | POST revoke → `revoked_at` set → next ingest cycle skips credential → no new raw rows for that customer |
| PIPEDA purge | POST purge → 30d later, purge worker runs → zero rows for customer in all `datalake_*` tables → `datalake_ingest_runs.status = 'purged'` retained |
| Cross-tenant query | Kaveh's token (workspace: gaf) calls `query(customer_id='agentlink-customer-1', ...)` → 403 |
| Role gate | `observer` token: `query` succeeds; attempt to call a mutation → 403 |
| Backfill | First credential for GSC: ingest runs with `scheduler = backfill`, pulls 487 days, all chunks inserted in `datalake_gsc_raw` |
| Staleness check | `latest_ingest(customer_id, 'gsc')` returns `{last_run_at, status: 'success'}` within 6h window |

---

## 10. Build Order

| # | Item | Owner | Days |
|---|---|---|---|
| 1 | Kernel tables: `datalake_source_credentials`, `datalake_ingest_runs`, encryption setup (pgcrypto + KV key provisioning) | Kasra | 2 |
| 2 | OAuth + consent flow — generic, reusable per-source; PIPEDA consent record write | Kasra | 2 |
| 3 | GSC adapter — first, validates the full pattern (credentials → raw → normalized → event emit) | Kasra | 2 |
| 4 | Bing adapter | Kasra | 1 |
| 5 | GA4 adapter | Kasra | 2 |
| 6 | Google Ads adapter | Kasra | 1 |
| 7 | GHL adapter — webhook receiver + reconciliation cron (partial webhook infra exists) | Kasra | 1 |
| 8 | Stripe adapter — webhook receiver + reconciliation cron (partial webhook infra exists) | Kasra | 1 |
| 9 | QBO adapter — daily poll, class-level transaction normalization for SR&ED cost allocation | Kasra | 2 |
| 10 | GitHub adapter — commits, PRs, issues; repo scoping by `customer_id` | Kasra | 1 |
| 11 | Boast/Leyton placeholder adapter — no live API; schema + stub worker that no-ops | Kasra | 0.5 |
| 12 | MCP query tools + role gate (`query`, `compare_periods`, `trend`, `anomalies`, `funnel`, `latest_ingest`) | Kasra + Loom | 2 |
| 13 | Business graph integration — ingest workers upsert `business_nodes` + `business_edges` on each run (blocked on Section 7 landing) | Kasra | 2 |

**Total:** ~19.5 engineering days. Kasra solo or with subagent workers on adapters 4–10 in parallel after GSC acid test.

---

## 11. Open Questions

1. **Encryption granularity:** per-workspace KMS key (recommended — blast radius one workspace) vs. single KMS key + per-row IV. Decision needed before kernel table migration runs.
2. **QBO ingest strategy:** normalize in the same run or separate runs (raw first, normalize async)? Raw + normalize in one run is simpler; separate runs gives replayability if normalization logic changes. Leaning separate for SR&ED auditability.
3. **Cross-source joins:** materialized views refreshed every 6h vs. on-read joins. Materialized views win at dashboard scale; on-read joins win at flexibility. Recommend materialized for the funnel tool, on-read for ad-hoc `query`.
4. **Raw retention policy:** 18 months raw recommended (covers two fiscal years for CRA audits); normalized kept forever. Open: does Boast/Leyton have a different retention requirement? Placeholder adapter should default to 6 years (GAF compliance profile) until partnership defines terms.
5. **GHL/Stripe webhook vs. polling fallback:** webhook-first is right, but Cloudflare Workers have a 30s timeout. Missed webhooks rely on the 6h reconciliation cron. Is the 6h gap acceptable for AgentLink showing events (probably yes) and Stripe payment events (probably yes — Stripe webhooks are reliable at >99.9%)?
