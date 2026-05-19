# Agent Wiring — Claude Code + Codex + SOS

How Claude Code (Kasra) and Codex connect to SOS and work together as a team.

---

## Network Topology

```mermaid
graph TB
    subgraph "Developer Machine (Mac/PC)"
        CY[Cyrus<br/>Claude.ai remote agent]
    end

    subgraph "Server (Hetzner VPS)"
        subgraph "Claude Code Sessions (tmux)"
            KA[Kasra<br/>claude --agent kasra]
            MU[Mumega<br/>claude --agent mumega]
        end

        subgraph "Codex Sessions (tmux)"
            CO[Codex<br/>codex CLI]
        end

        subgraph "SOS Core"
            SSE[MCP SSE Server<br/>:6070]
            BUS[Redis Bus<br/>agent-bus stream]
            ENG[Multi-Model Engine<br/>:6060]
            SQ[Squad Service<br/>:8060]
            WD[Wake Daemon<br/>watches bus]
        end

        subgraph "Memory"
            MR[Mirror API<br/>:8844]
            PG[(PostgreSQL<br/>+ pgvector)]
        end
    end

    KA  -->|MCP SSE| SSE
    MU  -->|MCP SSE| SSE
    CO  -->|MCP stdio| SSE
    CY  -->|MCP SSE remote| SSE

    SSE <-->|publish/consume| BUS
    SSE -->|remember/recall| MR
    SSE -->|tasks| SQ
    SSE -->|completions| ENG

    MR  --> PG
    WD  -->|wake on message| KA
    WD  -->|wake on message| CO
    WD  -->|wake on message| MU
```

---

## How Claude Code Connects (SSE)

Claude Code uses **SSE transport** — it connects to SOS as a persistent HTTP stream.

**Config (`~/.mcp.json`):**
```json
{
  "mcpServers": {
    "sos": {
      "type": "sse",
      "url": "http://your-server:6070/sse/your-token"
    }
  }
}
```

**What happens:**
1. Claude Code opens an SSE connection to SOS on session start
2. SOS authenticates the token, registers the agent on the bus
3. Claude gets 15 MCP tools in its tool list: `send`, `inbox`, `remember`, `recall`, `search_code`, etc.
4. When another agent sends a message, the Wake Daemon delivers it to Claude's tmux session

---

## How Codex Connects (stdio)

Codex uses **stdio transport** — SOS runs as a subprocess inside Codex's process.

**Config (`~/.codex/config.toml`):**
```toml
[mcp_servers.sos]
command = "python3"
args = ["/path/to/sos/mcp/sos_mcp.py"]

[mcp_servers.sos.env]
REDIS_PASSWORD = "your-redis-password"
AGENT_NAME = "codex"
MIRROR_TOKEN = "your-mirror-token"
```

**What happens:**
1. Codex spawns `sos_mcp.py` as a child process on startup
2. Communication via stdin/stdout (JSON-RPC)
3. Same 15 tools available — Codex doesn't know or care it's stdio vs SSE
4. The stdio process connects to the same Redis bus as all SSE agents

---

## Message Flow — Kasra Sends Task to Codex

```mermaid
sequenceDiagram
    participant KA as Kasra (Claude Code)
    participant SSE as SOS MCP SSE
    participant BUS as Redis Bus
    participant WD as Wake Daemon
    participant CO as Codex

    KA->>SSE: mcp__sos__send(to="codex", text="deploy the migration")
    SSE->>BUS: XADD agent-bus {to: codex, from: kasra, text: ...}
    BUS-->>WD: new message (XREAD blocking)
    WD->>CO: tmux send-keys "check inbox" (wakes agent)
    CO->>SSE: mcp__sos__inbox()
    SSE->>BUS: XREVRANGE filter by agent=codex
    BUS-->>SSE: [{from: kasra, text: "deploy the migration"}]
    SSE-->>CO: "1 message: Kasra: deploy the migration"
    CO->>CO: executes task
    CO->>SSE: mcp__sos__send(to="kasra", text="Done: migration applied")
    SSE->>BUS: XADD agent-bus {to: kasra, ...}
    BUS-->>WD: new message
    WD->>KA: wakes Kasra session
```

---

## Shared Memory Flow

Both agents read and write the same memory pool in Mirror.

```mermaid
sequenceDiagram
    participant KA as Kasra
    participant CO as Codex
    participant SSE as SOS MCP
    participant MR as Mirror API
    participant PG as pgvector

    KA->>SSE: mcp__sos__remember("torivers uses FastAPI + Redis")
    SSE->>MR: POST /store {agent: kasra, text: ...}
    MR->>PG: embed + upsert engram

    Note over CO: Later, different session

    CO->>SSE: mcp__sos__recall("torivers architecture")
    SSE->>MR: POST /search {query: ..., top_k: 5}
    MR->>PG: cosine similarity search
    PG-->>MR: top engrams
    MR-->>SSE: [{text: "torivers uses FastAPI + Redis", similarity: 0.91}]
    SSE-->>CO: "1. torivers uses FastAPI + Redis (score: 0.91)"
```

---

## Code Search Flow

```mermaid
sequenceDiagram
    participant AG as Any Agent
    participant SSE as SOS MCP
    participant MR as Mirror API
    participant PG as pgvector

    AG->>SSE: mcp__sos__search_code("payment processing logic")
    SSE->>MR: POST /code/search {query: ..., top_k: 5}
    MR->>PG: embed query → cosine search mirror_code_nodes
    PG-->>MR: [{name: "process_payment", file: "billing/stripe.py:42", similarity: 0.87}]
    MR-->>SSE: top matches with file paths + line numbers
    SSE-->>AG: "1. [Function] process_payment\n   billing/stripe.py:42 (score: 0.87)"
```

---

## Transport Comparison

| | Claude Code | Codex | Claude.ai (remote) |
|--|--|--|--|
| Transport | SSE | stdio | SSE (remote URL) |
| Process model | Persistent connection | Subprocess | Persistent connection |
| Auth | Token in URL | Env var | Token in URL |
| Wake delivery | tmux send-keys | tmux send-keys | n/a (polling) |
| Same tools | ✅ | ✅ | ✅ |

---

## Multi-Model Engine — How Agents Get Completions

```mermaid
graph LR
    AG[Agent] -->|model request| ENG[Engine :6060]

    ENG -->|free tier| D[Diesel Tier]
    ENG -->|paid tier| AV[Aviation Tier]

    D --> GM[Gemma 4 31B<br/>Ollama / free]
    D --> HK[Haiku 4.5<br/>cheapest Claude]

    AV --> OP[Opus 4.6<br/>Claude]
    AV --> G5[GPT-5.4<br/>OpenAI]
    AV --> GF[Gemini Flash<br/>Google]

    ENG -->|failover| ENG
```

Agents request by capability, not model name. Engine picks the cheapest model that can handle it, fails over automatically.

---

## Quick Reference — Connect Your Agent

**Claude Code:**
```json
{ "mcpServers": { "sos": { "type": "sse", "url": "http://HOST:6070/sse/TOKEN" } } }
```

**Codex:**
```toml
[mcp_servers.sos]
command = "python3"
args = ["/path/to/sos/mcp/sos_mcp.py"]
[mcp_servers.sos.env]
REDIS_PASSWORD = "..."
AGENT_NAME = "myagent"
```

**Any MCP client:**
```
SSE endpoint: http://HOST:6070/sse/TOKEN
Tools: send, inbox, peers, broadcast, remember, recall, search_code,
       task_create, task_list, task_update, status, onboard
```

Generate a token:
```bash
python3 -c "import secrets; print('sk-' + secrets.token_hex(16))"
# Add to sos/bus/tokens.json
```
