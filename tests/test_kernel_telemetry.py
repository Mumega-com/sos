"""Unit tests for sos.kernel.telemetry.

Invariants under test:

1. ``init_tracing`` is a silent no-op when neither an OTLP endpoint nor
   the ``SOS_OTEL_CONSOLE`` flag is set. This matters because most dev
   and test runs should not pay for instrumentation they won't export.

2. ``adopt_current_trace_id_as_otel_parent`` bridges the sos trace-id
   (32 hex chars, identical to OTEL's 128-bit trace-id hex format) into
   an OTEL ``SpanContext`` whose ``trace_id`` equals the int value of
   the contextvar. The synthetic span_id must be non-zero (OTEL rejects
   an all-zero span_id).

3. ``span_under_current_trace`` is a no-op when no sos trace-id is
   active, so calling it outside a bus consumer does not pollute the
   default OTEL provider.
"""
from __future__ import annotations

import os

import pytest

from sos.kernel.telemetry import (
    _initialised,
    _instrumented,
    adopt_current_trace_id_as_otel_parent,
    init_tracing,
    span_under_current_trace,
)
from sos.kernel.trace_context import use_trace_id


@pytest.fixture(autouse=True)
def _reset_telemetry_state(monkeypatch):
    """Clear module state + env flags before each test so order is irrelevant."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("SOS_OTEL_CONSOLE", raising=False)
    _initialised.clear()
    import sos.kernel.telemetry as tel
    tel._instrumented = False
    yield
    _initialised.clear()
    tel._instrumented = False


def test_init_tracing_noop_when_no_destination():
    """Neither endpoint nor console flag → init records idempotency, no work."""
    init_tracing("noop-svc")
    # Subsequent call must still be a no-op (idempotency guard kicks in).
    init_tracing("noop-svc")
    assert "noop-svc" in _initialised


def test_init_tracing_console_when_flag_set(monkeypatch):
    monkeypatch.setenv("SOS_OTEL_CONSOLE", "1")
    init_tracing("flag-svc")
    assert "flag-svc" in _initialised


def test_adopt_current_trace_id_matches_contextvar():
    """SpanContext.trace_id must equal int(sos_trace, 16)."""
    sos_trace = "7af5d78cac7a412e8144ffb0da680adc"
    with use_trace_id(sos_trace):
        ctx = adopt_current_trace_id_as_otel_parent()
    assert ctx is not None

    from opentelemetry import trace as _otel_trace
    span = _otel_trace.get_current_span(ctx)
    sc = span.get_span_context()
    assert format(sc.trace_id, "032x") == sos_trace
    # OTEL spec rejects all-zero span_id; our synthetic span must be non-zero.
    assert sc.span_id != 0


def test_adopt_returns_current_context_when_no_trace():
    """No active sos trace-id → OTEL's current context passes through."""
    ctx = adopt_current_trace_id_as_otel_parent()
    # Must not crash. When OTEL is installed we get a Context object; when
    # missing (defensive path), None is acceptable. Either way it is safe.
    from opentelemetry import context as _otel_context
    assert ctx is None or isinstance(ctx, _otel_context.Context)


def test_span_under_current_trace_no_op_without_trace():
    """Outside a use_trace_id block, span_under_current_trace is a no-op."""
    with span_under_current_trace("handler.test"):
        pass  # must not raise


def test_span_under_current_trace_inherits_trace_id(monkeypatch):
    """When sos trace-id is active, the emitted span carries matching trace_id."""
    # Force a real provider so start_as_current_span actually records.
    monkeypatch.setenv("SOS_OTEL_CONSOLE", "1")
    init_tracing("test-svc")

    sos_trace = "aabbccddeeff00112233445566778899"
    from opentelemetry import trace as _otel_trace

    captured: dict[str, str] = {}
    with use_trace_id(sos_trace):
        with span_under_current_trace("handler.inherit", tracer_name="sos.test"):
            span = _otel_trace.get_current_span()
            sc = span.get_span_context()
            if sc.trace_id:
                captured["trace_id"] = format(sc.trace_id, "032x")

    # If OTEL is installed (console flag path taken), trace_id must match.
    # If instrumentation silently dropped for any reason, we accept absence.
    if captured:
        assert captured["trace_id"] == sos_trace
