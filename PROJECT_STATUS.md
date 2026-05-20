# Project Status

Last updated: 2026-05-20

SOS is active alpha software. The public repo is standalone-clean and has a
local first-run profile for Redis, MCP, bus, and task smoke verification.

## Current Public Baseline

- Public SOS fresh clone: pytest collection clean, 2744 tests, 0 collection
  errors.
- Public SOS local profile: `scripts/sos-local-dev.sh up` plus `doctor`
  verifies Redis, bus, MCP, Squad, send/inbox, and task create/claim/complete.
- Public Mirror fresh clone: 145 passed, 2 skipped.
- Public Inkwell fresh clone: `npm install` and `npm run build` exit 0.
- Public SOS boundary: private/add-on paths are expected to stay out of public
  main and are checked by CI.
- Public SOS security model: edge map, threat model, webhook policy, health
  policy, CORS posture, Redis policy, and residual-risk checklist live under
  `docs/security/`.
- Public plugin/profile boundary: host runtimes integrate through
  `docs/architecture/plugin-boundary.md`, the public SDK, MCP, bus-watch, and
  Squad APIs.
- Current release candidate: `0.10.3`; release notes and tag checklist live in
  `docs/releases/`.

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
- Gateway/webhook ingress. Present, but public exposure must follow
  `docs/security/public-edge-map.md` and `docs/security/public-route-checklist.md`.

## What Is Private Or Host-Owned

- Mumega customer flows.
- Billing and provider integrations.
- Production deployment overlays.
- Secrets and live token registries.
- Host packages such as `mumega_sos_addons`.

## Known Gaps

- S086: maintainability split of large MCP/service modules
  (https://github.com/Mumega-com/sos/issues/149).
- Tenant-scoped inbox test drift is tracked separately from the S086 status
  extraction (https://github.com/Mumega-com/sos/issues/172).
- Public edge hardening remains tracked before broad route exposure
  (https://github.com/Mumega-com/sos/issues/145).
- Fresh-install and onboarding polish continue under the public release
  trackers (https://github.com/Mumega-com/sos/issues/148 and
  https://github.com/Mumega-com/sos/issues/169).
- S088 dashboard migration and tenant provisioning smoke notes live under
  `docs/operations/`. Tenant provisioning currently has a runtime-store path
  blocker before production use on the public kernel.

See [docs/plans/2026-05-20-sos-composition-sprints.md](docs/plans/2026-05-20-sos-composition-sprints.md).
