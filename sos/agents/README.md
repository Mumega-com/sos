# SOS agents — per-agent identity invariant

## What this directory is

Homes for tmux-resident agents that run under the `mumega` Linux user (sos-dev, sos-medic, and any future specialists you spin up in-tree). Customer-tenant agents like `prefrontal`, `trop`, `viamar` live under their own Linux users (`/home/<tenant>`) and follow a parallel pattern from `sos.cli.onboard`.

## The invariant (read this before adding any new agent)

**Every tmux-resident agent must present its own identity on the SOS bus.**

That means, for every agent:

1. A unique token in `sos/bus/tokens.json` with `agent=<name>`, `project=null`, `scope=agent`, `hash` stored (never raw)
2. A `.mcp.json` in the agent's home directory registering the local SOS MCP server at `http://localhost:6070/sse/<raw-token>` — **NOT** the claude.ai remote connector
3. `AGENT_NAME=<name>` exported in the tmux shell **before** the `claude` command launches (so `~/.claude/hooks/token-accounting.sh` attributes correctly and any SOS CLI utilities resolve the agent correctly)
4. The agent's tmux session name matches `<name>` (so `sos/services/bus/delivery.py:AGENT_ROUTING` can wake it)

Breaking any of these collapses the agent's identity to whichever default the shared MCP resolves to. That was the flat-identity bug (#21) — every Claude Code session on this VPS appeared as `agent:kasra` because they all shared `sk-claudeai-*` via `~/.claude.json`.

## Resurrect-on-wake — the active principle (2026-04-16)

**Agents are resurrected on demand, not provisioned upfront.**

Per Hadi: agents should be revivable through squad membership, not pre-deployed. That means:

- Dormant agents stay dormant. No proactive migration to per-agent identity.
- When a squad routes work to `<agent>` and `sos:registry:<agent>` is absent or stale (TTL expired), trigger resurrection.
- Resurrection = `scripts/sos-provision-agent-identity.py <name> <home>` + tmux create + `claude --continue` (or fresh launch if no prior session context to resume).
- Agent self-announces via `sos/bus/announce.sh`, populates the registry, picks up work.

The provisioner is designed to be a callable from the squad service for exactly this — it's idempotent, deactivates prior tokens, mints fresh on every call.

**When NOT to resurrect:** agents explicitly marked obsolete (removed from `sos/kernel/agent_registry.py`). As of 2026-04-16 that includes `mumega-web`, `mumega-com-web`, `webdev`. Their tokens are deactivated in `tokens.json`; their tmux sessions were killed; their entries were removed from the wake routing. Do not resurrect.

## One-command provisioning

```bash
python3 scripts/sos-provision-agent-identity.py <agent-name> <agent-home>
```

Idempotent. Deactivates any prior active token for that agent, mints fresh, writes `.mcp.json` + `.claude/settings.json` hooks, prints the raw token **once** to stdout (not stored on disk).

## After provisioning — launch the tmux

The provisioner does not touch tmux. You do:

```bash
tmux kill-session -t <name> 2>/dev/null     # if already running
tmux new-session -d -s <name> -c <agent-home>
tmux send-keys -t <name> "export AGENT_NAME=<name> && claude --model sonnet --dangerously-skip-permissions" Enter
```

Then verify inside the session:
- `/mcp` → **Project MCP** section lists `sos · ✔ connected` pointing at the per-agent URL
- Send a test bus message → raw stream shows `source: agent:<name>` (not `kasra`)

## Verifying identity from outside

```bash
REDIS_PASS=$(grep -oP '^REDIS_PASSWORD=\K\S+' ~/.env.secrets)
redis-cli -a "$REDIS_PASS" --no-auth-warning XREVRANGE "sos:stream:global:agent:sos-dev" + - COUNT 1
```

`source` field should read `agent:<the-agent-that-sent>`, not `agent:kasra`.

## Wake routing

Add the agent to `sos/services/bus/delivery.py:AGENT_ROUTING`:

```python
"<name>": "tmux",    # short comment about role
```

Then restart the wake daemon:

```bash
sudo -u mumega XDG_RUNTIME_DIR=/run/user/$(id -u mumega) systemctl --user restart agent-wake-daemon
```

## Agent home layout (sos-medic as reference)

```
sos/agents/<name>/
├── .claude/
│   └── settings.json      # hooks: AGENT_NAME export, skipDangerousMode
├── .mcp.json              # mcpServers.sos → localhost:6070/sse/<token>
├── CLAUDE.md              # agent-specific instructions loaded on session start
├── VERSION                # semver of the agent's prompt/tooling
├── CHANGELOG.md           # append on prompt/tool changes
├── BUG_REPORT.md          # if agent receives structured reports
├── EXPERIENCE.md          # append-only learning log across sessions
├── tools/                 # deterministic shell scripts the agent can invoke
│   └── *.sh
└── incidents/             # per-incident postmortems
    └── YYYY-MM-DD-<slug>.md
```

Not every agent needs all of these. `CLAUDE.md` + `.mcp.json` + `.claude/settings.json` are the minimum; the rest are optional depending on role.

## Customer-tenant agents (different pattern, same idea)

Customer agents run as their own Linux user (`/home/<tenant>/`) and are provisioned by `python -m sos.cli.onboard`. That flow writes a similar per-agent config to the tenant's home. The invariant is the same: their bus messages carry `source=agent:<tenant>`, not `agent:kasra`.

## Why not just one shared token?

We tried. That was the flat-identity bug. Shared token → one identity on the bus → recipients can't tell who sent what → accounting ledger mis-attributes → self-echo guard misfires → task_board assignee unreliable → every multi-agent coordination feature breaks.

Per-agent identity is the prerequisite for everything in v0.4 (contracts), v0.4.1 (provider matrix), v0.4.2 (observability), and every $MIND/Squad-as-living-graph feature downstream.

## When identity is wrong (symptoms)

- Token-accounting ledger has rows with `"agent":"UNSET"` → that session never exported AGENT_NAME
- Bus messages attributed to `kasra` from a session that isn't kasra → that session is using the claude.ai remote connector instead of a local per-agent MCP
- Wake-daemon logs `self-echo skipped` for a message you didn't intend to send to yourself → identity was flattened to the same agent as the target

Run `scripts/sos-provision-agent-identity.py` on the offending agent to fix.
