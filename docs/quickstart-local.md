# Local Quickstart

This quickstart is for the public SOS core. It starts Redis plus SOS services
from a local checkout. Mirror is optional.

## 1. Install

```bash
git clone https://github.com/Mumega-com/sos.git
cd sos

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 2. Start Redis

In one terminal:

```bash
redis-server --appendonly yes
```

If you use a non-default Redis URL, export it before starting SOS services:

```bash
export SOS_REDIS_URL=redis://localhost:6379/0
```

## 3. Start The Bus Bridge

In a second terminal:

```bash
source .venv/bin/activate
python -m sos.bus.bridge
```

The bridge listens on `http://localhost:6380`.

## 4. Start The MCP Server

In a third terminal:

```bash
source .venv/bin/activate
python -m sos.mcp.sos_mcp_sse
```

The MCP server listens on `http://localhost:6070`.

## 5. Verify The Public Repo

```bash
python scripts/check_public_release_boundary.py --show-ok
pytest
```

Expected S077 baseline:

- `pytest`: 2723 tests, 0 collection errors
- boundary check: clean

## 6. Optional Mirror Memory

Mirror is a separate repo. Without Mirror, bus, inbox, peers, status, and task
flows should still be usable. With Mirror configured, memory tools such as
`remember`, `recall`, and `memories` can store and retrieve shared context.

```bash
git clone https://github.com/Mumega-com/mirror.git
```

Follow the Mirror repo's setup instructions, then configure SOS with the Mirror
URL and token expected by your deployment.

## Current Gap

This quickstart is intentionally honest: S079 will replace the operator-grade
multi-terminal flow with a tighter public install profile, seed/dev token flow,
doctor command, and one copy/paste two-agent smoke test.

