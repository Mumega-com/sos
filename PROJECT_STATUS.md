# Project Status

Last updated: 2026-05-20

SOS is active alpha software. The public repo is standalone-clean and has a
local first-run profile for Redis, MCP, bus, and task smoke verification.

## Current Public Baseline

- Public SOS fresh clone: pytest collection clean, 2727 tests, 0 collection
  errors.
- Public SOS local profile: `scripts/sos-local-dev.sh up` plus `doctor`
  verifies Redis, bus, MCP, Squad, send/inbox, and task create/claim/complete.
- Public Mirror fresh clone: 145 passed, 2 skipped.
- Public Inkwell fresh clone: `npm install` and `npm run build` exit 0.
- Public SOS boundary: private/add-on paths are expected to stay out of public
  main and are checked by CI.

## What Is Core

- Redis-backed bus bridge.
- MCP server and agent-facing tools.
- Agent send/inbox/peers/broadcast/status flows.
- Squad task primitives.
- Kernel schemas and contracts.
- Local receive bridge for off-server agents.

## What Is Optional

- Mirror memory. SOS should still run without Mirror; memory tools require a
  configured Mirror service.
- Engine/model routing. Useful for hosted agent work, not required for basic
  bus and task coordination.
- Gateway/webhook ingress. Present, but public exposure needs the S080 security
  and edge pass.

## What Is Private Or Host-Owned

- Mumega customer flows.
- Billing and provider integrations.
- Production deployment overlays.
- Secrets and live token registries.
- Host packages such as `mumega_sos_addons`.

## Known Gaps

- S080: security and public edge pass.
- S081: plugin/profile boundary for OpenClaw, Hermes, and host overlays.
- S086: maintainability split of large MCP/service modules.

See [docs/plans/2026-05-20-sos-composition-sprints.md](docs/plans/2026-05-20-sos-composition-sprints.md).
