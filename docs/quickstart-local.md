# Local Quickstart

This quickstart is for the public SOS core. It starts Redis plus the MCP/bus
surface from a local checkout. Mirror is optional.

## 1. Install

```bash
git clone https://github.com/Mumega-com/sos.git
cd sos

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 2. Start The Local Profile

One command starts Redis, the bus bridge, the MCP server, and the Squad task
service:

```bash
scripts/sos-local-dev.sh up
```

This also creates local-only dev tokens in `.sos/local/tokens.json`, picks free
local ports for Redis, bus, MCP, and Squad, and writes the shell environment to
`.sos/local/dev.env`. You do not need to edit `sos/bus/tokens.json`.

## 3. Run The Public Doctor

```bash
scripts/sos-local-dev.sh doctor
```

The doctor checks:

- Redis
- Bus bridge `/health`
- MCP `/health`
- Squad `/health`
- agent `send` and `inbox`
- task create, claim, and complete
- Mirror-disabled behavior

## 4. Stop The Local Profile

```bash
scripts/sos-local-dev.sh down
```

## Manual Commands

The helper script wraps these CLI commands:

```bash
sos local init
sos local migrate
sos local doctor
```

If you prefer to run services manually, source `.sos/local/dev.env` first and
then run these commands in separate terminals:

```bash
python -m sos.bus.bridge
python -m sos.mcp.sos_mcp_sse
python -m sos.services.squad.app
```

## 5. Verify The Public Repo

```bash
python scripts/check_public_release_boundary.py --show-ok
pytest --collect-only -q
```

Expected S087 baseline:

- `pytest --collect-only`: 2744 tests, 0 collection errors
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

This local profile is for development and first-run verification. Production
operators should provide real tokens, service supervision, TLS, backups, and a
separate security review of public ingress routes.
