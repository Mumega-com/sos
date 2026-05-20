# What Is Working Today

This page is a current-state ledger for public SOS. It separates running code
from roadmap intent so a new operator can pick up work safely.

## Working Today

### Public Kernel

The public repo contains the kernel, contracts, clients, service modules,
MCP/bus surfaces, local setup helpers, and release gates needed to run a local
SOS profile.

Working public surfaces include:

- package entrypoint: `sos` / `mumega`
- local profile helpers: `sos local init`, `sos local migrate`,
  `sos local doctor`
- operator snapshot: `sos operator`
- bus bridge module: `sos.bus.bridge`
- MCP SSE module: `sos.mcp.sos_mcp_sse`
- Squad task service module: `sos.services.squad.app`
- registry service module: `sos.services.registry.app`
- docs service module: `sos.services.docs.app`
- engine service module: `sos.services.engine`

### Local Developer Loop

The public local profile can generate dev tokens and environment files without
private Mumega secrets.

Expected smoke path:

```bash
sos local init
sos local doctor
sos operator
```

Mirror and hosted-product services are optional for the local profile.

### Operator Loop

`sos operator` gives a compact snapshot of:

- service health
- Redis availability
- registry agent sample
- top bus streams
- failed-wakeup-like streams
- gate/audit/health event streams
- blocked task count when a Squad token is configured

The command is best-effort. Missing optional dependencies are reported as
unavailable, not fatal.

### Public Release Gates

CI currently checks public boundary rules, version synchronization, security
docs, and plugin-boundary docs. The release gate is the public guardrail against
private host paths and internal assumptions returning to the kernel.

## Working In The Mumega Host Profile

Mumega's private deployment has proven the public-kernel composition model:
public SOS code can run live services while private env files, add-ons, tokens,
and customer workflows remain outside the public checkout.

The S084 migration train moved these live services onto the public kernel:

- asset forge worker
- build worker
- bus canary
- registry
- SaaS API
- Squad
- engine
- MCP SSE
- bus bridge
- docs service

Dashboard was intentionally held for host-specific work.

Private deployment details, rollback file paths, PIDs, and secret-bearing env
locations belong in the host's private issue tracker/runbook. Public SOS keeps
only the reusable pattern.

## Planned, Not Complete

These are not done yet:

- one-command public demo that starts every required local service
- polished web operator dashboard
- complete lifecycle state model for external agents
- full stuck-task and failed-wakeup semantics across all hosts
- stable host adapter package for OpenClaw, Hermes, and other supervisors
- public comparison page against adjacent agent frameworks
- full threat-model coverage for every future ingress route

## Known Gaps

- Some docs still contain older architecture language and should be reconciled
  against the public microkernel boundary.
- The operator command is a compact snapshot, not a full observability backend.
- Task and wakeup summaries are limited by the data available in the local
  Redis/Squad profile.
- Optional Mirror memory must degrade cleanly, but many examples still assume a
  memory layer is present.
- Host deployments need private runbooks for exact systemd units, env files,
  rollback paths, and reporting targets.

## Maintenance Rule

Update this page when any of these change:

- a service moves package/module path
- a default port changes
- a health endpoint changes
- a local-profile command changes
- a release gate is added or removed
- a planned capability becomes working code
