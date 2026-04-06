---
sidebar_position: 1
title: Architecture Overview
---

# Architecture Overview

Mumega is a microkernel platform. A thin kernel defines the schema and capability primitives. Independent services own their domains. A decision layer sits above them all, continuously scoring and dispatching work.

## Layers

```
┌─────────────────────────────────────┐
│              Agents                 │  AI + human executors
├─────────────────────────────────────┤
│               Brain                 │  Scoring, dispatch, decisions
├──────────┬──────────┬───────────────┤
│  Squad   │  Mirror  │  SOS Engine   │  Domain services
│  Service │  Memory  │  Model Router │
├──────────┴──────────┴───────────────┤
│            Message Bus              │  Pub/sub event backbone
├─────────────────────────────────────┤
│             SOS Kernel              │  Schema, identity, capability
└─────────────────────────────────────┘
```

## Components

### SOS Kernel
Foundation layer. Defines the data models (task, squad, skill, engram, agent), identity primitives, trust tiers, and the skill loader/validator. Services depend on the kernel — not on each other.

### Services
Each service is an independent HTTP process with a single domain of responsibility. See [Services](services) for the full map.

### Message Bus
Redis pub/sub backbone. All inter-agent and inter-service events flow here. Services emit events; other services and agents subscribe. No direct service-to-service calls except through the bus or the public API.

Key event channels: `task.created`, `task.claimed`, `task.done`, `agent.woke`, `squad.*`

### Brain
The autonomous decision layer. Subscribes to bus events, re-scores the portfolio when relevant things happen (new task, task completed, agent came online), and dispatches the next highest-value task. See [Brain](brain).

### MCP Gateway
Model Context Protocol server. Any MCP-compatible AI tool — Claude Code, Cursor, Codex — connects and gains access to the full task, memory, and messaging system. See [MCP Tools](../api/sos-mcp).

## Data Flow

```
Project registered
      ↓
Brain scores open work across all projects
      ↓
Highest-scoring task claimed from Squad Service
      ↓
Skill matched to task → executor runs
      ↓
Result stored in Mirror (persistent memory)
      ↓
Task marked done in Squad Service
      ↓
Bus event published → dependent tasks unblocked
      ↓
Brain re-scores → next task dispatched
```
