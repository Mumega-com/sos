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
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("sos.governance")

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

    # Gap 7: Budget enforcement — check before allowing action
    estimated_cost = (metadata or {}).get("estimated_cost", 0.0)
    project = (metadata or {}).get("project", tenant)
    try:
        from sos.services.economy.metabolism import can_spend
        budget_check = can_spend(project, estimated_cost)
        if not budget_check["allowed"]:
            logger.warning(
                f"BUDGET BLOCKED: {agent} → {action} on {target}. "
                f"Project {project} over budget: {budget_check['reason']}"
            )
            return {
                "allowed": False,
                "tier": "budget_exceeded",
                "intent_id": intent_id,
                "reason": budget_check["reason"],
                "budget": budget_check,
            }
        if budget_check.get("warning"):
            logger.info(
                f"BUDGET WARNING: {project} at {budget_check['pct_used']:.0f}% "
                f"(${budget_check['spent']:.4f} / ${budget_check['budget']:.4f})"
            )
    except ImportError:
        pass  # metabolism module optional

    # Always log intent (accountability)
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

    # Log to file (always works, even if Mirror is down)
    intent_dir = _governance_dir() / "intents" / tenant
    intent_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    intent_file = intent_dir / f"{date_str}.jsonl"
    with open(intent_file, "a") as f:
        f.write(json.dumps(intent) + "\n")

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
    """Get intent log for a tenant. For audit trail."""
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    intent_file = _governance_dir() / "intents" / tenant / f"{date}.jsonl"
    if not intent_file.exists():
        return []
    results = []
    for line in intent_file.read_text().strip().split("\n"):
        if line:
            results.append(json.loads(line))
    return results[-limit:]
