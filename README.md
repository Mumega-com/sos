# SOS - Sovereign Operating System

SOS is a local-first coordination kernel for heterogeneous AI agents. It gives
different agent runtimes one small shared substrate: authenticated MCP tools, a
Redis message bus, agent inboxes, task queues, wake hooks, and optional memory.

Use SOS when you want Claude Code, Codex, Gemini workers, local scripts,
OpenClaw/Hermes hosts, and humans to coordinate without forcing them into one
agent framework.

Public SOS is the reusable kernel. Mumega's production runtime is one host
overlay built on top of it; Mumega-specific services, customer flows, billing,
and deployment secrets are not part of the public core.

## Why SOS Exists

Most agent frameworks help build one agent or one workflow. SOS is the shared
operating layer underneath many of them:

- **Postal bus:** agents can send messages, read inboxes, broadcast, and watch
  for wake signals.
- **Work queue:** agents can create, claim, update, and complete durable tasks.
- **MCP surface:** humans and agents can use the same tool plane over local
  HTTP/SSE.
- **Optional memory:** Mirror can be attached for recall, but the kernel still
  runs without it.
- **Host overlays:** private products and deployment policy live outside the
  public kernel through the plugin/profile boundary.

## What Works Today

The current public gate from fresh clones is:

- SOS: public pytest collection clean, 2744 tests, 0 collection errors.
- Mirror: standalone pytest clean, 145 passed, 2 skipped.
- Inkwell: `npm install` and `npm run build` exit 0.

SOS itself can run without Mirror. In that mode bus, inbox, peers, status, and
task primitives remain useful. Memory tools are optional and depend on a
separate Mirror deployment.

The public operator proof exercises a clean install, doctor checks, MCP health,
tool calls, bus send/inbox, broadcast, peers, status, and Mirror-off behavior:

```bash
scripts/prove_public_operator_install.sh
```

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
For public exposure and security posture, see [docs/security/README.md](docs/security/README.md).

## Run The Core

Start the local public profile:

```bash
scripts/sos-local-dev.sh up
scripts/sos-local-dev.sh doctor
```

This starts Redis, the bus bridge, the MCP server, and the Squad task service
on generated free local ports. It also creates local-only dev tokens under
`.sos/local/`, then proves send/inbox and task create/claim/complete.

For a lower-level manual run, start Redis and then bring up the service you are
testing:

```bash
redis-server
python -m sos.mcp.sos_mcp_sse
python -m sos.services.squad.app
python -m sos.services.engine
```

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

## What Is Not In Public SOS

The public repo intentionally excludes:

- Mumega customer workflows, billing, tenant operations, and hosted dashboards.
- Live production secrets, token registries, and deployment-only overlays.
- Product-specific agent behavior that belongs in a host add-on package.
- Mirror and Inkwell implementations, which are separate optional repos.

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
reintroduced. CI also runs `scripts/check_public_security_docs.py` so public
edge/security policy docs remain present, and
`scripts/check_plugin_boundary_docs.py` so the host profile contract remains
present.

Private Mumega runtime code belongs in host overlays such as
`mumega_sos_addons`, not in the public kernel.
External runtimes and overlays should integrate through
[docs/architecture/plugin-boundary.md](docs/architecture/plugin-boundary.md).

## Version And Release State

Current package version: `0.10.4`.

This is active alpha software. The repo is useful for operators who are
comfortable with Python services, Redis, and MCP. See [PROJECT_STATUS.md](PROJECT_STATUS.md) and
[docs/plans/2026-05-20-sos-composition-sprints.md](docs/plans/2026-05-20-sos-composition-sprints.md).
For the current release candidate, see
[docs/releases/v0.10.4.md](docs/releases/v0.10.4.md) and
[docs/releases/v0.10.4-tag-checklist.md](docs/releases/v0.10.4-tag-checklist.md).

## License

SOS is released under the MIT License. See [LICENSE](LICENSE).

## Related Repos

- [Mirror](https://github.com/Mumega-com/mirror): optional shared memory layer.
- [Inkwell](https://github.com/Mumega-com/inkwell): optional publishing/content framework.
