"""
SOS Distributed Tracing.

Trace context propagation for cross-service requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar
from typing import Optional
import uuid

# Context variables for trace propagation
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
span_id_var: ContextVar[str] = ContextVar("span_id", default="")
parent_span_id_var: ContextVar[str] = ContextVar("parent_span_id", default="")

# Header names for HTTP propagation
TRACE_ID_HEADER = "X-SOS-Trace-ID"
SPAN_ID_HEADER = "X-SOS-Span-ID"
PARENT_SPAN_HEADER = "X-SOS-Parent-Span-ID"


@dataclass
class TraceContext:
    """
    Distributed trace context.

    Contains trace and span identifiers for correlating
    requests across services.
    """
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None

    @classmethod
    def new(cls) -> TraceContext:
        """Create a new trace context (start of trace)."""
        return cls(
            trace_id=str(uuid.uuid4()),
            span_id=str(uuid.uuid4())[:8],
        )

    @classmethod
    def child(cls, parent: TraceContext) -> TraceContext:
        """Create a child span context."""
        return cls(
            trace_id=parent.trace_id,
            span_id=str(uuid.uuid4())[:8],
            parent_span_id=parent.span_id,
        )

    @classmethod
    def from_headers(cls, headers: dict[str, str]) -> TraceContext:
        """
        Extract trace context from HTTP headers.

        If no trace ID present, creates a new trace.
        """
        trace_id = headers.get(TRACE_ID_HEADER) or str(uuid.uuid4())
        parent_span = headers.get(SPAN_ID_HEADER)
        span_id = str(uuid.uuid4())[:8]

        return cls(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span,
        )

    def to_headers(self) -> dict[str, str]:
        """
        Inject trace context into HTTP headers for outgoing requests.
        """
        headers = {
            TRACE_ID_HEADER: self.trace_id,
            SPAN_ID_HEADER: self.span_id,
        }
        if self.parent_span_id:
            headers[PARENT_SPAN_HEADER] = self.parent_span_id
        return headers

    def activate(self) -> None:
        """Set this context as the current context."""
        trace_id_var.set(self.trace_id)
        span_id_var.set(self.span_id)
        if self.parent_span_id:
            parent_span_id_var.set(self.parent_span_id)

    @classmethod
    def current(cls) -> Optional[TraceContext]:
        """Get current trace context if any."""
        trace_id = trace_id_var.get()
        if not trace_id:
            return None

        return cls(
            trace_id=trace_id,
            span_id=span_id_var.get() or str(uuid.uuid4())[:8],
            parent_span_id=parent_span_id_var.get() or None,
        )


class TraceSpan:
    """
    Context manager for creating trace spans.

    Example:
        with TraceSpan("process_request") as span:
            # ... do work ...
            span.add_tag("user_id", user_id)
    """

    def __init__(self, name: str, parent: Optional[TraceContext] = None):
        self.name = name
        self.parent = parent or TraceContext.current()
        self.context: Optional[TraceContext] = None
        self.tags: dict[str, str] = {}
        self._previous_context: Optional[TraceContext] = None

    def __enter__(self) -> TraceSpan:
        # Save current context
        self._previous_context = TraceContext.current()

        # Create new span context
        if self.parent:
            self.context = TraceContext.child(self.parent)
        else:
            self.context = TraceContext.new()

        # Activate new context
        self.context.activate()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # Restore previous context
        if self._previous_context:
            self._previous_context.activate()
        else:
            trace_id_var.set("")
            span_id_var.set("")
            parent_span_id_var.set("")

    def add_tag(self, key: str, value: str) -> None:
        """Add a tag to the span."""
        self.tags[key] = value

    @property
    def trace_id(self) -> Optional[str]:
        """Get trace ID."""
        return self.context.trace_id if self.context else None

    @property
    def span_id(self) -> Optional[str]:
        """Get span ID."""
        return self.context.span_id if self.context else None


def extract_trace_context(headers: dict[str, str]) -> TraceContext:
    """
    Extract trace context from incoming request headers.

    Creates new trace if not present.
    """
    return TraceContext.from_headers(headers)


def inject_trace_context(headers: dict[str, str]) -> dict[str, str]:
    """
    Inject current trace context into outgoing request headers.
    """
    ctx = TraceContext.current()
    if ctx:
        headers.update(ctx.to_headers())
    return headers
