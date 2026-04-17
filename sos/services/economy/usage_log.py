"""Usage event log — append-only record of model-call telemetry.

Separate from `work_ledger.py`, which settles squad work units and bounties.
This log captures model-call events (tokens, cost, latency) reported by any
tenant — Python adapters, Cloudflare workers, edge Pages Functions, or any
external client authenticated with a bus token.

Storage: append-only JSONL at `~/.mumega/usage_events.jsonl` by default.
Override with `SOS_USAGE_LOG_PATH` env var.

**Boundary note (SOS vs Mumega):**
This module is SOS (protocol): canonical `UsageEvent` shape, tenant scoping,
append-only log primitive. Currency-agnostic — `cost_micros` can represent
USD, $MIND token units, or any other unit the operator defines.

Mumega's commercial layer reads this log to compute per-tenant invoices,
apply volume tiers, issue Stripe charges, etc. None of that is SOS concern.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _default_log_path() -> Path:
    """`~/.mumega/usage_events.jsonl` — overridable via `SOS_USAGE_LOG_PATH`."""
    override = os.environ.get("SOS_USAGE_LOG_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".mumega" / "usage_events.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class UsageEvent:
    """One model-call telemetry event.

    Canonical on-the-wire shape for the `POST /usage` economy endpoint. Matches
    `sos.adapters.base.UsageInfo` conceptually but adds tenant/endpoint/
    occurred_at so the record is self-describing without context.

    All cost fields in **micros** (1e-6 of a currency unit). Edge tenants
    computing cost from `PricingEntry.estimate_micros()` should pass the
    integer result directly. USD ledger entries with cost_cents can be
    converted: `cost_micros = cost_cents * 10_000`.
    """
    id: str = field(default_factory=lambda: str(uuid4()))
    tenant: str = ""                         # tenant slug — must match the bus-token's project/tenant scope
    provider: str = ""                       # "google" | "anthropic" | "openai" | "vertex" | ...
    model: str = ""                          # provider model id, e.g. "gemini-flash-lite-latest"
    endpoint: str = ""                       # tenant-side endpoint that triggered the call, e.g. "/api/archetype-report"
    input_tokens: int = 0
    output_tokens: int = 0
    image_count: int = 0                     # flat-billed image models
    cost_micros: int = 0                     # integer micros in whatever unit the tenant uses
    cost_currency: str = "USD"               # "USD" | "MIND" | operator-defined
    metadata: dict[str, Any] = field(default_factory=dict)  # tenant-side correlation (request_id, report_id, ...)
    occurred_at: str = field(default_factory=_now_iso)
    received_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


class UsageLog:
    """Append-only JSONL log of `UsageEvent`s.

    Thread-safety: the append path uses `O_APPEND` under the hood (Python's
    default `"a"` mode on POSIX), which guarantees atomic appends for writes
    up to PIPE_BUF (4096 bytes on Linux). Each event's JSON line is well
    below that, so concurrent writers on the same machine do not corrupt the
    file. For multi-machine deployments, front this with a proper queue.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_log_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: UsageEvent) -> UsageEvent:
        """Write one event. Returns the stored event (with server-assigned id / received_at if unset)."""
        if not event.id:
            event.id = str(uuid4())
        if not event.received_at:
            event.received_at = _now_iso()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return event

    def read_all(self, tenant: str | None = None, limit: int | None = None) -> list[UsageEvent]:
        """Read events, optionally filtered by tenant. Newest last."""
        if not self.path.exists():
            return []
        out: list[UsageEvent] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if tenant and data.get("tenant") != tenant:
                    continue
                out.append(UsageEvent(**data))
        if limit is not None and limit > 0:
            return out[-limit:]
        return out

    def count(self, tenant: str | None = None) -> int:
        return len(self.read_all(tenant=tenant))
