# SOS - Sovereign Operating System

SOS is a local-first coordination kernel for heterogeneous AI agents.

It gives Claude Code, Codex, Gemini workers, local scripts, OpenClaw/Hermes
hosts, and humans a shared operating surface: authenticated MCP tools, a Redis
message bus, agent inboxes, task queues, wake hooks, and optional memory.

Public SOS is the reusable kernel. Mumega's production runtime is one host
overlay built on top of it; Mumega-specific services, customer flows, billing,
and deployment secrets are not part of the public core.

## What Works Today

The current public gate from fresh clones is:

- SOS: public pytest collection clean, 2723 tests, 0 collection errors.
- Mirror: standalone pytest clean, 145 passed, 2 skipped.
- Inkwell: `npm install` and `npm run build` exit 0.

SOS itself can run without Mirror. In that mode bus, inbox, peers, status, and
task primitives remain useful. Memory tools are optional and depend on a
separate Mirror deployment.

## Core Surface

| Plane | Public core status | Notes |
|---|---|---|
| MCP tool plane | Core | Agent-facing tools over the SOS MCP server. |
| Bus postal plane | Core | Redis Streams for send, inbox, broadcast, and wake-compatible delivery. |
| Squad labor plane | Core | Task create/list/update and atomic work coordination. |
| Gateway/ingress plane | Public but hardening | Webhook and HTTP ingress surfaces need explicit public-edge review before broad exposure. |
| Mirror memory plane | Optional | Mirror is a separate repo and should degrade cleanly when absent. |
| Engine inference plane | Optional | Multi-model routing is available but not required for bus/task use. |

## Install

Prerequisites:

- Python 3.10+
- Redis 7+
- Git

```bash
git clone https://github.com/Mumega-com/sos.git
cd sos

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy the example environment if you want to run services locally:

```bash
cp .env.example .env
```

For the public quickstart, see [docs/quickstart-local.md](docs/quickstart-local.md).

## Run The Core

Start Redis, then run the bus bridge and MCP server:

```bash
redis-server --appendonly yes
python -m sos.bus.bridge
python -m sos.mcp.sos_mcp_sse
```

The public bus bridge exposes HTTP send/inbox/peers endpoints on `:6380`.
The MCP server exposes the agent tool surface on `:6070`.

The Docker Compose file is present but still operator-grade. S079 is the
dedicated public install and doctor pass that will make the first 15 minutes
fully copy/paste.

## Agent Tools

The public MCP surface is centered on:

- `send`
- `inbox`
- `peers`
- `broadcast`
- `status`
- `task_create`
- `task_list`
- `task_update`
- `remember`, `recall`, and `memories` when Mirror is configured

Some internal or host-specific tools may be disabled, tier-gated, or absent in
a clean public install.

## Project Layout

```text
sos/
  bus/         Redis bus bridge, envelopes, token store, OpenAPI schema
  mcp/         MCP server and tool dispatch
  kernel/      small shared primitives and schemas
  services/    optional service modules such as squad, engine, memory, gateway
  sdk/         Python SDK helpers for agent inbox/send flows
  watch/       local receive bridge for off-server agents
  contracts/   public boundary types
tests/         public test suite
scripts/       release and maintenance checks
docs/          public docs, plans, and archived design notes
```

## Public/Private Boundary

Public SOS must not include Mumega-private hosted-product modules, secrets,
live token registries, or deployment-only overlays. CI runs
`scripts/check_public_release_boundary.py` to prevent those paths from being
reintroduced.

Private Mumega runtime code belongs in host overlays such as
`mumega_sos_addons`, not in the public kernel.

## Version And Release State

Current package version: `0.10.3`.

This is active alpha software. The repo is useful for operators who are
comfortable with Python services, Redis, and MCP, but the public onboarding path
is still being tightened. See [PROJECT_STATUS.md](PROJECT_STATUS.md) and
[docs/plans/2026-05-20-sos-composition-sprints.md](docs/plans/2026-05-20-sos-composition-sprints.md).

## License

SOS is released under the MIT License. See [LICENSE](LICENSE).

## Related Repos

- [Mirror](https://github.com/Mumega-com/mirror): optional shared memory layer.
- [Inkwell](https://github.com/Mumega-com/inkwell): optional publishing/content framework.
