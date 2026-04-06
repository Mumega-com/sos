# SOS Observability Standard

Status: Draft v0.1
Owner: Claude Code
Last Updated: 2026-01-10

## Purpose
Define consistent observability patterns across all SOS services to enable debugging, monitoring, and incident response.

## Three Pillars

### 1. Structured Logging
### 2. Metrics
### 3. Distributed Tracing

---

## 1. Structured Logging

All services MUST emit JSON-formatted logs to stdout/stderr.

### Log Schema

```json
{
  "ts": "2026-01-10T12:00:00.123Z",
  "level": "info",
  "service": "engine",
  "trace_id": "abc123def456",
  "span_id": "span789",
  "agent_id": "river",
  "msg": "Request processed successfully",
  "duration_ms": 45,
  "extra": {}
}
```

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `ts` | ISO 8601 | Timestamp with milliseconds |
| `level` | string | `debug`, `info`, `warn`, `error`, `fatal` |
| `service` | string | Service name (engine, memory, economy, etc.) |
| `msg` | string | Human-readable message |

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `trace_id` | string | Distributed trace ID (propagated across services) |
| `span_id` | string | Current span within trace |
| `agent_id` | string | Agent performing the action |
| `duration_ms` | number | Operation duration |
| `error` | object | Error details if level is error/fatal |
| `extra` | object | Additional context |

### Log Levels

```
debug  - Verbose debugging, disabled in production
info   - Normal operations, request lifecycle
warn   - Recoverable issues, degraded performance
error  - Failed operations, requires attention
fatal  - Service cannot continue, will exit
```

### Python Implementation

```python
import json
import sys
from datetime import datetime, timezone
from typing import Any
from contextvars import ContextVar

# Context for trace propagation
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
span_id_var: ContextVar[str] = ContextVar("span_id", default="")

class SOSLogger:
    def __init__(self, service: str):
        self.service = service

    def _emit(self, level: str, msg: str, **extra: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "service": self.service,
            "msg": msg,
        }
        if trace_id := trace_id_var.get():
            record["trace_id"] = trace_id
        if span_id := span_id_var.get():
            record["span_id"] = span_id
        if extra:
            record["extra"] = extra

        print(json.dumps(record), file=sys.stderr if level in ("error", "fatal") else sys.stdout)

    def debug(self, msg: str, **extra): self._emit("debug", msg, **extra)
    def info(self, msg: str, **extra): self._emit("info", msg, **extra)
    def warn(self, msg: str, **extra): self._emit("warn", msg, **extra)
    def error(self, msg: str, **extra): self._emit("error", msg, **extra)
    def fatal(self, msg: str, **extra): self._emit("fatal", msg, **extra)

# Usage
log = SOSLogger("engine")
log.info("Request received", endpoint="/chat", agent_id="river")
```

---

## 2. Metrics

All services MUST expose a `/metrics` endpoint in Prometheus format.

### Required Metrics

#### Request Metrics
```prometheus
# Total requests by service and status
sos_requests_total{service="engine", status="success"} 1234
sos_requests_total{service="engine", status="error"} 56

# Request duration histogram
sos_request_duration_seconds_bucket{service="engine", le="0.1"} 100
sos_request_duration_seconds_bucket{service="engine", le="0.5"} 150
sos_request_duration_seconds_bucket{service="engine", le="1.0"} 160
sos_request_duration_seconds_sum{service="engine"} 45.67
sos_request_duration_seconds_count{service="engine"} 160
```

#### Service Health
```prometheus
# Service uptime
sos_uptime_seconds{service="engine"} 3600

# Active connections/sessions
sos_active_connections{service="engine"} 5

# Queue depth (if applicable)
sos_queue_depth{service="engine", queue="requests"} 12
```

#### Memory Service Specific
```prometheus
# Embedding operations
sos_embeddings_total{backend="openai"} 500
sos_embedding_duration_seconds_sum{backend="openai"} 12.5

# Vector search operations
sos_vector_searches_total 1000
sos_vector_search_duration_seconds_sum 8.2
```

#### Economy Service Specific
```prometheus
# Transaction counts
sos_transactions_total{type="payout"} 100
sos_transactions_total{type="slash"} 5

# Ledger balance
sos_ledger_balance{currency="MIND"} 50000
```

### Python Implementation

```python
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# Define metrics
REQUEST_COUNT = Counter(
    'sos_requests_total',
    'Total requests',
    ['service', 'status']
)

REQUEST_DURATION = Histogram(
    'sos_request_duration_seconds',
    'Request duration',
    ['service'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

ACTIVE_CONNECTIONS = Gauge(
    'sos_active_connections',
    'Active connections',
    ['service']
)

# Usage in request handler
@REQUEST_DURATION.labels(service="engine").time()
async def handle_request(request):
    try:
        result = await process(request)
        REQUEST_COUNT.labels(service="engine", status="success").inc()
        return result
    except Exception as e:
        REQUEST_COUNT.labels(service="engine", status="error").inc()
        raise

# Metrics endpoint
async def metrics_endpoint(request):
    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )
```

---

## 3. Distributed Tracing

Cross-service requests MUST propagate trace context.

### Trace Header

```
X-SOS-Trace-ID: abc123def456
X-SOS-Span-ID: span789
X-SOS-Parent-Span-ID: parent456
```

### Trace Propagation

```python
import uuid
from contextvars import ContextVar

trace_id_var: ContextVar[str] = ContextVar("trace_id")
span_id_var: ContextVar[str] = ContextVar("span_id")

def extract_trace_context(headers: dict) -> tuple[str, str]:
    """Extract trace context from incoming request headers."""
    trace_id = headers.get("X-SOS-Trace-ID") or str(uuid.uuid4())
    parent_span = headers.get("X-SOS-Span-ID")
    span_id = str(uuid.uuid4())[:8]
    return trace_id, span_id

def inject_trace_context(headers: dict) -> dict:
    """Inject trace context into outgoing request headers."""
    headers["X-SOS-Trace-ID"] = trace_id_var.get()
    headers["X-SOS-Span-ID"] = span_id_var.get()
    return headers

# Middleware example
async def tracing_middleware(request, call_next):
    trace_id, span_id = extract_trace_context(dict(request.headers))
    trace_id_var.set(trace_id)
    span_id_var.set(span_id)

    response = await call_next(request)
    response.headers["X-SOS-Trace-ID"] = trace_id
    return response
```

---

## 4. Health Endpoints

All services MUST expose health endpoints.

### GET /health

Returns service health status.

```json
{
  "status": "ok",
  "version": "0.1.0",
  "service": "engine",
  "uptime_seconds": 3600,
  "checks": {
    "memory_service": "ok",
    "economy_service": "ok",
    "database": "ok"
  }
}
```

### Status Values

| Status | Description |
|--------|-------------|
| `ok` | Fully operational |
| `degraded` | Operational but with issues |
| `unhealthy` | Not operational |

### Python Implementation

```python
from dataclasses import dataclass
from typing import Dict
import time

@dataclass
class HealthCheck:
    status: str  # ok, degraded, unhealthy
    version: str
    service: str
    uptime_seconds: float
    checks: Dict[str, str]

start_time = time.time()

async def health_endpoint(request):
    checks = {
        "memory_service": await check_memory_service(),
        "database": await check_database(),
    }

    # Aggregate status
    if all(v == "ok" for v in checks.values()):
        status = "ok"
    elif any(v == "unhealthy" for v in checks.values()):
        status = "unhealthy"
    else:
        status = "degraded"

    return {
        "status": status,
        "version": "0.1.0",
        "service": "engine",
        "uptime_seconds": time.time() - start_time,
        "checks": checks
    }
```

---

## 5. Alerting Recommendations

### Critical Alerts (Page immediately)
- `sos_requests_total{status="error"}` rate > 10% for 5 minutes
- Any service health status = "unhealthy"
- `sos_queue_depth` > 1000 for 5 minutes

### Warning Alerts (Notify during business hours)
- `sos_request_duration_seconds` p99 > 5s for 10 minutes
- Any service health status = "degraded"
- `sos_active_connections` approaching limit

---

## 6. Log Aggregation

### Recommended Stack
- **Collection:** Vector, Fluent Bit, or Promtail
- **Storage:** Loki (logs), Prometheus/VictoriaMetrics (metrics)
- **Visualization:** Grafana

### Docker Compose Example

```yaml
services:
  loki:
    image: grafana/loki:latest
    ports:
      - "3100:3100"

  promtail:
    image: grafana/promtail:latest
    volumes:
      - /var/log:/var/log
      - ./promtail-config.yml:/etc/promtail/config.yml

  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
```

---

## 4. Audit Logs

Security-relevant events are append-only. Tools service writes:
- `SOS_HOME/data/audit/tools.jsonl`
- `SOS_HOME/data/ledger/audit.jsonl`

Each entry includes timestamp, tool name, status, and args (avoid secrets).

---

## Implementation Checklist

- [ ] JSON structured logging to stdout/stderr
- [ ] `/health` endpoint returning status, version, checks
- [ ] `/metrics` endpoint in Prometheus format
- [ ] Trace ID propagation via X-SOS-Trace-ID header
- [ ] Request duration histograms
- [ ] Error rate counters
- [ ] Service-specific metrics documented
