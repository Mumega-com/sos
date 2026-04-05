# SOS CLI Decoupling Log — 2026-04-05

## Goal

Remove live SOS runtime dependencies on `/home/mumega/cli` so SOS can own its
tooling and control plane boundaries directly.

## Changes Made

### 1. Tools bridge no longer hard-imports CLI modules

Updated [`sos/services/tools/mcp_bridge.py`](/mnt/HC_Volume_104325311/SOS/sos/services/tools/mcp_bridge.py):

- Removed automatic `sys.path` injection of `/home/mumega/cli`
- Removed hardcoded imports of `mumega.core.mcp.*`
- Replaced them with explicit MCP server specs loaded from:
  - `SOS_MCP_SERVER_MODULES` env var, or
  - `services.mcp.servers` in `~/.sos/config/sos.json`
- Default behavior with no config is now safe no-op discovery

### 2. SOS MCP usage docs now point to SOS-owned entrypoints

Updated:

- [`sos/mcp/redis_bus.py`](/mnt/HC_Volume_104325311/SOS/sos/mcp/redis_bus.py)
- [`sos/mcp/tasks.py`](/mnt/HC_Volume_104325311/SOS/sos/mcp/tasks.py)

The usage strings no longer refer operators to `cli` MCP scripts.

### 3. Unified SOS MCP no longer falls back to `~/cli/.env`

Updated [`sos/mcp/sos_mcp.py`](/mnt/HC_Volume_104325311/SOS/sos/mcp/sos_mcp.py):

- Removed secret loading from `/home/mumega/cli/.env`
- Kept SOS-owned loading from `/home/mumega/.env.secrets`
- Kept Codex config fallback for MCP subprocess environments

### 4. Operations runner no longer falls back to `~/cli/.env`

Updated [`sos/services/operations/runner.py`](/mnt/HC_Volume_104325311/SOS/sos/services/operations/runner.py):

- Removed secret loading from `/home/mumega/cli/.env`
- Kept secret loading from `/home/mumega/.env.secrets`

## Verification

- Python syntax check passed for:
  - `sos/services/tools/mcp_bridge.py`
  - `sos/mcp/redis_bus.py`
  - `sos/mcp/tasks.py`
  - `sos/mcp/sos_mcp.py`
  - `sos/services/operations/runner.py`

- `MCPBridge()` now initializes without requiring `cli`
- With no explicit MCP server config, bridge discovery safely returns zero servers

## Remaining `cli` References

These still exist, but are not part of the immediate live decoupling completed in
this pass:

- Source attribution comments in:
  - `sos/services/autonomy/coordinator.py`
  - `sos/services/autonomy/service.py`
  - `sos/services/engine/resilience.py`
  - `sos/kernel/dreams.py`
- Architecture docs that still mention `mumega.core.mcp.*`
- Legacy compatibility comments in `sos/bus/bridge.py`

## Result

SOS no longer requires `/home/mumega/cli` to boot its tools bridge or load its
main MCP and operations secrets. This removes the main live runtime coupling and
turns remaining `cli` references into migration/documentation debt instead of
active boot-time dependencies.
