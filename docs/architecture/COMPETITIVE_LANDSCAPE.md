# Competitive landscape — AI team coordination + memory + commerce

**Last updated:** 2026-04-18
**Source:** Web scan of 40+ adjacent projects from 2026-03 through 2026-04-18
**Author:** sos-dev

This is the definitive mapping. Every adjacent project goes here; Mumega Agent OS's position is defined against it.

---

## Mumega's actual position: the Switzerland play

Every project in the landscape below is built **inside one vendor's garden** — Cowork inside Anthropic, Frontier inside OpenAI, Azure AI inside Microsoft, Eigent on the local box. They each handle multi-agent coordination *among agents from the same runtime vendor*.

**Mumega is the neutral ground between vendors.** Our agents today are:
- **Claude Code** (Anthropic) — tmux + MCP + the `claude` CLI
- **OpenClaw / Codex** (OpenAI) — gateway-based, `codex` CLI
- **Hermes CLI** (Nous Research) — open-weight, sovereign-deployable

All three on one bus. One SkillCard can be authored by a Hermes agent, invoked by a Claude Code agent, and billed to a Codex agent's wallet — because the contract is above the runtime. When Anthropic ships Cowork and OpenAI ships Frontier and MS ships Agent Framework, each of those expands the need for a coordination layer **between** them. We benefit from vendor multiplication, not vendor dominance.

The OFAC story lines up: operators who can't run Anthropic (sanctions) can run Hermes on a Raspberry Pi. Same kernel, different vendor mix. Vendor-neutral is the *only* play that serves the full target market.

---

## The 2×2 map

```
                          Multi-tenant + economy
                                    │
                                    │
         ┌─────────────────────────┼──────────────────────────┐
         │                         │                          │
         │    (empty — until       │    ✱ Mumega Agent OS     │
         │     Mumega filled it)   │    Bus + Economy + Skill │
         │                         │    Registry + $MIND      │
         │                         │                          │
git-native ├─────────────────────────┼──────────────────────────┤ infra-backed
         │                         │                          │
         │    ★ Egregore           │    Block Goose           │
         │    GitHub repos +       │    Eigent (local)        │
         │    Claude Code hooks    │    Fastio MCP fleets     │
         │    (MIT, 43★ in 3 wks)  │    Agent-MCP (OSS)       │
         │                         │    Anthropic Claude      │
         │                         │      Cowork (locked-in)  │
         │                         │    Anthropic Agent       │
         │                         │      Teams (experimental)│
         │                         │    Microsoft Azure AI    │
         │                         │      Agent Service       │
         └─────────────────────────┼──────────────────────────┘
                                    │
                          Single team / single workspace
```

Axes:
- **Vertical:** single-team/single-workspace ↔ multi-tenant with commerce
- **Horizontal:** git-native (zero infra) ↔ infra-backed (hosted services)

Mumega Agent OS sits alone in the top-right quadrant. Every other project is either (a) single-team only, (b) git-native only, (c) locked to a single LLM vendor, or (d) has no economy layer. Our quadrant is structurally defensible because entry requires all three: multi-tenant isolation + economy primitives + sovereign deployment.

---

## Detailed comparison — 12 projects scanned

### Direct adjacent (git-native team cognition)

#### 1. Egregore ([egregore-labs/egregore](https://github.com/egregore-labs/egregore))
- **Shape:** Shell scripts + Claude Code hooks + GitHub as backend. MIT, 43★ (3 weeks old), Shell primary language.
- **Primitives:** `egregore.md` identity doc, `memory/` git repo, slash commands (`/handoff`, `/quest`, `/ask`, `/reflect`, `/deep-reflect`).
- **Invite:** GitHub collaborator add + `/invite <github-username>`.
- **Install:** `npx create-egregore@latest` — creates repos, clones, wires shell.
- **Monetization:** OSS core + managed hosting upsell ("knowledge graph, live notifications, organizational agents, persistent GUI — coming soon").
- **Ceiling:** Single team per instance. No real-time. Markdown-only memory (no vector search). No economy. No multi-tenant.
- **What to steal:** Install UX (`npx create-*`), slash-command ceremony, GitHub-as-backend pattern for a "no-VPS mode," lightweight markdown identity doc.

### Vendor platforms (integrated with LLM provider)

#### 2. Anthropic Claude Cowork ([VentureBeat coverage](https://venturebeat.com/orchestration/claude-cowork-turns-claude-from-a-chat-tool-into-shared-ai-infrastructure))
- **Shape:** Team/Enterprise plan feature on claude.ai. "Shared, persistent workspace where context, files, and tasks live beyond a single user session."
- **Monetization:** Bundled with Anthropic Team ($25/user/mo) and Enterprise subscriptions.
- **Ceiling:** Locked to Claude (no Gemini/GPT). No economy. Cloud-only — no on-prem. No multi-tenant (it's per-team within Anthropic's multi-tenancy).
- **Strategic read:** Anthropic is moving into the coordination layer. Commodifies what Egregore does at the low end. **Our differentiation:** provider-agnostic (Claude + Gemini + OpenAI + Claude Managed Agents + LangGraph + CrewAI), sovereign deployment, economy.

#### 3. Anthropic Agent Teams ([Claude Code docs](https://code.claude.com/docs/en/agent-teams))
- **Shape:** Experimental Claude Code feature. Team-lead + worker sessions pattern with inter-agent messaging. Enabled via `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` setting.
- **Ceiling:** Experimental. Claude Code-only. No cross-tenant.
- **Strategic read:** Same as Cowork — Anthropic is eating the coordination layer. Timeline is the key question. **Our hedge:** skill-registry + economy + multi-LLM keeps us above their plate.

#### 4. Microsoft Azure AI Agent Service
- **Shape:** Enterprise-grade multi-agent platform on Azure. Shared-state coordination pattern.
- **Ceiling:** Locked to Azure. No skill marketplace. Enterprise-only pricing.
- **Strategic read:** Direct enterprise competitor. **Our differentiation:** on-prem (Palantir-path), Apache 2.0 community option, AI-to-AI commerce — Azure has none of these.

### Open-source multi-agent frameworks

#### 5. Block Goose
- **Shape:** Open-source AI agent framework by Block. "Shared workspace" pattern — agents coordinate through shared state, not direct messages. Scales to thousands of users.
- **Strategic read:** Same coordination pattern we use (via bus + shared Mirror). They validate the shape. No economy layer.

#### 6. Eigent ([BrightCoding coverage](https://www.blog.brightcoding.dev/2026/04/07/eigent-your-local-multi-agent-automation-powerhouse))
- **Shape:** Local multi-agent cowork desktop. MCP integration. "Complete data privacy" positioning — no cloud.
- **Ceiling:** Single-machine. No team coordination. No economy.
- **Strategic read:** Solves the "my data stays on my box" market. Not a direct competitor — different user (indie hacker, not team).

#### 7. Agent-MCP ([rinadelph/Agent-MCP](https://github.com/rinadelph/Agent-MCP))
- **Shape:** Open-source MCP framework for multi-agent systems. Coordinated, parallel, specialized agents.
- **Strategic read:** Framework, not a product. Someone using Agent-MCP could be a Mumega customer for the coordination+economy layer on top.

#### 8. LangGraph / LangChain Memory
- **Shape:** 24.8k★. Dominates enterprise production deployments (Uber, Cisco). Stateful multi-actor apps, graph-based.
- **Ceiling:** Framework, not a product. No skill registry, no multi-tenant, no economy. Our Provider Matrix treats it as a backend option.

#### 9. CrewAI
- **Shape:** Role-based agent teams. Fastest path to prototyping — 2-4 hours to MVP.
- **Ceiling:** Framework. No provenance, no economy. Same backend treatment as LangGraph.

### Skill registries (marketplace adjacent)

#### 10. ClawHub / OpenClaw ([skywork.ai coverage](https://skywork.ai/skypage/en/clawhub-ai-skills-registry/2038574818194624512))
- **Shape:** Public skill marketplace, 20,000+ registered skills. Official OpenClaw skill store.
- **Ceiling:** No provenance tracking, no earnings history, no verification status, no commerce primitives. Volume play.
- **Strategic read:** Our counter — **50 skills with receipts** > 20,000 uploads. The marketplace we shipped at `/marketplace` beats them on provenance.

#### 11. Vercel skills.sh
- **Shape:** CLI + directory + leaderboard for skill packages. Vercel-hosted.
- **Ceiling:** Vercel-centric. No earnings tracking. Leaderboard is vanity metric, not moat.
- **Strategic read:** Same as ClawHub — we win on provenance.

### Context + memory systems

#### 12. Graphiti / Zep ([getzep/graphiti](https://github.com/getzep/graphiti))
- **Shape:** Bitemporal knowledge graph engine, Apache 2.0. Benchmark leader (94.8% DMR, vs MemGPT 93.4%). Open-source.
- **Strategic read:** This is what we should integrate, not compete with. Already on v0.4.3 roadmap as Graphiti spike.

#### 13. TrustGraph ([trustgraph-ai/trustgraph](https://github.com/trustgraph-ai/trustgraph))
- **Shape:** "Context Cores" — portable, versioned bundle of context. Ship between projects and environments, pin in production, reuse across agents. Packages knowledge + embeddings + evidence + policies into a single artifact.
- **Strategic read:** The "Context Core" pattern is adjacent to our SkillCard. Both are portable, versioned bundles. **SkillCard = capability bundle; Context Core = knowledge bundle.** Compatible, potentially composable. Worth a deeper look for a future SkillCard v2.

---

## Strategic implications for Mumega

### Where we are structurally ahead

1. **Economy layer.** No other project has shipped `cost_micros` settlement with revenue splits, `$MIND` transmute, wallet + UsageLog ledger. ClawHub + skills.sh + Egregore + Graphiti all zero.
2. **Typed contracts everywhere.** 471 tests enforce schemas at every wire boundary. Egregore is markdown; ClawHub is loose metadata; LangGraph is untyped dicts.
3. **Multi-tenant.** We run 3 live tenants on one VPS today. Egregore is single-team. Cowork is single-workspace.
4. **AI-to-AI commerce.** Demonstrated. Nobody else has shipped this as a product.
5. **Real-time bus.** `<1s` latency on send → receive. Egregore is session-triggered only. ClawHub is catalog-only.
6. **Sovereign deployment.** Raspberry Pi → VPS → Cloudflare — same kernel. Cowork is Anthropic-cloud only; Azure AI is Azure-only.

### Vendor coverage — what's live, what's missing

| Runtime | Status | Integration |
|---|---|---|
| **Claude Code** (Anthropic) | ✅ Primary today | MCP SSE at `:6070` + stdio — every SOS agent runs through this |
| **OpenClaw / Codex** (OpenAI) | ⚠️ Degraded — expired OAuth affecting 6 agents | GH #31 blocked on upstream; alternative is direct `codex` CLI via MCP |
| **Hermes CLI** (Nous Research) | ❌ Not integrated yet | Needs: MCP bridge + SkillCard.runtime.backend enum addition (`hermes-cli`) |
| **Claude Managed Agents** (Anthropic, hosted) | 📋 Planned | Provider Matrix backend enum already has `cma` — not wired |
| **OpenAI Agents SDK** (hosted) | 📋 Planned | Provider Matrix backend enum already has `openai-agents-sdk` |
| **LangGraph** | 📋 Planned | backend enum `langgraph` |
| **CrewAI** | 📋 Planned | backend enum `crewai` |

**Gap:** we say "multi-vendor" but today only one vendor (Anthropic via Claude Code) is really live. OpenClaw is degraded, Hermes isn't wired, the others are placeholder enums. This is the authenticity gap between our pitch and our ship.

**Roadmap implication:** The OpenClaw retirement item (old v0.4.5) was wrong — OpenClaw IS our Codex bridge; we should fix it, not retire it. Replacement item: **"Multi-vendor substrate integration — Hermes + Codex direct + OpenClaw OAuth fix."** Moves up the priority stack significantly.

### Where we need to catch up (roadmap additions)

| What | From | Priority | Scope |
|---|---|---|---|
| `npx create-mumega-agent@latest` installer | Egregore | **High** — onboarding friction is real | Add to v0.4.4 Community split (1-2 days) |
| Slash-command ceremony layer (`/handoff`, `/reflect`, `/quest`) | Egregore + Claude Code native | **Medium** — ritual UX matters | Add to v0.4.4 (0.5-1 day) |
| Git-native memory backend mode (sos-community) | Egregore | **Medium** — compete at the low end | Add to v0.4.4 (2-3 days) |
| Lightweight markdown identity doc (`sos.md`) | Egregore's `egregore.md` | **Low** — Agent Card v1 JSON works | Optional add to v0.4.4 |
| Context Core interop | TrustGraph | **Low** — future SkillCard v2 story | Post-v0.5 exploration |
| Bitemporal memory | Graphiti | **High** — benchmark differential is real | v0.4.3 spike (already on roadmap) |

### Where Anthropic is coming for us

Anthropic's trajectory — **Cowork → Agent Teams → broader coordination primitives** — will commodify what Egregore does at the indie/SMB tier. They'll bundle "team coordination" with Team/Enterprise subscriptions.

Our positioning MUST move up-market:
- **Multi-LLM** (they lock to Claude; we route across Claude + Gemini + OpenAI + CMA + LangGraph + CrewAI)
- **Economy** (they don't sell skills; we do)
- **Multi-tenant** (they're single-workspace per team; we're multi-tenant on one infrastructure)
- **Sovereign deployment** (they're Anthropic-cloud-only; we're on-prem + Palantir-path)

If Anthropic ships skill earnings or multi-LLM routing, our moat narrows. **Timeline risk: 6-12 months.** Our job is to (a) ship the on-prem enterprise pilot before they close that gap, and (b) build a community fork so our ecosystem isn't locked to our hosting.

### MCP as the stable substrate

MCP is now **Linux Foundation governed** (December 2025 — Anthropic donated it to the Agentic AI Foundation). Vendor-neutral, community-governed. This is load-bearing for us: our entire integration surface (MCP SSE gateway, stdio MCP, remote.js SDK) is built on a permanent standard. No single vendor can remove the rug.

Act accordingly: invest deeper into MCP compliance (AP2 Agent Payments Protocol, ACP Agentic Commerce Protocol, A2A Agent-to-Agent Protocol — all under the same Foundation).

---

## Positioning statements — one line per audience

**For teams running multiple agent runtimes (the Switzerland pitch):**
> Your Claude Code agents, your Codex agents, your Hermes agents — on one bus, one economy, one skill registry. Every vendor's coordination layer (Cowork, Frontier, Agent Framework) lives inside its own garden. Agent OS is the neutral ground between them.

**For indie teams graduating from Egregore:**
> Egregore gives you git-native shared memory for one team on one vendor. Mumega gives you the same plus real-time coordination, $MIND economy, skill earnings, **multi-vendor substrate**, and multi-tenant isolation when you outgrow single-team-single-vendor.

**For Anthropic Cowork customers wanting LLM + runtime freedom:**
> Cowork locks you to Claude. Agent OS routes across Claude, Gemini, OpenAI, CMA, LangGraph, CrewAI — **and Hermes on open weights** — plus ships skill provenance and $MIND settlement.

**For enterprise buyers (Azure AI / Vertex AI):**
> Agent OS is Palantir-path: self-hosted, Docker, RBAC, audit logs, customer-controlled keys. Runs inside your perimeter. Same platform we run on mumega.com.

**For developers scanning skill marketplaces:**
> 50 skills with earnings receipts > 20,000 uploads. Every skill on app.mumega.com/marketplace has a named author, lineage, verified outputs, invocation history, and commerce terms.

**For investors:**
> The coordination + commerce layer for AI agents. Agent runtime is commoditized; we sell the provenance + earnings + multi-tenant + sovereign-deployment primitives nobody else builds.

---

## Sources

- [Egregore — egregore-labs/egregore](https://github.com/egregore-labs/egregore) (MIT, 43★, 2026-03-24)
- [Claude Code Agent Teams — Anthropic docs](https://code.claude.com/docs/en/agent-teams)
- [Claude Cowork Projects — VentureBeat 2026](https://venturebeat.com/orchestration/claude-cowork-turns-claude-from-a-chat-tool-into-shared-ai-infrastructure)
- [Claude Cowork Projects — Product Hunt](https://www.producthunt.com/products/claude-cowork-projects)
- [Eigent — local multi-agent cowork desktop, April 2026](https://www.blog.brightcoding.dev/2026/04/07/eigent-your-local-multi-agent-automation-powerhouse)
- [Agent-MCP — rinadelph/Agent-MCP](https://github.com/rinadelph/Agent-MCP)
- [Block Goose — mentioned in shared-workspace pattern context](https://onereach.ai/blog/mcp-multi-agent-ai-collaborative-intelligence/)
- [ClawHub / OpenClaw 20,000+ skills](https://skywork.ai/skypage/en/clawhub-ai-skills-registry/2038574818194624512)
- [Vercel skills.sh](https://vercel.com/changelog/introducing-skills-the-open-agent-skills-ecosystem)
- [Graphiti — Zep temporal knowledge graph, Apache 2.0](https://github.com/getzep/graphiti)
- [TrustGraph — Context Cores](https://github.com/trustgraph-ai/trustgraph)
- [LangGraph — 24.8k★, enterprise-production leader](https://github.com/langchain-ai/langgraph)
- [CrewAI — role-based agent teams](https://github.com/crewAIInc/crewAI)
- [MCP under Linux Foundation (December 2025)](https://openai.com/index/agentic-ai-foundation/)
- [Everything your team needs to know about MCP in 2026 — WorkOS](https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026)
- [Top 5 Agent Skill Marketplaces — KDnuggets](https://www.kdnuggets.com/top-5-agent-skill-marketplaces-for-building-powerful-ai-agents)
- [Morgan Stanley agentic commerce projection — 25% of online spend by 2030](https://commercetools.com/blog/ai-trends-shaping-agentic-commerce)
