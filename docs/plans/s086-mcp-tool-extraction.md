# S086 MCP Tool Extraction Plan

Purpose: reduce the blast radius of `sos/mcp/sos_mcp_sse.py` without changing
external MCP behavior.

The target shape is transport/session code in `sos_mcp_sse.py` and domain
handlers under `sos/mcp/tools/`.

## Principles

- Extract one domain at a time.
- Keep tool names, JSON-RPC envelopes, RBAC, rate limits, and text output stable.
- Domain modules receive dependencies explicitly instead of importing transport
  globals when practical.
- Add contract tests for every extracted domain before broad refactors.
- Leave compatibility shims only when existing tests or public imports need them.

## Domains

### Status Tools

Scope:

- `status`
- later: `outbox_status` after the first extraction proves the shape

Dependencies:

- Redis read access for agent/project visibility
- user-systemd probe for service state
- Squad service read for task counts

Contract tests:

- renderer preserves the current Markdown sections and icons
- project-scoped tokens only see project-registered agents
- task counts tolerate missing Squad token or HTTP failure

First extraction:

- `sos.mcp.tools.status`
- `status` dispatch calls `handle_status_tool(...)` from the transport

### Bus Tools

Scope:

- `ask`
- `send`
- `inbox`
- `check_in`
- `peers`
- `broadcast`

Dependencies:

- Redis stream naming helpers
- tenant/project isolation helpers
- audit emission
- optional subscriptions

Contract tests:

- same-tenant send allowed
- cross-tenant send blocked
- inbox includes authorized subscription streams
- `check_in` writes registry data without exposing secrets

### Task Tools

Scope:

- `task_create`
- `task_list`
- `task_update`
- `task_board`
- `request`
- `request_squad`
- `squad_status`

Dependencies:

- Squad service URL and token
- project/tenant scoping
- task rendering

Contract tests:

- create/list/update preserve current Squad HTTP calls
- project-scoped filtering remains enforced
- board scoring/rendering remains stable
- request creates the same task shape

### Memory Tools

Scope:

- `remember`
- `recall`
- `squad_remember`
- `squad_recall`
- `search_code`
- `memories`

Dependencies:

- Mirror direct DB when present
- Mirror HTTP fallback
- memory scope calculation
- optional project/workspace scoping

Contract tests:

- memory disabled returns graceful text
- direct DB and HTTP fallback preserve response shape
- project/workspace isolation remains enforced

### Admin And Onboarding Tools

Scope:

- `onboard`
- `sprout_tenant`
- `invite`
- `sign_in`
- `sign_out`
- `as_agent`
- `my_profile`
- `list_projects`
- `boot_context`
- `flow_health`
- `sprint_capsule`

Dependencies:

- token store
- onboarding invites/requests
- tenant node contract helpers
- service authority checks
- lifecycle/session mutation

Contract tests:

- unauthenticated onboarding endpoints remain public-safe
- invite/join idempotency is stable
- `as_agent` mutates only the current session
- flow health stays green in public-kernel composition

## Sequencing

1. Status tool extraction.
2. Bus tools extraction.
3. Task tools extraction.
4. Memory tools extraction.
5. Admin/onboarding extraction.
6. Move shared helpers into small internal support modules only after two
   domains prove the dependency shape.

## Exit Criteria

- Adding or reviewing a public MCP tool no longer requires reading the full
  transport file.
- Each extracted domain has focused tests.
- Existing JSON-RPC behavior remains stable for every moved tool.
