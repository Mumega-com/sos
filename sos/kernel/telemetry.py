"""SOS kernel — OpenTelemetry bootstrapping and contextvar bridge.

Wires every FastAPI service into an OTEL pipeline sharing one conceptual
trace across HTTP hops, bus envelopes, and audit writes.

The one non-obvious bit is the bridge from ``sos.kernel.trace_context``
to OTEL. OTEL's auto-instrumentation starts a fresh trace per inbound
request; our bus consumers want to *continue* the trace that already
arrived on the envelope. ``adopt_current_trace_id_as_otel_parent()``
materialises a zero-parent ``SpanContext`` with the contextvar's
trace_id so that the next span auto-instrumentation creates inherits
it. The 32-hex-char format we use on envelopes is exactly OTEL's
128-bit trace_id in hex, so no conversion logic is needed.

Call ``init_tracing(service_name)`` once from each service's startup
hook. It is idempotent — calling twice with the same name is a no-op.
"""
from __future__ import annotations

import contextlib
import logging
import os
import secrets
from typing import Iterator, Optional

logger = logging.getLogger("sos.kernel.telemetry")

_initialised: set[str] = set()
_instrumented: bool = False


def init_tracing(
    service_name: str,
    *,
    otlp_endpoint: Optional[str] = None,
    console_fallback: Optional[bool] = None,
) -> None:
    """Configure OTEL tracing for a FastAPI service.

    ``otlp_endpoint`` defaults to ``OTEL_EXPORTER_OTLP_ENDPOINT`` from
    the environment. ``console_fallback`` defaults to whatever
    ``SOS_OTEL_CONSOLE`` (``"1"/"true"``) is set to — off by default so
    test runs stay quiet. When neither an endpoint nor the console flag
    is active, init becomes a full no-op (OTEL's default no-op provider
    stays in place).
    """
    if service_name in _initialised:
        return
    if console_fallback is None:
        console_fallback = os.environ.get("SOS_OTEL_CONSOLE", "").lower() in {"1", "true", "yes"}

    endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint and not console_fallback:
        # No destination configured — stay a full no-op. Skipping
        # instrumentation matters: httpx/redis hooks add overhead and
        # surface in stacktraces even when no processor is attached.
        logger.debug("OTEL tracing disabled (no endpoint, console off) for %s", service_name)
        _initialised.add(service_name)
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
    except ImportError as exc:
        logger.warning("OTEL not installed; tracing disabled for %s (%s)", service_name, exc)
        return

    resource = Resource.create({"service.name": f"sos-{service_name}"})
    provider = TracerProvider(resource=resource)

    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
            logger.info("OTEL tracing → %s (service=%s)", endpoint, service_name)
        except Exception as exc:
            logger.warning("OTLP exporter failed, falling back to console: %s", exc)
            if console_fallback:
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif console_fallback:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.info("OTEL tracing → console (service=%s)", service_name)

    trace.set_tracer_provider(provider)
    _install_instrumentations()
    _initialised.add(service_name)


def _install_instrumentations() -> None:
    """Install httpx + redis auto-instrumentation once per process.

    FastAPI instrumentation is per-app, so it is handled by
    ``instrument_fastapi(app)`` below. httpx and redis are global and
    only need one install.
    """
    global _instrumented
    if _instrumented:
        return
    _instrumented = True
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception as exc:
        logger.debug("httpx instrumentation skipped: %s", exc)

    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()
    except Exception as exc:
        logger.debug("redis instrumentation skipped: %s", exc)


def instrument_fastapi(app) -> None:
    """Attach FastAPI auto-instrumentation to an app.

    Call after ``init_tracing(service_name)``. Safe to call on an app
    whose tracer provider is the no-op default — spans are simply
    dropped.
    """
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    except Exception as exc:
        logger.debug("fastapi instrumentation skipped: %s", exc)


def adopt_current_trace_id_as_otel_parent():
    """Return an OTEL Context pre-populated with our contextvar trace_id.

    Use as::

        from sos.kernel.telemetry import adopt_current_trace_id_as_otel_parent
        with trace.use_span(..., context=adopt_current_trace_id_as_otel_parent()):
            ...

    If no sos trace_id is active, returns OTEL's current context
    unchanged so auto-instrumentation keeps its default behaviour.
    """
    from sos.kernel.trace_context import get_current_trace_id

    try:
        from opentelemetry import context, trace
        from opentelemetry.trace import SpanContext, TraceFlags
    except ImportError:
        return None

    sos_trace = get_current_trace_id()
    if sos_trace is None:
        return context.get_current()

    # OTEL rejects all-zero span_id, so mint a real 64-bit one. We cannot
    # recover the upstream span_id from a 32-hex trace-id alone — the parent
    # span will appear synthetic in the UI, but the trace_id links the tree.
    parent_ctx = SpanContext(
        trace_id=int(sos_trace, 16),
        span_id=secrets.randbits(64),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    span = trace.NonRecordingSpan(parent_ctx)
    return trace.set_span_in_context(span, context.get_current())


@contextlib.contextmanager
def span_under_current_trace(
    name: str,
    *,
    tracer_name: str = "sos",
    attributes: Optional[dict] = None,
) -> Iterator[None]:
    """Open a span rooted at the sos contextvar trace_id.

    If OTEL is unavailable, this is a silent no-op — callers stay tracing-
    agnostic. Use this inside bus consumers to make every handler appear
    as one span on the inbound trace::

        with span_under_current_trace("bus.handle.task.created", attributes={...}):
            await handle(fields)
    """
    try:
        from opentelemetry import context as _otel_context
        from opentelemetry import trace as _otel_trace
    except ImportError:
        yield
        return

    parent = adopt_current_trace_id_as_otel_parent()
    if parent is None:
        yield
        return

    tracer = _otel_trace.get_tracer(tracer_name)
    token = _otel_context.attach(parent)
    try:
        with tracer.start_as_current_span(name, attributes=attributes or {}):
            yield
    finally:
        _otel_context.detach(token)


__all__ = [
    "init_tracing",
    "instrument_fastapi",
    "adopt_current_trace_id_as_otel_parent",
    "span_under_current_trace",
]
