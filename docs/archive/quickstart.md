# Quick Start

Get SOS running in 5 minutes.

## Prerequisites

- Python 3.11+
- Redis 7+
- Git

## Option 1: Install from source

```bash
git clone https://github.com/servathadi/mumega.git
cd mumega/SOS
pip install -e .
```

## Option 2: Docker

```bash
cd SOS
docker-compose up -d
```

This starts Redis, the MCP SSE server, Bus Bridge, Squad Service, and Dashboard.

## Start services manually

```bash
# Terminal 1: MCP SSE server (agent communication)
python -m sos.services.gateway.mcp

# Terminal 2: Bus Bridge (HTTP API for agents)
python -m sos.services.gateway.bridge

# Terminal 3: Squad Service (tasks + teams)
python -m sos.services.squad.app

# Terminal 4: Dashboard (web UI)
python -m sos.services.dashboard
```

## Create your first agent

```python
from sos.adapters.base import SOSBaseAdapter

agent = SOSBaseAdapter(
    agent_name="my-agent",
    token="sk-dev-test",
    bus_url="http://localhost:6380",
)
```

## Send your first message

```python
import asyncio

async def main():
    agent = SOSBaseAdapter(
        agent_name="my-agent",
        token="sk-dev-test",
    )
    # Register on the bus
    await agent.announce()

    # Send a message to another agent
    await agent.send("other-agent", "hello from my-agent")

    # Check inbox
    messages = await agent.inbox()
    print(messages)

    await agent.close()

asyncio.run(main())
```

## Check health

```bash
curl http://localhost:6070/health/full
```

Response:

```json
{
  "status": "healthy",
  "services": {
    "bus": "up",
    "squad": "up",
    "mirror": "up",
    "dashboard": "up"
  }
}
```

## MCP connection (for Claude, Cursor, etc.)

Point your MCP client at the SSE endpoint:

```json
{
  "mcpServers": {
    "sos": {
      "url": "http://localhost:6070/sse"
    }
  }
}
```

For remote access with auth:

```json
{
  "mcpServers": {
    "sos": {
      "url": "https://mcp.yourdomain.com/sse/sk-your-token-here"
    }
  }
}
```

## Next steps

- [Architecture](architecture.md) -- understand how the pieces fit
- [Services](services.md) -- what each service does
- [API Reference](api.md) -- all endpoints and MCP tools
