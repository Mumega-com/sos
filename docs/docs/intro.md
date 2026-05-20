---
sidebar_position: 1
id: intro
title: SOS
---

# SOS

SOS is a local-first coordination kernel for heterogeneous AI agents.

The public core provides:

- MCP tools for agent communication.
- Redis-backed send, inbox, peers, broadcast, and status flows.
- Squad task primitives.
- Kernel contracts for identity, schemas, and extension boundaries.
- Optional integration with Mirror for shared memory.

Mumega's production runtime is a host overlay built on top of SOS. Public SOS
does not include Mumega customer services, billing flows, private deployment
config, or live token registries.

Start with the local quickstart:

- [Local quickstart](../quickstart-local.md)
- [Runtime planes](../architecture/runtime-planes.md)
- [Project status](../../PROJECT_STATUS.md)
