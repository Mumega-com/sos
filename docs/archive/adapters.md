# Adapters

Adapters connect external agent frameworks to the SOS bus. Use the built-in adapters or build your own.

## SOSBaseAdapter

The shared HTTP client that all adapters inherit. Handles bus operations, auth, and Mirror memory.

```python
from sos.adapters.base import SOSBaseAdapter

agent = SOSBaseAdapter(
    agent_name="my-agent",
    token="sk-mytoken",
    bus_url="http://localhost:6380",
    skills=["code", "deploy"],
    mirror_url="http://localhost:8844",
)

# Register on the bus
await agent.announce()

# Send messages
await agent.send("other-agent", "task done")

# Check inbox
messages = await agent.inbox()

# Broadcast to all
await agent.broadcast("deployment complete")

# Memory operations
await agent.remember("learned that X causes Y")
results = await agent.recall("what causes Y?")

# Heartbeat (keep-alive)
await agent.heartbeat()

# Clean up
await agent.close()
```

## LangGraph adapter

```python
from sos.adapters.langgraph import SOSLangGraphAdapter

adapter = SOSLangGraphAdapter(
    agent_name="langgraph-agent",
    token="sk-mytoken",
)
await adapter.announce()

# The adapter exposes SOS tools as LangGraph tool nodes.
# Wrap your graph to include bus send/receive as tool calls.
# See sos/adapters/langgraph/example.py for a full working graph.
```

## CrewAI adapter

```python
from sos.adapters.crewai import SOSCrewAIAdapter

adapter = SOSCrewAIAdapter(
    agent_name="crewai-agent",
    token="sk-mytoken",
)
await adapter.announce()

# The adapter wraps SOS bus operations as CrewAI tools.
# Your crew can send messages, check inbox, and use memory.
# See sos/adapters/crewai/example.py for a full crew setup.
```

## Vertex ADK adapter

```python
from sos.adapters.vertex_adk import SOSVertexADKAgent

agent = SOSVertexADKAgent(
    agent_name="vertex-agent",
    token="sk-mytoken",
)
await agent.announce()
```

## Build your own adapter

Inherit `SOSBaseAdapter` and set `framework`:

```python
from sos.adapters.base import SOSBaseAdapter

class MyFrameworkAdapter(SOSBaseAdapter):
    framework: str = "my-framework"

    async def run(self):
        """Your framework's main loop."""
        await self.announce()
        while True:
            messages = await self.inbox()
            for msg in messages:
                # Process with your framework
                result = await self.my_framework_process(msg)
                await self.send(msg["from"], result)
            await self.heartbeat()
```

## Docker: run any framework

If your framework has dependencies that conflict with SOS, run it in a container:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
ENV SOS_BUS_URL=http://host.docker.internal:6380
ENV SOS_TOKEN=sk-mytoken
CMD ["python", "agent.py"]
```

```yaml
# docker-compose.yml
services:
  my-agent:
    build: ./my-agent
    environment:
      SOS_BUS_URL: http://bus-bridge:6380
      SOS_TOKEN: sk-mytoken
    depends_on:
      - bus-bridge
```

The agent connects to the bus via HTTP. No shared memory or process coupling needed.
