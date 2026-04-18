# sos-dev Scope & Boundary — Mumega Customer Portal, OSS-Aware

**Author:** sos-dev
**Date:** 2026-04-16
**Status:** Draft, needs Hadi approval
**Companion to:** `/home/mumega/docs/plans/2026-04-16-app-mumega-dashboard.md` (Kasra)

## Why this document exists

Kasra's plan defines the Mumega customer portal (`app.mumega.com/dashboard`). I work on SOS — the runtime underneath. This doc draws the line between what I will build on the SOS side and what stays in Mumega's proprietary layer, with one constraint running through every decision:

> **SOS may go open source. Mumega is the business.**

Every piece of work I own gets categorised by whether a stranger running a totally different business on a fork of SOS would also want it. If yes → SOS. If no → Mumega.

## The principle (the one-line rule)

**Generic, protocol-grade, reusable → belongs in SOS (open source candidate).
Specific to Mumega's customers, branding, pricing, UX language → belongs in Mumega (proprietary).**

Applied:

| Concept | OSS (SOS) | Proprietary (Mumega) |
|---|---|---|
| Agent Card **schema** | ✅ shipped in SOS | |
| Agent Card **values** for Viamar/TROP/DNU | | ✅ tenant config |
| `/my/tasks` **endpoint spec** (OpenAPI) | ✅ in SOS | |
| `/my/tasks` **implementation body** (Stripe lookup, billing logic) | | ✅ on SaaS service Kasra owns |
| Task status enum (`backlog, claimed, done, …`) | ✅ in SOS contract | |
| Translation "backlog → Coming up" | | ✅ Mumega frontend |
| Wallet balance field (cents) | ✅ in SOS | |
| "Budget remaining" UI label | | ✅ Mumega |
| Bus event shape (`task_completed` JSON) | ✅ in SOS | |
| Which bus events trigger a customer email | | ✅ Mumega policy |
| Magic-link auth **protocol** (JWT shape) | ✅ in SOS (optional) | |
| Magic-link **Resend integration + sender domain** | | ✅ Mumega |
| Stripe customer integration as **interface** | ✅ in SOS (adapter) | |
| Mumega Stripe **account + prices** | | ✅ Mumega |
| Marketplace **protocol** ($MIND) | ✅ in SOS (whitepaper is already public) | |
| Mumega commission rates | | ✅ Mumega |
| Dashboard framework (tenant config-driven) | ✅ could ship as SOS reference impl | |
| `app.mumega.com` specific portal | | ✅ Mumega |
| `status.mumega.com` public page | | ✅ Mumega (but mumega-watch Worker could be OSS template) |

## What I will handle (SOS side, sos-dev scope)

Each item tagged with its release (v0.4.x from the earlier plan) and its OSS/Mumega classification.

### 1. v0.4.0 "Contracts" — fully OSS

- **Agent Card v1 JSON Schema + Pydantic model + contract tests** (already shipped today)
- **Message schema registry** — every bus message type has a JSON Schema, `source` structurally required; kills flat-identity by construction
- **OpenAPI specs per service** — squad :8060, mirror :8844, saas :8075 (including the `/my/*` endpoints Kasra's portal consumes), bus gateway :6070, engine :6060, memory :6061, content :8020, dashboard :8090
- **Contract tests** in `tests/contracts/` covering every schema and endpoint
- **Error taxonomy** — machine-readable codes (SOS-1xxx auth, 2xxx routing, 3xxx task, 4xxx wallet, 5xxx fmaap, 6xxx mirror, 7xxx tenant, 8xxx platform)

**Handoff to Kasra:** his frontend renders friendly messages from typed SOS error codes. "SOS-4001" → "Insufficient budget — add funds to continue." Translation table lives in his frontend; the codes live in SOS.

### 2. v0.4.1 "Provider Matrix" — fully OSS

- **Provider Card v1 schema** (same pattern as Agent Card)
- **Redis health table** `sos:provider:{name}` with 60s probe cron
- **Read-before-dispatch routing** — select cheapest healthy provider in required tier
- **Per-provider circuit breakers** with exponential backoff
- **Pre-expiry OAuth refresh**
- **Fallback order as YAML config** (not hardcoded)
- **FMAAP Metabolism gate extension** — actions blocked when no healthy provider

**Handoff to Kasra:** his frontend can surface provider state in a Developer-toggle panel if desired; the default Mumega customer UX hides it entirely. Customers see "your team is operating normally" or "your team is temporarily unavailable, we're restoring service."

### 3. v0.4.2 "Observability Plane" — OSS core, Mumega-branded deployment

- **`breakables.yaml`** inventory schema — OSS
- **Breakable Card JSON Schema** — OSS
- **`mumega-watch` Cloudflare Worker scaffold** — code is OSS template; Mumega's deployment (with Mumega's breakables list + CF account) is Mumega
- **Bus event `sos:event:breakable_down`** — OSS
- **Escalation ladder (Discord → Telegram → phone → auto-restart)** — ladder mechanism is OSS; specific sinks (Mumega's Discord webhook URL, Mumega's Twilio account) are Mumega
- **`sos status` CLI** — OSS
- **`status.mumega.com` public page** — Mumega deploys; the Worker source is OSS

**Handoff to Kasra:** Settings > Developer toggle can embed the status page; by default customers see a simplified "all systems nominal" banner.

### 4. Per-agent identity migration — fully OSS

- `AGENT_NAME` export standardised in every tmux launch
- Per-agent bus tokens for internal agents (same pattern as customer agents)
- sos-claude connector URL-per-identity (already did for `hadi`)
- Accounting ledger gets real per-agent attribution
- Closes issues #20 (MCP send text drop) and #21 (identity flatness) by construction

### 5. sos-medic — fully OSS

- Home at `sos/agents/sos-medic/` (already built today)
- CLAUDE.md instructions, CHANGELOG, BUG_REPORT format, EXPERIENCE.md, incidents/
- Deterministic toolkit: `check-all-pipes.sh`, `journal-tail.sh`, `tokens-audit.sh`
- Wake routing live, prompt-guard fix shipped

**This is an OSS pattern.** Any SOS fork can have its own medic with its own incidents log. Mumega-specific incidents stay in our `incidents/` but the framework is shared.

### 6. SOS Engine Dashboard (`app.mumega.com/sos`) — mixed

Kasra's plan makes `/dashboard` the customer portal with zero SOS/Redis/MCP language. The infrastructure view lives at a different subpath. Scope:

- **Audience:** developers, ops, coordinators (Hadi, kasra, sos-dev, sos-medic, codex)
- **Content:** agent status, bus health, squad coherence, provider matrix, wake queue, Redis stats, disk/memory, incident feed, task board
- **Tech:** read-only dashboard, pulls from SOS APIs (not `/my/*` — those are Kasra's customer-scoped endpoints; this uses the admin-scoped surface)
- **OSS classification:** the *framework* (component library, status widgets, incident view) is OSS because any SOS fork needs an ops dashboard; Mumega's deployment with its own branding/auth is Mumega

**I will draft the plan next** as `docs/plans/2026-04-16-sos-engine-dashboard.md` once Kasra confirms the `/sos` subpath is his preferred landing.

### 7. Ongoing — pipe and bus maintenance (OSS)

This is my standing role. Today alone:
- Fixed SEC-001 auth regression in `sos/services/dashboard/app.py` + `/home/mumega/mirror/mirror_api.py`
- Fixed nginx routing for `app.mumega.com → :8090`
- Fixed wake-daemon prompt-guard capture window (3 → 10 lines)
- Registered kasra + trop as typed Agent Cards on Redis
- Minted per-agent claude.ai token for hadi identity

All generic infrastructure — stays in SOS.

## What I will NOT handle (Kasra's / Mumega's domain)

Explicitly out of my scope — protects the boundary:

- **`sos/services/saas/app.py`** — per Kasra's 22:07 message, "don't touch app.py — that's my file"
- **The `/my/*` endpoint **implementations**** (request bodies, DB queries, Stripe calls) — I own the specs (OpenAPI), he owns the bodies
- **Inkwell frontend pages** at `/dashboard/*` — his component work, his language translation layer
- **Customer-facing language choices** — "Budget remaining", "Marketing Team", "Urgent", etc. are frontend-only
- **Per-tenant config values** — which KPIs Viamar vs TROP vs DNU shows
- **Stripe account integration** — which Mumega Stripe account, which products/prices
- **Magic-link auth flow implementation** — the Resend integration + mumega-edge Worker are Kasra's layer
- **Assistant UI chat implementation** — frontend routing of user intent into task creation
- **Commercial decisions** — pricing, plans, commission, which features ship to which plan tier
- **Customer onboarding UX** — the wizard, the welcome emails, the tenant provisioning Stripe webhooks
- **Marketing site + brand voice** — different team

## The handoff interface (what I give Kasra)

Kasra's frontend depends on stable, typed contracts from SOS. My job is to make those contracts load-bearing. Specifically:

1. **Typed OpenAPI for every `/my/*` endpoint** — his frontend codegens types from our spec; drift is impossible
2. **Versioned bus events** with JSON Schema — his Assistant UI can `sos:event:task_completed` subscribe and surface completions in real time without polling
3. **Error codes** — structured `{code, message, details}` responses → his UI renders friendly copy from a translation table he owns
4. **Agent Cards** from `sos:registry:*` — his Team page reads these to show squad member names, skills, status; translation table renames `seo` → `Marketing Team`
5. **Breakable Cards** from observability plane — optional Developer toggle surfaces them; default UX hides
6. **Provider Cards** — same as above; customer never sees, devs can inspect
7. **Auth protocol** — customer magic-link session yields a bus-compatible token; his frontend makes API calls with it; backend enforces tenant scoping automatically

Boundary at a glance:

```
        Kasra's Mumega portal (proprietary)
        ─────────────────────────────────────
        Inkwell frontend · Assistant UI · Shadcn · magic link
        Stripe · Resend · mumega-edge · Cloudflare Pages
               │
               │  OpenAPI typed HTTP calls
               │  Bus event subscriptions
               ▼
        ─────────────────────────────────────
        SOS kernel & services (OSS candidate)
        ─────────────────────────────────────
        squad · mirror · saas · dashboard (stopgap) · bus gateway
        MCP SSE · economy · wallets · FMAAP · calcifer · sos-medic
        Agent Card · Message Schema · Provider Card · Breakable Card
        error taxonomy · mumega-watch Worker · status page Worker
```

## Decision items I need from you

These block me from finishing the v0.4 scope cleanly:

### Q1. SOS repo split — when?

Today everything lives in `/mnt/HC_Volume_104325311/SOS`. When we prepare to open-source, do we:
- (a) Extract an `sos-core` repo (public) and keep `mumega` (private) that depends on it, OR
- (b) Keep a monorepo with `sos/` public-ready and `mumega/` private subdirs (submodule or workspace pattern), OR
- (c) Fork today, move everything SOS-side to a public repo, Mumega keeps only the proprietary layer

Any of these work — the answer determines what "commit" means for each of my deliverables.

### Q2. Kasra's portal — OSS reference implementation or pure proprietary?

Two legitimate options:
- **(a) Proprietary Mumega:** the portal is a closed Mumega product. SOS ships only the `/my/*` API contracts; anyone else building on SOS builds their own portal.
- **(b) OSS reference:** ship a minimal portal as `sos-portal-reference` in the SOS org — generic Shadcn shell, reads `/my/*` endpoints, renders Agent Cards. Mumega forks it for the commercial product with their branding, tenant configs, Stripe hookup, marketing language.

My preference: **(b)**, because it multiplies SOS adoption. But (a) is the fastest path to Mumega revenue. Your call.

### Q3. `app.mumega.com/sos` ownership

Confirming: the SOS engine dashboard (developer/ops view) at `/sos` subpath is mine to build?

### Q4. `breakables.yaml` scope

Does Mumega publish one set of breakables (internal + external + tenant certs), or do each tenant's breakables get their own file? I'd argue one central file for infra + per-tenant `breakables.yaml` for customer certs/webhooks. Confirming before I commit to a shape.

### Q5. Escalation phone-call sink

Twilio exists. Do we have a Twilio number + sender already, or should I procure one? Needed for the L3 escalation rung.

## Ship plan — what I'll do next (in order)

1. **Wait for Q1-Q5 decisions** (unblocks how I commit)
2. **Finish v0.4 deliverable #2** — message schema registry (kills flat-identity structurally)
3. **Write `docs/plans/2026-04-16-sos-engine-dashboard.md`** — the ops dashboard at `/sos`
4. **Send Kasra the OpenAPI for `/my/*`** — so his Phase 1 has a typed backend
5. **Provision Cloudflare Worker + KV + D1 for `mumega-watch`** (or hand off to codex if infra provisioning sits there)
6. **Ship v0.4.0 alpha tag** — the contracts layer becomes the frozen surface Kasra's portal commits against

## One-line summary

My scope is **the SOS substrate** (kernel, services, contracts, observability, identity). Kasra's scope is **the Mumega product** (portal, language, branding, commerce). SOS could be open-sourced tomorrow without affecting Mumega's business because the boundary is drawn at the API contract, not at the codebase directory.
