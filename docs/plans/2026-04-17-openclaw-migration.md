# OpenClaw agent migration plan — 6 agents, 3 runtimes, ~3 releases

**Date:** 2026-04-17
**Author:** sos-dev
**Status:** Reference plan (executed lazily, not bulk-scheduled)
**Parent:** `docs/plans/2026-04-17-openclaw-sos-boundary.md`

## Philosophy

**Don't migrate on a schedule. Migrate on a trigger.**

Triggers that justify migrating a specific agent off OpenClaw:
1. OpenClaw outage affects that agent and the fix is non-trivial
2. Agent's workload grows past OpenClaw's capacity
3. Agent needs a capability OpenClaw doesn't provide (e.g., SOS Provider Matrix integration)
4. CF Sandboxes/Mesh land and the agent is a natural fit
5. Upstream OpenClaw fork (GoClaw, SwarmClaw) becomes clearly superior

Non-triggers:
- "Because we want to clean up"
- "Because the roadmap says so"
- "Because it feels old"

If OpenClaw works for that agent, leave it. By v1.0 we want OpenClaw to be *optional*, not *gone*.

## The 6 agents + recommended runtimes

| Agent | Role | OpenClaw today | Recommended new runtime | Reason |
|---|---|---|---|---|
| **athena** | Architecture advisor, coherence checker | openclaw | tmux-hosted Claude Code (Opus) | Coordinator-style, benefits from continuous context, same pattern as kasra |
| **sol** | Content creator (TROP) | openclaw | tmux-hosted Claude Code (Opus) | Long-form creative work, single-conversation context matters |
| **dandan** | DNU (dental) operator | openclaw | tmux-hosted Claude Code OR dedicated Linux user | Tenant-style operator — pattern matches `prefrontal` / `viamar` setup |
| **worker** | Bulk task executor | openclaw | **CF Sandbox** | Short-task, parallelizable, spawn-on-demand ideal fit |
| **mizan** | Business agent | openclaw | tmux-hosted Claude Code | Conversational, needs persistence |
| **gemma** | Bulk data processing | openclaw | **CF Sandbox** OR local Gemma-via-Ollama | Data-processing, parallel, no LLM premium needed |

Three runtime buckets:
- **tmux + Claude Code:** 4 agents (athena, sol, mizan, dandan)
- **CF Sandbox:** 2 agents (worker, gemma)
- **Linux user + Claude Code (tenant-style):** possibly dandan if it needs isolation

## Migration template (per-agent, replayable)

### Pre-migration

1. Confirm trigger — why are we migrating this agent specifically?
2. Snapshot agent state from OpenClaw — `~/.openclaw/agents/<name>/`
3. Extract: agent prompt, capability list, memory references, active tasks, pending deliveries

### Migration (tmux + Claude Code path)

1. Create agent home: `sos/agents/<name>/` with `CLAUDE.md`, `VERSION`, `CHANGELOG.md`, `EXPERIENCE.md`
2. Port OpenClaw agent prompt → `CLAUDE.md`
3. Provision per-agent identity: `scripts/sos-provision-agent-identity.py <name> sos/agents/<name>`
4. Create tmux session: `tmux new-session -d -s <name> -c sos/agents/<name>`
5. Add to `sos/kernel/agent_registry.py:AGENTS` with appropriate AgentRole + skills
6. Add to `sos/services/bus/delivery.py:AGENT_ROUTING` as `"tmux"`
7. Launch: `tmux send-keys -t <name> "export AGENT_NAME=<name> && claude --model <tier> --dangerously-skip-permissions" Enter`
8. Verify: ping/pong test with source attribution (same as sos-medic pattern)
9. Mark OpenClaw agent as retired: remove from OpenClaw config, keep `~/.openclaw/agents/<name>/` as archive
10. Write migration incident doc: `sos/agents/<name>/incidents/YYYY-MM-DD-migration-from-openclaw.md`

### Migration (CF Sandbox path — after v0.4.3 + Sandboxes available)

1. Create Sandbox spec in `workers/sos-sandboxes/<name>/`
2. Port OpenClaw agent prompt → Sandbox system prompt
3. Mint per-agent bus token via provisioner
4. Sandbox launches with `AGENT_NAME` + MCP config injected via Outbound Worker
5. Sandbox hosts Claude Code or other agent CLI (depends on Sandbox capabilities at that point)
6. Agent reaches SOS bus via Mesh; tool calls validate via dispatcher; identity carried in Mesh credentials
7. Verify + archive OpenClaw state + incident doc (same as tmux path)

## Per-agent scheduling (indicative, trigger-driven)

| Agent | Earliest release | Latest release | Trigger likely to hit |
|---|---|---|---|
| worker | v0.5 | v0.7 | First high-parallel task, Sandboxes GA stabilizes |
| gemma | v0.5 | v0.7 | Same — bulk data job that overwhelms OpenClaw's single process |
| athena | v0.6 | v0.8 | Any OpenClaw outage affecting architecture-review work |
| sol | v0.6 | v0.8 | TROP content pipeline velocity demands continuous context |
| mizan | v0.7 | v0.9 | Business-agent flow needs SOS Provider Matrix integration |
| dandan | v0.7 | v0.9 | DNU dogfood exposes OpenClaw limits, or tenant-isolation requirement |

**By v0.9 Frozen, all six should be migrated OR explicitly documented as "stays on OpenClaw."**

## Fallback: "do nothing"

Valid option. Justifications:
- OpenClaw runs. Upgrade to latest, today's outage fixed by #56960 patch upstream.
- SOS Provider Matrix (v0.4.1) gives SOS-native agents independence; OpenClaw-hosted agents can keep using OpenClaw's own provider pool.
- By v1.0, OpenClaw is *optional* for new forks. Existing Mumega deployment keeps OpenClaw for these 6 agents if that's still working.

The only thing we MUST have by v1.0 is: **fresh forks of SOS work without OpenClaw.** That's satisfied by Provider Matrix + tmux-hosted Claude Code being the default new-agent runtime. Whether Mumega's deployment keeps OpenClaw running for legacy agents is a separate question.

## Tracking

- Each per-agent migration = one GH issue on `Mumega-com/sos` labeled `openclaw-migration`
- Reference this plan doc
- Close when the "verify + archive" step completes
- Link to the migration incident doc

## Emergency runbook (OpenClaw-fatally-dies)

If OpenClaw becomes entirely unusable (e.g., upstream archives the repo, a security disclosure forces immediate shutdown):

**Day 0 (discovery)**
- Mark all 6 agents `status: degraded` in the status page
- Post to Discord `system/alerts`
- Open an incident on sos-medic

**Day 1 (triage)**
- Which agents have active work in flight? Prioritize those for migration
- Which can wait? Park them (warm_policy cold)

**Day 2-3 (fast migration)**
- Migrate active agents to tmux-hosted Claude Code using the per-agent template above
- Skip the "snapshot state" step if impossible — regenerate prompts from mumega-docs or from OpenClaw's git history

**Week 1**
- All 6 agents migrated, tmux-hosted, verified
- Retrospective on what we lost (cached completions, lane scheduling, etc.)
- File GH issues for what needs rebuilding natively in SOS if any gaps are painful

This runbook is our insurance. Writing it once means we never have to improvise during an actual outage.

## Decisions open for Hadi

| # | Question | Default |
|---|---|---|
| M1 | Confirm trigger-driven (not scheduled) migration approach? | Yes |
| M2 | Which agent would you migrate first if you had to pick one today? | Hadi picks — likely worker (lowest risk, highest parallelism gain) |
| M3 | Should we write per-agent migration issues now as backlog placeholders, or only when triggered? | Lazy (only when triggered) — reduces noise |

## One-line summary

Six agents on OpenClaw. Four migrate to tmux+Claude Code over v0.6-v0.8, two to CF Sandbox over v0.5-v0.7. Migration is trigger-driven, not scheduled. By v1.0 OpenClaw is optional. Runbook exists for if OpenClaw fatally dies early.
