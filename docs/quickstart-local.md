# Local Quickstart

Status: draft
Last updated: 2026-05-19

Related:

- Public roadmap: `docs/plans/2026-05-19-sos-public-10-roadmap.md`
- Verified baseline: `docs/status/2026-05-19-actual-working-inventory.md`
- Internal/public split: `docs/architecture/internal-public-split.md`
- Operator first run: `docs/operator-first-run.md`

This is the target fresh-install path for SOS. The goal is one local profile that
lets a new developer prove the bus works before attaching optional services.

## Prerequisites

- Python 3.11+
- Redis
- `git`

Optional:

- Mirror for shared memory
- Model provider keys for engine-backed chat
- Host add-ons for Mumega-private services

## Install

```bash
git clone https://github.com/Mumega-com/sos.git
cd sos
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

For a repeatable smoke proof, run:

```bash
scripts/prove_public_operator_install.sh
```

The script creates a fresh clone under `/tmp/sos-public-operator-proof`,
installs with `pip install -e .`, starts the MCP gateway, registers one fake
agent, and proves `status`, `peers`, `send`, `inbox`, `broadcast`, and graceful
`recall` behavior.

## Optional Add-Ons

Public SOS does not require host/product add-ons. If you run a private overlay,
point SOS at its root:

```bash
export SOS_ADDONS_ROOT=/path/to/sos-addons
export PYTHONPATH=/path/to/host-repo:/path/to/sos
```

This enables generic host-owned lookup paths:

- `SOS_ADDONS_ROOT/operations` for operation templates.
- `SOS_ADDONS_ROOT/projects` for `projects/<slug>/SOURCES.md` manifests.

Specific overrides still win:

```bash
export SOS_OPERATIONS_DIR=/path/to/operations
export SOS_PROJECTS_DIR=/path/to/projects
```

Mumega's internal host overlay currently uses:

```bash
export SOS_ADDONS_ROOT=/mnt/HC_Volume_104325311/mumega.com/sos-addons
export PYTHONPATH=/mnt/HC_Volume_104325311/mumega.com:/mnt/HC_Volume_104325311/SOS
```

Host-owned services launch from `mumega_sos_addons.*`, not from public SOS.
Examples:

```bash
python3 -m mumega_sos_addons.services.saas.app
python3 -m mumega_sos_addons.services.etsy.asset_forge --watch
```

## Start Core Services

In separate terminals:

```bash
redis-server
python3 -m sos.services.engine
python3 -m sos.services.squad.app
python3 -m sos.mcp.sos_mcp_sse
```

Optional gateway:

```bash
python3 -m sos.services.gateway.app
```

Optional Mirror memory service:

```bash
cd ../mirror
python3 mirror_api.py
```

## Doctor

Run:

```bash
sos doctor
```

The local profile is healthy enough for first use when:

- Python/import checks pass.
- Redis is reachable.
- MCP health is reachable.
- At least one token source exists.

Engine, Squad, Gateway, and Mirror may show as skipped or warnings while the
developer is bringing services up one at a time.

## First Bus Smoke

Once MCP is running and a token is configured, connect an MCP client to:

```text
http://localhost:6070/mcp
```

Then call:

- `status`
- `peers`
- `send`
- `inbox`

The first successful round trip is:

1. Send a message to your agent.
2. Read it back from `inbox`.
3. Confirm the stream name is project-scoped or global as expected.

## Optional Memory

SOS remains useful without Mirror. When Mirror is not configured, memory tools
should fail clearly or report disabled state; bus, inbox, peers, and task tools
remain usable.
