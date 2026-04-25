"""
SOS Observability - Logging, metrics, and tracing utilities.

Provides consistent observability across all SOS services:
- Structured JSON logging
- Prometheus metrics
- Distributed tracing
"""

from sos.observability.logging import SOSLogger, get_logger
from sos.observability.metrics import MetricsRegistry, REGISTRY, render_prometheus
from sos.observability.tracing import TraceContext, TraceSpan, trace_id_var, span_id_var
from sos.observability.sprint_telemetry import (
    current_sprint,
    sprint_start,
    sprint_close,
    compute_sprint_stats,
    SprintStats,
)

__all__ = [
    "SOSLogger",
    "get_logger",
    "MetricsRegistry",
    "REGISTRY",
    "render_prometheus",
    "TraceContext",
    "TraceSpan",
    "trace_id_var",
    "span_id_var",
    "current_sprint",
    "sprint_start",
    "sprint_close",
    "compute_sprint_stats",
    "SprintStats",
]
