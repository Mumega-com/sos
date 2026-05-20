# Public Release Boundary

Status: release-candidate gate
Last updated: 2026-05-20

SOS public core is the reusable agent coordination substrate. It should not ship
Mumega-hosted product operations, named internal agents, private personas,
customer/project operations, or provider-specific business-growth adapters.

## Gate

Run:

```bash
python3 scripts/check_public_release_boundary.py --repo-root . --show-ok
```

Expected result for a public release candidate:

```text
public release boundary clean
```

## Removed From Public Core

The boundary pass removes these families from the public repository:

- `athena/`
- `operations/`
- `personas/`
- `projects/mumega/`
- `sos/agents/`
- `sos/services/etsy/`
- `sos/services/ghl/`
- `sos/services/gtm/`
- `sos/services/saas/`
- private tests for those surfaces

These belong in Mumega private overlays or optional plugin packages. Public SOS
keeps generic contracts, bus/MCP/task primitives, and extension points.

## Replacement Surface

`sos.agent_profiles` is the public-safe replacement for hardcoded internal agent
definitions. The public package ships no named Mumega agents by default. Hosts
can register their own profiles through plugins or service overlays.

Agent self-join is also host/plugin territory. Public SOS can still use local
development token records and external MCP clients without shipping Mumega's
private onboarding flow.

See `docs/PLUGIN_PROFILE_CONTRACT.md` for the contract that governs host
overlays, compatibility shims, launch paths, and deletion sequencing.
