"""Canonical audit event shape for the unified kernel audit stream.

Frozen because v0.5.x writers bind to this shape; future modules add new
``AuditEventKind`` values, not new fields.  Writers in ``sos/kernel/audit.py``
handle all event construction — this module is pure contract.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditEventKind(str, Enum):
    """Lifecycle phase of the audited operation."""

    INTENT = "intent"                     # governance.before_action
    POLICY_DECISION = "policy_decision"   # kernel.policy.can_execute (v0.5.1)
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"
    ARBITRATION = "arbitration"           # (v0.5.2)


class AuditDecision(str, Enum):
    """Outcome of a policy evaluation."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    NOT_APPLICABLE = "n/a"               # use for ACTION_COMPLETED / ACTION_FAILED


class AuditEvent(BaseModel):
    """Immutable record of one auditable moment in the kernel execution path."""

    model_config = ConfigDict(frozen=True)

    # Identity
    id: str = Field(description="ULID — lexicographically sortable unique event identifier")
    timestamp: str = Field(description="ISO 8601 UTC instant the event was emitted")

    # Actor
    agent: str = Field(description="Agent slug that initiated the action")
    tenant: str = Field(description="Tenant slug — isolates events in multi-tenant reads")
    trace_id: str | None = Field(default=None, description="Distributed trace ID for cross-service correlation")
    parent_event_id: str | None = Field(default=None, description="ULID of the INTENT event that spawned this event")

    # Kind + target
    kind: AuditEventKind
    action: str = Field(description="Tool or skill name being invoked (e.g. 'web_search', 'send_email')")
    target: str = Field(description="Resource or recipient the action operates on")

    # Decision (NOT_APPLICABLE for ACTION_COMPLETED/FAILED)
    decision: AuditDecision = Field(
        default=AuditDecision.NOT_APPLICABLE,
        description="Policy evaluation outcome; NOT_APPLICABLE when kind is ACTION_COMPLETED or ACTION_FAILED",
    )
    reason: str = Field(default="", description="Human-readable explanation of the decision or failure")
    policy_tier: str | None = Field(default=None, description="Named policy tier that produced the decision (v0.5.1)")

    # Economics
    cost_micros: int = Field(default=0, description="Cost in millionths of cost_currency (e.g. 1_000_000 = $1.00 USD)")
    cost_currency: str = Field(default="USD", description="ISO 4217 currency code or 'MIND' for token units")

    # Payload (bounded; writers truncate/hash large blobs before emitting)
    inputs: dict[str, Any] = Field(default_factory=dict, description="Sanitised inputs passed to the action")
    outputs: dict[str, Any] = Field(default_factory=dict, description="Sanitised outputs returned by the action")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary structured context (tags, versions, etc.)")


__all__ = ["AuditEventKind", "AuditDecision", "AuditEvent"]
