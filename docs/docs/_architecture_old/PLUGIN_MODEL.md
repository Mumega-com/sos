---
title: SOS Plugin Model
status: draft
updated: 2026-02-03
---

## Overview
SOS tools can be extended via plugins. Each plugin ships a `plugin.json` manifest and optional tool entrypoints.

## Manifest Schema (v0.1)
```json
{
  "name": "web-search-plugin",
  "version": "1.0.0",
  "author": "mumega",
  "description": "Web search capability via Tavily",
  "trust_level": "community",
  "capabilities_required": [
    "network:outbound:api.tavily.com",
    "config:read:TAVILY_API_KEY"
  ],
  "capabilities_provided": [
    "tool:web.search"
  ],
  "entrypoints": {
    "tools": {
      "tool:web.search": "plugin:search"
    }
  },
  "sandbox": {
    "filesystem": "read-only",
    "network": ["api.tavily.com:443"],
    "max_memory_mb": 256,
    "max_cpu_seconds": 30
  },
  "signature": "ed25519:..."
}
```

## Trust Levels
- `core`: ships with SOS, implicit trust  
- `verified`: signed by Mumega key  
- `community`: signed by author, requires approval in production  
- `unsigned`: blocked in production  

## Loader Behavior
- Reads `plugin.json` from `SOS_HOME/plugins/<plugin>/plugin.json`
- Rejects `trust_level=unsigned` when `SOS_ENV=production`
- Registers tools in `capabilities_provided`
- Resolves entrypoints in `entrypoints.tools` as `<module>:<callable>`

## Security
- Plugins must pass capability checks before tool execution.
- Sandboxing limits are enforced by `sos-tools` runtime/container policy.
