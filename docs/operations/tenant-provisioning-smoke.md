# Tenant Provisioning Smoke

Date: 2026-05-20
Scope: S088-B live companion-endpoint smoke against the running bus bridge.
Tracker: https://github.com/Mumega-com/sos/issues/174

## Commands Exercised

The smoke called the running bus bridge on `http://localhost:6380` with
`INTERNAL_API_SECRET` from `/home/mumega/.env.secrets`.

Test tenant:

- slug: `s088-smoke-296165`
- tenant id: `tenant-s088-smoke-296165`
- display name: `S088 Smoke Tenant`
- industry: `saas`
- agent kind: `athena`

Endpoints:

- `POST /api/internal/tenants/provision`
- `POST /api/internal/tenants/{tenant_id}/agents/activate`

The unauthenticated probe returned `401`, so the fail-closed guard is active.

## Result

Provision returned `200`:

```json
{
  "mirror_minted": true,
  "scaffold_created": true,
  "token_minted": true
}
```

Activation returned `200`:

```json
{
  "qnft_minted": true,
  "routing_registered": true,
  "scaffold_created": true,
  "token_minted": true
}
```

Verified immediately after the call:

- Mirror key cache row existed.
- customer scaffold existed.
- tenant metadata existed.
- tenant-agent scaffold existed.
- package-local token and qNFT records existed in the public-kernel checkout.

The test artifacts were then removed: scaffold, Mirror key cache row, routing
row, and the public-kernel package-local `tokens.json` / `qnft_registry.json`
files created by the smoke.

## Blocker Found

The tenant companion code currently writes token and qNFT state relative to the
imported package directory:

- `sos.bus.tenant_provisioning.TOKENS_PATH`
- `sos.bus.tenant_agent_activation.QNFT_REGISTRY_PATH`

The running `bus-bridge.service` already sets:

```ini
Environment=SOS_BUS_TOKENS_PATH=/home/mumega/SOS/sos/bus/tokens.json
```

But the companion provisioning modules do not honor that env var. Because the
bus bridge now runs from `/home/mumega/sos-public-kernel`, the smoke created
secret-bearing runtime files inside the public checkout before cleanup.

This is a production-readiness blocker for tenant provisioning on the public
kernel, now tracked as https://github.com/Mumega-com/sos/issues/174. The
endpoint can return `200` while persisting credentials to the wrong runtime
store.

## Required Fix

- Make tenant provisioning and tenant-agent activation resolve token and qNFT
  state from explicit env-backed runtime paths, not package-local files.
- Keep public defaults out of the repo tree, for example under `/home/mumega/.sos/`.
- Add tests proving `SOS_BUS_TOKENS_PATH` is honored by:
  - tenant admin token minting;
  - tenant-agent token minting;
  - bridge companion endpoint behavior.
- Add an env-backed qNFT registry path or move qNFT mint state to a proper
  runtime store.
- Re-run this smoke after the fix and verify the internal/live token registry
  contains the tenant admin and tenant-agent records.

## Production-Ready Gap

Tenant provisioning is structurally present and the auth/idempotency paths work,
but it should not be considered production-ready on the public-kernel runtime
until the runtime-store path bug is fixed and covered by tests.
