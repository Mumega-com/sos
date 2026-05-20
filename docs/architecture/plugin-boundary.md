# Plugin And Host Profile Boundary

Status: S081 baseline  
Last updated: 2026-05-20

Public SOS is the bus, task, and optional memory kernel. Host runtimes such as
OpenClaw, Hermes, local shell scripts, Codex, Claude Code, or private overlays
connect through a small profile contract. They do not import Mumega-private
modules, and public SOS does not import host overlays.

## Contract Shape

A host profile is a deployment-owned record with these fields:

| Field | Required | Meaning |
|---|---:|---|
| `agent` | yes | Stable lowercase bus identity, e.g. `hermes-worker`. |
| `runtime` | yes | Runtime label such as `openclaw`, `hermes`, `claude-code`, `codex`, or `generic`. |
| `project` | no | Project/workspace scope for bus streams and tasks. |
| `bridge_url` | yes for HTTP-only hosts | Bus bridge URL, usually `http://localhost:<port>` locally or an authenticated edge URL. |
| `token_env` or `token_file` | yes | Host-owned token source. Tokens are not stored in public source. |
| `subscriptions` | no | Extra bus channels to read, e.g. `project:sos:global` or `squad:research`. |
| `wake` | no | Runtime-specific wake command or callback. Public SOS treats it as an opaque delivery target. |
| `health_url` | no | Runtime-specific health probe for operator dashboards. |

The profile is configuration, not code. It may live in a host repo, a secret
manager, systemd environment, Kubernetes secret, or a local file outside the
public SOS tree.

## Public Kernel Interfaces

Host runtimes should use these public interfaces:

| Need | Public interface |
|---|---|
| Register or announce an agent | `sos.sdk.Agent.heartbeat()` or bus bridge `/announce` |
| Send a message | `sos.sdk.Agent.send()` or bus bridge `/send` |
| Read inbox | `sos.sdk.Agent.inbox()` or bus bridge `/inbox` |
| Wake a local process | `mumega-bus-watch` with an allowlisted transport |
| Create/list/claim/complete tasks | Squad HTTP API or MCP tools `task_create`, `task_list`, `task_update` |
| Expose tools to agents | MCP server tools over `/mcp` or `/sse/{token}` |
| Optional memory | Mirror-backed MCP tools `remember`, `recall`, `memories` when Mirror is configured |
| Health reporting | minimal `/health` for liveness; authenticated/private full health for details |

Host runtimes must not call private paths such as `sos.agents.*`,
`sos.services.<host-addon>.*`, or `mumega_sos_addons.*` from public SOS. Those
paths belong to a host overlay.

## OpenClaw/Hermes-Style Adapter

An external runtime only needs an adapter that:

1. Loads its host profile.
2. Resolves the bearer token from an environment variable or token file.
3. Constructs `sos.sdk.Agent` with `name`, `project`, `bridge_url`, and
   `subscriptions`.
4. Calls `heartbeat()` on startup and periodically.
5. Polls `inbox()` and passes messages into the runtime's native prompt/task
   queue.
6. Uses `send()` for replies and handoffs.
7. Reports a minimal health state to the operator plane.

See `examples/host_profiles/openclaw_hermes_adapter.py` for a minimal,
public-safe adapter sketch. It imports only the public SDK and standard library.

## Tool Exposure

SOS tools are exposed through MCP. Host runtimes should treat MCP tools as the
tool boundary and should not import internal service modules to get behavior.

Recommended baseline tool set:

- `send`
- `inbox`
- `peers`
- `broadcast`
- `status`
- `task_create`
- `task_list`
- `task_update`

Memory tools are optional and should be advertised only when Mirror is
configured.

## Wake Delivery

Wake delivery is host-owned. Public SOS emits bus streams and wake-compatible
events; it does not know how OpenClaw, Hermes, tmux, a desktop app, or a
Kubernetes worker should receive a prompt.

Supported public pattern:

- Use `mumega-bus-watch` to poll `/inbox`.
- Configure an allowlisted transport command.
- Mark messages delivered only after the transport exits `0`.
- Keep cursors and delivered-message state outside source control.

## Host Overlays

Host overlays such as `mumega_sos_addons` may provide private agents, billing,
SaaS, provider adapters, customer flows, service units, or deployment glue.
They must follow these rules:

- Depend on public `sos`; public `sos` must not depend on them.
- Use stable host package names for imports and launch paths.
- Keep compatibility shims temporary and documented.
- Keep secrets, live token registries, customer data, and service-unit
  overrides outside the public repo.
- Provide their own tests for host package imports and service launch paths.
- Migrate live units to host package paths before deleting old compatibility
  paths.

## Compatibility Shims

Compatibility shims are allowed only when all of the following are true:

- They are in a host/private tree or are explicitly marked temporary.
- They import from the host-owned package, not the other way around.
- They have an owner, retirement condition, and test proving the new path.
- They do not reintroduce private code into public SOS.

Public SOS should not add new shims for Mumega-private paths.

## Minimal Smoke Checklist

For any new host runtime:

- [ ] Profile resolves token without committing the token.
- [ ] `Agent.heartbeat()` or `/announce` succeeds.
- [ ] Runtime sends one message through `Agent.send()` or `/send`.
- [ ] Runtime reads one message through `Agent.inbox()` or `/inbox`.
- [ ] Runtime can complete one Squad task or explicitly declares tasks out of
      scope.
- [ ] Optional memory behavior is documented as enabled or disabled.
- [ ] No public SOS import references host-private packages.
