# {{DISPLAY_NAME}} — Tenant Home

## What this directory holds
Tenant-scoped state for `{{TENANT_SLUG}}`. Mirror key + bus token are minted at the SOS substrate level (`~/.sos/mirror_keys.json` + `SOS/sos/bus/tokens.json`); this directory holds the tenant-facing CLAUDE.md and config artifacts.

## Files
- `CLAUDE.md` — agent context for tools running at this tenant scope
- `.env.example` — environment variables (copy to `.env` and fill in secrets)
- `.gitignore` — standard ignores
- `README.md` — this file

## Provisioning provenance
Provisioned via `POST /api/internal/tenants/provision` (S027 D-1b LOCK). Idempotent — re-running provision for an existing tenant returns the existing mirror_key + bus_token without remint.

## Touch policy
**Do not live-edit files in `~/.mumega/templates/customer/`** — those are templates rendered at provision time. Edit per-tenant copies here in `~/.mumega/customers/{{TENANT_SLUG}}/` if customization is needed.
