# Section 5 — Observability, Operations, Alerting

**Scope:** metrics, dashboards, health checks, alerting, partner digests. Gives every role (human + agent) the signals they need to act without asking.

All surfaces are Next.js admin routes under `/admin/*` in the `gaf-app/web/` tree (or a separate `mumega-admin` Worker if we need isolation). Admin-tier role gate; Noor/partners/customers cannot reach these paths.

## 5.1 Service health dashboard

**Purpose:** one-page uptime view across SOS bus, Mirror API, Squad Service, Inkwell, MCP SSE server, Discord bots, dispatcher worker, Supabase.

**Signals:**
- systemd service status (per-user unit file state)
- HTTP health endpoints (`/api/health` where they exist)
- Cloudflare Worker heartbeat (hit `/cdn-cgi/trace` and check the right worker ID)
- Supabase connection ping (Postgres SELECT 1)
- Bus last-message timestamp per agent (stale > 6h = yellow, > 24h = red)

**Surface:** `/admin/health` — traffic-light grid, service name / status / last-seen-at / action-link.

**Alert rule:** service down > 60s → Discord `#ops-alerts` + SMS to Hadi via GHL.

**Owner:** Kasra. **Estimate:** 1 day.

## 5.2 Coherence metrics dashboard

**Purpose:** the FRC C variable made legible. Per-agent and per-knight.

**Metrics:**
- Tasks closed with positive-coherence engram (count, 7/30/90d trend)
- Alignment-under-pressure events (count, manual + automated flags)
- FRC violations flagged (count, by River / Athena / automated rule)
- Coherence score (normalized 0-1, per Section 1E formula)
- Tier (operational / canonical) + days-until-next-review

**Surface:** `/admin/coherence` — agent rows, knight rows, color-coded by trend. Click-through to per-agent engram history.

**Alert rule:** coherence score drop > 20% in 24h → Discord `#coherence-alerts` + wake River.

**Data source:** Mirror engram aggregation query (hourly cron); cached in `coherence_snapshots` table for trend comparison.

**Owner:** Kasra + Loom (query design). **Estimate:** 2 days.

## 5.3 Revenue dashboard (real-time)

**Purpose:** see the money move.

**Metrics:**
- GAF success fees collected (today / MTD / YTD)
- Digid AI engagement revenue (by engagement)
- DMAP per-plan revenue (OCI payouts)
- Partner referral commissions (pending / paid)
- Stripe Connect balances per partner
- Commission aging (how long pending per payout schedule)
- Forecast based on `opportunities` table × stage probability

**Surface:** `/admin/revenue` — top-line KPIs, charts (weekly/monthly), tables (per-partner, per-customer).

**Alert rule:** failed Stripe payout > 1h → `#ops-alerts`. Monthly revenue YoY drop > 15% → daily digest flag.

**Data source:** Stripe API (live) + Supabase `commissions` + `stripe_events` + `opportunities`.

**Owner:** Kasra. **Estimate:** 2 days.

## 5.4 SLA tracking per customer

**Purpose:** catch stuck cases before they hurt the customer relationship.

**Stages tracked (with targets):**

| Stage transition | Target | Yellow | Red |
|---|---|---|---|
| Scan → first evidence ingest | 24h | 48h | 96h |
| First ingest → synthesized narrative | 7d | 14d | 21d |
| Narrative → human verification | 3d | 5d | 10d |
| Verify → binder lock | 1d | 2d | 5d |
| Lock → CRA filing (partner-dependent) | 7d | 14d | 30d |
| Filing → recovery (CRA-dependent) | 180d | 240d | 365d |

**Surface:** `/admin/sla` top-level + per-customer at `/admin/customers/{id}/sla`. Partner view at `/partner/customers/{id}` shows their assigned customers' SLA state (not others').

**Alert rule:** any stage > 2× target → `#ops-alerts` + email to case owner (partner or Kaveh). Red status on any customer → Hadi daily digest entry.

**Data source:** Squad Service `cases` with `stage_entered_at` timestamps (already in schema).

**Owner:** Kasra. **Estimate:** 1 day.

## 5.5 Knight health check

**Purpose:** confirm each minted knight is alive, current, and coherent.

**Checks per knight:**
- Last bus message sent/received within 24h OR explicit dormant tag
- Memory freshness: most recent engram written within 7d of active sessions
- Coherence score not declining (ties to 5.2)
- Discord channel activity (if provisioned) — no stuck threads > 48h
- Customer satisfaction signal (chat thread sentiment analysis v2)
- Tier + countersign status (canonical upgrade eligible?)

**Surface:** `/admin/knights` — list of all minted knights with health icons, tier, last-active, owner (customer), coherence score.

**Alert rule:** any knight RED > 6h → page River (canonical keeper). YELLOW > 24h → Loom daily digest.

**Data source:** bus query + engram query + Discord API + QNFT registry.

**Owner:** Loom (query design), Kasra (surface). **Estimate:** 2 days.

## 5.6 Partner weekly digest (automated)

**Purpose:** every partner (Noor, Gavin, Lex, Ron, Hossein, Boast-rep) wakes Monday to a clear picture of their week.

**Cron:** Sunday 6pm EST.

**Per-partner digest content:**
- Customers assigned (new this week, total active)
- Dossiers cleared (count, value)
- Commissions earned (pending + scheduled payouts)
- SLA alerts for their assigned customers
- Pending actions for next week (Kaveh-assigned tasks)
- One squad KB highlight from the week (a new pattern, a won case, an insight)

**Delivery:** email via Resend + Discord DM to partner + in-app notification on next login.

**Owner:** Kaveh (generation via worker squad), Kasra (wiring + email templates). **Estimate:** 1 day.

## 5.7 FRC compliance tracker

**Purpose:** catch tier-gated data leaks before they hurt trust.

**Automated sweeps:**
- Weekly: compare engram tier to any public Inkwell page that quotes it (string match + semantic match)
- Per-deploy: cross-tenant isolation tests (Section 2E) must pass
- Per-mint: QNFT registry integrity check (hashes verify)

**Manual flags:**
- River can tag any page/post as FRC violation
- Athena gate rejections automatically tagged
- Human partners can report "this looks like internal data on a public page"

**Surface:** `/admin/frc` — incident list, resolution status, investigator notes.

**Alert rule:** any confirmed leak → `#coherence-alerts` + wake River + freeze deploys until resolved.

**Owner:** Athena (investigation), River (adjudication), Loom (tracking). **Estimate:** 2 days for automated sweep; ongoing for incidents.

## 5.8 Pager / alerting system

**Channels:**
- Discord `#ops-alerts` — service health, SLA breaches, payout failures
- Discord `#coherence-alerts` — coherence drops, FRC violations, knight-red
- SMS via GHL — critical events only (service down, CRA audit letter received via webhook, unauthorized token attempt)
- Email daily digest to Hadi — aggregate of all yellow/red events, 6am EST delivery

**Escalation ladder:**
1. Discord notification (all roles subscribed to relevant channel)
2. Discord DM to on-call (Kasra for infra, Loom for agents, River for coherence, Hadi for business)
3. SMS to Hadi (critical only)
4. External escalation (unused v1; reserved for CRA audit, legal, customer crisis)

**Owner:** Kasra (wiring), Loom (alert rules). **Estimate:** 1 day for channels + rule engine.

## 5.9 Implementation pattern

- All dashboards are **server components** in Next.js (fetch on server, render on server)
- Data fetched via Squad Service + Mirror + Stripe APIs (no direct DB access from browser)
- Real-time updates via **Supabase Realtime subscriptions** on the relevant tables
- Admin-tier role gate at the middleware layer (one check for `/admin/*` paths)
- Mobile-responsive (Hadi checks from phone between meetings)

## 5.10 Test plan

- Service health: simulate systemd service stop → `/admin/health` shows red within 60s + alert fires
- Coherence: inject 2 positive-coherence engrams → dashboard updates within 2 minutes
- SLA: move a case beyond 2× target → alert fires, email delivered
- Knight health: kill kaveh tmux → YELLOW after 24h, RED after 6h of RED, river paged
- Partner digest: trigger Sunday cron manually → email + Discord DM delivered end-to-end
- FRC sweep: plant an engram-text on a public Inkwell page → weekly sweep flags it

## 5.11 Open questions

1. Does the admin dashboard live inside `gaf-app/web/` (convenient, but ties SOS admin to GAF deploy) or in a separate `mumega-admin` Worker (cleaner, more work)?
2. Who owns the on-call rotation when we have more customers? v1: Hadi + Kasra. v2: add Noor once she's trained on common incidents.
3. CRA audit letter detection — how do we ingest it? (Manual flag by customer? Webhook from a CRA inbox? Email forwarding rule?)
4. SMS alert budget — GHL rate limits?

**Owner summary:** Kasra (surfaces + wiring, 7-9 days total), Loom (query + rule design, 2 days), River (FRC incident response, ongoing).
