"""
Tests for SOS Prometheus metrics.
"""

import pytest
import time

from sos.observability.metrics import (
    MetricsRegistry,
    Counter,
    Gauge,
    Histogram,
    render_prometheus,
    # SOS metrics
    CIRCUIT_BREAKER_STATE,
    CIRCUIT_BREAKER_FAILURES,
    CIRCUIT_BREAKER_TRIPS,
    RATE_LIMITER_TOKENS,
    RATE_LIMITER_REQUESTS,
    DREAMS_TOTAL,
    AUTONOMY_PULSES,
    AUTONOMY_STATE,
    MODEL_REQUESTS,
    FAILOVER_TOTAL,
    # Helper functions
    record_circuit_breaker_trip,
    record_circuit_breaker_failure,
    record_circuit_breaker_success,
    set_circuit_breaker_state,
    record_rate_limit_request,
    set_rate_limiter_tokens,
    record_dream,
    record_pulse,
    record_autonomy_dream_triggered,
    set_autonomy_state,
    record_model_request,
    record_failover,
)


class TestCounter:
    """Tests for Counter metric."""

    def test_counter_increment(self):
        """Counter should increment correctly."""
        registry = MetricsRegistry()
        counter = registry.counter("test_counter", "A test counter")

        counter.inc()
        counter.inc(5)

        output = registry.render_prometheus()
        assert "test_counter 6" in output

    def test_counter_with_labels(self):
        """Counter with labels should work correctly."""
        registry = MetricsRegistry()
        counter = registry.counter("test_labeled", "Labeled counter", ["service"])

        counter.labels(service="api").inc()
        counter.labels(service="api").inc(2)
        counter.labels(service="worker").inc()

        output = registry.render_prometheus()
        assert 'test_labeled{service="api"} 3' in output
        assert 'test_labeled{service="worker"} 1' in output

    def test_counter_negative_increment_raises(self):
        """Counter should not allow negative increments."""
        registry = MetricsRegistry()
        counter = registry.counter("test_neg", "Test")

        with pytest.raises(ValueError, match="cannot be decreased"):
            counter.inc(-1)


class TestGauge:
    """Tests for Gauge metric."""

    def test_gauge_set(self):
        """Gauge should set values correctly."""
        registry = MetricsRegistry()
        gauge = registry.gauge("test_gauge", "A test gauge")

        gauge.set(42)

        output = registry.render_prometheus()
        assert "test_gauge 42" in output

    def test_gauge_inc_dec(self):
        """Gauge should increment and decrement correctly."""
        registry = MetricsRegistry()
        gauge = registry.gauge("test_gauge2", "Another gauge")

        gauge.set(10)
        gauge.inc(5)
        gauge.dec(3)

        output = registry.render_prometheus()
        assert "test_gauge2 12" in output

    def test_gauge_with_labels(self):
        """Gauge with labels should work correctly."""
        registry = MetricsRegistry()
        gauge = registry.gauge("test_labeled_gauge", "Labeled gauge", ["env"])

        gauge.labels(env="prod").set(100)
        gauge.labels(env="staging").set(50)

        output = registry.render_prometheus()
        assert 'test_labeled_gauge{env="prod"} 100' in output
        assert 'test_labeled_gauge{env="staging"} 50' in output


class TestHistogram:
    """Tests for Histogram metric."""

    def test_histogram_observe(self):
        """Histogram should observe values correctly."""
        registry = MetricsRegistry()
        hist = registry.histogram("test_hist", "A histogram", buckets=(0.1, 0.5, 1.0))

        hist.observe(0.05)  # <= 0.1
        hist.observe(0.3)   # <= 0.5
        hist.observe(0.8)   # <= 1.0
        hist.observe(2.0)   # > 1.0 (+Inf)

        output = registry.render_prometheus()
        assert 'test_hist_bucket{le="0.1"} 1' in output
        assert 'test_hist_bucket{le="0.5"} 2' in output
        assert 'test_hist_bucket{le="1.0"} 3' in output
        assert 'test_hist_bucket{le="+Inf"} 4' in output
        assert "test_hist_sum" in output
        assert "test_hist_count 4" in output

    def test_histogram_timer(self):
        """Histogram timer context manager should work."""
        registry = MetricsRegistry()
        hist = registry.histogram("test_timer", "Timer test", buckets=(0.01, 0.1, 1.0))

        with hist.time():
            time.sleep(0.005)

        output = registry.render_prometheus()
        assert "test_timer_count 1" in output

    def test_histogram_with_labels(self):
        """Histogram with labels should work correctly."""
        registry = MetricsRegistry()
        hist = registry.histogram("test_labeled_hist", "Labeled", ["op"], buckets=(1.0,))

        hist.labels(op="read").observe(0.5)
        hist.labels(op="write").observe(1.5)

        output = registry.render_prometheus()
        assert 'test_labeled_hist_bucket{op="read",le="1.0"} 1' in output
        assert 'test_labeled_hist_bucket{op="write",le="+Inf"} 1' in output


class TestCircuitBreakerMetrics:
    """Tests for circuit breaker metrics."""

    def test_record_trip(self):
        """Should record circuit breaker trip."""
        # Reset state
        CIRCUIT_BREAKER_TRIPS._values.clear()
        CIRCUIT_BREAKER_STATE._values.clear()

        record_circuit_breaker_trip("test_gemini")

        output = render_prometheus()
        assert 'sos_circuit_breaker_trips_total{service="test_gemini"} 1' in output
        assert 'sos_circuit_breaker_state{service="test_gemini"} 2' in output

    def test_record_failure(self):
        """Should record circuit breaker failure."""
        CIRCUIT_BREAKER_FAILURES._values.clear()

        record_circuit_breaker_failure("test_claude")
        record_circuit_breaker_failure("test_claude")

        output = render_prometheus()
        assert 'sos_circuit_breaker_failures_total{service="test_claude"} 2' in output

    def test_set_state(self):
        """Should set circuit breaker state."""
        CIRCUIT_BREAKER_STATE._values.clear()

        set_circuit_breaker_state("test_openai", "half_open")

        output = render_prometheus()
        assert 'sos_circuit_breaker_state{service="test_openai"} 1' in output


class TestRateLimiterMetrics:
    """Tests for rate limiter metrics."""

    def test_record_request(self):
        """Should record rate limiter requests."""
        RATE_LIMITER_REQUESTS._values.clear()

        record_rate_limit_request("test_api", allowed=True)
        record_rate_limit_request("test_api", allowed=True)
        record_rate_limit_request("test_api", allowed=False)

        output = render_prometheus()
        assert 'sos_rate_limiter_requests_total{limiter="test_api",result="allowed"} 2' in output
        assert 'sos_rate_limiter_requests_total{limiter="test_api",result="rejected"} 1' in output

    def test_set_tokens(self):
        """Should set rate limiter tokens."""
        RATE_LIMITER_TOKENS._values.clear()

        set_rate_limiter_tokens("test_chat", 5.5)

        output = render_prometheus()
        assert 'sos_rate_limiter_tokens{limiter="test_chat"} 5.5' in output


class TestDreamMetrics:
    """Tests for dream metrics."""

    def test_record_dream(self):
        """Should record dream synthesis."""
        DREAMS_TOTAL._values.clear()

        record_dream(
            agent="test_river",
            trigger="scheduled",
            duration=2.5,
            relevance=0.85,
            memories_used=10
        )

        output = render_prometheus()
        assert 'sos_dreams_total{agent="test_river",trigger="scheduled"} 1' in output


class TestAutonomyMetrics:
    """Tests for autonomy metrics."""

    def test_record_pulse(self):
        """Should record autonomy pulse."""
        AUTONOMY_PULSES._values.clear()

        record_pulse("test_river")
        record_pulse("test_river")

        output = render_prometheus()
        assert 'sos_autonomy_pulses_total{agent="test_river"} 2' in output

    def test_set_autonomy_state(self):
        """Should set autonomy state."""
        AUTONOMY_STATE._values.clear()

        set_autonomy_state("test_river", "dreaming")

        output = render_prometheus()
        assert 'sos_autonomy_state{agent="test_river"} 2' in output


class TestModelMetrics:
    """Tests for model routing metrics."""

    def test_record_model_request(self):
        """Should record model request."""
        MODEL_REQUESTS._values.clear()

        record_model_request(
            model="test_gemini",
            success=True,
            latency=1.5,
            input_tokens=100,
            output_tokens=50
        )

        output = render_prometheus()
        assert 'sos_model_requests_total{model="test_gemini",status="success"} 1' in output

    def test_record_failover(self):
        """Should record model failover."""
        FAILOVER_TOTAL._values.clear()

        record_failover("test_from", "test_to")

        output = render_prometheus()
        assert 'sos_failover_total{from_model="test_from",to_model="test_to"} 1' in output


class TestPrometheusOutput:
    """Tests for Prometheus output format."""

    def test_output_format(self):
        """Output should be valid Prometheus format."""
        registry = MetricsRegistry()
        counter = registry.counter("test_format", "Test counter", ["service"])
        counter.labels(service="test").inc()

        output = registry.render_prometheus()

        assert "# HELP test_format Test counter" in output
        assert "# TYPE test_format counter" in output
        assert 'test_format{service="test"} 1' in output

    def test_label_escaping(self):
        """Labels with special characters should be escaped."""
        registry = MetricsRegistry()
        counter = registry.counter("test_escape", "Test", ["path"])
        counter.labels(path='/api/v1/"test"').inc()

        output = registry.render_prometheus()
        assert r'test_escape{path="/api/v1/\"test\""} 1' in output


class TestMetricValidation:
    """Tests for metric name/label validation."""

    def test_invalid_metric_name_raises(self):
        """Invalid metric name should raise ValueError."""
        registry = MetricsRegistry()
        with pytest.raises(ValueError, match="Invalid metric name"):
            registry.counter("1invalid-name", "Test")

    def test_invalid_label_name_raises(self):
        """Invalid label name should raise ValueError."""
        registry = MetricsRegistry()
        with pytest.raises(ValueError, match="Invalid label name"):
            registry.counter("test_valid", "Test", ["invalid-label"])

    def test_duplicate_registration_raises(self):
        """Duplicate metric registration should raise."""
        registry = MetricsRegistry()
        registry.counter("test_dup", "First")
        with pytest.raises(ValueError, match="already registered"):
            registry.counter("test_dup", "Second")

