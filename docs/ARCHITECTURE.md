# Architecture

SOS is a coordination kernel for AI agent teams. The public core is small:
MCP tools, a Redis bus, task primitives, schemas, identity, and extension
contracts. Product-specific workflows live in host overlays.

For the current runtime-plane map, see
[architecture/runtime-planes.md](architecture/runtime-planes.md).

## High-Level Shape

```text
Claude Code / Codex / scripts / host runtimes
        |
        | MCP or HTTP bridge
        v
SOS MCP server and bus bridge
        |
        +-- Redis bus: send, inbox, broadcast, wake-compatible streams
        +-- Squad tasks: create, list, update, claim/complete flows
        +-- Kernel contracts: identity, schemas, capability boundaries
        +-- Optional services: Mirror memory, Engine inference, Gateway ingress
```

## Public Core

The public core is responsible for:

1. Authenticating agents and requests.
2. Moving messages through the bus.
3. Exposing MCP tools to agents.
4. Coordinating task state.
5. Providing stable schemas and extension contracts.
6. Failing gracefully when optional services are absent.

## Optional Planes

Mirror memory, Engine inference, Gateway ingress, and host overlays are useful
but not required for the basic bus/task kernel. Public docs should describe
them as optional unless a quickstart or test proves otherwise.

## Host Overlays

Mumega-specific hosted-product code, billing flows, provider integrations,
customer data, and deployment units belong outside public SOS. Public SOS should
compose with those overlays without importing them.

## Current Verification

The S077 fresh-clone baseline is:

- SOS: 2727 tests, 0 collection errors.
- Mirror: 145 passed, 2 skipped.
- Inkwell: `npm install` and `npm run build` exit 0.

S078 adds the CI release gate that keeps the public/private boundary explicit.
