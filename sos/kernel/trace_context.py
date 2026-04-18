"""SOS kernel — trace-id context propagation.

Holds the current W3C trace-id inside a ``contextvars.ContextVar`` so that
bus consumers, audit writers, and envelope emitters can correlate work
without passing ``trace_id`` through every function signature.

Design principles:

- **Opt-in**: reading functions (``get_current_trace_id``) return ``None``
  if no trace is active. Audit/envelope code must honour an explicit
  ``trace_id`` kwarg first and only fall back to the contextvar.
- **Async-safe**: ``ContextVar`` survives asyncio task hops, so spawning
  child tasks within a handler keeps the trace context intact.
- **Cheap**: set on message ingress, reset on exit. No Redis hops, no
  thread-local weirdness.

Usage from a bus consumer::

    from sos.kernel.trace_context import use_trace_id
    from sos.contracts.messages import BusMessage

    async def _tick(self):
        for _eid, fields in entries:
            trace_id = fields.get("trace_id") or BusMessage.new_trace_id()
            with use_trace_id(trace_id):
                await self._handle(fields)

Downstream ``new_event(...)`` and envelope emissions will pick up that
trace_id automatically.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

_current_trace_id: ContextVar[Optional[str]] = ContextVar(
    "sos_current_trace_id", default=None
)


def get_current_trace_id() -> Optional[str]:
    """Return the trace-id for the current async task, or ``None``."""
    return _current_trace_id.get()


@contextmanager
def use_trace_id(trace_id: Optional[str]) -> Iterator[None]:
    """Set the current trace-id for the duration of the ``with`` block.

    Passing ``None`` is a no-op — the existing context is preserved.
    On exit the previous value is restored even if the block raises.
    """
    if trace_id is None:
        yield
        return

    token = _current_trace_id.set(trace_id)
    try:
        yield
    finally:
        _current_trace_id.reset(token)


__all__ = ["get_current_trace_id", "use_trace_id"]
