# SOS — Sovereign Operating System

Microkernel runtime for autonomous AI agents: multi-model routing, squad coordination, memory, economy, and tools.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  ADAPTERS  Telegram · Discord · CLI · Web        │
├─────────────────────────────────────────────────┤
│  GATEWAY   Auth · Rate limits · Audit (:6062)    │
├─────────────────────────────────────────────────┤
│  ENGINE    Model routing · Failover · Dreams (:6060) │
├─────────────────────────────────────────────────┤
│  SERVICES  Memory · Squad · Economy · Identity   │
├─────────────────────────────────────────────────┤
│  KERNEL    schema · identity · capability ·      │
│            config · physics · soul · intent      │
└─────────────────────────────────────────────────┘
            ↕ Redis bus (sos:channel:*)
```

## Services

| Service  | Port | Purpose                                      |
|----------|------|----------------------------------------------|
| engine   | 6060 | Multi-model orchestration, model failover     |
| memory   | 6061 | Vector storage, engram retrieval              |
| gateway  | 6062 | JSON-RPC entrypoint, auth, rate limiting      |
| squad    | 8060 | Teams, tasks, skills, pipelines, connectors   |
| economy  | —    | Work ledger, $MIND token accounting           |
| identity | —    | Agent pairing, capability grants              |
| tools    | —    | Tool registry                                 |
| content  | —    | Blog, social, content generation              |
| voice    | —    | Voice I/O pipeline                            |
| mcp      | 6070 | SSE server exposing agent tools via MCP       |

## Quick Start

```bash
# Start core services via systemd
sudo systemctl start sos-engine
sudo systemctl start sos-memory
sudo systemctl start sos-gateway-mcp
sudo systemctl start sos-gateway-bridge

# Check status
sudo systemctl status sos-engine

# Run engine directly (dev)
cd ~/SOS && SOS_ENGINE_PORT=6060 python3 -m sos.services.engine
```

## Squad System

Squad is the team coordination layer (built 2026-04-05).

- **Squad**: a named group of agents with shared channels (`sos:channel:squad:{id}`)
- **Task**: unit of work assigned to a squad or agent; tracked in state
- **Skill**: a packaged capability with a `SKILL.md` (Anthropic standard); 13 skills in `sos/skills/`
- **Pipeline**: ordered sequence of skills applied to an input
- **Connector**: integration adapter (GHL, Zapier, etc.)

```bash
# Squad service runs on :8060
cd ~/SOS && python3 -m sos.services.squad
```

Bus channels follow the pattern `sos:channel:squad:{squad_id}` over Redis.

## Models

| Model              | Role                        |
|--------------------|-----------------------------|
| Gemma 4 31B        | Brain / portfolio scoring (free) |
| Claude Haiku 4.5   | Workers / high-volume tasks |
| GPT-5.4            | Architecture decisions      |
| Claude Opus 4.6    | Complex reasoning            |

## Development

**Add a service:**
1. Create `sos/services/<name>/` with `service.py` and `__init__.py`
2. Add contract at `sos/contracts/<name>.py` defining request/response types
3. Register in `sos/services/__init__.py`
4. Add systemd unit under `systemd/sos-<name>.service` if long-running

**Add a skill:**
1. Create `sos/skills/<skill-name>/`
2. Add `SKILL.md` (Anthropic standard: name, description, input schema, output schema)
3. Add implementation file; register in squad skills registry (`sos/services/squad/skills.py`)

## Project Structure

```
SOS/
├── sos/
│   ├── kernel/          # schema, identity, capability, config, physics, soul
│   ├── services/        # engine, memory, gateway, squad, economy, identity, tools, content, voice
│   ├── skills/          # 13 packaged skills (each with SKILL.md)
│   ├── contracts/       # service boundary definitions
│   ├── agents/          # agent definitions (squad_id, roles, capabilities)
│   ├── bus/             # Redis messaging (redis_bus.py)
│   ├── mcp/             # MCP SSE server (:6070)
│   ├── adapters/        # Telegram, Discord, CLI, Web
│   └── clients/         # service clients
├── sovereign/
│   ├── cortex.py        # portfolio scoring brain
│   └── cortex_events.py # event-driven brain
├── systemd/             # systemd service units
├── scripts/             # ops scripts
└── tests/
```

## Environment

```bash
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
REDIS_URL=redis://localhost:6379
SOS_ENGINE_PORT=6060
```
