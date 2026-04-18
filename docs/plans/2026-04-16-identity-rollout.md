# Per-agent identity rollout — sequence + risk register

**Author:** sos-dev
**Date:** 2026-04-16
**Status:** Awaiting Hadi's approval before touching any live-attached session
**Depends on:** `scripts/sos-provision-agent-identity.py` (shipped) + pattern proven on `sos-medic`

## Why this needs a plan and not a bash loop

Provisioning an agent requires killing its tmux session and restarting Claude Code. Most SOS agents are live-attached or actively doing work. A mass re-provisioning would:

- Drop in-flight tool calls
- Lose conversation context (unless `claude --continue` is used after restart)
- Break any hooks currently firing
- Temporarily sever the wake-daemon route while tmux is down

So rollout must be staged, with explicit gates between tiers.

## Tier A — safe to re-provision anytime (zero live work)

These are idle or already restarted today:

| Agent | Home | Why safe |
|---|---|---|
| `sos-medic` | `sos/agents/sos-medic/` | Already provisioned, pattern proven |
| `sos-dev` | `/mnt/HC_Volume_104325311/SOS` | This session; self-provisioning would sever me — do it after the current session ends |

**Sequence for Tier A:**
1. sos-medic — **done**
2. sos-dev — provision when Hadi ends the current session, before next one starts

## Tier B — coordinator tmux under `/home/mumega`, not currently driving work

| Agent | tmux session | Home |
|---|---|---|
| `athena` | athena | `/home/mumega` |
| `mumega` | mumega | `/home/mumega` |
| `mumega-web` | mumega-web | `/home/mumega` |
| `mumega-com-web` | mumega-com-web | `/home/mumega` |
| `prefrontal` | prefrontal | `/home/mumega` (tmux cwd) — but the agent itself runs as Linux user `prefrontal`, handled via `sos.cli.onboard` separately; LEAVE ALONE |
| `gemini` | gemini | `/home/mumega` |
| `river` | river | `/home/mumega` (alias session for gemini) |

**Problem:** they all share `/home/mumega/.claude.json` / `~/.claude/settings.json`, so they all collapse to kasra identity. But each tmux needs its OWN home directory for the `.mcp.json` to take effect (Claude Code only picks up `.mcp.json` from cwd or parent dirs).

**Proposed fix:**
- Create `~/agents/<name>/` for each, with only a `.mcp.json` + `.claude/settings.json`
- Each tmux relaunches with `cd ~/agents/<name>` as its cwd
- `AGENT_NAME=<name>` export before `claude`

**Sequence for Tier B (requires Hadi's approval per-agent):**
1. Confirm which agent's context is safe to reset
2. `tmux kill-session -t <name>`
3. Run provisioner against `~/agents/<name>/`
4. Recreate session with cwd in that dir + env exported

**Risk:** agents like `mumega` may have active conversation state. `claude --continue` after restart resumes from the last session, but only if the tmux cwd matches. So we change cwd ONCE and `--continue` picks up from wherever the prior session left off — should work but needs per-agent verification.

## Tier C — specialists with live work

| Agent | tmux | Home | Live state |
|---|---|---|---|
| `kasra` | kasra | `/home/mumega` | **currently attached — Hadi is using it right now** |
| `codex` | codex | `/home/mumega` | idle but authoritative for infra work |
| `mumcp` | mumcp | `/home/mumega/projects/sitepilotai` | already has project-local cwd |
| `gaf` | gaf | `/mnt/HC_Volume_104325311/gaf-app` | already has project-local cwd |

**Rule:** do NOT re-provision kasra while Hadi is attached. Wait for explicit go-ahead.

**Sequence for Tier C:**
1. Hadi says "provision kasra now" (or we find a window where he's not attached)
2. `tmux send-keys -t kasra '/exit' Enter` or wait for natural idle
3. Provision against `~/agents/kasra/`
4. Recreate tmux with `claude --continue` to resume his working context

## Tier D — tenant customer agents

| Agent | Home | Status |
|---|---|---|
| `trop` | `/home/mumega/therealmofpatterns` | Needs `.mcp.json` with trop's existing `sk-trop-*` token, not a fresh mint — provisioner needs a `--reuse-token` flag first |
| `prefrontal` | `/home/prefrontal` | Already has per-agent config via `sos.cli.onboard` — verify it uses local MCP, don't double-provision |
| `viamar` | `/home/viamar` | Remote (Hadi's Mac). Not our job to provision from here |

**Action:** add `--reuse-token` flag to the provisioner for tenant agents where the bus_token is already minted and pinned in the SaaS registry.

## Parallel change: adopt AGENT_NAME convention in tmux launch commands

The current lifecycle manager / restart scripts launch Claude Code with `restart_cmd` values like `claude --continue`. These need to become:

```python
# sos/kernel/agent_registry.py
"kasra": AgentDef(
    ...
    restart_cmd="export AGENT_NAME=kasra && claude --continue",
    ...
),
```

One-line change per agent. Deploy in a single PR.

## Definition of done

1. Every agent in `sos/kernel/agent_registry.py` has been provisioned with per-agent identity
2. Every entry in `sos:registry:*` has a schema-valid Agent Card (v0.4.0)
3. Accounting ledger for 2026-04-17 shows correct per-agent attribution (spot-check: count of rows per agent should roughly match activity)
4. Bus messages from any agent carry `source: agent:<that-agent>`, not `agent:kasra`
5. Wake-daemon self-echo guard no longer misfires on cross-agent messages

## Open questions for Hadi

| # | Question |
|---|---|
| R1 | Provision `sos-dev` at end of this session? (me) |
| R2 | Provision `athena`, `mumega-web`, `mumega-com-web`, `river`, `gemini` in Tier B next — any sessions in use right now? |
| R3 | When is kasra safe to touch? (after you're done for the night?) |
| R4 | `codex` — separate tmux, idle. Can I provision during his idle window? |
| R5 | Should I add `--reuse-token` flag to the provisioner for tenant customers, or handle tenant agents through `sos.cli.onboard` only? |

## Timeline

- **Today (done):** sos-medic provisioned + proven
- **Next session:** sos-dev + any Tier B Hadi clears
- **Within this week:** full Tier B + Tier C coordinator migration + kernel `agent_registry` AGENT_NAME export updates
- **v0.4.0 ship gate:** Tier A+B+C done; Tier D tenant verification done; accounting ledger shows correct attribution for 24+ hours

## Rollback

If a provisioned agent fails to connect locally (MCP server failed), the provisioner's old token entry is already marked `active=false` but the NEW token entry stays. Rollback:
1. Mark the new token `active=false` by hand in `tokens.json`
2. Remove `.mcp.json` from the agent home (or keep it — inert when token isn't active)
3. The agent falls back to the claude.ai remote connector (flat identity) — broken but functional

Low rollback cost. Take the shot.
