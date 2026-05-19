"""
SOS Metrics.

Lightweight Prometheus-compatible metrics primitives (no external deps).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_METRIC_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(label_names: Sequence[str], label_values: Sequence[str]) -> str:
    if not label_names:
        return ""
    parts = [
        f'{name}="{_escape_label_value(value)}"'
        for name, value in zip(label_names, label_values, strict=True)
    ]
    return "{" + ",".join(parts) + "}"


def _validate_metric_name(name: str) -> None:
    if not _METRIC_NAME_RE.match(name):
        raise ValueError(f"Invalid metric name: {name!r}")


def _validate_label_names(label_names: Sequence[str]) -> None:
    for label in label_names:
        if not _LABEL_NAME_RE.match(label):
            raise ValueError(f"Invalid label name: {label!r}")


class MetricsRegistry:
    def __init__(self) -> None:
        self._metrics: Dict[str, Metric] = {}

    def register(self, metric: Metric) -> None:
        if metric.name in self._metrics:
            raise ValueError(f"Metric already registered: {metric.name}")
        self._metrics[metric.name] = metric

    def counter(self, name: str, description: str, label_names: Sequence[str] = ()) -> "Counter":
        metric = Counter(name=name, description=description, label_names=tuple(label_names))
        self.register(metric)
        return metric

    def gauge(self, name: str, description: str, label_names: Sequence[str] = ()) -> "Gauge":
        metric = Gauge(name=name, description=description, label_names=tuple(label_names))
        self.register(metric)
        return metric

    def histogram(
        self,
        name: str,
        description: str,
        label_names: Sequence[str] = (),
        buckets: Optional[Sequence[float]] = None,
    ) -> "Histogram":
        metric = Histogram(
            name=name,
            description=description,
            label_names=tuple(label_names),
            buckets=buckets,
        )
        self.register(metric)
        return metric

    def render_prometheus(self) -> str:
        lines: List[str] = []
        for metric in sorted(self._metrics.values(), key=lambda m: m.name):
            lines.extend(metric.render_prometheus())
        return "\n".join(lines) + ("\n" if lines else "")


class Metric:
    name: str
    description: str
    label_names: Tuple[str, ...]

    def render_prometheus(self) -> List[str]:
        raise NotImplementedError


class Counter(Metric):
    def __init__(self, name: str, description: str, label_names: Tuple[str, ...] = ()) -> None:
        _validate_metric_name(name)
        _validate_label_names(label_names)
        self.name = name
        self.description = description
        self.label_names = label_names
        self._values: Dict[Tuple[str, ...], float] = {}

    def labels(self, **labels: str) -> "CounterChild":
        if set(labels.keys()) != set(self.label_names):
            raise ValueError(f"Labels must match {self.label_names!r}")
        label_values = tuple(labels[name] for name in self.label_names)
        return CounterChild(self, label_values)

    def inc(self, amount: float = 1.0) -> None:
        if self.label_names:
            raise ValueError("This counter requires labels; use .labels(...).inc()")
        self._inc((), amount)

    def _inc(self, label_values: Tuple[str, ...], amount: float) -> None:
        if amount < 0:
            raise ValueError("Counter cannot be decreased")
        self._values[label_values] = self._values.get(label_values, 0.0) + amount

    def render_prometheus(self) -> List[str]:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} counter",
        ]
        if not self._values:
            return lines
        for label_values, value in sorted(self._values.items()):
            labels = _format_labels(self.label_names, label_values)
            lines.append(f"{self.name}{labels} {value}")
        return lines


@dataclass(frozen=True)
class CounterChild:
    _parent: Counter
    _label_values: Tuple[str, ...]

    def inc(self, amount: float = 1.0) -> None:
        self._parent._inc(self._label_values, amount)


class Gauge(Metric):
    def __init__(self, name: str, description: str, label_names: Tuple[str, ...] = ()) -> None:
        _validate_metric_name(name)
        _validate_label_names(label_names)
        self.name = name
        self.description = description
        self.label_names = label_names
        self._values: Dict[Tuple[str, ...], float] = {}

    def labels(self, **labels: str) -> "GaugeChild":
        if set(labels.keys()) != set(self.label_names):
            raise ValueError(f"Labels must match {self.label_names!r}")
        label_values = tuple(labels[name] for name in self.label_names)
        return GaugeChild(self, label_values)

    def set(self, value: float) -> None:
        if self.label_names:
            raise ValueError("This gauge requires labels; use .labels(...).set()")
        self._set((), value)

    def inc(self, amount: float = 1.0) -> None:
        if self.label_names:
            raise ValueError("This gauge requires labels; use .labels(...).inc()")
        self._set((), self._values.get((), 0.0) + amount)

    def dec(self, amount: float = 1.0) -> None:
        self.inc(-amount)

    def _set(self, label_values: Tuple[str, ...], value: float) -> None:
        self._values[label_values] = value

    def render_prometheus(self) -> List[str]:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} gauge",
        ]
        if not self._values:
            return lines
        for label_values, value in sorted(self._values.items()):
            labels = _format_labels(self.label_names, label_values)
            lines.append(f"{self.name}{labels} {value}")
        return lines


@dataclass(frozen=True)
class GaugeChild:
    _parent: Gauge
    _label_values: Tuple[str, ...]

    def set(self, value: float) -> None:
        self._parent._set(self._label_values, value)

    def inc(self, amount: float = 1.0) -> None:
        current = self._parent._values.get(self._label_values, 0.0)
        self._parent._set(self._label_values, current + amount)

    def dec(self, amount: float = 1.0) -> None:
        self.inc(-amount)


class Histogram(Metric):
    def __init__(
        self,
        name: str,
        description: str,
        label_names: Tuple[str, ...] = (),
        buckets: Optional[Sequence[float]] = None,
    ) -> None:
        _validate_metric_name(name)
        _validate_label_names(label_names)
        self.name = name
        self.description = description
        self.label_names = label_names
        self.buckets = tuple(sorted(buckets if buckets is not None else (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)))
        if not self.buckets:
            raise ValueError("Histogram buckets cannot be empty")

        self._bucket_counts: Dict[Tuple[str, ...], List[int]] = {}
        self._sums: Dict[Tuple[str, ...], float] = {}
        self._counts: Dict[Tuple[str, ...], int] = {}

    def labels(self, **labels: str) -> "HistogramChild":
        if set(labels.keys()) != set(self.label_names):
            raise ValueError(f"Labels must match {self.label_names!r}")
        label_values = tuple(labels[name] for name in self.label_names)
        return HistogramChild(self, label_values)

    def observe(self, value: float) -> None:
        if self.label_names:
            raise ValueError("This histogram requires labels; use .labels(...).observe()")
        self._observe((), value)

    def time(self) -> "HistogramTimer":
        if self.label_names:
            raise ValueError("This histogram requires labels; use .labels(...).time()")
        return HistogramTimer(self, ())

    def _observe(self, label_values: Tuple[str, ...], value: float) -> None:
        bucket_counts = self._bucket_counts.setdefault(label_values, [0 for _ in range(len(self.buckets) + 1)])
        index = None
        for i, boundary in enumerate(self.buckets):
            if value <= boundary:
                index = i
                break
        if index is None:
            index = len(self.buckets)  # +Inf bucket
        bucket_counts[index] += 1

        self._sums[label_values] = self._sums.get(label_values, 0.0) + value
        self._counts[label_values] = self._counts.get(label_values, 0) + 1

    def render_prometheus(self) -> List[str]:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} histogram",
        ]
        if not self._counts:
            return lines

        for label_values in sorted(self._counts.keys()):
            counts = self._bucket_counts[label_values]
            cumulative = 0

            for i, boundary in enumerate(self.buckets):
                cumulative += counts[i]
                labels = _format_labels(
                    (*self.label_names, "le"),
                    (*label_values, str(boundary)),
                )
                lines.append(f"{self.name}_bucket{labels} {cumulative}")

            cumulative += counts[len(self.buckets)]
            labels = _format_labels(
                (*self.label_names, "le"),
                (*label_values, "+Inf"),
            )
            lines.append(f"{self.name}_bucket{labels} {cumulative}")

            labels = _format_labels(self.label_names, label_values)
            lines.append(f"{self.name}_sum{labels} {self._sums[label_values]}")
            lines.append(f"{self.name}_count{labels} {self._counts[label_values]}")

        return lines


@dataclass(frozen=True)
class HistogramChild:
    _parent: Histogram
    _label_values: Tuple[str, ...]

    def observe(self, value: float) -> None:
        self._parent._observe(self._label_values, value)

    def time(self) -> "HistogramTimer":
        return HistogramTimer(self._parent, self._label_values)


@dataclass(frozen=True)
class HistogramTimer:
    _histogram: Histogram
    _label_values: Tuple[str, ...]
    _start: float = 0.0

    def __enter__(self) -> "HistogramTimer":
        object.__setattr__(self, "_start", time.perf_counter())
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        duration = time.perf_counter() - self._start
        self._histogram._observe(self._label_values, duration)


REGISTRY = MetricsRegistry()


def render_prometheus(registry: MetricsRegistry = REGISTRY) -> str:
    return registry.render_prometheus()


# ============================================================================
# SOS Metrics - Circuit Breaker
# ============================================================================

CIRCUIT_BREAKER_STATE = REGISTRY.gauge(
    "sos_circuit_breaker_state",
    "Current circuit breaker state (0=closed, 1=half-open, 2=open)",
    label_names=["service"],
)

CIRCUIT_BREAKER_FAILURES = REGISTRY.counter(
    "sos_circuit_breaker_failures_total",
    "Total number of failures recorded by circuit breaker",
    label_names=["service"],
)

CIRCUIT_BREAKER_TRIPS = REGISTRY.counter(
    "sos_circuit_breaker_trips_total",
    "Total number of times circuit breaker tripped to open",
    label_names=["service"],
)

CIRCUIT_BREAKER_SUCCESSES = REGISTRY.counter(
    "sos_circuit_breaker_successes_total",
    "Total successful calls through circuit breaker",
    label_names=["service"],
)


# ============================================================================
# SOS Metrics - Rate Limiter
# ============================================================================

RATE_LIMITER_TOKENS = REGISTRY.gauge(
    "sos_rate_limiter_tokens",
    "Current number of available tokens",
    label_names=["limiter"],
)

RATE_LIMITER_REQUESTS = REGISTRY.counter(
    "sos_rate_limiter_requests_total",
    "Total requests to rate limiter",
    label_names=["limiter", "result"],
)

RATE_LIMITER_WAIT = REGISTRY.histogram(
    "sos_rate_limiter_wait_seconds",
    "Time spent waiting for rate limiter tokens",
    label_names=["limiter"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)


# ============================================================================
# SOS Metrics - Dreams
# ============================================================================

DREAMS_TOTAL = REGISTRY.counter(
    "sos_dreams_total",
    "Total number of dreams synthesized",
    label_names=["agent", "trigger"],
)

DREAM_DURATION = REGISTRY.histogram(
    "sos_dream_duration_seconds",
    "Time taken to synthesize dreams",
    label_names=["agent"],
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
)

DREAM_RELEVANCE = REGISTRY.histogram(
    "sos_dream_relevance",
    "Relevance scores of synthesized dreams",
    label_names=["agent"],
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)

DREAM_MEMORIES_USED = REGISTRY.histogram(
    "sos_dream_memories_used",
    "Number of memories used in dream synthesis",
    label_names=["agent"],
    buckets=(1, 2, 5, 10, 20, 50, 100),
)


# ============================================================================
# SOS Metrics - Autonomy
# ============================================================================

AUTONOMY_PULSES = REGISTRY.counter(
    "sos_autonomy_pulses_total",
    "Total number of autonomy pulses executed",
    label_names=["agent"],
)

AUTONOMY_DREAMS_TRIGGERED = REGISTRY.counter(
    "sos_autonomy_dreams_triggered_total",
    "Total dreams triggered by autonomy",
    label_names=["agent"],
)

AUTONOMY_STATE = REGISTRY.gauge(
    "sos_autonomy_state",
    "Current autonomy state (0=idle, 1=pulsing, 2=dreaming)",
    label_names=["agent"],
)

AUTONOMY_LAST_PULSE = REGISTRY.gauge(
    "sos_autonomy_last_pulse_timestamp",
    "Timestamp of last pulse",
    label_names=["agent"],
)


# ============================================================================
# SOS Metrics - Model Routing
# ============================================================================

MODEL_REQUESTS = REGISTRY.counter(
    "sos_model_requests_total",
    "Total requests to model providers",
    label_names=["model", "status"],
)

MODEL_LATENCY = REGISTRY.histogram(
    "sos_model_latency_seconds",
    "Model request latency",
    label_names=["model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

MODEL_TOKENS = REGISTRY.counter(
    "sos_model_tokens_total",
    "Total tokens used by model",
    label_names=["model", "type"],
)

FAILOVER_TOTAL = REGISTRY.counter(
    "sos_failover_total",
    "Total failovers between models",
    label_names=["from_model", "to_model"],
)


# ============================================================================
# Helper Functions
# ============================================================================

def record_circuit_breaker_trip(service: str) -> None:
    """Record a circuit breaker trip event."""
    CIRCUIT_BREAKER_TRIPS.labels(service=service).inc()
    CIRCUIT_BREAKER_STATE.labels(service=service).set(2)  # open


def record_circuit_breaker_failure(service: str) -> None:
    """Record a circuit breaker failure."""
    CIRCUIT_BREAKER_FAILURES.labels(service=service).inc()


def record_circuit_breaker_success(service: str) -> None:
    """Record a successful call through circuit breaker."""
    CIRCUIT_BREAKER_SUCCESSES.labels(service=service).inc()


def set_circuit_breaker_state(service: str, state: str) -> None:
    """Set circuit breaker state (closed, half_open, open)."""
    state_map = {"closed": 0, "half_open": 1, "open": 2}
    CIRCUIT_BREAKER_STATE.labels(service=service).set(state_map.get(state, 0))


def record_rate_limit_request(limiter: str, allowed: bool) -> None:
    """Record a rate limiter request."""
    result = "allowed" if allowed else "rejected"
    RATE_LIMITER_REQUESTS.labels(limiter=limiter, result=result).inc()


def set_rate_limiter_tokens(limiter: str, tokens: float) -> None:
    """Set current token count for rate limiter."""
    RATE_LIMITER_TOKENS.labels(limiter=limiter).set(tokens)


def record_dream(
    agent: str = "river",
    trigger: str = "scheduled",
    duration: float = 0.0,
    relevance: float = 0.0,
    memories_used: int = 0,
) -> None:
    """Record a dream synthesis event."""
    DREAMS_TOTAL.labels(agent=agent, trigger=trigger).inc()
    if duration > 0:
        DREAM_DURATION.labels(agent=agent).observe(duration)
    if relevance > 0:
        DREAM_RELEVANCE.labels(agent=agent).observe(relevance)
    if memories_used > 0:
        DREAM_MEMORIES_USED.labels(agent=agent).observe(memories_used)


def record_pulse(agent: str = "river") -> None:
    """Record an autonomy pulse event."""
    AUTONOMY_PULSES.labels(agent=agent).inc()
    AUTONOMY_LAST_PULSE.labels(agent=agent).set(time.time())


def record_autonomy_dream_triggered(agent: str = "river") -> None:
    """Record a dream triggered by autonomy."""
    AUTONOMY_DREAMS_TRIGGERED.labels(agent=agent).inc()


def set_autonomy_state(agent: str, state: str) -> None:
    """Set autonomy state (idle, pulsing, dreaming)."""
    state_map = {"idle": 0, "pulsing": 1, "dreaming": 2}
    AUTONOMY_STATE.labels(agent=agent).set(state_map.get(state, 0))


def record_model_request(
    model: str,
    success: bool,
    latency: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """Record a model request."""
    status = "success" if success else "failure"
    MODEL_REQUESTS.labels(model=model, status=status).inc()
    MODEL_LATENCY.labels(model=model).observe(latency)
    if input_tokens > 0:
        MODEL_TOKENS.labels(model=model, type="input").inc(input_tokens)
    if output_tokens > 0:
        MODEL_TOKENS.labels(model=model, type="output").inc(output_tokens)


def record_failover(from_model: str, to_model: str) -> None:
    """Record a model failover event."""
    FAILOVER_TOTAL.labels(from_model=from_model, to_model=to_model).inc()

