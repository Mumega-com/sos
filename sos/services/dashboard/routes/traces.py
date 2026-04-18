"""GET /sos/traces — observability view over the unified audit stream.

The disk sink ``~/.sos/audit/{tenant}/{YYYY-MM-DD}.jsonl`` is authoritative
for every kernel-governed action. This route reads those files, groups
events by ``trace_id``, and returns a summary index plus a per-trace
detail endpoint so operators can see one request's full footprint across
services (intent → policy decision → action completion) without leaving
the dashboard.

Design notes:

- Disk-only. Redis is observational and can be down; the audit directory
  is the source of truth and the only thing we need to render traces.
- Read is bounded: last ``days`` days (default 1), and summary is capped
  at ``limit`` traces (default 50, sorted by most-recent ``last_ts``).
- Events without a ``trace_id`` are ignored on the index; they still show
  up in ``read_events`` but carry no correlation key so there is nothing
  to group on.
- ``_audit_dir()`` is re-resolved per request so tests can point
  ``Path.home()`` at a tmp dir.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, Header, HTTPException, Query

from sos.contracts.audit import AuditEvent
from sos.contracts.traces import TraceDetailResponse, TraceIndexResponse, TraceSummary
from sos.kernel.auth import verify_bearer

router = APIRouter(tags=["traces"])


def _audit_root() -> Path:
    # Match sos.kernel.audit._audit_dir without reaching into a private
    # helper — if both paths ever diverge, that's a bug on the writer side.
    return Path.home() / ".sos" / "audit"


def _recent_jsonl_files(root: Path, days: int) -> Iterable[Path]:
    """Yield every audit JSONL file covering the last ``days`` days."""
    if not root.exists():
        return
    today = datetime.now(timezone.utc).date()
    wanted = {(today - timedelta(days=i)).isoformat() for i in range(days)}
    for tenant_dir in root.iterdir():
        if not tenant_dir.is_dir():
            continue
        for jsonl in tenant_dir.glob("*.jsonl"):
            if jsonl.stem in wanted:
                yield jsonl


def _iter_events(days: int) -> Iterable[AuditEvent]:
    for path in _recent_jsonl_files(_audit_root(), days):
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield AuditEvent.model_validate_json(line)
            except Exception:
                # Replay must tolerate corrupted lines — see kernel.audit.read_events.
                continue


@router.get("/sos/traces", response_model=TraceIndexResponse)
async def list_traces(
    authorization: str | None = Header(None),
    days: int = Query(1, ge=1, le=7, description="How many days back to scan"),
    limit: int = Query(50, ge=1, le=500, description="Max traces in the response"),
) -> TraceIndexResponse:
    """Return a summary row per distinct ``trace_id`` in the audit log."""
    if verify_bearer(authorization) is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    by_trace: dict[str, dict] = {}
    for ev in _iter_events(days):
        tid = ev.trace_id
        if not tid:
            continue
        bucket = by_trace.setdefault(
            tid,
            {
                "first_ts": ev.timestamp,
                "last_ts": ev.timestamp,
                "event_count": 0,
                "tenants": set(),
                "agents": set(),
                "kinds": {},
            },
        )
        bucket["event_count"] += 1
        bucket["tenants"].add(ev.tenant)
        bucket["agents"].add(ev.agent)
        bucket["kinds"][ev.kind.value] = bucket["kinds"].get(ev.kind.value, 0) + 1
        if ev.timestamp < bucket["first_ts"]:
            bucket["first_ts"] = ev.timestamp
        if ev.timestamp > bucket["last_ts"]:
            bucket["last_ts"] = ev.timestamp

    summaries = [
        TraceSummary(
            trace_id=tid,
            first_ts=bucket["first_ts"],
            last_ts=bucket["last_ts"],
            event_count=bucket["event_count"],
            tenants=sorted(bucket["tenants"]),
            agents=sorted(bucket["agents"]),
            kinds=bucket["kinds"],
        )
        for tid, bucket in by_trace.items()
    ]
    summaries.sort(key=lambda s: s.last_ts, reverse=True)
    return TraceIndexResponse(traces=summaries[:limit])


@router.get("/sos/traces/{trace_id}", response_model=TraceDetailResponse)
async def get_trace(
    trace_id: str,
    authorization: str | None = Header(None),
    days: int = Query(1, ge=1, le=7, description="How many days back to scan"),
) -> TraceDetailResponse:
    """Return every audit event carrying ``trace_id``, oldest first."""
    if verify_bearer(authorization) is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    matching = [ev for ev in _iter_events(days) if ev.trace_id == trace_id]
    if not matching:
        raise HTTPException(status_code=404, detail="trace not found")

    matching.sort(key=lambda e: e.timestamp)
    return TraceDetailResponse(trace_id=trace_id, events=matching)
