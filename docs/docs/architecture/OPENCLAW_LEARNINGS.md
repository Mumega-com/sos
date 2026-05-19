---
title: OpenClaw learnings for SOS
status: draft
updated: 2026-02-03
---

## Goals
- Reuse proven patterns from OpenClaw without importing its runtime.
- Keep alignment with existing SOS docs: `SECURITY_MODEL.md`, `TASK_SYSTEM.md`, `MESSAGE_BUS.md`.

## What OpenClaw does well (relevant to SOS)
- **Gateway as single control plane**: JSON-RPC over WS/HTTP; routes channels → agents; provides sessions/presence/config.
- **Pairing/allowlists**: DM pairing mode with approval codes; per-channel allowlists stored locally; unknown senders gated.
- **Channel-first adapters**: Telegram/Discord/Slack/Signal/iMessage/WhatsApp web share a common routing layer.
- **Plugin/skills packaging**: extension packages with their own deps; runtime loads only declared entrypoints.
- **Doctor/onboarding**: interactive wizard + doctor commands validating ports, creds, channels, permissions.
- **Hardening defaults**: loopback bind, token-gated gateway, minimal write paths, explicit env files.

## Recommended adoptions for SOS
### Gateway contract
- Expose `sos-tools` via JSON-RPC (WS/HTTP) with `Authorization: Bearer <capability>`; forbid querystring tokens.
- Default bind `127.0.0.1`; require explicit allowlist for remote binds; include rate limits per capability.
- Emit audit events (who/what/resource) to economy/ledger; store minimal summaries in `sos-memory`.

### Pairing & identity
- Add pairing flow to `sos-identity`: issue short-lived pairing codes; approve → persistent allowlist entry (channel, user, agent).
- Capabilities issued on approval: `tool:execute` + resource pattern scoped to that channel/user.
- Store allowlists centrally (not flat files); surface CRUD via identity service API.

### Plugin/skills model
- Define `plugin.json` for `sos-tools` plugins:
  - `name`, `version`, `author`, `trust_level (core|verified|community|unsigned)`
  - `capabilities_required` (network targets, config keys, fs scopes)
  - `capabilities_provided` (tool names)
  - `entrypoints.tool`, `sandbox` (fs: ro/rw paths, network allowlist, cpu/mem/time)
  - optional `signature` (ed25519) → map to `SECURITY_MODEL` trust levels.
- Loader enforces trust + sandbox + capabilities before activation.

### Sandboxing & ops
- Service defaults: `NoNewPrivileges`, read-only root, explicit `ReadWritePaths`, seccomp/AppArmor, tmpfs `/tmp`.
- Gateway and tools run with per-plugin workspaces; deny FS/network by default; open only what manifest + capability allow.
- Health endpoints per service; structured logs with rotation.
- `sos doctor`: validate ports/binds, capability requirements, pairing state, plugin signatures, and channel configs.

### Telemetry & privacy
- Disable phone-home/update checks by default; require explicit opt-in with logging.
- Keep secrets only in env/secret store; lint to fail on checked-in secrets or script-inline keys.

## Quick action list
1) Draft JSON-RPC schema + error codes for `sos-tools` (auth failures, capability denied, rate limit).  
2) Implement pairing API in `sos-identity` + allowlist store; issue scoped capabilities on approval.  
3) Define `plugin.json` schema and loader in `sos-tools`; enforce sandbox + capability gates.  
4) Add systemd/container hardening presets to all services (mirror gateway unit hardening).  
5) Add `sos doctor` CLI covering binds, creds, capabilities, pairing, plugins.  
6) Rotate Moltbot/OpenClaw gateway token after gateway path is pointed to a local build (if used for benchmarking only).  

## Issue/PR backlog (codex branch)
- **Gateway RPC contract**: JSON-RPC spec + auth/rate-limit rules; update `MESSAGE_BUS.md`.  
- **Pairing/allowlists in identity**: pairing code issue/approve/list; persisted allowlists; scoped capability issuance.  
- **Plugin manifest + loader**: `plugin.json` schema, trust-level enforcement, sandbox + capability gate; sample plugin.  
- **Runtime hardening**: shared systemd/container security profile; deny-by-default FS/network for tools.  
- **`sos doctor` CLI**: checks binds, capabilities, pairing state, plugin signatures, channel config.  
- **Telemetry/secret hygiene**: disable phone-home by default; lint for secrets-in-repo/scripts; opt-in path documented.  
- **Audit trail**: log each tool call (who/what/resource) to economy/ledger; optional summary to `sos-memory`.  
- **Optional benchmark**: build/run OpenClaw gateway on loopback with unique token; measure latency; tear down.  

### Proposed issue titles (ready to file)
1) “SOS: define tools JSON-RPC contract + auth/rate limits”  
2) “SOS Identity: add pairing + allowlist flow with scoped capabilities”  
3) “SOS Tools: plugin manifest/loader with trust + sandbox gates”  
4) “SOS Services: apply default hardening profile (systemd/container)”  
5) “SOS CLI: add `sos doctor` health checks (binds/capabilities/pairing/plugins)”  
6) “SOS Security: disable phone-home by default + secret lint”  
7) “SOS Economy/Memory: audit trail for tool calls”  
8) “[Benchmark] Run OpenClaw gateway on loopback for latency comparison”  

## Out of scope (by design)
- Do **not** vendor OpenClaw runtime into SOS; use it only for reference/benchmarking on loopback with a unique token.
