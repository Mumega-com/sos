---
sidebar_position: 2
title: Getting Started
---

# Getting Started

This page reflects the public SOS kernel, not Mumega's private hosted-product
overlay.

## Install

```bash
git clone https://github.com/Mumega-com/sos.git
cd sos

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Verify

```bash
python scripts/check_public_release_boundary.py --show-ok
pytest
```

## Run Locally

Start Redis:

```bash
redis-server --appendonly yes
```

Start the bridge and MCP server in separate terminals:

```bash
python -m sos.bus.bridge
python -m sos.mcp.sos_mcp_sse
```

See the copy/paste quickstart at [../quickstart-local.md](../quickstart-local.md).

## Optional Memory

Mirror is optional and lives in a separate repo:

```bash
git clone https://github.com/Mumega-com/mirror.git
```

Without Mirror, SOS should still support bus, inbox, peers, status, and task
flows. Memory tools require Mirror configuration.
