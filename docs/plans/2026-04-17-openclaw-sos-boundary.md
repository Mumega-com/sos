# OpenClaw ↔ SOS — the honest boundary

**Date:** 2026-04-17
**Author:** sos-dev
**Status:** Architectural reference (not a commitment — framing for future work)
**Context:** OpenClaw is a third-party tool Hadi adopted and is adapting; it is not a first-party SOS component. Today's `openclaw-gateway` OAuth outage surfaced the maintenance cost of depending on external code we don't own.

## The current reality

OpenClaw runs on this VPS as `openclaw-gateway` (Node.js, `:18789`, PID 1206, ~56 hours uptime). It hosts agents **we rely on**: athena, sol, dandan, worker, mizan, gemma — every non-tmux coordinator/executor in the current ecosystem.

Home directory: `/home/mumega/.openclaw/` — 10 subdirectories of OpenClaw-internal state:
- `agents/`, `cache/`, `canvas/`, `completions/`, `credentials/`, `cron/`, `delivery-queue/`, `devices/`, `discord/`, `identity/`

**Hadi did not write OpenClaw.** He picked it up online and has been adapting it to SOS's needs. That shapes every decision below.

## What OpenClaw actually does (best-inferred from running state)

OpenClaw is a **multi-agent host + LLM-provider router**, not an MCP/bus/squad platform. Three functional layers:

1. **Agent hosting** — spawns and supervises agent processes inside its own runtime. The OpenClaw agents (athena etc.) don't exist as independent OS processes; they live inside the gateway.
2. **LLM provider abstraction** — receives "run model X with prompt Y" from hosted agents, routes the request through an OAuth/API-key pool across 9+ providers (Anthropic direct, Codex OAuth, GitHub Copilot, OpenRouter, Google Gemini, Cloudflare AI Gateway, Imagen, etc.).
3. **Model fallback waterfall** — when a provider fails (auth, rate limit, error), automatically retries with the next provider in a configured chain. Today's outage: primary (`openai-codex` OAuth) expired, all 9 fallbacks also unauthenticated, cascade failure.

## What SOS does

SOS is a **multi-agent coordination + tenant-isolation platform**, not an agent host. Functional layers:

1. **Bus** — Redis Streams for agent-to-agent messaging with authenticated tokens, tenant scoping, durable delivery
2. **Squad service** — tasks, skill routing, wallets, goals, FMAAP gate, multi-tenant squads
3. **Mirror** — semantic memory (pgvector engrams) with tenant isolation
4. **SaaS layer** — customer onboarding, Stripe, tenant registry, subdomain provisioning
5. **Economy** — $MIND token, on-chain settlement, cost metering
6. **Identity + MCP** — per-agent bus tokens, MCP SSE gateway, wake daemon for tmux agents

## The boundary — where does SOS stop and OpenClaw start?

```
┌──────────────────────────────────────────────┐
│              AGENT PROCESSES                  │
│                                               │
│   tmux-hosted (Claude Code, Codex CLI,        │
│     Gemini CLI): kasra, mumcp, gaf, sos-dev,  │
│     sos-medic, trop, prefrontal               │
│                                               │
│   openclaw-hosted (Node runtime inside        │
│     gateway process): athena, sol, dandan,    │
│     worker, mizan, gemma                      │
└──────────────────┬───────────────────────────┘
                   │
      ┌────────────┴────────────┐
      │ for coordination        │ for LLM calls
      ▼                         ▼
┌──────────────┐          ┌────────────────────┐
│  SOS (ours)  │          │ OpenClaw (external) │
│              │          │                    │
│  bus         │          │  agent runtime     │
│  squad       │          │  LLM provider pool │
│  mirror      │          │  OAuth refresh     │
│  saas        │          │  model fallback    │
│  MCP gateway │          │  cache/completions │
│  $MIND       │          │                    │
└──────────────┘          └────────────────────┘
```

- **For a tmux-hosted agent** (kasra, sos-medic): the agent's Claude Code process uses Hadi's Claude Max subscription directly for LLM calls. OpenClaw is not in the path. SOS handles coordination via MCP.
- **For an openclaw-hosted agent** (athena, sol, dandan): the agent lives inside OpenClaw's Node runtime. LLM calls go through OpenClaw's provider pool. SOS coordination (bus, task, memory) is reached via MCP/HTTP from inside that runtime — but today, they may not all be fully wired; some openclaw agents only register on the SOS bus via `sos:registry:*` and don't consume SOS tools richly.

## What OpenClaw has that SOS lacks

Naming them explicitly so future decisions are honest:

1. **LLM provider pool + OAuth management.** OpenClaw has `credentials/` with live refresh tokens across 9 providers. SOS has no equivalent — every Claude Code session burns Hadi's Max sub, every external API call requires manually-set env vars, no pooling, no rotation.
2. **Model fallback waterfall.** OpenClaw retries across providers on failure. SOS has zero fallback — if Anthropic is down, every SOS agent is dead.
3. **Response cache** (`cache/`). Cached LLM responses save on repeat prompts. SOS has semantic memory (Mirror) but no prompt-response cache.
4. **Agent runtime.** OpenClaw spawns, supervises, and shuts down agent instances programmatically. SOS relies on tmux + systemd + manual orchestration.
5. **Lane scheduling.** Parallel work streams per agent (main, heartbeat, background). SOS has one-shot-per-call.
6. **Delivery queue with retry.** `delivery-queue/` implies outbound messages with retry semantics. SOS bus is fire-and-forget.

## Strategic framing — three options, one recommendation

Given OpenClaw is external:

### Option A: Replace (absorb fully into SOS)

Build LLM-provider router, OAuth pool, cache, fallback, lane scheduling — all natively in SOS. ~6-12 months. At the end, OpenClaw is gone.

- **Pro:** Full ownership, single codebase, aligned with SOS architecture, no external dependency
- **Con:** Half-year detour from product work. Rebuilding patterns someone already solved. Every bug is ours to fix from scratch.

### Option B: Integrate (treat OpenClaw as first-class vendor)

Commit to OpenClaw as the LLM layer. Invest in maintaining the adaptation. Build SOS around the assumption that OpenClaw handles model access.

- **Pro:** Ship v0.4 faster. Use working 9-provider routing today.
- **Con:** Depending on code we didn't write, upstream that may not exist, patches that may not be merged. Today's OAuth outage is a preview.

### Option C: Learn and ship shallow (recommended) ⭐

Use OpenClaw as **prior art**, not dependency. Build simplified, SOS-native versions of the patterns that matter most:

- **v0.4.1 Provider Matrix** — 3-4 providers (Anthropic, OpenAI, Google, OpenRouter) with health probes, fallback, OAuth where applicable. Not 9-deep, not every provider. Good enough for SOS-native agents.
- **Credentials pool** — small `~/.sos/credentials/` pattern, rotate via provisioner.
- **Prompt-response cache** — skip for v0.4; add in v0.5+ if a specific use case requires it.
- **Agent runtime** — don't build. tmux + systemd is fine. If an agent needs to be hosted without a full CLI, write a thin Python worker spec, not a full runtime.
- **Lane scheduling** — skip. Squad task system with priorities + claims is SOS's equivalent of lanes and it's simpler.

Keep OpenClaw running **as-is** for the agents that already depend on it (athena, sol, dandan, worker, mizan, gemma). Don't migrate them. When OpenClaw breaks (like today), fix the immediate issue (re-auth the OAuth token). Don't invest in OpenClaw improvements.

Over 12-24 months, as SOS-native capabilities mature, we gradually migrate individual agents from OpenClaw-hosted to SOS-native-tmux when there's a specific reason to. OpenClaw shrinks naturally.

### Why Option C

- **Honest about ownership.** OpenClaw is external → SOS doesn't stake its roadmap on it.
- **Shipping matters.** v0.4 has concrete deliverables that close real bugs. Option A delays those by 6 months. Option C ships v0.4 on schedule.
- **Patterns worth absorbing are specific, not wholesale.** Provider Matrix captures 80% of OpenClaw's value for 5% of the code volume.
- **OpenClaw stays a working reference.** When we build Provider Matrix, we can literally read OpenClaw's `credentials/` format and its fallback config as reference material for our shallow equivalent.
- **Exit cost is low.** If OpenClaw dies completely (upstream project dies, config format drifts, OAuth flow breaks irreparably), we migrate affected agents (athena, sol, etc.) to tmux-hosted Claude Code one at a time. Not elegant, but possible.

## Upstream OpenClaw — what research surfaced (2026-04-17)

OpenClaw is **not** a dead/orphaned project. It's actively maintained at `github.com/openclaw/openclaw` with:

- Tens of thousands of issues filed (#56960, #56206, #55777, #30055 observed)
- Official docs across multiple mirrors (`docs.openclaw.ai`, `openclaws.io`, `openclaw-ai.com`)
- An ecosystem of related projects:
  - **GoClaw** (`nextlevelbuilder/goclaw`) — Go rewrite with multi-tenant isolation and 5-layer security
  - **SwarmClaw** (`swarmclawai/swarmclaw`) — self-hosted MCP-native multi-agent runtime, 23+ LLM providers
  - **OpenClaw Mission Control** (`abhi1693/openclaw-mission-control`) — ops dashboard for gateway governance
  - **Octopus Orchestrator** — multi-agent subsystem for OpenClaw (one-head-many-arms pattern)

Key upstream bugs that match our observed behavior:

- **[#56960](https://github.com/openclaw/openclaw/issues/56960) — `openai-codex provider: refresh_token_reused loop causes severe gateway event-loop degradation`** — this is EXACTLY today's outage. Reported in OpenClaw v2026.3.28 on Linux/systemd/Debian 13. "~120 failed HTTP cycles/hour injected into gateway event loop, multi-minute response latency, heap growth to 5–6GB before OOM." Our host is Debian-derived Ubuntu, uptime 56h on `openclaw-gateway` matches this signature.
- **[#55777](https://github.com/openclaw/openclaw/issues/55777) — `Anthropic OAuth auto-refresh silently fails, gateway falls back to secondary provider`** — related OAuth-refresh class.
- **[#56206](https://github.com/openclaw/openclaw/issues/56206) — `Gateway continues selecting openrouter/... models after complete provider removal`** — provider-list staleness bug.
- **[#30055](https://github.com/openclaw/openclaw/issues/30055) — `Feature: Explicit OAuth vs API key selection in model routing + fallback chain`** — the feature they're building for exactly the auth ambiguity our journal log shows.

### Operational takeaway for Hadi

**Upgrade OpenClaw to the latest release before assuming today's outage is "ours to fix."** OpenClaw's version on our VPS is probably `v2026.3.28` or similar, matching the reporter in #56960. Upstream may have a patched release.

Commands to check + upgrade (safe, non-invasive):

```bash
# Check current version
openclaw-gateway --version  # or: grep version /home/mumega/.openclaw/manifest.json
# Check upstream latest
gh release list --repo openclaw/openclaw --limit 3
# If a newer release fixes #56960, upgrade per OpenClaw's upgrade docs
```

If upgrading fixes the Anthropic/Codex OAuth loop, we get the 6 OpenClaw-hosted agents back without any SOS-side work. **That's free mean-time-to-recovery.**

## Today's OpenClaw OAuth outage — concrete takeaway

At ~23:00 UTC on 2026-04-16, `openclaw-gateway` logged:

```
[openai-codex] Token refresh failed: 401 "invalid_request_error"
[diagnostic] lane task error: lane=session:agent:athena:main:heartbeat
  error="FailoverError: OAuth token refresh failed for openai-codex"
[model-fallback] 9 providers tried, all failed
  result: "No API key found for provider openai"
```

All 6 openclaw-hosted agents (athena, sol, dandan, worker, mizan, gemma) were effectively dead. No alarm fired because SOS has no external watchdog yet (that's v0.4.2).

**Lessons:**
1. **OpenClaw is a single point of failure** for 6 agents. Diversification matters — SOS-native agents (tmux-hosted) don't share this failure mode.
2. **No LLM fallback in SOS means we can't route around OpenClaw's outage.** v0.4.1 Provider Matrix fixes this for SOS-native agents.
3. **mumega-watch CF Worker (v0.4.2)** needs OpenClaw health probes: specifically, "can openclaw-gateway's `lane=main` complete a trivial request right now?" If not, alarm.
4. **The adaptation cost is real.** Every upstream break is Hadi's problem.

## What this means for the immediate roadmap

| Release | Change driven by OpenClaw externality |
|---|---|
| v0.4.0 Contracts | No change — contracts are SOS-native work |
| v0.4.1 Provider Matrix | **More urgent.** This is SOS's answer to OpenClaw dependency risk. Ship as planned. |
| v0.4.2 Observability Plane | **Add OpenClaw probes explicitly.** Breakables include `openclaw-gateway`, OAuth token expiry (if scrapable from credentials dir), each hosted agent's heartbeat. |
| v0.4.3 claude-dispatcher | Unchanged — dispatcher is SOS's front door, independent of OpenClaw |
| v0.5.0 Traceable | Add OpenClaw's `completions/` log as a trace sink if we can (low effort) |
| Long-term | SOS-native Provider Matrix matures. OpenClaw-hosted agents migrate to tmux-hosted incrementally, one per quarter, as specific agent evolves. No big-bang migration. |

## What NOT to do (common temptations to avoid)

- **Don't fork OpenClaw into our repo.** Keeps it external, keeps the exit path clean.
- **Don't rewrite OpenClaw in Python.** That's Option A. Don't.
- **Don't make SOS tightly integrate with OpenClaw's internal shapes.** Agents talk to SOS via MCP + bus; agents talk to OpenClaw via its native APIs. No SOS code should import OpenClaw types.
- **Don't invest in OpenClaw admin tooling.** Treat it like a utility — keep it running, fix outages, don't enhance.

## Decisions needed from Hadi

| # | Question |
|---|---|
| OB1 | Confirm Option C (learn, ship shallow). If you prefer A or B, I adjust v0.4.1 scope accordingly. |
| OB2 | OpenClaw OAuth re-auth — is that something you re-login to periodically, or do we automate it somehow? The outage will recur. |
| OB3 | Long-term: should athena, sol, dandan, worker, mizan, gemma eventually migrate to tmux-hosted Claude Code (like the others), or stay on OpenClaw indefinitely? |
| OB4 | Do we want a documented "if OpenClaw dies, here's how each hosted agent resurrects on SOS-native" migration doc? (Recommend: yes, write it once, shelve it.) |

## One-line summary

OpenClaw is prior art we run, not infrastructure we own. SOS absorbs the patterns that matter (provider matrix, credentials pool) natively, leaves the rest alone, and plans for OpenClaw's eventual decline rather than its expansion.
