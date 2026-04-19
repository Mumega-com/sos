# Multi-Tenant Isolation

SOS serves multiple customers from a single instance. Each tenant is fully isolated.

## How isolation works

Each tenant gets:

- **Redis DB** -- DB 0 is system/kernel, DB 1+ are tenant-specific
- **Scoped tokens** -- tokens carry a `tenant_id` that limits what they can access
- **Isolated namespaces** -- tasks, memory, analytics, and bus messages are tenant-scoped
- **Separate DNS** -- each tenant gets a Cloudflare subdomain

```
Tenant A token → Redis DB 1 → only sees Tenant A data
Tenant B token → Redis DB 2 → only sees Tenant B data
System token   → Redis DB 0 → sees kernel + all tenants (admin only)
```

## Token scoping

Tokens are stored in `sos/bus/tokens.json`:

```json
{
  "sk-acme-abc123": {
    "label": "Acme Corp",
    "tenant": "acme",
    "project": "acme-website",
    "scopes": ["send", "inbox", "peers", "task_create", "task_list", "remember", "recall"]
  }
}
```

The kernel checks the token on every request and enforces:
- Agent can only send/receive within its tenant
- Agent can only access memory in its tenant namespace
- Agent can only see tasks in its tenant scope

## Provisioning a new tenant

### Manual

```bash
# Creates Redis DB, token, DNS, and initial config
bash SOS/sos/bus/onboard.sh <tenant-slug> <label>
```

### Stripe auto-provisioning

When a customer pays via Stripe:

1. Stripe sends webhook to the billing service
2. Billing service emits `payment.received` event
3. Provisioning handler catches the event and runs:
   - Allocates next available Redis DB
   - Generates scoped token
   - Creates Cloudflare DNS record
   - Mints Cloudflare API token for the tenant
   - Sends welcome message via bus
4. Customer receives MCP connection config:

```json
{
  "mcpServers": {
    "sos": {
      "url": "https://mcp.mumega.com/sse/sk-tenant-token"
    }
  }
}
```

## Cloudflare integration

Each tenant gets:

- **DNS**: `<tenant>.mumega.com` via Cloudflare API
- **Worker binding**: scoped to their zone
- **Token**: limited-scope Cloudflare API token for their resources only

## Customer dashboard

Tenants access their dashboard at `:8090` (or their subdomain) to see:

- Active agents and their status
- Task queue and history
- Memory usage and recent engrams
- Service health
- Usage and billing

## Tenant lifecycle events

| Event | When |
|-------|------|
| `tenant.created` | After provisioning completes |
| `tenant.deleted` | After cleanup (data retention period honored) |
| `payment.received` | Stripe confirms payment |
| `payment.failed` | Stripe reports failure |

Services subscribe to these events to react (send welcome emails, adjust rate limits, etc.).
