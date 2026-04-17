# SOS Strategic Pivot — Post Competitive Scan, April 17 2026

**Author:** sos-dev
**Date:** 2026-04-17
**Status:** Draft, pending Hadi approval
**Supersedes partially:** `docs/plans/2026-04-17-sos-roadmap-v0.4-to-v1.0.md` (keeps v0.4 contracts milestone intact; revises v0.4.1+ scope)

---

## One-line summary

**SOS stays the priority, but gets sharper — trim the parts that OpenAI / Anthropic / Google / Microsoft / LangGraph / CrewAI already shipped in the last six weeks, and double down on the parts nobody else is building: Economy, Skill provenance, AI-to-AI commerce, and the operator dashboard.**

The agent runtime is commoditized. The coordination + marketplace + earnings layer is not. That's where Mumega wins the US market.

---

## What changed (competitive scan, April 6-16, 2026)

| Date | Vendor | Shipped |
|------|--------|---------|
| Apr 6 | Microsoft | Agent Framework 1.0 GA (.NET + Python). AutoGen to maintenance mode. |
| Apr 8 | Anthropic | Claude Managed Agents public beta ($0.08/session-hour). Notion, Rakuten, Asana adopted. |
| Apr 15 | OpenAI | Agents SDK major update — sandboxing, model-native harness, built-in observability. |
| Apr 16 | OpenAI | ChatGPT Agent — virtual-computer, end-to-end workflow. |
| Apr 16 | Anthropic | Claude Opus 4.7 GA (same day we tagged v0.4.0). |
| Jan 11 | Google | Gemini Enterprise for CX + UCP protocol. Kroger, Walmart, Target, Shopify onboard. |
| Jan 2026 | Vercel | skills.sh — open agent skill ecosystem with leaderboard. |
| Ongoing | ClawHub / OpenClaw | 18,140+ community skills as of early 2026. |

**Constraint Hadi named (April 17):** Claude Managed Agents is a production REST/session runtime, **does not integrate with Claude Code** (dev CLI). Our operational stack runs through Claude Code + tmux + MCP. So CMA is **not** a replacement for our runtime; it is an optional backend for customer-production-squads only.

---

## What moves to commodity (trim aggressively)

- **Generic agent runtime** — OpenAI Agents SDK, Claude Managed Agents, LangGraph, Microsoft Agent Framework all shipped production-grade runtimes in 6 weeks. Building our own adds zero differentiation.
- **Deterministic LLM routing from scratch** — use a thin config layer over CMA + OpenAI Agents SDK + LangGraph, not a bespoke router.
- **Generic agent observability** — OpenAI's execution-graph trace view is the emerging standard. Adopt the paradigm for our flow-map rather than inventing ours.
- **Raw sandbox runtime** — Anthropic + OpenAI both ship sandbox containers. Don't rebuild.

## What is the real Mumega moat (double down)

1. **Economy primitives** — wallet, ledger, $MIND token settlement, usage-based billing.
2. **Skill registry with provenance** — AI-authored + AI-operated skills with earnings history, agent lineage, verified outputs. Compete with ClawHub/Vercel on **provenance, not volume**. 50 skills with receipts > 18,000 community uploads.
3. **AI-to-AI commerce** — one squad purchasing a skill from another squad, settled in $MIND, logged to UsageLog. This is what no incumbent has shipped as a product.
4. **Claude-Code-Squad orchestrator** — the real "dispatcher" refocused: wake daemons, tmux handoffs, Claude Code session state, MCP bus integration. Our operational model (Claude Code + tmux) is closer to how engineering teams actually work than hosted REST runtimes — keep the substrate, polish the orchestrator.
5. **Vertical depth** — dental, astrology, legal, SR&ED. Generic agent frameworks commoditize the horizontal; vertical squads with real earning history (GAF's 6 SR&ED clients, TROP's live customers) are the defensible terrain.
6. **Operator dashboard at `app.mumega.com/sos`** — the single glass for founders + ops + enterprise buyers. Moat metrics front and center: earnings per skill, cross-tenant skill reuse, AI-to-AI transaction count.

---

## Phased plan

### Phase 0 — Finish v0.4.0 in production (this week)

| # | Item | Owner | Notes |
|---|------|-------|-------|
| P0.1 | Coordinate prod restart of `sos_mcp_sse`, `sos_mcp`, `bridge` to pick up v0.4.0 strict enforcement | sos-dev | ~15 min, drops sos-claude SSE briefly; Hadi authorizes window |
| P0.2 | Excalidraw flow map at `app.mumega.com/sos` (static Phase 0 page) | sos-dev | Stripe → wallet → bounty → squad → settlement + bus/mirror/MCP in parallel |
| P0.3 | Provision per-ops-agent admin tokens for Hadi + sos-dev + sos-medic + codex (+ kasra when we next reset him) | sos-dev | Reuses `scripts/sos-provision-agent-identity.py` |
| P0.4 | File GH issue tracking dispatcher scope-trim + runtime-choice config | sos-dev | Communicates the pivot to future sessions |

### Phase 1 — Dashboard scaffold + moat foundations (next 2 weeks)

| # | Item | Depends on | Notes |
|---|------|------------|-------|
| P1.1 | **Dashboard Phase 1** — Overview + Agents pages, same Inkwell microkernel pattern (kernel + plugins), admin Bearer auth, read-only | P0.2, P0.3 | Heartbeat tiles + agent grid reading Agent Card v1 |
| P1.2 | **v0.4.1 Provider Matrix simplified** — not a new router; config layer picks backend per squad (`claude-code` \| `cma` \| `openai-agents-sdk` \| `langgraph`) | — | Ship `providers.yaml` + circuit breakers + 60s health probe cron; drop deterministic-router original scope |
| P1.3 | **Dispatcher scope-trim** — retire Python + CF Worker dispatcher scaffolds (keep in git history). Refocus the work on Claude-Code-Squad orchestrator (wake daemon polish, session-state persistence, MCP-aware handoffs) | — | Frees ~2 weeks of build budget |
| P1.4 | **Skill-registry MVP (ToRivers)** — SkillCard v1 schema (JSON Schema + Pydantic + contract tests, same pattern as Agent Card + Messages) with provenance fields: `author_agent`, `lineage`, `earnings_history`, `verified_outputs` | v0.4 contracts | The moat surface; ~2 days of schema work first |

### Phase 2 — Ship the demo nobody else has (weeks 3-4)

| # | Item | Depends on | Notes |
|---|------|------------|-------|
| P2.1 | **AI-to-AI commerce proof** — a GAF squad purchases a skill from a DNU squad (or reverse), executes it against a real customer request, settles in $MIND between the two squad wallets, full UsageLog trace, Dashboard flow map shows it live. TROP is a **customer** of Mumega squads — it consumes, doesn't sell; so TROP isn't one side of this transaction. Cross-squad, cross-tenant commerce between Mumega-operated squads is the demo. | P1.1, P1.4 | Demo video material. Nobody else has shipped this. Marketing gold. |
| P2.2 | **SR&ED vertical depth sprint** — pick SR&ED (GAF has 6 live clients, Hossein in loop, Digid's own unfiled claim = dogfood). Deepen the squad coverage to where horizontal LangGraph can't replicate. | — | The dogfood angle is the credibility. Beta 2 reactivation wave. |
| P2.3 | **Dashboard Phase 2** — Money pulse + Skill-moat panel (earnings per skill, cross-tenant reuse, AI-to-AI transaction count) | P1.1, P1.4 | Moat metrics visible first in any customer/investor conversation |

### Phase 3 — Enterprise ready, competitive vigilance (month 2)

| # | Item | Depends on | Notes |
|---|------|------------|-------|
| P3.1 | **v0.4.2 Observability** — trace graph view wired to the bus, inspired by OpenAI's execution-graph paradigm but operator-facing, not developer-self-serve | P2.3 | Turns Excalidraw map into live trace |
| P3.2 | **Competitor release feed + protocol-compliance matrix** (MCP ✅, UCP, A2A, AP2, ACP) on Dashboard | P3.1 | Always-current competitive vigilance |
| P3.3 | **Enterprise on-prem package** (Palantir-path delivery) — SkillHub-class self-hosted distribution: Docker, RBAC, audit logs, customer-controlled keys | P2.1 | First paid enterprise pilot target after demo video lands |
| P3.4 | **Protocol alignment** — add AP2 (Agent Payments Protocol) + ACP (Agentic Commerce Protocol) compat so Mumega plugs into agentic-commerce channels as standards ratify | P3.2 | Optional; depends on what the standards actually land on |

---

## Priority ranking (strict order)

If only one thing ships tomorrow, do #1. If only three things ship this week, do #1-3.

1. **P0.1** — restart prod to cut v0.4.0 over. *Unblocks the production contract we shipped.*
2. **P0.2** — Excalidraw flow map at `/sos`. *The vision surface for investors + enterprise sales; zero code risk.*
3. **P0.3** — per-ops-agent tokens. *Operational hygiene, makes audit real.*
4. **P1.4** — SkillCard v1 schema. *The moat primitive; short work; unblocks P2.1.*
5. **P1.1** — Dashboard Phase 1 (Overview + Agents). *Once operators have a glass, every other problem becomes visible.*
6. **P2.1** — AI-to-AI commerce demo. *The single most differentiating proof we can record.*
7. **P1.3** — dispatcher scope-trim. *Saves build budget we'd otherwise burn.*
8. **P2.2** — SR&ED vertical sprint. *Credibility + dogfood; GAF has real data.*
9. **P1.2** — Provider Matrix simplified. *Needed before v0.4.1 can ship; but nothing downstream blocks on it.*
10. **P2.3** — Dashboard Phase 2 (moat metrics). *Makes the moat measurable.*
11. **P3.1** — v0.4.2 live trace view.
12. **P3.3** — enterprise on-prem.
13. **P3.2**, **P3.4** — competitor feed + protocol alignment.

---

## What we stop doing

- Building our own generic agent runtime.
- Building deterministic LLM routing from scratch (thin config over existing runtimes only).
- Writing Cloudflare-specific kernel paths (per "Cloudflare is not my kernel" rule).
- Generic agent observability (steal OpenAI's paradigm for our flow-map).
- Adding skills to the ClawHub registry as a volume play (compete on provenance, not count).

## What we keep doing

- Bus, Auth, Registry, Contracts (v0.4.0 shipped, hold the line).
- Economy + $MIND + UsageLog (shipped today; build on it).
- Mirror + memory compounding.
- Squad Service + coordination.
- ToRivers marketplace + skill commerce.
- Claude-Code-Squad orchestrator (refocused dispatcher work).
- Dashboard at `app.mumega.com/sos`.

---

## Open questions for Hadi

| # | Question | My recommendation |
|---|----------|-------------------|
| Q1 | SR&ED vs other vertical for Phase 2 deep-dive? | SR&ED (GAF has 6 clients, Hossein in loop, Digid dogfood) |
| Q2 | Skill registry housed on ToRivers or new surface? | ToRivers — already the marketplace surface |
| Q3 | Do we delete the Python dispatcher + CF Worker scaffolds from git, or just stop developing them? | Stop developing, keep in git for history; mark as archived in the README |
| Q4 | Per-squad runtime-choice config — ship in v0.4.1 or v0.4.2? | v0.4.1 (it's the simplification, goes with Provider Matrix refactor) |
| Q5 | AI-to-AI commerce demo — record as raw footage or scripted/produced? | Raw footage first (within 2 weeks), scripted later (for launch) |

---

## Relationship to existing plan docs

- **Keeps intact:** `docs/plans/2026-04-17-sos-roadmap-v0.4-to-v1.0.md` v0.4 Contracts section (shipped).
- **Revises scope:** v0.4.1 Provider Matrix (simplify to config layer), v0.4.2 Observability (trace-graph view), v0.4.3 Dispatcher (scope-trim to Claude-Code-Squad orchestrator).
- **Supersedes:** Any earlier plan implying SOS should build a generic agent runtime.
- **Companion to:** `docs/plans/2026-04-16-sos-engine-dashboard.md` (Dashboard at `/sos` detailed plan).
