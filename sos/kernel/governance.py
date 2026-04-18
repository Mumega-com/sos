# Moved from scripts — SOS governance layer
# Lightweight accountability + configurable policy tiers.
# Not a framework. Not FMAAP. Just intent logging + tier checks.

"""SOS Governance — accountability without bureaucracy.

Every action gets logged (intent). Some actions get gated (tiers).
Configurable per tenant via ~/.sos/governance/{tenant}.yaml.

Tiers:
  act_freely    — log intent, execute immediately
  batch_approve — queue for batch approval (outreach emails, social posts)
  human_gate    — notify human, wait for approval (payments, deploys)
  dual_approval — notify two approvers (data deletion, security changes)

Default: everything is act_freely. Governance grows with trust.

v0.5.0:
  - Budget check flipped from inline `sos.services.economy.metabolism`
    import to HTTP via `sos.clients.economy.AsyncEconomyClient`. Kernel
    no longer imports any service module. Fail-open on HTTP error.
  - Every intent also written to the unified audit stream
    (`sos.kernel.audit`). Legacy `~/.sos/governance/intents/` files
    still populated for one version as a read-side compat shim.

v0.5.1:
  - ``before_action`` consults ``sos.kernel.policy.gate.can_execute``
    first. FMAAP pillar failures are now authoritative denials without
    governance having to re-implement the check. The gate also writes
    its own ``POLICY_DECISION`` audit event; governance still writes
    the ``INTENT`` event (different concern, different kind).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sos.clients.economy import AsyncEconomyClient
from sos.contracts.audit import AuditDecision, AuditEventKind
from sos.kernel.audit import append_event as _audit_append
from sos.kernel.audit import new_event as _audit_new_event

logger = logging.getLogger("sos.governance")

# Module-level client — kernel→economy boundary crossing via HTTP.
# Fail-open on any error so governance availability is preserved.
_economy_client = AsyncEconomyClient()

TIERS = {"act_freely", "batch_approve", "human_gate", "dual_approval"}

# Default policy — everything is free unless configured otherwise
DEFAULT_POLICY = {
    "content_publish": "act_freely",
    "content_edit": "act_freely",
    "seo_update": "act_freely",
    "analytics_read": "act_freely",
    "outreach_email": "batch_approve",
    "social_post": "batch_approve",
    "payment_create": "human_gate",
    "payment_refund": "human_gate",
    "deploy_production": "human_gate",
    "credential_change": "dual_approval",
    "tenant_delete": "dual_approval",
    "data_delete": "dual_approval",
}


def _governance_dir() -> Path:
    return Path.home() / ".sos" / "governance"


def load_policy(tenant: str) -> dict[str, str]:
    """Load governance policy for a tenant. Falls back to defaults."""
    policy_file = _governance_dir() / f"{tenant}.yaml"
    if policy_file.exists():
        try:
            import yaml
            return {**DEFAULT_POLICY, **yaml.safe_load(policy_file.read_text())}
        except Exception:
            pass

    # Try JSON fallback
    policy_json = _governance_dir() / f"{tenant}.json"
    if policy_json.exists():
        try:
            return {**DEFAULT_POLICY, **json.loads(policy_json.read_text())}
        except Exception:
            pass

    return DEFAULT_POLICY.copy()


def get_tier(tenant: str, action: str) -> str:
    """Get the governance tier for a specific action."""
    policy = load_policy(tenant)
    return policy.get(action, "act_freely")


async def before_action(
    agent: str,
    action: str,
    target: str,
    reason: str = "",
    tenant: str = "mumega",
    metadata: dict[str, Any] | None = None,
) -> dict:
    """Log intent and check policy tier. Call before every significant action.

    Returns:
        {"allowed": True/False, "tier": str, "intent_id": str}

    If allowed=False, the action was queued for approval. Check later.
    """
    tier = get_tier(tenant, action)
    now = datetime.now(timezone.utc).isoformat()
    intent_id = f"{agent}:{action}:{int(datetime.now(timezone.utc).timestamp())}"

    # v0.5.1: consult the unified policy gate first. FMAAP pillar failures
    # (missing squad, insufficient coherence, budget-grade mismatch) are
    # authoritative denials — budget/scope/capability all roll up here.
    # Kernel-internal callers pass no authorization header; gate still
    # runs FMAAP + tier lookup in that mode.
    try:
        from sos.kernel.policy.gate import can_execute as _gate_can_execute

        gate_decision = await _gate_can_execute(
            agent=agent,
            action=action,
            resource=target,
            tenant=tenant,
            authorization=None,
            context={**(metadata or {}), "agent_id": agent},
        )
        if not gate_decision.allowed:
            logger.warning(
                f"GATE DENIED: {agent} → {action} on {target} "
                f"(tier={gate_decision.tier}): {gate_decision.reason}"
            )
            return {
                "allowed": False,
                "tier": gate_decision.tier,
                "intent_id": intent_id,
                "reason": gate_decision.reason,
                "audit_id": gate_decision.audit_id,
            }
    except Exception as exc:
        # Fail-open: gate unavailability must not block legitimate actions.
        logger.debug("policy gate unavailable (%s): %s", type(exc).__name__, exc)

    # v0.5.0: Budget check via HTTP — kernel never imports services directly.
    # Fail-open on any error (connection refused, timeout, economy down) so
    # governance availability is preserved. Denials here are authoritative.
    estimated_cost = (metadata or {}).get("estimated_cost", 0.0)
    project = (metadata or {}).get("project", tenant)
    try:
        budget_check = await _economy_client.can_spend(project, estimated_cost)
        if not budget_check.get("allowed", True):
            logger.warning(
                f"BUDGET BLOCKED: {agent} → {action} on {target}. "
                f"Project {project} over budget: {budget_check.get('reason', 'unknown')}"
            )
            # Emit audit record for the denial before returning
            try:
                await _audit_append(_audit_new_event(
                    agent=agent,
                    tenant=tenant,
                    kind=AuditEventKind.INTENT,
                    action=action,
                    target=target,
                    decision=AuditDecision.DENY,
                    reason=budget_check.get("reason", "budget exceeded"),
                    policy_tier="budget_exceeded",
                    metadata={"intent_id": intent_id, "budget": budget_check, **(metadata or {})},
                ))
            except Exception as audit_exc:
                logger.debug("audit append failed on budget denial: %s", audit_exc)
            return {
                "allowed": False,
                "tier": "budget_exceeded",
                "intent_id": intent_id,
                "reason": budget_check.get("reason", "budget exceeded"),
                "budget": budget_check,
            }
        if budget_check.get("warning"):
            logger.info(
                f"BUDGET WARNING: {project} at {budget_check.get('pct_used', 0):.0f}% "
                f"(${budget_check.get('spent', 0):.4f} / ${budget_check.get('budget', 0):.4f})"
            )
    except Exception as exc:
        # Fail-open: economy unreachable should never block governance.
        logger.debug("budget check unavailable (%s): %s", type(exc).__name__, exc)

    # Intent record (used by queue paths below + Mirror best-effort log).
    intent = {
        "id": intent_id,
        "agent": agent,
        "action": action,
        "target": target,
        "reason": reason,
        "tier": tier,
        "tenant": tenant,
        "timestamp": now,
        "metadata": metadata or {},
    }

    # v0.5.2: audit stream is the sole durable write path for intents.
    # The legacy ~/.sos/governance/intents/{tenant}/{date}.jsonl shim was
    # removed per the v0.5.0 CHANGELOG — audit has been authoritative for
    # a full minor version.
    try:
        await _audit_append(_audit_new_event(
            agent=agent,
            tenant=tenant,
            kind=AuditEventKind.INTENT,
            action=action,
            target=target,
            decision=AuditDecision.NOT_APPLICABLE,
            reason=reason,
            policy_tier=tier,
            metadata={"intent_id": intent_id, **(metadata or {})},
        ))
    except Exception as audit_exc:
        logger.debug("audit append failed on intent log: %s", audit_exc)

    # Log to Mirror (best effort)
    try:
        import httpx
        mirror_url = os.environ.get("MIRROR_URL", "http://localhost:8844")
        mirror_token = os.environ.get("MIRROR_TOKEN", "")
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(
                f"{mirror_url}/engrams",
                json={
                    "content": f"INTENT: {agent} → {action} on {target}. Reason: {reason}. Tier: {tier}.",
                    "context": {"type": "intent", **intent},
                },
                headers={"Authorization": f"Bearer {mirror_token}"},
            )
    except Exception:
        pass  # Mirror is optional for intent logging

    logger.info(f"INTENT [{tier}]: {agent} → {action} on {target}")

    # Check tier
    if tier == "act_freely":
        return {"allowed": True, "tier": tier, "intent_id": intent_id}

    elif tier == "batch_approve":
        # Queue for batch approval
        queue_file = _governance_dir() / "queues" / tenant / "pending.jsonl"
        queue_file.parent.mkdir(parents=True, exist_ok=True)
        with open(queue_file, "a") as f:
            f.write(json.dumps(intent) + "\n")
        logger.info(f"QUEUED for batch approval: {intent_id}")
        return {"allowed": False, "tier": tier, "intent_id": intent_id}

    elif tier == "human_gate":
        # Notify human via bus
        try:
            import redis.asyncio as aioredis
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            r = aioredis.from_url(redis_url, decode_responses=True)
            await r.xadd(
                "sos:stream:global:agent:hadi",
                {
                    "source": "governance",
                    "text": f"[APPROVAL NEEDED] {agent} wants to {action} on {target}. Reason: {reason}. Reply 'approve {intent_id}' to allow.",
                    "type": "approval_request",
                    "timestamp": now,
                },
            )
            await r.aclose()
        except Exception:
            pass
        logger.info(f"GATED (human): {intent_id} — waiting for Hadi")
        return {"allowed": False, "tier": tier, "intent_id": intent_id}

    elif tier == "dual_approval":
        # Notify both Athena and Hadi
        try:
            import redis.asyncio as aioredis
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            r = aioredis.from_url(redis_url, decode_responses=True)
            for approver in ["athena", "hadi"]:
                await r.xadd(
                    f"sos:stream:global:agent:{approver}",
                    {
                        "source": "governance",
                        "text": f"[DUAL APPROVAL NEEDED] {agent} wants to {action} on {target}. Both you and {'hadi' if approver == 'athena' else 'athena'} must approve. Reply 'approve {intent_id}'.",
                        "type": "dual_approval_request",
                        "timestamp": now,
                    },
                )
            await r.aclose()
        except Exception:
            pass
        logger.info(f"GATED (dual): {intent_id} — waiting for Athena + Hadi")
        return {"allowed": False, "tier": tier, "intent_id": intent_id}

    # Unknown tier — default allow
    return {"allowed": True, "tier": tier, "intent_id": intent_id}


async def approve(intent_id: str, approver: str, tenant: str = "mumega") -> bool:
    """Approve a queued action."""
    queue_file = _governance_dir() / "queues" / tenant / "pending.jsonl"
    approved_file = _governance_dir() / "queues" / tenant / "approved.jsonl"

    if not queue_file.exists():
        return False

    found = False
    remaining = []
    approved_file.parent.mkdir(parents=True, exist_ok=True)

    for line in queue_file.read_text().strip().split("\n"):
        if not line:
            continue
        intent = json.loads(line)
        if intent.get("id") == intent_id:
            intent["approved_by"] = approver
            intent["approved_at"] = datetime.now(timezone.utc).isoformat()
            with open(approved_file, "a") as f:
                f.write(json.dumps(intent) + "\n")
            found = True
            logger.info(f"APPROVED: {intent_id} by {approver}")
        else:
            remaining.append(line)

    queue_file.write_text("\n".join(remaining) + "\n" if remaining else "")
    return found


def get_pending(tenant: str) -> list[dict]:
    """Get all pending approval requests for a tenant."""
    queue_file = _governance_dir() / "queues" / tenant / "pending.jsonl"
    if not queue_file.exists():
        return []
    results = []
    for line in queue_file.read_text().strip().split("\n"):
        if line:
            results.append(json.loads(line))
    return results


def get_intent_log(tenant: str, date: str | None = None, limit: int = 50) -> list[dict]:
    """Get intent log for a tenant.

    v0.5.2: reads from the audit spine (``sos.kernel.audit``) rather than
    the removed ``~/.sos/governance/intents/`` shim. Returns dicts shaped
    like the legacy records so existing callers keep working.
    """
    from sos.kernel.audit import read_events as _audit_read

    events = _audit_read(tenant, date=date, kind=AuditEventKind.INTENT, limit=limit)
    results: list[dict] = []
    for ev in events:
        meta = dict(ev.metadata or {})
        intent_id = meta.pop("intent_id", ev.id)
        results.append(
            {
                "id": intent_id,
                "agent": ev.agent,
                "action": ev.action,
                "target": ev.target,
                "reason": ev.reason,
                "tier": ev.policy_tier,
                "tenant": ev.tenant,
                "timestamp": ev.timestamp,
                "metadata": meta,
            }
        )
    return results
