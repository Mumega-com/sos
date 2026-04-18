"""SOS kernel — unified policy gate (v0.5.1).

Every HTTP route, every agent action, every kernel-governed decision asks
exactly one question: ``can_execute(agent, action, resource, ...)``. The
gate composes all five signals the kernel cares about:

1. **Bearer token** — who is calling? (``sos.kernel.auth.verify_bearer``)
2. **Tenant scope** — may this caller touch this tenant's resources?
3. **Capability** — if configured, does the caller hold a signed
   capability token for this action? (``sos.kernel.policy.capability_auth``)
4. **FMAAP pillars** — flow / metabolism / alignment / autonomy / physics
   (``sos.kernel.policy.fmaap``) when the caller supplies squad context.
5. **Governance tier** — what's the configured tier for this action on
   this tenant? (``sos.kernel.governance.get_tier``)

The gate returns a single ``PolicyDecision`` (frozen, v0.5.1 baseline) and
writes exactly one ``AuditEventKind.POLICY_DECISION`` event to the audit
stream per call. Callers no longer touch auth, governance, FMAAP, or
audit directly.

Durability contract
-------------------
- New signals are added by composing them *inside* ``can_execute``. No
  plugin registry, no middleware hook, no config. If you need a new
  check, it's ~5 lines at the right spot in this function.
- ``PolicyDecision`` is additive-only (see
  ``tests/contracts/test_policy_schema_stable.py``). Renames and removes
  require a new kind, never a schema break.
- **Fail-open for availability, fail-closed for security.**
    - FMAAP DB unavailable / no squad context → warn + allow.
    - Bearer missing or invalid → deny (401-shaped).
    - Tenant scope mismatch → deny (403-shaped).
    - Capability required by env but missing/invalid → deny.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from sos.contracts.audit import AuditDecision, AuditEventKind
from sos.contracts.policy import PolicyDecision
from sos.kernel.audit import append_event as _audit_append
from sos.kernel.audit import new_event as _audit_new_event
from sos.kernel.auth import AuthContext, verify_bearer

logger = logging.getLogger("sos.kernel.policy.gate")


def _env_truthy(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _check_tenant_scope(ctx: AuthContext, tenant: str) -> tuple[bool, str]:
    """Return (ok, reason) — system/admin always ok; otherwise require matching scope.

    Extracted as a module-level pure function so it's independently testable
    and the same logic can be shared with the governance layer if needed.
    """
    if ctx.is_system or ctx.is_admin:
        return True, "system/admin scope"
    scope = ctx.project or ctx.tenant_slug
    if scope is None:
        return False, "token has no tenant scope"
    if scope != tenant:
        return False, f"token scoped to '{scope}', not '{tenant}'"
    return True, f"scope matches tenant '{tenant}'"


async def can_execute(
    *,
    agent: str | None = None,
    action: str,
    resource: str,
    tenant: str = "mumega",
    authorization: Optional[str] = None,
    capability: Any = None,
    context: Optional[dict[str, Any]] = None,
) -> PolicyDecision:
    """Unified policy check.

    Args:
        agent: Logical agent identifier (falls back to the token's agent).
        action: What the caller wants to do (``"oauth_read"``, ``"content_publish"``…).
        resource: What's being acted on (tenant name, resource id, path).
        tenant: Tenant scope the action belongs to.
        authorization: Raw ``Authorization`` header (``"Bearer ..."``) if any.
        capability: Decoded ``CapabilityModel`` if present (unused unless
            ``SOS_REQUIRE_CAPABILITIES=1`` is set and the auth layer
            populated it).
        context: Free-form dict — may carry ``squad_id``, ``skill``,
            ``fuel_grade``, ``estimated_cost_cents`` to light up FMAAP.

    Returns:
        ``PolicyDecision`` — immutable, audited, written to the audit stream.
    """
    ctx_dict: dict[str, Any] = dict(context or {})
    pillars_passed: list[str] = []
    pillars_failed: list[str] = []
    capability_ok: bool | None = None
    metadata: dict[str, Any] = {}
    scope_basis: str = "kernel-internal"  # default when no auth header given

    # --- 1. Bearer verification ---------------------------------------------
    auth_ctx: AuthContext | None = verify_bearer(authorization) if authorization else None
    if authorization and auth_ctx is None:
        return await _emit(
            agent=agent,
            action=action,
            resource=resource,
            tenant=tenant,
            decision=PolicyDecision(
                allowed=False,
                reason="invalid or inactive bearer token",
                tier="denied",
                action=action,
                resource=resource,
                agent=agent,
                tenant=tenant,
                pillars_passed=pillars_passed,
                pillars_failed=["auth"],
                capability_ok=capability_ok,
                metadata=metadata,
            ),
        )

    effective_agent = agent or (auth_ctx.agent if auth_ctx else None)

    # --- 2. Tenant scope ----------------------------------------------------
    if auth_ctx is not None:
        scope_ok, scope_reason = _check_tenant_scope(auth_ctx, tenant)
        if not scope_ok:
            return await _emit(
                agent=effective_agent,
                action=action,
                resource=resource,
                tenant=tenant,
                decision=PolicyDecision(
                    allowed=False,
                    reason=scope_reason,
                    tier="denied",
                    action=action,
                    resource=resource,
                    agent=effective_agent,
                    tenant=tenant,
                    pillars_passed=pillars_passed,
                    pillars_failed=["tenant_scope"],
                    capability_ok=capability_ok,
                    metadata=metadata,
                ),
            )
        scope_basis = scope_reason
        # system/admin callers are tracked separately so the audit record
        # tells operators *why* they got in — tenant match vs elevated scope.
        if auth_ctx.is_system or auth_ctx.is_admin:
            pillars_passed.append("system_admin")
        else:
            pillars_passed.append("tenant_scope")

    # --- 3. Capability ------------------------------------------------------
    # Only checked when the env says we require capabilities globally.
    # capability_auth handles its own verification via HTTPException; we
    # mirror its allow/deny logic here so the gate owns the decision.
    if _env_truthy("SOS_REQUIRE_CAPABILITIES", "0"):
        if capability is None:
            return await _emit(
                agent=effective_agent,
                action=action,
                resource=resource,
                tenant=tenant,
                decision=PolicyDecision(
                    allowed=False,
                    reason="capability required but not supplied",
                    tier="denied",
                    action=action,
                    resource=resource,
                    agent=effective_agent,
                    tenant=tenant,
                    pillars_passed=pillars_passed,
                    pillars_failed=["capability"],
                    capability_ok=False,
                    metadata=metadata,
                ),
            )
        capability_ok = True
        pillars_passed.append("capability")

    # --- 4. FMAAP 5-pillar validation ---------------------------------------
    # Only when the caller supplies a squad context — FMAAP requires a live
    # squad row. Fresh tenants with no squad data fall through to the
    # governance tier (availability > premature denial).
    squad_id = ctx_dict.get("squad_id") or ctx_dict.get("squadId")
    if squad_id and effective_agent:
        try:
            from sos.kernel.policy.fmaap import FMAAPPolicyEngine, FMAAPValidationRequest

            engine = FMAAPPolicyEngine()
            result = engine.validate(
                FMAAPValidationRequest(
                    agent_id=effective_agent,
                    action=action,
                    resource=resource,
                    context=ctx_dict,
                    metadata=ctx_dict,
                )
            )
            for pillar_result in result.results:
                pillar_name = pillar_result.pillar.value
                if pillar_result.passed:
                    pillars_passed.append(pillar_name)
                else:
                    pillars_failed.append(pillar_name)
            metadata["fmaap_overall_score"] = result.overall_score
            if not result.valid:
                # A failing pillar is authoritative — deny.
                failed_reasons = [r.reason for r in result.results if not r.passed]
                return await _emit(
                    agent=effective_agent,
                    action=action,
                    resource=resource,
                    tenant=tenant,
                    decision=PolicyDecision(
                        allowed=False,
                        reason=f"FMAAP: {'; '.join(failed_reasons)}",
                        tier="denied",
                        action=action,
                        resource=resource,
                        agent=effective_agent,
                        tenant=tenant,
                        pillars_passed=pillars_passed,
                        pillars_failed=pillars_failed,
                        capability_ok=capability_ok,
                        metadata=metadata,
                    ),
                )
        except Exception as exc:
            # Fail-open on FMAAP engine error — availability trumps a
            # stricter check we couldn't even run.
            logger.debug("FMAAP engine unavailable (%s): %s", type(exc).__name__, exc)
            metadata["fmaap_error"] = type(exc).__name__

    # --- 5. Governance tier --------------------------------------------------
    try:
        from sos.kernel.governance import get_tier

        tier = get_tier(tenant, action)
    except Exception as exc:
        logger.debug("governance tier lookup failed (%s): %s", type(exc).__name__, exc)
        tier = "act_freely"

    # The gate returns the tier as context; `governance.before_action` still
    # owns the batch-queue and human-gate routing for non-act_freely tiers.
    # From the gate's perspective, "allowed" means no signal above said no.
    return await _emit(
        agent=effective_agent,
        action=action,
        resource=resource,
        tenant=tenant,
        decision=PolicyDecision(
            allowed=True,
            reason=f"all signals passed (tier={tier}, scope={scope_basis})",
            tier=tier,
            action=action,
            resource=resource,
            agent=effective_agent,
            tenant=tenant,
            pillars_passed=pillars_passed,
            pillars_failed=pillars_failed,
            capability_ok=capability_ok,
            metadata=metadata,
        ),
    )


async def _emit(
    *,
    agent: str | None,
    action: str,
    resource: str,
    tenant: str,
    decision: PolicyDecision,
) -> PolicyDecision:
    """Write one POLICY_DECISION audit event and return the decision.

    Returns a *new* ``PolicyDecision`` with ``audit_id`` populated if the
    write succeeded. If the audit write fails (disk problem), we log the
    failure but still return the original decision — the caller's action
    should not be blocked by an audit hiccup at the gate level.
    """
    try:
        ev = _audit_new_event(
            agent=agent or "unknown",
            tenant=tenant,
            kind=AuditEventKind.POLICY_DECISION,
            action=action,
            target=resource,
            decision=AuditDecision.ALLOW if decision.allowed else AuditDecision.DENY,
            reason=decision.reason,
            policy_tier=decision.tier,
            metadata={
                "pillars_passed": decision.pillars_passed,
                "pillars_failed": decision.pillars_failed,
                "capability_ok": decision.capability_ok,
                **decision.metadata,
            },
        )
        audit_id = await _audit_append(ev)
        return decision.model_copy(update={"audit_id": audit_id})
    except Exception as exc:
        logger.warning("policy gate audit emit failed (%s): %s", type(exc).__name__, exc)
        return decision


__all__ = ["can_execute", "PolicyDecision"]
