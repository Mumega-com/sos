# {{DISPLAY_NAME}}

## Connection
This project is connected to Mumega SOS.
- Tenant slug: `{{TENANT_SLUG}}`
- Industry: `{{INDUSTRY}}`
- Memory: scoped to `{{TENANT_SLUG}}` namespace
- Bus: project-isolated messaging (project=`{{TENANT_SLUG}}`)

## Tools
All tools are available via the `sos` MCP:
- `send` / `inbox` / `peers` / `broadcast` — team messaging (tenant-scoped)
- `remember` / `recall` / `memories` — persistent memory (tenant-scoped)

## Tenant Identity
- Bus token: scoped to `agent={{TENANT_SLUG}}-admin`, `scope=tenant`, `role=owner`
- Mirror key: `agent_slug={{TENANT_SLUG}}`
- Provisioned via `POST /api/tenants/init` (S027 D-1)

## Activate Agents
To fork Mumega agents (athena/loom/calliope/etc.) at this tenant scope, use:
```
POST /api/tenants/{{TENANT_SLUG}}/agents/activate
```
(S027 D-2 surface)
