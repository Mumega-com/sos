"""Canonical arbitration decision shape for the kernel arbitration execution path.

``ArbitrationDecision`` is the single return type of
``sos.kernel.arbitration.arbitrate()``.  It is frozen at v0.5.2 for
durability: callers bind to this shape; the kernel may emit richer data in
future releases by *adding* optional fields, never by renaming, removing, or
narrowing existing ones.  New conflict-resolution strategies ship as new
``strategy`` string values — never as new fields on the model.

Enforcement: ``tests/contracts/test_arbitration_schema_stable.py`` locks the
v0.5.2 baseline and will fail loudly on any breaking change.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LoserRecord(BaseModel):
    """One losing proposal in an arbitration decision.

    Frozen at v0.5.2. Additive-only."""

    model_config = ConfigDict(frozen=True)

    agent: str
    proposal_id: str
    reason: str
    priority: int | None = None


class ArbitrationDecision(BaseModel):
    """Result of resolving competing proposals on one resource.

    Frozen at v0.5.2. New strategies ship as new ``strategy`` string
    values — never as new fields. See
    tests/contracts/test_arbitration_schema_stable.py.
    """

    model_config = ConfigDict(frozen=True)

    resource: str
    tenant: str
    strategy: str                                # "priority+coherence+recency" baseline
    window_ms: int
    winner_agent: str | None = None              # None when no proposals in window
    winner_proposal_id: str | None = None
    winner_reason: str = ""
    losers: list[LoserRecord] = Field(default_factory=list)
    proposal_count: int = 0
    audit_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["LoserRecord", "ArbitrationDecision"]
