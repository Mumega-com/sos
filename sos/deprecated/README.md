**DEPRECATED MODULES — Do not import from this directory.**

These files are preserved for git history only. All replacements are listed below.

| File | Retired | Why | Replacement |
|------|---------|-----|-------------|
| `redis_bus.py` | 2026-04-17 | Superseded by SSE-based MCP bus; Redis pub/sub transport abandoned in v0.4.0 migration | `sos/mcp/sos_mcp_sse.py` |
| `sos_mcp.py` | 2026-04-17 | stdio MCP variant replaced by SSE transport; no active imports or systemd units reference it | `sos/mcp/sos_mcp_sse.py` |

## Notes

- **`remote.js`** (`sos/mcp/remote.js`) — NOT archived. Actively served as `/sdk/remote.js` by `sos/bus/bridge.py` and `workers/bus-worker`. It is the client-side SDK distributed to external agents.
- **`workers/sos-dispatcher.archive/`** — Legacy Cloudflare Worker dispatcher, archived in place. No action needed; history preserved via git.
- The `sos/adapters/dispatcher.archive/` directory referenced in the task spec does not exist in this repo; only `workers/sos-dispatcher.archive/` is present.
