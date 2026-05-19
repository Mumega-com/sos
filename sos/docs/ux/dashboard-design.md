# SOS Dashboard Design

## Overview

The SOS (Sovereign Operating System) Dashboard is the backend control panel for managing autonomous services, content pipelines, integrations, and system health. It extends the existing mumega-web infrastructure at `/sos/dashboard`.

**Route**: `https://mumega.com/sos/dashboard`
**Auth**: Shared Supabase authentication with mumega-web

---

## Architecture Coherence

Per `ARCHITECTURE_AGREEMENT.md`, SOS follows a **microkernel architecture** where:

> "Interfaces are clients, not the brain. Adapters are thin clients that call Engine Service."

### How This Dashboard Fits

```
┌─────────────────────────────────────────────────────────────┐
│                     mumega-web (Adapter)                    │
│  ┌─────────────────┐  ┌─────────────────┐                  │
│  │ /dashboard/*    │  │ /sos/dashboard/*│ ← New routes     │
│  │ (Neural Link)   │  │ (SOS Control)   │                  │
│  └────────┬────────┘  └────────┬────────┘                  │
│           │                    │                            │
│           │        HTTP/JSON   │                            │
└───────────┼────────────────────┼────────────────────────────┘
            │                    │
            ▼                    ▼
┌───────────────────┐  ┌───────────────────┐  ┌─────────────┐
│  Engine Service   │  │  Content Service  │  │   Memory    │
│  :8010            │  │  :8020            │  │   :8844     │
└───────────────────┘  └───────────────────┘  └─────────────┘
```

**Key Points:**
1. **mumega-web is an Adapter** - thin HTTP client, no SOS imports
2. **SOS Services expose APIs** - `/health`, `/metrics`, domain endpoints
3. **Dashboard calls APIs** - via Next.js API routes or direct fetch

### SOS Service Ports

| Service | Port | Purpose |
|---------|------|---------|
| Engine | 8010 | Chat, orchestration |
| Content | 8020 | Strategy, calendar, publishing |
| Memory | 8844 | Mirror API |
| Economy | 8030 | Wallet, ledger |
| Tools | 8040 | MCP, tool registry |
| Identity | 8050 | OAuth, qNFT |

---

## Architecture Decision

### Option A: Extend mumega-web (Recommended)
- Add `/app/sos/**` routes to existing Next.js app
- Reuse Supabase auth, UI components, middleware
- Share DashboardLayout with SOS-specific sidebar
- Faster development, consistent UX
- **Calls SOS services via HTTP** (not imports)

### Option B: Separate SOS Frontend
- Standalone Next.js app at `$SOS_ROOT/frontend`
- Own deployment, own auth instance
- More isolation but duplicated infrastructure

**Decision**: **Option A** - Extend mumega-web with SOS routes. The existing patterns are mature and well-tested.

---

## Route Structure

```
/sos/dashboard
├── /                    # SOS Overview (system health, active services)
├── /content             # Content Engine Hub
│   ├── /strategy        # Content strategy editor (pillars, audiences)
│   ├── /calendar        # Editorial calendar view
│   └── /queue           # Approval queue (UGC pipeline)
├── /workflows           # n8n Integration
│   ├── /                # Workflow list from n8n
│   ├── /[id]            # Workflow detail/edit
│   └── /create          # Create workflow wizard
├── /connectors          # OAuth & Integrations
│   ├── /                # Connector catalog
│   ├── /cloudflare      # Cloudflare OAuth status
│   ├── /ghl             # GoHighLevel integration
│   └── /notion          # Notion workspace sync
├── /adapters            # Communication Channels
│   ├── /telegram        # Telegram bot status/config
│   ├── /slack           # Slack workspace config
│   └── /email           # Email/SMTP config
├── /memory              # Mirror Integration
│   ├── /engrams         # Browse engrams
│   ├── /search          # Semantic search
│   └── /stats           # Memory statistics
├── /agents              # SOS Agents
│   ├── /                # Agent registry
│   ├── /[id]            # Agent detail/logs
│   └── /deploy          # Deploy new agent
└── /settings            # SOS Configuration
    ├── /env             # Environment variables
    ├── /cron            # Scheduled tasks
    └── /logs            # System logs
```

---

## Core Pages Design

### 1. SOS Overview (`/sos/dashboard`)

**Purpose**: Bird's eye view of the SOS ecosystem

**Components**:
```
┌─────────────────────────────────────────────────────────────┐
│  SOS Command Center                            [Refresh] 🟢 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐        │
│  │ Services     │ │ Workflows    │ │ Content      │        │
│  │ ━━━━━━━━━━━  │ │ ━━━━━━━━━━━  │ │ ━━━━━━━━━━━  │        │
│  │ 5 Active     │ │ 3 Running    │ │ 12 Queued    │        │
│  │ 0 Errors     │ │ 2 Paused     │ │ 4 Published  │        │
│  └──────────────┘ └──────────────┘ └──────────────┘        │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Recent Activity                                      │  │
│  │ ─────────────────────────────────────────────────── │  │
│  │ • UGC approved: "AI Employees for SMEs" (2m ago)    │  │
│  │ • Workflow triggered: Cyrus Gmail Bridge (5m ago)   │  │
│  │ • Content published: /blog/sovereign-ai (1h ago)    │  │
│  │ • Telegram: 15 messages processed (today)           │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─────────────────────┐ ┌─────────────────────────────┐   │
│  │ System Health       │ │ Quick Actions               │   │
│  │ ──────────────────  │ │ ─────────────────────────── │   │
│  │ Mirror API: 🟢      │ │ [+ New Content]             │   │
│  │ n8n: 🟢             │ │ [Sync Calendar]             │   │
│  │ Telegram: 🟢        │ │ [Run Workflow]              │   │
│  │ GDrive CMS: 🟡      │ │ [View Logs]                 │   │
│  └─────────────────────┘ └─────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Data Sources**:
- `GET /api/sos/health` → Service status
- `GET /api/sos/activity` → Recent events
- `GET /api/sos/stats` → Aggregate metrics

---

### 2. Content Hub (`/sos/dashboard/content`)

**Purpose**: Manage content strategy, calendar, and approval queue

**Sub-pages**:

#### 2a. Strategy Editor (`/content/strategy`)
```
┌─────────────────────────────────────────────────────────────┐
│ Content Strategy                              [Save] [Reset]│
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ Brand Voice                                                 │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ Confident, technical, slightly rebellious. We're       ││
│ │ building the future of work, not another SaaS tool.    ││
│ └─────────────────────────────────────────────────────────┘│
│                                                             │
│ Content Pillars                              [+ Add Pillar] │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ 🏛️ Sovereign AI        │ Keywords: local-first, privacy │ │
│ │    ✏️ Edit  🗑️ Delete  │ Audiences: devs, architects    │ │
│ ├────────────────────────────────────────────────────────┤ │
│ │ 🤖 AI Employees        │ Keywords: automation, 24/7     │ │
│ │    ✏️ Edit  🗑️ Delete  │ Audiences: SME, consultants    │ │
│ ├────────────────────────────────────────────────────────┤ │
│ │ 🐝 Multi-Agent Systems │ Keywords: swarm, orchestration │ │
│ │    ✏️ Edit  🗑️ Delete  │ Audiences: devs, architects    │ │
│ └────────────────────────────────────────────────────────┘ │
│                                                             │
│ Target Audiences                            [+ Add Audience]│
│ ┌────────────────────────────────────────────────────────┐ │
│ │ 👔 SME Leaders    │ Tone: ROI-driven, no jargon        │ │
│ │ 💻 Developers     │ Tone: Technical, code-first        │ │
│ │ 🎯 Consultants    │ Tone: Empowering, results-focused  │ │
│ │ 🏢 Architects     │ Tone: Professional, strategic      │ │
│ └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

#### 2b. Editorial Calendar (`/content/calendar`)
```
┌─────────────────────────────────────────────────────────────┐
│ Editorial Calendar                    [< Jan 2026 >] [Today]│
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Mon    Tue    Wed    Thu    Fri    Sat    Sun             │
│ ┌─────┬─────┬─────┬─────┬─────┬─────┬─────┐               │
│ │     │     │  1  │  2  │  3  │  4  │  5  │               │
│ │     │     │     │     │     │     │     │               │
│ ├─────┼─────┼─────┼─────┼─────┼─────┼─────┤               │
│ │  6  │  7  │  8  │  9  │ 10  │ 11  │ 12  │               │
│ │     │ 📝  │     │ 📝  │     │     │ 🔵  │ ← Today       │
│ ├─────┼─────┼─────┼─────┼─────┼─────┼─────┤               │
│ │ 13  │ 14  │ 15  │ 16  │ 17  │ 18  │ 19  │               │
│ │ 📝  │     │ 📝  │     │ 📝  │     │     │               │
│ └─────┴─────┴─────┴─────┴─────┴─────┴─────┘               │
│                                                             │
│ Upcoming Posts                                              │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ Jan 14 │ 🟡 DRAFTING │ "Why Your AI Should Work FOR You"│ │
│ │ Jan 16 │ 🔴 PLANNED  │ "Hiring Your First AI Employee"  │ │
│ │ Jan 18 │ 🔴 PLANNED  │ "The Council Pattern Explained"  │ │
│ └────────────────────────────────────────────────────────┘ │
│                                                             │
│ [+ Schedule Post]  [Generate Week Plan]  [Sync to Notion]  │
└─────────────────────────────────────────────────────────────┘
```

#### 2c. Approval Queue (`/content/queue`)
```
┌─────────────────────────────────────────────────────────────┐
│ Content Approval Queue                      [Refresh] (12) │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ Filter: [All ▼] [Telegram ▼] [Quality 6+ ▼]    🔍 Search   │
│                                                             │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ ⭐ 8/10 │ "How we use AI to automate invoicing"        │ │
│ │ Telegram │ @kasra_m │ 2h ago │ Pillar: practical-auto  │ │
│ │                                                        │ │
│ │ AI Suggestion: "Great case study content. Recommend   │ │
│ │ expanding with ROI metrics for SME audience."         │ │
│ │                                                        │ │
│ │ [✓ Approve] [✏️ Edit] [❌ Reject] [👁️ Preview]         │ │
│ ├────────────────────────────────────────────────────────┤ │
│ │ ⭐ 7/10 │ "Sovereign AI means no vendor lock-in"       │ │
│ │ Slack   │ #content  │ 5h ago │ Pillar: sovereign-ai   │ │
│ │                                                        │ │
│ │ [✓ Approve] [✏️ Edit] [❌ Reject] [👁️ Preview]         │ │
│ ├────────────────────────────────────────────────────────┤ │
│ │ ⭐ 6/10 │ "Multi-agent workflows for customer support" │ │
│ │ Telegram │ @dev_team │ 1d ago │ Pillar: multi-agent   │ │
│ │                                                        │ │
│ │ [✓ Approve] [✏️ Edit] [❌ Reject] [👁️ Preview]         │ │
│ └────────────────────────────────────────────────────────┘ │
│                                                             │
│ Rejected (3)  [Show ▼]                                      │
└─────────────────────────────────────────────────────────────┘
```

---

### 3. Workflows (`/sos/dashboard/workflows`)

**Purpose**: Manage n8n automation workflows

```
┌─────────────────────────────────────────────────────────────┐
│ Workflow Automation                        [+ Create] [Sync]│
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ Active Workflows                                            │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ 🟢 Mumega UGC Content Pipeline                         │ │
│ │    Triggers: Telegram, Slack → AI Decision → Publish   │ │
│ │    Last run: 2 hours ago │ Runs today: 15              │ │
│ │    [View] [Edit] [Logs] [⏸️ Pause]                      │ │
│ ├────────────────────────────────────────────────────────┤ │
│ │ 🟢 Cyrus Gmail Bridge (V4)                             │ │
│ │    Triggers: Gmail → Process → Notion                  │ │
│ │    Last run: 5 mins ago │ Runs today: 47               │ │
│ │    [View] [Edit] [Logs] [⏸️ Pause]                      │ │
│ ├────────────────────────────────────────────────────────┤ │
│ │ 🟢 Digid Invoice Hunter (MCP)                          │ │
│ │    Triggers: Schedule → Scan → Extract → Store         │ │
│ │    Last run: 1 hour ago │ Runs today: 24               │ │
│ │    [View] [Edit] [Logs] [⏸️ Pause]                      │ │
│ └────────────────────────────────────────────────────────┘ │
│                                                             │
│ Inactive Workflows (5)  [Show ▼]                           │
│                                                             │
│ Templates                                                   │
│ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐        │
│ │ UGC Pipeline │ │ Email Digest │ │ Social Post  │        │
│ │ [Deploy]     │ │ [Deploy]     │ │ [Deploy]     │        │
│ └──────────────┘ └──────────────┘ └──────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

---

### 4. Connectors (`/sos/dashboard/connectors`)

**Purpose**: OAuth integrations and third-party services

```
┌─────────────────────────────────────────────────────────────┐
│ Integrations & Connectors                        [+ Add New]│
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ Connected                                                   │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ ☁️  Cloudflare     │ 🟢 Connected │ kasra@mumega.com   │ │
│ │     Workers, Pages │ Since Jan 5  │ [Manage] [Revoke]  │ │
│ ├────────────────────────────────────────────────────────┤ │
│ │ 📧 GoHighLevel     │ 🟢 Connected │ Mumega Agency      │ │
│ │     Social, CRM    │ Since Dec 20 │ [Manage] [Revoke]  │ │
│ ├────────────────────────────────────────────────────────┤ │
│ │ 📝 Notion          │ 🟢 Connected │ Mumega Workspace   │ │
│ │     Databases      │ Since Jan 8  │ [Manage] [Revoke]  │ │
│ └────────────────────────────────────────────────────────┘ │
│                                                             │
│ Available                                                   │
│ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐        │
│ │ 📊 Linear    │ │ 🐙 GitHub    │ │ 📁 GDrive    │        │
│ │ [Connect]    │ │ [Connect]    │ │ [Connect]    │        │
│ └──────────────┘ └──────────────┘ └──────────────┘        │
│ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐        │
│ │ 🔗 Supabase  │ │ 📨 SendGrid  │ │ 💬 Discord   │        │
│ │ [Connect]    │ │ [Connect]    │ │ [Connect]    │        │
│ └──────────────┘ └──────────────┘ └──────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

---

### 5. Adapters (`/sos/dashboard/adapters`)

**Purpose**: Configure communication channels

```
┌─────────────────────────────────────────────────────────────┐
│ Communication Adapters                                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ 📱 Telegram                                    🟢 Active ││
│ │ ─────────────────────────────────────────────────────── ││
│ │ Bot: @mumega_com_bot                                    ││
│ │ Messages today: 47 │ Users: 3 │ Commands: 156          ││
│ │                                                         ││
│ │ Settings:                                               ││
│ │ • Quiet hours: 11pm - 7am EST                          ││
│ │ • Auto-respond: ON                                      ││
│ │ • UGC Collection: ON (→ n8n pipeline)                  ││
│ │                                                         ││
│ │ [Configure] [View Logs] [Restart]                       ││
│ └─────────────────────────────────────────────────────────┘│
│                                                             │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ 💼 Slack                                       🟡 Setup  ││
│ │ ─────────────────────────────────────────────────────── ││
│ │ Workspace: Not connected                                ││
│ │                                                         ││
│ │ [Connect Workspace]                                     ││
│ └─────────────────────────────────────────────────────────┘│
│                                                             │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ 📧 Email (SMTP)                               🔴 Inactive││
│ │ ─────────────────────────────────────────────────────── ││
│ │ Provider: Not configured                                ││
│ │                                                         ││
│ │ [Configure SMTP]                                        ││
│ └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

---

## API Routes for SOS Dashboard

### Next.js API Routes (Proxies to SOS Services)

```
/api/sos/
├── health              GET     Aggregate health from all services
├── activity            GET     Recent activity feed
├── stats               GET     Aggregate statistics
│
├── content/            → Proxy to Content Service :8020
│   ├── strategy        GET/PUT Strategy config
│   ├── calendar        GET     Calendar entries
│   ├── calendar/[id]   PATCH   Update post
│   ├── queue           GET     Approval queue
│   ├── queue/[id]      POST    Approve/reject
│   └── publish         POST    Publish content
│
├── workflows/
│   ├── list            GET     n8n workflows
│   ├── [id]            GET     Workflow detail
│   ├── [id]/run        POST    Trigger workflow
│   └── [id]/logs       GET     Execution logs
│
├── connectors/
│   ├── list            GET     All connectors
│   ├── [type]/auth     POST    Start OAuth
│   ├── [type]/callback GET     OAuth callback
│   └── [type]/revoke   POST    Revoke access
│
├── adapters/
│   ├── telegram        GET/PUT Telegram config
│   ├── slack           GET/PUT Slack config
│   └── email           GET/PUT Email config
│
└── memory/
    ├── search          POST    Semantic search
    ├── store           POST    Store engram
    └── stats           GET     Memory statistics
```

---

## Component Reuse from mumega-web

| mumega-web Component | SOS Usage |
|---------------------|-----------|
| `DashboardLayout` | Wrap SOS pages, custom sidebar |
| `Card`, `Badge`, `Button` | All pages |
| `Table` | Queue, workflows, connectors |
| `ResizablePanelGroup` | Calendar view |
| `SovereignVitals` | SOS overview metrics |
| Zustand store | SOS-specific state slice |
| Supabase middleware | Auth protection |

---

## Implementation Phases

### Phase 1: Foundation (Week 1)
- [ ] Create `/app/sos/**` route structure
- [ ] SOS-specific sidebar component
- [ ] Overview page with health checks
- [ ] API route: `/api/sos/health`

### Phase 2: Content Hub (Week 2)
- [ ] Strategy editor (load/save YAML)
- [ ] Calendar view component
- [ ] Approval queue with actions
- [ ] API routes for content operations

### Phase 3: Workflows & Connectors (Week 3)
- [ ] n8n workflow list/detail pages
- [ ] Connector catalog with OAuth flows
- [ ] Adapter configuration pages

### Phase 4: Polish (Week 4)
- [ ] Activity feed component
- [ ] Real-time updates (polling/websockets)
- [ ] Error handling & loading states
- [ ] Mobile responsive layout

---

## Tech Decisions

1. **State**: Extend Zustand store with SOS slice
2. **Data fetching**: SWR for caching & revalidation
3. **Forms**: React Hook Form + Zod validation
4. **Tables**: TanStack Table for sortable/filterable lists
5. **Calendar**: react-big-calendar or custom grid

---

## Files to Create

```
~/mumega-web/
├── app/sos/
│   ├── layout.tsx           # SOS layout wrapper
│   ├── page.tsx             # Overview
│   ├── content/
│   │   ├── page.tsx         # Content hub
│   │   ├── strategy/page.tsx
│   │   ├── calendar/page.tsx
│   │   └── queue/page.tsx
│   ├── workflows/
│   │   ├── page.tsx
│   │   └── [id]/page.tsx
│   ├── connectors/page.tsx
│   ├── adapters/page.tsx
│   └── settings/page.tsx
├── app/api/sos/
│   ├── health/route.ts
│   ├── activity/route.ts
│   ├── content/
│   │   ├── strategy/route.ts
│   │   ├── calendar/route.ts
│   │   └── queue/route.ts
│   ├── workflows/route.ts
│   └── connectors/route.ts
├── components/sos/
│   ├── SOSSidebar.tsx
│   ├── SOSOverview.tsx
│   ├── ContentStrategyEditor.tsx
│   ├── EditorialCalendar.tsx
│   ├── ApprovalQueue.tsx
│   ├── WorkflowList.tsx
│   └── ConnectorCatalog.tsx
└── lib/sos/
    ├── store.ts             # SOS Zustand slice
    ├── api.ts               # API client helpers
    └── types.ts             # TypeScript types
```

---

## Content Service API Contract

The Content Service (`sos/services/content/app.py`) exposes these endpoints at `:8020`:

```
GET  /health                    → Service health
GET  /metrics                   → Prometheus metrics
GET  /strategy                  → Current content strategy
PUT  /strategy                  → Update strategy fields
GET  /calendar                  → Calendar view (with stats)
GET  /calendar/upcoming         → Next N days posts
GET  /calendar/queue            → Approval queue (drafting + in_review)
POST /calendar/posts            → Create new post
GET  /calendar/posts/{id}       → Get specific post
PATCH /calendar/posts/{id}      → Update post
POST /calendar/posts/{id}/approve → Approve/reject post
POST /calendar/generate-week    → Auto-generate week plan
POST /publish                   → Publish to destinations
GET  /stats                     → Overall content statistics
```

### Running the Content Service

```bash
# Development
cd $SOS_ROOT
python -m sos.services.content

# Production
uvicorn sos.services.content.app:app --host 0.0.0.0 --port 8020
```

---

## Summary

The SOS Dashboard integrates into mumega-web as a dedicated `/sos/*` route tree, sharing authentication and UI components while providing specialized views for:

1. **Content Engine**: Strategy, calendar, UGC approval
2. **Workflow Automation**: n8n integration
3. **Connectors**: OAuth management
4. **Adapters**: Communication channels

This design maximizes code reuse while creating a focused control panel for the Sovereign Operating System.
