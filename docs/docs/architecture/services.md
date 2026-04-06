---
sidebar_position: 2
title: Services
---

# Services

The platform runs as a set of independent services, each owning a single domain. All are accessible via the public API at `https://api.mumega.com`.

## Service Map

| Service | Domain | Public Endpoint |
|---------|--------|-----------------|
| Squad Service | Tasks, skills, pipelines, squad state | `https://api.mumega.com/squads`, `/tasks`, `/skills` |
| SOS Engine | Model routing, agent dispatch | `https://api.mumega.com/engine` |
| Mirror | Memory, engrams, semantic search | `https://api.mumega.com/memory` |
| MCP Server | Agent tools over SSE | `https://api.mumega.com/mcp/sse` |
| OpenClaw | Discord, Telegram, Slack gateway | Inbound webhooks |
| Bus Bridge | HTTP bridge for remote agent messaging | `https://api.mumega.com/bus` |

## Squad Service

Core service. All tasks, skills, and pipelines live here. Exposes the primary management API. See [Squad Service API](../api/squad-service).

## SOS Engine

Routes prompts to the right model. Multi-model failover chain ensures reliability and cost efficiency:

```
Gemma (local) → Gemini → Claude → GPT-4 → DeepSeek → Ollama
```

Cheap models handle routine tasks. Expensive models are reserved for complex decisions. Agents call the engine — they never call model APIs directly.

## Mirror

Persistent memory for all agents. Built on pgvector for semantic search. Every task result, agent decision, and significant event is stored here. The brain queries Mirror when making context-aware decisions. See [Mirror API](../api/mirror-api).

## MCP Server

SSE-based server implementing the Model Context Protocol. Gives any compatible AI tool — Claude Code, Cursor, Codex — access to messaging, memory, and task management without custom integration. See [MCP Tools](../api/sos-mcp).

## OpenClaw

Unified gateway for human interaction. Routes inbound Discord and Telegram messages to agents. Routes outbound agent messages (task notifications, human queue posts) to the right channels. Supports Discord slash commands and the Human Queue bot flow.

## Bus Bridge

HTTP proxy that lets remote agents and external services publish and subscribe to the internal message bus without direct access to infrastructure. Required for agents running outside the platform boundary.
