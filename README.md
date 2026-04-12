# SOS — Sovereign Operating System

**MCP-native AI agent OS. Redis bus. Squad service. Multi-model engine. One URL to connect any agent.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Status: Active](https://img.shields.io/badge/status-active-brightgreen)](https://github.com/Mumega-com/sos)

---

SOS is an open source runtime for teams of AI agents. It runs on a single server — a $20/mo VPS works fine. Agents connect via MCP SSE (one URL, one config snippet). The Redis bus wakes agents on message. The squad service manages tasks. The engine routes to the right model and falls back automatically when one is unavailable.

Built for people who want to run a real multi-agent system without cloud lock-in or framework overhead.

---

## Why SOS?

| Feature | SOS | CrewAI | AutoGen | LangGraph |
|---|---|---|---|---|
| MCP-native from day one | Yes | Bolted on later | Bolted on later | Bolted on later |
| Real-time bus (wake on message) | Redis Streams + SSE | No | No | No |
| Connect any agent with one URL | Yes (SSE) | No | No | No |
| Shared agent memory | Mirror (pgvector) | No | No | No |
| Multi-model failover | Built in | Manual | Manual | Manual |
| Local-first (no cloud required) | Yes | No | No | No |
| Task queues with atomic claim | Squad Service | Basic | No | No |
| Runs on a $20/mo VPS | Yes | Needs more | Cloud-first | Cloud-first |

The core difference: **the bus IS MCP SSE**. Not a wrapper. Not an adapter added in v2. Every tool call, every agent message, every task update flows through the same MCP transport Claude Code already speaks.

---

## Architecture

```
  Claude Code / Cursor / any MCP client
        │
        │  SSE (one URL)
        ▼
  ┌─────────────────────────────────────────────┐
  │            SOS MCP Server (:6070)           │
  │   12 tools: send · inbox · peers · ask      │
  │   remember · recall · search_code           │
  │   task_create · task_list · task_update     │
  │   onboard · broadcast · status              │
  └──────────┬──────────────────────────────────┘
             │
    ┌────────▼────────┐
    │   Redis Bus     │  ← streams, pub/sub, wake-on-message
    └────┬──────┬─────┘
         │      │
   ┌─────▼──┐ ┌─▼──────────┐ ┌────────────┐
   │ Squad  │ │   Engine   │ │   Mirror   │
   │ :8060  │ │   :6060    │ │   :8844    │
   │ tasks  │ │ model route│ │  memory    │
   │ skills │ │ failover   │ │  pgvector  │
   └────────┘ └────────────┘ └────────────┘
```

Agents are not processes SOS manages. They are external — Claude Code sessions, Python scripts, whatever. They connect to the MCP SSE endpoint, get 12 tools, and can talk to each other, create tasks, read memory, and route work.

---

## Quick Start

**Prerequisites:** Python 3.11+, Redis, git

```bash
# 1. Clone
git clone https://github.com/Mumega-com/sos.git
cd sos

# 2. Install
pip install -e .

# 3. Configure
cp .env.example .env
# Edit .env: add REDIS_URL, ANTHROPIC_API_KEY, GEMINI_API_KEY, etc.
cp sos/bus/tokens.json.example sos/bus/tokens.json
# Edit tokens.json: add your agent tokens

# 4. Start core services
python3 -m sos.services.engine    # model engine      :6060
python3 -m sos.services.squad     # squad + tasks     :8060
python3 -m sos.mcp.sos_mcp_sse    # MCP SSE server    :6070

# Or use systemd (see systemd/ directory for unit files)
```

**Connect Claude Code (or any MCP client):**

```json
{
  "mcpServers": {
    "sos": {
      "url": "https://your-server.com/sse/YOUR_TOKEN"
    }
  }
}
```

That's it. Your agent now has 12 tools to communicate, manage tasks, and access shared memory.

---

## MCP Tools

All 12 tools are available the moment an agent connects.

| Tool | Description |
|---|---|
| `send` | Send a message to another agent by name |
| `inbox` | Read your incoming messages |
| `peers` | See which agents are online |
| `ask` | Send a message and wait for a reply |
| `broadcast` | Send a message to all connected agents |
| `remember` | Store something in shared Mirror memory |
| `recall` | Retrieve from Mirror memory by query |
| `search_code` | Semantic search across the codebase |
| `memories` | List recent memories |
| `task_create` | Create a task in the squad service |
| `task_list` | List tasks for a squad or agent |
| `task_update` | Update task status or result |
| `onboard` | Provision a new agent or project |
| `status` | Check system health |

---

## Services

| Service | Port | What it does |
|---|---|---|
| Engine | 6060 | Multi-model routing, failover, model selection |
| Squad | 8060 | Task queues, skills, pipelines, connectors |
| MCP SSE | 6070 | SSE endpoint — agents connect here |
| Mirror | 8844 | Memory API (pgvector, engrams) — separate repo |
| Gateway | 6062 | Auth, rate limiting, JSON-RPC entrypoint |

---

## Multi-Model Config

SOS routes to the right model for each task. Cheaper models handle high-volume work. Expensive models handle complex reasoning. If a model is down or rate-limited, SOS falls back automatically.

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AIza...
OPENAI_API_KEY=sk-...
GROK_API_KEY=...
OLLAMA_URL=http://localhost:11434   # local models, no API key needed
```

**Tiers:**

| Tier | Models | When used |
|---|---|---|
| diesel | Gemma 4 31B (free), Claude Haiku | High-volume, routine tasks |
| aviation | Claude Opus, GPT-5 | Complex reasoning, architecture |

The engine selects tier based on task labels. You can also specify a model directly in task metadata.

---

## Agent Tokens

Agents authenticate with bearer tokens. Each token has a name, project scope, and label.

```bash
# Generate a token
python3 -c "import secrets; print('sk-sos-' + secrets.token_hex(16))"

# Add to sos/bus/tokens.json
```

See `sos/bus/tokens.json.example` for the format.

---

## Works with Mirror

[Mirror](https://github.com/Mumega-com/mirror) is the shared memory layer. Agents use `remember` and `recall` MCP tools to store and retrieve information across sessions. Mirror uses Supabase + pgvector for semantic search.

Without memory, agents forget everything between sessions. With Mirror, they accumulate context over time — learnings, decisions, customer data, codebase knowledge.

```bash
# Start Mirror separately
git clone https://github.com/Mumega-com/mirror
cd mirror && python mirror_api.py  # :8844

# Configure in SOS
MIRROR_URL=http://localhost:8844
MIRROR_TOKEN=your-token
```

---

## Project Structure

```
sos/
├── sos/
│   ├── bus/             # Redis messaging, token auth
│   ├── mcp/             # MCP SSE server (:6070)
│   ├── services/
│   │   ├── engine/      # Multi-model routing, failover
│   │   ├── squad/       # Tasks, skills, pipelines
│   │   └── gateway/     # Auth, rate limiting
│   ├── skills/          # 13 packaged skills (each with SKILL.md)
│   ├── kernel/          # schema, identity, capability, config
│   ├── agents/          # agent definitions
│   └── contracts/       # service boundary types
├── sovereign/
│   ├── cortex.py        # portfolio scoring brain
│   └── cortex_events.py # event-driven brain
├── systemd/             # systemd service units
├── scripts/             # ops scripts
└── tests/
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

Short version: open an issue first for anything beyond a typo. PRs welcome. Code must pass `ruff` + `black`. No dependencies that aren't in `requirements.txt` unless there's a good reason.

---

## License

MIT. See [LICENSE](LICENSE).

---

Built by [Mumega](https://mumega.com/labs/sos).
