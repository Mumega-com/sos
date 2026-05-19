"""Trace aggregation contract used by GET /sos/traces.

A *trace* is the set of audit events sharing a single ``trace_id``. The
dashboard route groups on that key so operators can see one request's
full footprint (intent → policy decision → action completion) across
services in one place.

Kept intentionally small: the route renders a summary list plus a
single-trace detail view. Richer analysis stays in external OTEL
tooling.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from sos.contracts.audit import AuditEvent


class TraceSummary(BaseModel):
    """One row in the /sos/traces index."""

    model_config = ConfigDict(frozen=True)

    trace_id: str = Field(description="32-char hex trace id shared by all events in this trace")
    first_ts: str = Field(description="ISO-8601 timestamp of the earliest event seen for this trace")
    last_ts: str = Field(description="ISO-8601 timestamp of the latest event seen for this trace")
    event_count: int = Field(description="Number of audit events carrying this trace_id")
    tenants: list[str] = Field(default_factory=list, description="Distinct tenants touched by the trace")
    agents: list[str] = Field(default_factory=list, description="Distinct agents involved in the trace")
    kinds: dict[str, int] = Field(default_factory=dict, description="AuditEventKind → count")


class TraceIndexResponse(BaseModel):
    """Response body for GET /sos/traces."""

    model_config = ConfigDict(frozen=True)

    traces: list[TraceSummary]


class TraceDetailResponse(BaseModel):
    """Response body for GET /sos/traces/{trace_id}."""

    model_config = ConfigDict(frozen=True)

    trace_id: str
    events: list[AuditEvent]


__all__ = ["TraceSummary", "TraceIndexResponse", "TraceDetailResponse"]
