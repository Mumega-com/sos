# `sos.kernel.policy.gate` — Unified Policy Gate

**Introduced:** v0.5.1 · **Contract:** `sos.contracts.policy.PolicyDecision` (frozen)

## What this is

The kernel's single decision point for every governed action. Every HTTP route,
every agent action, every kernel-governed decision asks exactly one question:
`can_execute(agent, action, resource, ...)`. The gate composes five signals —
bearer auth, tenant scope, capability tokens, FMAAP pillars, and governance
tier — into one immutable answer. Callers no longer touch auth, governance,
FMAAP, or audit directly.

## What this is NOT

- Not auth itself. Bearer verification lives in `sos.kernel.auth.verify_bearer`.
  The gate calls it; it does not replace it.
- Not governance tier storage. Tier lookup lives in `sos.kernel.governance.get_tier`.
  The gate reads the tier and returns it; routing decisions for `batch_approve`
  and `human_gate` tiers remain inside `governance.before_action`.
- Not FMAAP math. The five-pillar scoring lives in `sos.kernel.policy.fmaap`.
  The gate instantiates the engine and interprets the result; it does not own
  the scoring logic.

The gate composes these modules. It does not duplicate or replace them.

## Surface

One public function, one return type:

```python
from sos.contracts.policy import PolicyDecision
from sos.kernel.policy.gate import can_execute

decision: PolicyDecision = await can_execute(
    agent="kasra",                          # logical agent id (optional)
    action="oauth_credentials_read",        # what the caller wants to do
    resource="mumega/google",               # what's being acted on
    tenant="mumega",                        # tenant scope for the action
    authorization="Bearer <token>",         # raw Authorization header
    capability=None,                        # decoded CapabilityModel if present
    context={"squad_id": "sq_123"},         # free-form; enables FMAAP when squad_id present
)
```

`PolicyDecision` fields (frozen, additive-only):

```python
allowed: bool          # True = proceed; False = deny
reason: str            # human-readable explanation
tier: str              # act_freely | batch_approve | human_gate | dual_approval | denied
action: str            # echoed from the call
resource: str          # echoed from the call
agent: str | None      # resolved; may differ from input when token-derived
tenant: str | None     # echoed from the call
pillars_passed: list[str]   # signals that cleared
pillars_failed: list[str]   # signals that blocked (empty on allow)
capability_ok: bool | None  # None when capability check is disabled
audit_id: str | None        # populated after audit write succeeds
metadata: dict[str, Any]    # fmaap_overall_score, fmaap_error, etc.
```

## The five signals

The gate evaluates signals in order. A failure on any security-critical signal
short-circuits and returns immediately with `allowed=False`.

1. **Bearer token** — `sos.kernel.auth.verify_bearer` resolves the caller's
   identity. Present but invalid → immediate deny.
2. **Tenant scope** — the token's project/tenant slug must match the requested
   tenant, unless the caller is system or admin scope.
3. **Capability** — when `SOS_REQUIRE_CAPABILITIES=1`, the call must carry a
   decoded `CapabilityModel`. Missing → immediate deny. Off by default.
4. **FMAAP pillars** — flow / metabolism / alignment / autonomy / physics,
   evaluated by `sos.kernel.policy.fmaap.FMAAPPolicyEngine`. Only runs when
   `context` carries a `squad_id` and an agent is resolved. Any failing pillar
   denies the call.
5. **Governance tier** — `sos.kernel.governance.get_tier(tenant, action)` returns
   the configured tier. The gate returns it in `PolicyDecision.tier`; routing
   for non-`act_freely` tiers is handled by `governance.before_action`.

## Fail-open vs fail-closed

**Fail-closed (security-critical)** — short-circuits immediately with `allowed=False`:

- Bearer token present but invalid → `pillars_failed=["auth"]`, 401-shaped reason.
- Token scoped to the wrong tenant → `pillars_failed=["tenant_scope"]`, 403-shaped reason.
- Capability required by env (`SOS_REQUIRE_CAPABILITIES=1`) but absent → `pillars_failed=["capability"]`.
- FMAAP scoring completes and returns `valid=False` → reason enumerates the failing pillars.

**Fail-open (availability-critical)** — logs a warning and continues:

- FMAAP engine unavailable (import error, DB down, any exception) → `metadata["fmaap_error"]` set,
  execution falls through to governance tier.
- `get_tier` raises → tier defaults to `"act_freely"`.

A FMAAP engine that cannot start is not evidence of a policy violation. A forged
token or cross-tenant probe is. The two categories are kept strictly separate.

## Who calls the gate today

- **`sos.kernel.governance.before_action`** — consults the gate before routing
  any governed action. This is the primary call site for agent decisions.
- **`sos.services.integrations.app`** — migrated all three authenticated routes
  (`oauth_credentials_read`, `oauth_ghl_callback`, `oauth_google_callback`) as
  the proof-of-concept migration for v0.5.1.

## Who calls the gate next

v0.5.1.1+ mop-up commits will migrate: `sos.services.economy`,
`sos.kernel.agent_registry`, `sos.services.squad`, `sos.services.bus`, and
`sos.skills`. Each uses the same pattern described in "Migrating a service".

## Audit

Every `can_execute` call writes exactly one `AuditEventKind.POLICY_DECISION`
event before returning. The event carries `pillars_passed`, `pillars_failed`,
`capability_ok`, and any FMAAP metadata.

`PolicyDecision.audit_id` is populated after a successful write. If the audit
write fails, the gate logs a warning and returns the decision without an
`audit_id` — an audit hiccup should not be indistinguishable from a policy
denial.

**Disk-authoritative, bus-observational** — same pattern as `sos.kernel.audit`.
Redis down → bus emit skipped; disk record already written.

## Durability contract

`PolicyDecision` is frozen at v0.5.1: never remove fields, never narrow types,
never rename. New fields must be optional with a default. A rename is a remove
plus an add — both are forbidden.

`tests/contracts/test_policy_schema_stable.py` snapshots the baseline and fails
any PR that breaks these rules. If the test fails, the answer is almost never
"update the snapshot" — it is "find a non-breaking way to express the change."

## Migrating a service

Replace inline auth/scope/admin triples with a single gate call. Example from
`sos.services.integrations.app`:

```python
# Before — three checks, three audit paths, no shared contract.
auth_ctx = _verify_bearer(authorization)
_check_tenant_scope(auth_ctx, tenant)
_require_system_or_admin(auth_ctx)

# After — one call, one POLICY_DECISION audit event.
decision = await can_execute(
    action="oauth_ghl_callback",
    resource=tenant,
    tenant=tenant,
    authorization=authorization,
)
_raise_on_deny(decision, require_system=True)
```

`_raise_on_deny` is a local helper that maps `allowed=False` to a 401 or 403
`HTTPException` based on the `reason` field. The gate itself never raises — it
always returns a `PolicyDecision`.
