# SOS Editions — Packaging & Pricing (Draft)

Status: Draft v0.1  
Owner: Codex  
Last Updated: 2026-01-10

## Purpose
Translate SOS “editions” into product-ready packages: what’s enabled, what’s restricted, and what limits apply.

This doc should align with:
- `sos/kernel/config.py` defaults (`EDITION_DEFAULTS`)
- River’s edition policy sets (capabilities + allowlists)

## Editions (Core Positioning)

### Business
For professionals and organizations: autonomy + auditability.

### Education
For classrooms/labs: safe browsing, collaboration, no money by default.

### Kids
For minors: strict safety, no persistent memory by default, parental controls.

### Art
For creators: creative mode, plugins, artifacts, remix workflows.

## Feature Matrix (v0.1)

| Feature | Business | Education | Kids | Art |
|---|---:|---:|---:|---:|
| Memory persistence | ✅ | ✅ | ❌ | ✅ |
| Economy enabled | ✅ | ❌ | ❌ | ✅ |
| Tool execution | ✅ | ✅ | ✅ (strict) | ✅ |
| Content filter | off | on | strict | off |
| Creative mode | optional | optional | optional | ✅ |
| Safe search | optional | ✅ | ✅ | optional |

## Limits & Defaults (Starter Values)

These are defaults; higher tiers can raise them.

### Memory
- Business: high cap (e.g., 100k engrams)
- Education: moderate cap (e.g., 10k)
- Kids: off (session-only)
- Art: medium cap (e.g., 50k)

### Tools
- Education/Kids: domain allowlist, safe search enabled, response length caps.
- Business/Art: broader tool access; require capabilities for risky tools.

### Economy
- Business/Art only (v0.1):
  - daily payout limits per agent
  - witness gates above thresholds

## Packaging (Suggested Tiers)

### Tier 0 — Local (Free)
Self-hosted, local-only:
- single user
- limited tools (no outbound network by default)
- no economy by default
- basic memory (except kids)

### Tier 1 — Pro (Subscription + Usage)
For individuals:
- outbound tool access (allowlisted)
- persistent memory + backups
- 1–3 agents/squads
- usage-based compute add-on

### Tier 2 — Team / Classroom
For groups:
- multi-user identities
- shared memory spaces with permissioning
- audit logs + admin controls
- education edition defaults + classroom management

### Tier 3 — Enterprise / District
For organizations:
- SSO, compliance reporting
- custom policy packs and allowlists
- dedicated infrastructure (optional)
- premium support + SLA

## Add-Ons (Cross-Edition)
- Extra agents/squads
- Extra storage (memory + artifacts)
- Premium model routing (higher quality/faster)
- Marketplace subscription (curation + verified plugins)

## Implementation Mapping

### Enforcement Layers
1) **Config defaults** (`Config.edition` + `EDITION_DEFAULTS`)
2) **Policy engine** (River): capability grants/denials by edition
3) **Service gates**:
   - Engine: refuses disallowed routes/tools
   - Tools: sandbox + network allowlist
   - Memory: persistence on/off, quotas
   - Economy: enabled/disabled, limits

### What Engineering Needs Next
- A single “edition policy document” per edition (River task)
- Consistent limits schema (so web/CLI can display quotas)

