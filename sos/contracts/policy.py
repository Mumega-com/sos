"""Canonical policy decision shape for the kernel gate execution path.

``PolicyDecision`` is the single return type of
``sos.kernel.policy.gate.can_execute()``.  It is frozen at v0.5.1 for
durability: callers bind to this shape; the kernel may emit richer data in
future releases by *adding* optional fields, never by renaming, removing, or
narrowing existing ones.

Enforcement: ``tests/contracts/test_policy_schema_stable.py`` locks the
v0.5.1 baseline and will fail loudly on any breaking change.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PolicyDecision(BaseModel):
    """Frozen result of a kernel policy check.

    Fields are additive-only — see tests/contracts/test_policy_schema_stable.py.
    """

    model_config = ConfigDict(frozen=True)

    # Core verdict
    allowed: bool
    reason: str
    tier: str  # governance tier: act_freely | batch_approve | human_gate | dual_approval | denied

    # Target
    action: str
    resource: str

    # Actor (optional — not always resolved at gate time)
    agent: str | None = None
    tenant: str | None = None

    # Pillar accounting
    pillars_passed: list[str] = Field(default_factory=list)
    pillars_failed: list[str] = Field(default_factory=list)

    # Supplementary signals
    capability_ok: bool | None = None
    audit_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["PolicyDecision"]
