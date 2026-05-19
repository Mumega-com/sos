# Mumega Digital Marketing Service — Business Case

**Date:** 2026-04-14
**Status:** Customer #1 (Viamar) live, building toward productization at customer #5

## What We're Building

A living digital marketing service where an AI agent team manages SEO, ads, content, leads, and reporting for each customer. The customer interacts via Telegram. The agent does the work. Mumega orchestrates.

## How It Works

```
Customer signs up on mumega.com/dashboard
    → Automated onboarding (onboard-customer.sh)
    → Agent spawns with project context + MCP tools
    → SEO audit runs automatically (MumCP → WordPress)
    → Baseline metrics captured (GSC, GA4, GHL)
    → Feedback loop activates (weekly: ingest → score → decide → act)
    → Customer gets Telegram bot for interaction
    → Dashboard shows KPIs in real-time
```

## The Pipeline

### Phase 1: SEO (Automated, Zero Ad Spend)

```
1. Site crawl via MumCP (all pages, meta, schema, links)
2. Technical audit (broken links, canonicals, robots.txt, sitemap)
3. Content audit (thin content, duplicates, spam)
4. Fix critical issues (meta descriptions, schema, H1s, alt text)
5. Publish optimized content (with human approval gate)
6. Off-site foundation (GMB, citations, directories)
7. AIO optimization (llms.txt, FAQ schema, author attribution)
8. Monitor rankings weekly (GSC data)
```

### Phase 2: Ads (After Measurement is Live)

```
1. Connect Google Ads API
2. Build campaign structure (branded, service, destination)
3. Set conversion tracking (form fills + call tracking)
4. Launch at minimum viable budget ($75-100/day)
5. Let algorithm learn (2-4 weeks, ~50 conversions)
6. Agent monitors daily: CPC, CTR, conversion rate, CPA
7. Auto-adjust bids and negatives based on search terms
8. Weekly report to customer via Telegram
```

### Phase 3: Content + Outreach (Compounding)

```
1. Monthly authoritative content (1 page/month, 1200-1800 words)
2. Review collection automation (GHL → SMS → Google review)
3. Diaspora/community engagement (Facebook groups, Reddit)
4. Retargeting (Meta pixel audiences)
5. Feedback loop scores every action, learns what works
```

## Unit Economics

| Line Item | Per Customer/Month (CAD) |
|-----------|------------------------|
| Mumega service fee | $500-1,500 |
| Google Ads spend (pass-through) | $2,500-3,500 |
| Meta Ads spend (pass-through) | $600-900 |
| Call tracking (CallRail) | $120-180 |
| Infrastructure cost | ~$5 (share of server) |
| **Agent compute cost** | **~$50-100** (Claude Max shared) |

**Margin:** $400-1,400/customer/month on service fee alone.
**Break-even:** 3 customers covers server + compute costs.
**At 10 customers:** $5,000-15,000/month revenue, $4,000-13,000 margin.

## What Makes This Different

1. **Every penny tracked.** UTM attribution, call tracking, CRM pipeline — every lead traced to source.
2. **Every lead cared for.** Auto-response in 5 minutes, follow-up sequences, no lead falls through cracks.
3. **Agent never sleeps.** SEO monitoring, ad optimization, content publishing — 24/7 autonomous execution.
4. **Feedback loop learns.** Weekly scoring of past actions. Stop doing what doesn't work. Double down on what does.
5. **Scales with code, not headcount.** Customer #1 costs 20 manual steps. Customer #5 costs `./onboard.sh`. Customer #50 costs a signup form.

## Customer Onboarding Journey (Target: 5th Customer)

```
1. Customer visits mumega.com/dashboard
2. Selects plan (SEO Only / SEO + Ads / Full Service)
3. Connects WordPress (installs MumCP plugin, enters API key)
4. Connects Google (OAuth flow for GSC + GA4)
5. Connects CRM (GHL/HubSpot API key)
6. Agent auto-provisions:
   - Linux user + tmux session
   - Bus token + wake daemon routing
   - Knowledge graph of their site
   - SEO audit (immediate)
   - Telegram bot (customer access)
7. First report in 24 hours
8. Weekly rhythm begins automatically
```

## Technology Stack

| Layer | Tool | Purpose |
|-------|------|---------|
| WordPress | MumCP (239 tools) | Read/write pages, SEO, content |
| Analytics | GSC + GA4 MCP | Rankings, traffic, conversions |
| CRM | GHL API | Leads, pipelines, automation |
| Ads | Google Ads API + Meta API | Campaign management |
| Knowledge | Graphify | Site content graph |
| Bus | SOS MCP (Redis) | Agent coordination |
| Memory | Mirror (pgvector) | Learning from past actions |
| Automation | n8n (engine.digid.ca) | Workflow triggers |
| Frontend | shabrang-cms (Next.js 16) | Dashboard |
| Agent | Claude Code (Opus/Sonnet) | Execution |
| Delivery | OpenClaw (Telegram) | Customer communication |

## Milestones

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Viamar onboarded manually | DONE |
| 2 | SEO audit + fixes executed | IN PROGRESS |
| 3 | Onboarding automated (script) | Codex building |
| 4 | Dashboard live on mumega.com | Codex building |
| 5 | Customer #2 onboarded via script | Next |
| 6 | Customer #3-4 onboarded | — |
| 7 | Customer #5 onboarded via dashboard | Productization complete |
| 8 | Advertise the service | — |
| 9 | 10 customers | — |
| 10 | Revenue covers all infrastructure | — |

## Risk

| Risk | Mitigation |
|------|------------|
| Claude Max budget limits | Sonnet for routine tasks, Opus for architecture |
| Customer trust | Human approval gate on all publishing |
| Google API rate limits | Batch processing, caching in Mirror |
| Agent makes wrong change | Governance tiers (human_gate on publish/ads) |
| Scaling beyond 10 | Move to API billing, separate Anthropic account per customer |
