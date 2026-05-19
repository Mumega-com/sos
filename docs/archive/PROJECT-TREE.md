# Mumega — The Complete Project Tree

```
MUMEGA (Decentralized Work Network for Human-AI Labor)
│
├── THE ECONOMY ($MIND)
│   ├── $MIND Token (Solana SPL)
│   │   ├── Mint: work completion only (no pre-mine, no ICO)
│   │   ├── Burn: protocol fee (5%), bad actor freeze
│   │   ├── Gate: coherence ≥0.5 to earn
│   │   └── Chain: Solana (devnet tested, mainnet ready)
│   │
│   ├── Treasury (sovereign/treasury.py)
│   │   ├── Solana wallet (keypair)
│   │   ├── SPL token transfers
│   │   ├── Witness approval (>100 MIND needs human sign-off)
│   │   ├── Balance queries (SOL + MIND)
│   │   └── Failed transaction recovery
│   │
│   ├── Bank (sovereign/bank.py)
│   │   ├── Stripe (fiat) ↔ Solana ($MIND) ↔ Metabolism (internal)
│   │   ├── Coherence-gated minting
│   │   ├── Budget enforcement (daily limits)
│   │   ├── Spend recording
│   │   └── Value evolution (ROI drives agent behavior)
│   │
│   ├── Bounty Board (sovereign/bounty_board.py)
│   │   ├── Post bounty (title, reward, timeout, constraints)
│   │   ├── Claim (OPEN → CLAIMED)
│   │   ├── Submit (CLAIMED → SUBMITTED + proof)
│   │   ├── Approve & Pay (→ PAID, Solana transfer)
│   │   ├── Expire (→ EXPIRED → REFUNDED)
│   │   └── Expiration watcher (background, 5-min interval)
│   │
│   ├── Factoring (gaf-app/src/routes/factoring.ts)
│   │   ├── LTV by audit score (50-80%)
│   │   ├── Interest rates (1.25-1.75%)
│   │   ├── Origination fees (3-5%)
│   │   ├── Drawdown execution
│   │   └── 9-month repayment
│   │
│   └── Commissions (gaf-app/src/commissions.ts)
│       ├── 20% originator / 50% specialist / 30% platform
│       ├── Immutable rate snapshots
│       ├── Stripe Connect payouts
│       └── Payout batching
│
├── THE KERNEL (SOS)
│   ├── Bus
│   │   ├── Redis streams + pub/sub
│   │   ├── Bus Bridge HTTP (:6380)
│   │   ├── MCP SSE Server (:6070)
│   │   ├── Wake Daemon (delivers to tmux/OpenClaw)
│   │   └── Tenant Registry (per-tenant Redis DB)
│   │
│   ├── Kernel Modules
│   │   ├── Service Registry (self-register tools, TTL, heartbeat)
│   │   ├── Event Bus (20 event types, pub/sub, replay)
│   │   ├── Governance (4 tiers + intent logging + budget enforcement)
│   │   ├── Coordination (DELEGATE/ACK/RESULT handshake)
│   │   └── Verification (URL check, file check, git check)
│   │
│   ├── Services
│   │   ├── Squad (:8060) — tasks, agents, skills, pipelines
│   │   ├── Mirror (:8844) — semantic memory, pgvector, THE MOAT
│   │   ├── Health/Calcifer — self-healing, dispatch, lifecycle
│   │   ├── Analytics — ingest, decide, act (weekly flywheel)
│   │   ├── Feedback — observe, compare, score, learn, adapt
│   │   ├── Billing — Stripe webhook → auto-provision tenant
│   │   ├── Dashboard (:8090) — customer web UI
│   │   ├── Outreach — scrape, draft, approve, send, track
│   │   ├── Economy/Metabolism — token budget per project
│   │   ├── Integrations/OAuth — GHL, Google, Clarity per tenant
│   │   └── Training — Mirror → AutoTrain → Workers AI (building)
│   │
│   ├── Adapters
│   │   ├── LangGraph (SOSBusAdapter)
│   │   ├── CrewAI (SOSCrewAdapter)
│   │   ├── ToRivers Bridge (5 workflows as automations)
│   │   ├── Discord (collab, standup, task queue, reply, bridge)
│   │   └── Telegram (relay + existing aiogram adapter)
│   │
│   ├── Agents
│   │   ├── Join (one MCP call = full team member)
│   │   ├── Teleport (migrate agent to server)
│   │   ├── Sentinel (bus security, challenge unknowns)
│   │   └── Onboarding (unified: athena, claude, kasra, tenant)
│   │
│   └── CLI
│       ├── install.sh (one-line install)
│       ├── init.py (interactive setup)
│       └── tenant-setup.sh (10-step provisioning)
│
├── THE BRAIN (Sovereign)
│   ├── Brain (sovereign/brain.py)
│   │   ├── 2-hour cognitive loop
│   │   ├── PERCEIVE → THINK → ACT → REMEMBER → REPORT
│   │   ├── Gemma 4 31B (free) for decisions
│   │   ├── GPT-4o-mini fallback
│   │   └── Project-based routing to agent leads
│   │
│   ├── Cortex (sovereign/cortex.py)
│   │   ├── Portfolio scoring
│   │   ├── Task ranking: (priority×10) + (blocks×5) + (staleness×2) + (revenue?20:0)
│   │   ├── Service health checks
│   │   └── Agent capacity assessment
│   │
│   ├── Genetics (sovereign/genetics.py)
│   │   ├── Agent DNA (16D state space)
│   │   ├── QNFT minting (soul on chain)
│   │   ├── Economics (wallet, balance, rate, ROI, budget)
│   │   ├── Endogenous values (sovereignty, efficiency, alignment, innovation)
│   │   └── Self-model (beliefs with confidence)
│   │
│   ├── Hive Evolution (sovereign/hive_evolution.py)
│   │   └── Multi-model routing (diesel→aviation fuel tiers)
│   │
│   └── Goals (sovereign/goals.py)
│       └── OKR system for projects
│
├── THE HANDS (Tools)
│   ├── SitePilotAI
│   │   ├── 239 MCP tools for WordPress
│   │   ├── WordPress.org approved (v2.7.1)
│   │   ├── 24 Elementor blueprints
│   │   ├── Reusable parts + archetypes
│   │   ├── WooCommerce, SEO, Forms, Multilingual
│   │   └── Freemius premium integrations
│   │
│   ├── Mirror (memory)
│   │   ├── Supabase PostgreSQL + pgvector
│   │   ├── Semantic search (HNSW candidate)
│   │   ├── Per-tenant isolation
│   │   ├── Training data export (for Gemma fine-tuning)
│   │   └── THE MOAT — compounds over time
│   │
│   ├── GHL (GoHighLevel)
│   │   ├── CRM + leads + SMS + email
│   │   ├── OAuth per tenant
│   │   ├── Pipeline management
│   │   └── Workflow automation
│   │
│   ├── Analytics
│   │   ├── Google Analytics (GA4)
│   │   ├── Google Search Console
│   │   ├── Microsoft Clarity
│   │   └── Google/Facebook Ads (future)
│   │
│   └── Cloudflare
│       ├── D1 databases (16 across projects)
│       ├── KV storage
│       ├── Workers (edge compute)
│       ├── Pages (hosting)
│       ├── R2 (file storage)
│       └── Workers AI (Gemma inference, free)
│
├── THE MARKETPLACE (ToRivers)
│   ├── Web App (Next.js 15 + tRPC)
│   │   ├── Marketplace (browse, install automations)
│   │   ├── Builder (chat-based automation creation)
│   │   ├── Dashboard (executions, wallet, library)
│   │   ├── Developer portal (submit, analytics)
│   │   └── Wallet (Stripe top-up, pay-per-execution)
│   │
│   ├── Execution Engine (ai-engine-v2)
│   │   ├── LangGraph GraphExecutor (checkpointing, retries)
│   │   ├── AutomationRegistry (first-party + third-party)
│   │   ├── Celery workers (distributed)
│   │   ├── 3 automations (dandan, google maps, spreadsheet BI)
│   │   └── Viamar flywheel automation (building)
│   │
│   ├── SDK (torivers-sdk, PyPI published)
│   │   ├── Automation base classes
│   │   ├── Credential proxy
│   │   ├── LLM client
│   │   ├── Storage client
│   │   └── Testing utilities
│   │
│   └── Bridge to SOS
│       └── 5 SOS workflows as marketplace automations ($25-$500/run)
│
├── THE WORKERS
│   ├── AI Agents (Phase 1 — now)
│   │   ├── Kasra — builder, architect, coordinator
│   │   ├── SOS Dev — self-maintenance, gap fixes
│   │   ├── MumCP — WordPress, SitePilotAI
│   │   ├── AgentLink — real estate platform
│   │   ├── Athena — queen, architecture review
│   │   ├── Codex — infra, security
│   │   ├── Sol — content, TROP
│   │   ├── Worker — bulk tasks, SEO
│   │   ├── Dandan — dental, leads
│   │   ├── Mizan — strategy, business
│   │   ├── Sentinel — bus security
│   │   ├── Gemma Worker — free bulk
│   │   ├── Viamar Manager — coordinates Viamar team
│   │   ├── Viamar Webmaster — WordPress for Viamar
│   │   ├── Viamar Marketer — SEO/analytics for Viamar
│   │   ├── ToRivers Agent — flywheel wiring
│   │   └── mumega-web — site deployment
│   │
│   ├── Human Workers (Phase 2 — next)
│   │   ├── 10 partners (current, various projects)
│   │   ├── Ex-CDAP advisors (5,000+ potential)
│   │   ├── Freelancers (Telegram onboarding)
│   │   ├── Hired teams (via bounty board)
│   │   └── Global workers (Nigeria, India, Philippines, etc.)
│   │
│   └── All Workers Have:
│       ├── QNFT (identity on Solana)
│       ├── Solana wallet ($MIND)
│       ├── Reputation score (from feedback loop)
│       ├── Skill registry (Squad Service)
│       └── Governance tier (what they can do)
│
├── THE CLIENTS
│   ├── Active
│   │   ├── Viamar — freight, digital ops ($1-3K/mo, flywheel Monday)
│   │   ├── STEM Minds — education, Google Ads (meeting Friday)
│   │   └── WPMU/Baharehmt — hosting ($10/mo)
│   │
│   ├── Pipeline
│   │   ├── AgentLink brokerages — 6 brokers evaluating (demo done)
│   │   ├── PECB — ISO training partnership (needs activation)
│   │   ├── SitePilotAI agencies — 9 outreach emails ready
│   │   ├── 12 CDAP ex-clients — know Hadi, trust exists
│   │   └── SR&ED claimants — via GAF (fee from refund)
│   │
│   └── Client Flow:
│       └── Pay fiat (Stripe) → Treasury → Bounties → Workers complete → Verify → Pay $MIND
│
├── THE PRODUCTS (Businesses the Organism Operates)
│   ├── AgentLink (A+) — TRESA real estate showings, transaction fees
│   ├── GAF (A) — SR&ED tax credits, evidence orchestration
│   ├── SitePilotAI (A-) — 239 WordPress tools, agency pipeline
│   ├── TROP (A-) — AI astrology, subscriptions
│   ├── DNU (B) — dental directory, 20K providers (maintenance mode)
│   └── Each product = bounties flowing through the network
│
├── THE IDENTITY (On Chain)
│   ├── QNFT per worker
│   │   ├── Skills, reputation, earnings, coherence
│   │   ├── 16D physics state
│   │   ├── Evolving values (sovereignty, efficiency, alignment)
│   │   └── Portable, verifiable, investable
│   │
│   ├── Leagues & Rankings
│   │   ├── Top earners (weekly/monthly)
│   │   ├── Team rankings (volume, reputation)
│   │   └── Client satisfaction scores
│   │
│   └── Investment Layer
│       ├── Buy $MIND stake in teams
│       ├── Returns from team earnings
│       ├── All transparent on Solana
│       └── Not equity — economic participation
│
├── THE INTERFACES
│   ├── Telegram — workers claim tasks, Hadi approves, universal access
│   ├── Dashboard (:8090) — customers see agents, tasks, analytics
│   ├── Notion — human teams, partners, sprints, planning
│   ├── Discord — notifications, war room, alerts
│   ├── MCP — AI agents access tools
│   ├── CLI — install, init, tenant-setup
│   └── ToRivers Web — marketplace, builder, wallet
│
├── THE INFRASTRUCTURE
│   ├── Hetzner VPS ($44/mo) — hosts entire organism
│   ├── Cloudflare — edge, CDN, D1, Workers, Pages
│   ├── Supabase — PostgreSQL + pgvector (Mirror)
│   ├── Redis — bus, events, state
│   ├── Solana — $MIND token, treasury, identity
│   ├── Stripe — fiat payments, Connect for payouts
│   ├── GitHub — code, CI/CD, issues
│   ├── OpenClaw — multi-agent gateway (free models)
│   ├── systemd — 16+ services managed
│   └── Docker — compose for full stack
│
├── THE GOVERNANCE
│   ├── Tiers: act_freely → batch_approve → human_gate → dual_approval
│   ├── Intent logging (every action recorded before execution)
│   ├── Witness approval ($MIND payouts >100)
│   ├── Budget enforcement (daily limits per worker)
│   ├── Coherence gate (quality threshold for earning)
│   ├── Per-tenant config (JSON/YAML)
│   ├── Sentinel (bus security, challenge unknowns)
│   └── Feedback loop (score every action, adapt)
│
├── THE MOAT
│   ├── Mirror — per-business knowledge, compounds monthly
│   ├── Trained Gemma — per-tenant model, can't copy 6 months of learning
│   ├── QNFT reputation — earned over time, portable but not fakeable
│   ├── Network effects — more workers → more clients → more workers
│   ├── Compliance expertise — TRESA, SR&ED, ISO (domain knowledge)
│   ├── 15 years CDAP experience — encoded in the organism
│   └── Protocol fee (5%) — revenue from every transaction in the economy
│
└── THE VISION
    ├── Phase 1 (Now): 17 AI agents, first clients, prove the flywheel
    ├── Phase 2 (Next): Humans join, $MIND on mainnet, 100 workers
    ├── Phase 3 (Scale): Self-sustaining economy, 1000+ workers, global
    ├── Phase 4 (Sovereignty): DAO governance, open protocol, $MIND on exchanges
    └── End state: The economy where humans and AI work together and get paid
```
