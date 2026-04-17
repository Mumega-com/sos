# SOS Engine Dashboard — `app.mumega.com/sos`

**Author:** sos-dev
**Date:** 2026-04-16
**Status:** Proposed, Kasra requested (issue #19)
**Companion plan:** `/home/mumega/docs/plans/2026-04-16-app-mumega-dashboard.md` (Kasra's customer portal at `/dashboard`)

## What this is

The operator view of the organism. Not customer-facing. Agent status, bus health, squad coherence, wake queue, provider matrix, Redis stats, incident feed. The place a developer or ops agent goes to answer "why is the system acting weird" — in one page instead of six shells.

## What this is NOT

- Not a customer dashboard (that's `/dashboard`, Kasra's)
- Not a business-language surface — developer jargon is explicit and welcome here
- Not a control panel for now — read-only. Write operations come later via `sos status` CLI first, web later
- Not a log tailer — structured summaries only, with links to deeper views
- Not paginated tables of tasks (that's the customer portal's job at `/dashboard/work`)

## Audience

| Role | What they do here |
|---|---|
| Hadi | Check overall health in one glance; deep-dive when something's wrong |
| sos-dev | Monitor the plumbing, catch regressions the moment they happen |
| sos-medic | Triage incidents, confirm pipe states before acting |
| kasra | See that his backend assumptions match infrastructure reality |
| codex | Infrastructure-correct view of services and their backing systems |
| any auditor | Read-only window into SOS state without SSH access |

## Data sources (SOS APIs only — no direct SQL/Redis from browser)

| Source | Endpoint | What it provides |
|---|---|---|
| Squad | `:8060/squads`, `/tasks`, `/skills` | squad state, task board, skill matrix |
| Mirror | `:8844/stats`, `/recent/{agent}` | engram counts, recent memory entries per agent |
| SaaS registry | `:8075/tenants`, `/tenants/{slug}` | tenant status (admin-scoped endpoint; different from `/my/*`) |
| Bus gateway | `:6070/health` + MCP peers | active SSE sessions, registered agents |
| Redis | via a thin `GET /admin/redis/stats` on bus gateway (to be added) | key counts, memory usage, pubsub subscriber counts |
| Systemd | via `sos-status` CLI proxied through an admin endpoint | unit state, last log line |
| Calcifer | existing health events from `sos:channel:system:events` stream | service degradations, restarts |
| mumega-watch CF Worker (v0.4.2) | `status.mumega.com/api/status` | external breakables status — source of truth for anything external to the VPS |

**All endpoints except the CF Worker are on the VPS and require admin-scoped Bearer auth.** Not customer tokens.

## Pages

### 1. Overview — `/sos`

Single page, one screen, no scroll on a 1080p display. The answer to "is SOS healthy right now?"

Three row clusters:

**Row 1 — Heartbeat (deterministic, always visible)**
- Systemd units: count green / count red, with any red listed
- Bus gateway: active SSE sessions, message rate (last 5m)
- Calcifer last heartbeat age
- Disk/memory/Redis headline numbers

**Row 2 — Squad matrix**
- Active squads count
- Per-squad coherence bar (once living-graph schema lands in v0.5)
- Conductance heatmap per skill (once conductance routing lands in v0.4.1)

**Row 3 — Incident feed (last 5)**
- Pulled from mumega-watch D1
- Each row: severity icon, breakable id, duration, status (resolved / open)

### 2. Agents — `/sos/agents`

Table of every agent (from `sos:registry:*` — Agent Card v1 format).

Columns: name, role, type (tmux/openclaw/remote), model, warm_policy, last_seen, cache_state (when v0.4.1 lands), active squads, skills.

Click row → detail drawer with:
- Full Agent Card JSON
- Last 5 bus messages sent/received
- Active task claims
- Recent tool-call rate (from token-accounting ledger)

### 3. Bus — `/sos/bus`

- Active SSE sessions (count + list with age)
- Message rate per agent (last 5m sparkline)
- Wake queue depth (messages buffered, not yet delivered)
- Self-echo filter hit count (non-zero = identity bug lurking)
- Top N streams by XADD rate

### 4. Providers — `/sos/providers` (requires v0.4.1)

Provider matrix table. One row per provider in the `providers.yaml` config.

Columns: name, tier, status (🟢/🟡/🔴), last_probe, circuit_state, OAuth expires_at (if applicable), cost_per_Mtok.

Click row → probe log + last 10 failures.

### 5. Tasks — `/sos/tasks`

Admin view of squad tasks, complements Kasra's `/dashboard/work` (which is customer-scoped). Difference: this shows cross-tenant task state, assignee resolution, block/unblock chain, FMAAP gate results.

Filters: assignee, squad, status, tenant.

### 6. Incidents — `/sos/incidents`

List of incidents from mumega-watch D1 + sos-medic's `incidents/` dir.

Columns: date, severity, title, duration, resolver, pattern-class.

Links to the markdown postmortem when one exists.

### 7. Contracts — `/sos/contracts`

Read-only index of the schemas/specs shipped in v0.4.

Lists every JSON Schema under `sos/contracts/schemas/`, every OpenAPI spec under `sos/contracts/openapi/`, with version and last-updated-at. Click for the file contents rendered as a doc.

This surface is what makes "contracts" real to humans — without it, v0.4 is just files in git.

## Tech stack

- **Framework:** Inkwell (Astro 6 + React 19 islands) — same substrate as Kasra's portal. Lets us share the component library.
- **Components:** Shadcn UI — same 13 components. Admin-specific widgets added as needed (status badges, sparkline, heatmap, log viewer).
- **Auth:** admin-scoped Bearer token (separate from customer magic link). Matches the existing `kasra`-admin token shape; extend to multi-admin if/when more humans need access.
- **Data:** server-rendered by Inkwell at request time; client-side polls every 10s for live tiles (Row 1 of Overview, active SSE sessions on Bus page).
- **Deploy:** Cloudflare Pages as part of Inkwell build, same as the customer portal — different subpath + different auth middleware.

## Config-driven per viewer (minor)

The admin dashboard doesn't need deep per-user customization, but:
- Dark/light theme
- Default page (Overview vs whatever page the user last used)
- Which row clusters to show on Overview (for different ops roles)

Simple localStorage, no backend persistence.

## Implementation order (4 short phases, can run parallel to customer portal)

### Phase 1: Overview + Agents (2 sessions)
1. Auth middleware — admin Bearer check against the same token pool
2. Overview page with Row 1 (heartbeat) only — validates the data-source pipeline
3. Agents page — reads `sos:registry:*`, renders Agent Card v1 table

### Phase 2: Bus + Incidents (1 session)
4. Bus page — SSE session list, message rate, wake queue
5. Incidents page — pulls from mumega-watch D1 (depends on v0.4.2 shipping)

### Phase 3: Providers + Tasks (1 session, depends on v0.4.1)
6. Providers page — reads provider health table from Redis
7. Tasks page — admin view of squad tasks (new endpoint: `GET /admin/tasks` on squad service)

### Phase 4: Contracts + Polish (1 session)
8. Contracts index page — reads `sos/contracts/` directory via a thin server endpoint
9. Search, keyboard shortcuts, theme toggle

## What I need from Kasra

Three new endpoints on the SaaS service (he owns `/admin/*`, I consume):

| Endpoint | Returns | For page |
|---|---|---|
| `GET /admin/tenants` | all tenants with status (not just customer's own) | (optional, fills Tasks page tenant filter) |
| `GET /admin/redis/stats` | key counts, memory, pubsub subscribers | Bus page |
| `GET /admin/systemd/status` | unit states from `systemctl list-units` | Overview Row 1 |

Request filed as tasks on the squad/tenant board. Each is ~30-line additions to his SaaS service.

## What I need from codex

- Wake daemon to expose a `/metrics` endpoint: queue depth, rejection count, wake rate per agent
- Calcifer to publish structured events to `sos:channel:system:events` stream (already does partially)

## What I need from Hadi

| # | Question |
|---|---|
| E1 | Subpath `/sos` on `app.mumega.com`? Or separate subdomain like `engine.mumega.com`? Kasra's 22:07 message implied `/sos`. |
| E2 | Admin auth — one shared admin token for all ops, or per-ops-agent admin tokens? |
| E3 | Who owns the Overview-page KPI selection? Me by default, but if Kasra wants consistent KPI language across `/dashboard` and `/sos`, we should align. |
| E4 | Should this also be OSS-ready (reference impl for SOS forks) or Mumega-proprietary? My vote: OSS — same reasoning as customer portal. |

## Success criteria

A developer with SSH access to the VPS can, **without opening a terminal**:
1. See that every service is green or which one is red
2. See agent status for every tmux + openclaw agent
3. Read today's incident feed
4. Click into any agent and see its last 5 bus messages
5. Know whether the provider matrix is healthy
6. Confirm what contracts are shipped at what version

If I can debug a production issue from the browser before needing `journalctl`, it works.

## Non-goals for v1

- Writing operations (no "restart agent" buttons yet — CLI first, web later)
- Multi-tenant view (Mumega is currently single-tenant admin; when we have multiple ops teams for different customer groups, we revisit)
- Historical replay (incident feed is live + 90-day CF Worker retention; longer history needs a separate analytics plane)

## Relationship to Kasra's customer portal

**Same framework, different audience, different data endpoints, different auth.**

| | Kasra's `/dashboard` | My `/sos` |
|---|---|---|
| Tech | Inkwell + Shadcn | same |
| Auth | customer magic link | admin Bearer |
| Data | `/my/*` (tenant-scoped) | `/admin/*` + Redis + mumega-watch |
| Language | business ("Budget", "Team") | technical ("wallet_balance_cents", "squad_coherence") |
| Goal | business owner reads, acts | ops reads, diagnoses |
| Ships as | Mumega proprietary | OSS reference impl (see E4) |

Both consume from the same underlying SOS contracts — so v0.4's OpenAPI specs serve both. That's the leverage of the contracts layer.

## One-line summary

The engine dashboard is the `/sos` operator view — same Inkwell framework as the customer portal, different audience and auth. Phase 1 (Overview + Agents) ships in two sessions once v0.4 Agent Card contracts are landed. Everything after that hangs off v0.4.1 (provider matrix) and v0.4.2 (observability plane).
