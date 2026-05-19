# SOS Documentation

The Sovereign Operating System -- a microkernel for AI agent teams.

SOS gives your agents a shared bus, memory, task system, and feedback loop.
You bring the agents. SOS makes them a team.

## Contents

- [Quick Start](quickstart.md) -- Install and run in 5 minutes
- [Architecture](architecture.md) -- How the organism works
- [Services](services.md) -- All services and what they do
- [Kernel](kernel.md) -- Microkernel: registry, events, feedback
- [Adapters](adapters.md) -- Connect LangGraph, CrewAI, or any framework
- [Tenants](tenants.md) -- Multi-tenant isolation
- [Flywheel](flywheel.md) -- The feedback loop that makes it learn
- [API Reference](api.md) -- Endpoints and MCP tools

## What SOS is

- A **microkernel** (bus + auth + registry) with pluggable services
- An **agent bus** for sending messages between any agents
- A **memory system** (Mirror) with vector search
- A **task queue** with atomic claim, priority, and labels
- A **feedback loop** that scores results and adapts behavior
- **Multi-tenant** -- one instance serves many customers

## What SOS is not

- Not an agent framework (use LangGraph, CrewAI, or raw code)
- Not an LLM wrapper (bring your own models)
- Not a chatbot platform (agents do work, not conversation)
