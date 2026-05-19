# Plan: Viamar — Living Digital Marketing Organism

**Date:** 2026-04-14
**Customer:** Viamar Scilla Transport International (viamar.ca)
**Goal:** Build a self-improving digital marketing organism that accounts for every penny and cares about every lead. Double lead intake in 90 days. Use Viamar as the template for 10+ customers.

## The Vision

This is not a one-time SEO project. This is a living organism that:
- Feeds on analytics data (GA4, GSC, GHL, CallRail)
- Makes decisions based on what worked and what didn't
- Executes through MumCP (WordPress), Google Ads API, GHL API
- Learns from every lead, every conversion, every bounce
- Reports to the client via Telegram
- Scales to N customers with the same architecture

The 5th customer triggers productization. The 10th proves the model.

## What Exists

| Asset | Status |
|-------|--------|
| `/home/viamar/` | User account, mumega group, SOS+Mirror symlinked |
| `/home/viamar/CLAUDE.md` | Agent system defined (manager, webmaster, marketer) |
| `/home/viamar/projects/viamar-data/` | 15 docs, 3,443 lines of business intelligence |
| MumCP on viamar.ca | Installed, 239 tools available |
| Strategy doc | 240 lines, 3-phase plan (Fix→Capture→Grow) |
| Content audit | 402 items audited, rated, actionable |
| SEO audit | Junk cleanup done, 173 spam posts noindexed |
| Competitor analysis | 10 competitors mapped |
| Keyword economics | Route demand, CPC, seasonal patterns |
| Diaspora research | 6 communities, platform behavior, media channels |
| Graphify graph | 48 nodes (code), docs need LLM pass |
| Governance | human_gate on publish/outreach/ads |

## Architecture

```
┌─────────────────────────────────────┐
│ Viamar Team (Telegram via OpenClaw) │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│     Viamar Agent (tmux:viamar)      │
│     Claude Code on /home/viamar     │
│     Roles: manager+webmaster+marketer│
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│           SOS Bus                   │
│     Escalates to Kasra/Codex/Gemini │
└──┬───────┬───────┬───────┬──────────┘
   │       │       │       │
┌──▼──┐ ┌──▼──┐ ┌──▼──┐ ┌──▼─────────┐
│MumCP│ │ GA4 │ │ GSC │ │ GHL/Ads    │
│(WP) │ │(API)│ │(API)│ │ (API)      │
└─────┘ └─────┘ └─────┘ └────────────┘
               │
┌──────────────▼──────────────────────┐
│    Feedback Loop (weekly cycle)     │
│  INGEST → SCORE → DECIDE → ACT     │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│    mumega.com/dashboard             │
│    KPIs, leads, spend, ROI          │
└─────────────────────────────────────┘
```

## Phases

### Phase 1: SEO — Full On-Site + Off-Site (Weeks 1-3)

Everything MumCP can do without API credentials or ad spend.

```
Step 1: Full site SEO audit via MumCP
Tool: MumCP tools on viamar.ca
Change: Crawl all pages — meta titles, descriptions, schema, headings,
        internal links, broken links, canonical tags, robots.txt, sitemap
Outcome: Audit report with prioritized fix list

Step 2: Fix critical on-site issues
Tool: MumCP edit tools
Change: Based on audit — fix meta titles/descriptions for top 20 pages,
        add FAQPage schema to service pages, fix broken internal links,
        consolidate duplicate pages (6 quote pages → 1 with redirects),
        add structured data (LocalBusiness, Service, FAQPage)
Outcome: RankMath score improvement, schema validation passes

Step 3: Execute content audit fixes
Tool: MumCP
Change: Apply ratings from content-audit.md:
        - DELETE: 3 pages (virtual survey, competitor scrapes)
        - CONSOLIDATE: 8 duplicate quote/service pages → redirects
        - NOINDEX: already done (173 spam posts)
        - UPGRADE: 19 blog posts worth improving (rewrite intros, add schema)
Outcome: Clean sitemap, no duplicate content, crawl budget focused

Step 4: Publish pending content
Tool: MumCP
Change: Review and publish 3 draft pages awaiting approval:
        - CIFFA trust page (ID 39126)
        - Classic car Italy page (ID 39127)
        - Retirement Italy page (ID 39128)
Outcome: 3 new ranking-target pages live (with Hadi's approval per governance)

Step 5: Build full knowledge graph
Tool: Graphify with LLM pass
Change: Run /graphify on viamar-data docs + crawled site content
        to build complete business knowledge graph
Outcome: Agent understands the business deeply — services, routes,
         competitors, pricing, community angles

Step 6: Off-site foundation
Tool: Manual + agent research
Change: Submit sitemap to GSC, verify site ownership,
        list Viamar on CIFFA directory, FIATA member page,
        verify/claim Google Business Profile, set up review link,
        check/add BBB, YellowPages, Yelp listings
Outcome: Citation profile established, GMB optimized

Step 7: AIO optimization
Tool: MumCP
Change: Add llms.txt, direct-answer lead paragraphs on top 10 pages,
        author attribution (Bruno Scilla), CIFFA/FIATA citations,
        FAQ schema on every service page
Outcome: Pages formatted for AI citation (ChatGPT, Perplexity, Google AIO)
```

### Phase 2: Analytics + Lead Infrastructure (Week 3-4)

Connect the measurement layer so we can prove ROI.

```
Step 8: Connect Google Search Console
Tool: GSC API via MCP or service account
Change: Wire GSC data feed — impressions, clicks, CTR, position by query
Outcome: Weekly organic performance data flowing into feedback loop

Step 9: Connect Google Analytics 4
Tool: GA4 API (Data API v1)
Change: Wire GA4 data feed — sessions, conversions, traffic sources,
        landing page performance, user behavior flow
Outcome: Know which pages convert and which bounce

Step 10: Set up GHL pipelines
Tool: GHL API
Change: Create 3 pipelines (Auto Shipping / Intl Moving / Equipment),
        add service_type field, disable Contact→Opportunity workflow,
        tag existing leads by type
Outcome: Clean CRM, no new duplicates, leads routed correctly

Step 11: Lead intake worker (Cloudflare)
Tool: Cloudflare Workers + D1
Change: Deploy lead intake worker at viamar HQ domain:
        POST /lead — receives form submissions
        POST /webhook/wp — WordPress form webhook
        Attributes UTM, stores in D1, forwards to GHL
Outcome: Every lead tracked with source attribution

Step 12: Build feedback loop
Tool: SOS + Mirror
Change: Weekly cron that:
        1. INGEST — pull GSC + GA4 + GHL data
        2. COMPARE — week-over-week deltas
        3. SCORE — which actions from last week moved metrics
        4. DECIDE — generate next week's task list
        5. STORE — save scored actions in Mirror for learning
Outcome: Organism learns what works, stops doing what doesn't
```

### Phase 3: Ads + Retargeting (Week 4-6)

Only after measurement is live. "Do NOT run ads until call tracking is live."

```
Step 13: Google Ads setup
Tool: Google Ads API or MCC
Change: 4 campaigns per strategy doc:
        Auto Shipping Intl ($40/day), RoRo ($15/day),
        Brand ($10/day), Equipment test ($15/day)
        Conversion tracking: thank-you page + CallRail call >60s
Outcome: Paid search live, $75-100/day CAD, measurable

Step 14: Meta retargeting
Tool: Meta Marketing API
Change: Install pixel via GTM, build 3 audiences:
        Hot (7d visitors), Warm (30d no convert), Cold (lookalike)
        Run retargeting ads with social proof creative
Outcome: Retargeting live, $25-45/day, lost visitors come back

Step 15: Google Ads algorithm learning
Tool: Feedback loop + Google Ads API
Change: Let campaigns run 2-4 weeks. Feed conversion data back.
        Google's algorithm needs ~50 conversions to optimize.
        Agent monitors daily: CPC, CTR, conversion rate, CPA.
        Adjust bids and negatives based on search term reports.
Outcome: Algorithm trained, cost per lead stabilizing toward <$80 CAD
```

### Phase 4: Telegram Delivery + Client Access (Week 6-8)

```
Step 16: Wire OpenClaw Telegram bot
Tool: OpenClaw
Change: Create Viamar Telegram bot, connect to viamar agent via SOS bus
        Bot receives: weekly reports, lead alerts, metric updates
        Bot accepts: questions about campaign, content requests, approvals
Outcome: Viamar team interacts with their business through Telegram

Step 17: Dashboard access
Tool: mumega.com/dashboard
Change: Create Viamar tenant view showing:
        - Lead count (weekly, monthly, source)
        - Ad spend vs revenue
        - SEO rankings (top 20 keywords)
        - Content performance
        - Agent activity log
Outcome: Client sees their metrics, we see our delivery
```

### Phase 5: Content Machine + Diaspora (Ongoing)

```
Step 18: Monthly content pipeline
Tool: MumCP + agent
Change: 1 authoritative page per month from the content queue:
        May: Germany, Jun: Portugal D7, Jun: Nigeria Brampton,
        Jul: UK, Aug: UAE, Sep: Australia...
        Each page: 1200-1800 words, FAQPage schema, author attribution
Outcome: 1 new ranking page per month, compounding traffic

Step 19: Review collection
Tool: GHL automation
Change: When lead moves to "Won" → auto-SMS 3 days post-delivery
        asking for Google review. Target: 6 → 50 reviews in 6 months.
Outcome: Google reviews grow, local pack ranking improves

Step 20: Diaspora engagement
Tool: Agent research + community monitoring
Change: Monitor Italian-Vaughan, Nigerian-Brampton, Portuguese-Mississauga,
        Jamaican-Toronto Facebook groups. Track mentions.
        Content targeted to each community's shipping season.
Outcome: Community trust → referrals → leads without ad spend
```

## KPIs — What the Organism Tracks

| Metric | Current | 30-day | 90-day |
|--------|---------|--------|--------|
| Monthly form fills | Unknown | Baseline | +50% |
| Monthly tracked calls | 0 | Baseline | Measurable |
| Cost per lead (Ads) | — | <$80 CAD | <$60 CAD |
| CRM duplicates | 12,806 | 0 new | 0 new |
| Google reviews | 6 | 15 | 40 |
| Organic sessions | Unknown | Baseline | +25% |
| Pages with schema | ~5 | 30 | 50+ |
| AI citations | 0 | Monitor | 5+ |
| Telegram interactions | 0 | Active | Weekly rhythm |

## Scaling to N Customers

After customer 5, extract the pattern:

```
Customer onboarding = {
  1. Create user account (/sop onboard)
  2. Clone customer-template repo
  3. Install MumCP on their WordPress
  4. Connect Google tools (GSC, GA4, Ads)
  5. Build knowledge graph (Graphify)
  6. Run SEO audit (MumCP)
  7. Wire Telegram (OpenClaw)
  8. Activate feedback loop
  9. Dashboard tenant view
}
```

Each customer gets their own:
- Linux user + home directory
- SOS bus identity (agent on bus)
- Mirror memory namespace (tenant-isolated)
- MumCP connection to their WordPress
- Google API credentials
- Telegram bot
- Dashboard view
- Governance rules (what needs human approval)

The organism is the same. The data is different. The learning compounds across all customers.

## MCP Tools We May Need

| Need | MCP | Status |
|------|-----|--------|
| WordPress management | MumCP (SPAI) | Installed on viamar.ca |
| Google Search Console | GSC MCP or API | Need to connect |
| Google Analytics | GA4 MCP or API | Need to connect |
| Google Ads | Ads API or MCC | Need to set up |
| GHL CRM | GHL API | Keys in ~/.sos/.env |
| Cloudflare | Cloudflare MCP | Available (mumega account) |
| Facebook/Meta | Meta Marketing API | Need pixel + token |

## Start Order

1. **SEO full pass** (Steps 1-7) — zero cost, immediate value
2. **Analytics connection** (Steps 8-9) — baseline measurement
3. **Lead infrastructure** (Steps 10-12) — stop losing leads
4. **Ads** (Steps 13-15) — only after measurement is live
5. **Telegram** (Steps 16-17) — client delivery channel
6. **Content + diaspora** (Steps 18-20) — compounding growth

## Governance Reminder

Per `/home/viamar/CLAUDE.md`:
- Analytics read: **act_freely**
- Content edits: **batch_approve**
- Content publish: **human_gate** (Hadi approves)
- Outreach/email: **human_gate**
- Ad spend: **human_gate**
