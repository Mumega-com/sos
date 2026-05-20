# SOS Operator Runbook

This runbook is the first page a resumed agent or human operator should read
before changing SOS.

It describes the public operating loop. Host-specific secrets, live customer
data, and private add-on paths belong in the host's private runbook, not here.

## 1. Orient

Start with the public repo state:

```bash
git status --short --branch
git log -1 --oneline
sos --version
sos operator
```

`sos operator` is best-effort. It reports service health, Redis stream shape,
agent registry hints, failed-wakeup streams, recent gate/audit streams, and
blocked task count when a Squad token is available.

Use JSON when another tool needs to consume the snapshot:

```bash
sos operator --json
```

## 2. Repos

Public SOS work belongs in `Mumega-com/sos`.

Keep public SOS limited to the kernel, public services, public contracts,
public adapters, and public documentation. Host products, private customer
flows, private environment files, and secrets belong in the host/add-on repo.

Before opening a PR:

```bash
git status --short
python3 -m compileall sos tests
uv run --extra dev ruff check <changed paths>
uv run --extra dev black --check <changed paths>
```

For release-boundary work, also run the release/security/plugin gates documented
in CI.

## 3. Services

Public services should have:

- a module path
- a default port
- a health endpoint or smoke command
- required environment variable names, without secret values
- a rollback path when deployed through a host profile

The operator command checks common local defaults:

| Service | Default URL |
| --- | --- |
| engine | `http://127.0.0.1:6060/health` |
| mcp | `http://127.0.0.1:6070/health` |
| bus-bridge | `http://127.0.0.1:6380/health` |
| registry | `http://127.0.0.1:6067/health` |
| squad | `http://127.0.0.1:8060/health` |
| saas | `http://127.0.0.1:8075/health` |
| docs | `http://127.0.0.1:8085/health` |

Override URLs with `SOS_ENGINE_URL`, `SOS_MCP_HEALTH_URL`, `SOS_BUS_URL`,
`SOS_REGISTRY_URL`, `SOS_SQUAD_URL`, `SOS_SAAS_URL`, and `SOS_DOCS_URL`.

## 4. Bus

The bus is the operator spine:

- Redis Streams hold inboxes, audit events, health events, and project streams.
- MCP exposes bus tools for agents.
- `mumega-bus-watch` is the off-server receive bridge.
- The HTTP bus bridge supports remote SDK clients and local smoke flows.

When debugging a bus issue, check in this order:

```bash
sos operator
curl -fsS "${SOS_BUS_URL:-http://127.0.0.1:6380}/health"
curl -fsS "${SOS_MCP_HEALTH_URL:-http://127.0.0.1:6070}/health"
```

If Redis is unavailable, the operator command should show that directly. If
Redis is available but no agents or streams appear, inspect the host profile's
token file and agent registration path.

## 5. GitHub Issues

Use GitHub issues as durable work memory.

Every sprint issue should include:

- purpose
- deliverables
- exit criteria
- verification commands
- links to PRs and follow-up issues

Close an issue only after the PR is merged or the deployment proof is posted.
If a known gap is out of scope, document it on the issue instead of hiding it in
chat.

## 6. Gates, Changelog, Versioning, Releases

Public SOS release work must keep these in sync:

- `pyproject.toml`
- `sos.__version__`
- CLI version output
- `CHANGELOG.md`
- release-gate docs and scripts

For normal sprint PRs, update `CHANGELOG.md` under `[Unreleased]` when the
change affects operator behavior, public APIs, setup, release gates, or docs.

Do not tag a public release until CI passes and the release checklist says the
boundary, security, install, docs, and version checks are green.

## 7. Reporting

Report meaningful state transitions, not every command.

A useful handoff includes:

- branch and PR number
- issue number
- changed files
- verification commands and results
- known gaps
- next target

When operating inside a host that uses Kasra as the coordination agent, report
through that host's bus wrapper. Public SOS documents the pattern, but private
agent names and tokens stay in the private runbook.

## 8. Resumed-Agent Checklist

Use this checklist after compaction or a fresh session:

```bash
git status --short --branch
gh issue view <active-sprint>
sos operator
gh pr list --state open --limit 10
```

Then answer:

- What issue is active?
- What branch am I on?
- What services are healthy?
- What checks have already passed?
- What is the next irreversible operation?

If any answer is unclear, document the uncertainty on the issue before making a
risky change.
