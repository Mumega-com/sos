# Plugin And Profile Contract

Status: release-candidate contract
Last updated: 2026-05-20

SOS public core is a microkernel. It owns coordination primitives, stable
contracts, client boundaries, and extension loading rules. It does not own
Mumega-hosted product services, private agent identities, provider-specific
growth adapters, customer operations, or deployment state.

## Dependency Rule

Public `sos` may define contracts that plugins implement.

Plugins, host overlays, and private product repositories may import public
`sos`.

Public `sos` must not import a host overlay, `mumega.com`, named private agents,
or provider/product-specific modules.

## Extension Families

| Family | Public Core Owns | Plugin/Host Owns |
|---|---|---|
| Agent profiles | `AgentProfile` shape, lookup helpers, empty public registry | Named agents, private souls, live runbooks |
| Agent onboarding | Generic invite/session contracts and recovery messages | Host-specific self-join, token minting, local agent homes |
| Services | Health shape, client conventions, service registration contract | SaaS, billing, integrations, provider services |
| Provider adapters | Generic port contracts and client errors | Etsy, GHL, GTM, Google, Stripe, Apify, BrightData adapters |
| Squads | Generic task/squad contracts | Customer/project/private squad charters and memory |
| Deployment | CLI entry points for public core services | systemd units, Cloudflare workers, private env and tokens |

## Agent Profiles

Public SOS ships the `sos.agent_profiles.AgentProfile` dataclass and an empty
`PUBLIC_AGENT_PROFILES` tuple.

Hosts may provide profiles by adapter code, service overlay, or future plugin
entry points. Public core code should accept injected profile collections rather
than importing named private agents.

Required fields:

- `name`: stable lowercase-friendly identifier.
- `title`: short display title.
- `tagline`: one-line role summary.
- `model`: model family or `multi`.
- `roles`: tuple of role tags.

Public code may render a profile, search by name, or route against role tags.
Public code must not assume that agents such as `kasra`, `athena`, `river`,
`loom`, or `sos-medic` exist.

## Service Plugins

Public core can keep HTTP clients when they are stable boundaries. A client is
allowed in public core only when it talks over a URL, validates plain contract
types, and does not import service internals.

Allowed pattern:

```text
sos.clients.billing -> HTTP(S) URL from SOS_BILLING_URL
```

Forbidden pattern:

```text
sos.clients.billing -> import sos.services.billing.webhook
```

Private service implementations belong outside public core. Current Mumega
service implementations live in the host overlay:

- billing
- integrations
- SaaS
- Etsy
- GHL
- GTM

## Compatibility Shims

Shims are allowed only to protect live deployments during migration.

Allowed shim:

- Lives at the old public/internal import path.
- Imports a single host-provided implementation lazily.
- Has a deletion issue and removal date or release target.
- Contains no secrets, tokens, private data, or business logic.
- Has a test proving the shim delegates or fails with a clear installation
  message.

Forbidden shim:

- Reintroduces private implementation into public core.
- Imports `mumega.com` or a local absolute path from public `sos`.
- Silently falls back to stale internal code.
- Carries token-bearing config or runtime state.

## Launch Paths

Old launch paths such as `python -m sos.services.etsy.asset_forge` must move in
two steps:

1. Add a host-owned entry point or deployment unit for the add-on module.
2. Keep a temporary shim only if a live systemd unit, worker, or operator command
   still depends on the old path.

After deployment references are updated, delete the shim and add the old path to
the public release boundary checker if it is not already covered.

## Deletion Checklist

Before deleting an old internal/private path from SOS:

- Copy source and relevant tests into the host overlay.
- Exclude generated artifacts and token-bearing runtime files.
- Map live imports, entry points, ports, systemd units, workers, env vars, and
  tests.
- Decide whether a shim is needed.
- Add or update tests for the new host-owned path or shim.
- Run the public release boundary checker.
- Run focused tests for the touched family.
- Update the migration handoff and GitHub issue.

Current deletion order should be low-risk first:

1. Docs/identity paths with no runtime imports.
2. Provider adapters with explicit shims.
3. Agent profile/onboarding surfaces after injected profile lookup lands.
4. Billing, SaaS, and integrations after URL clients and launch paths are
   stable.

## Non-Goals

This contract does not define a package manager, marketplace, or remote plugin
registry. It only defines the boundary public SOS must preserve while host
overlays and private services continue to evolve.
