# Operator First Run

Status: S113 proof path
Last updated: 2026-05-21

This guide proves the public SOS kernel without any private host overlay. It
starts Redis, starts the MCP gateway, registers one fake agent, and exercises
the public primitives.

## Prerequisites

- Python 3.10+
- `git`
- Redis on `localhost:6379`, or `redis-server` available so the proof script can
  start a temporary local Redis

Optional:

- Mirror memory. Without Mirror, `recall` returns a clear disabled/unavailable
  message while bus tools continue to work.
- Model API keys. They are not required for the bus/MCP proof.

## Fast Proof

```bash
git clone https://github.com/Mumega-com/sos.git
cd sos
scripts/prove_public_operator_install.sh
```

Expected summary:

```text
[sos-proof] mcp health ok
[sos-proof] empty registry: ok
[sos-proof] send/inbox: ok
[sos-proof] broadcast: ok
[sos-proof] peers: ok
[sos-proof] status: ok
[sos-proof] recall: Mirror DB unavailable - recall disabled
[sos-proof] proof complete
```

During PR review, before the public branch is updated, run the same proof
against a local branch:

```bash
SOS_PUBLIC_REPO_URL=/path/to/sos \
SOS_PUBLIC_REF=s113-public-operator-install-proof \
scripts/prove_public_operator_install.sh
```

## Manual Install

```bash
git clone https://github.com/Mumega-com/sos.git
cd sos
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
sos version
sos doctor
```

`sos doctor` should exit successfully when required imports work. Missing model
keys, gateway URLs, and optional services should appear as warnings/skips during
first run, not as crashes.

## Minimal Runtime

Start Redis if it is not already running:

```bash
redis-server
```

Start the MCP gateway:

```bash
python -m sos.mcp.sos_mcp_sse
```

Check health:

```bash
curl http://127.0.0.1:6070/health
```

Then connect an MCP client to:

```text
http://127.0.0.1:6070/mcp
```

Public kernel tools expected without a host overlay:

- `status`
- `peers`
- `send`
- `inbox`
- `broadcast`
- `recall`, which needs Mirror for real memory search and otherwise reports
  unavailable cleanly

## Troubleshooting

- Redis connection errors: start Redis on `localhost:6379`. The current MCP
  gateway still uses that local default.
- `recall` says Mirror is unavailable: install/configure Mirror later, or treat
  memory search as optional for the first bus proof.
- Audit-chain warning about `asyncpg`: safe during local proof. Install the
  Postgres/audit extras when you want durable hash-chained audit persistence.
- Model API warning in `sos doctor`: set a provider key only when running
  engine-backed chat or agent execution.
- Gateway URL warning in `sos doctor`: safe to ignore for local public-kernel
  proof.
