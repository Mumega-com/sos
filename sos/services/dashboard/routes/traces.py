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

v0.7.1 adds operator-facing HTML renders: ``GET /sos/traces/html`` for
the index and ``GET /sos/traces/{trace_id}/html`` for per-trace detail.
Auth + 4xx semantics match the JSON routes. The HTML index route is
registered before ``GET /sos/traces/{trace_id}`` so ``html`` isn't
captured as a trace_id.
"""
from __future__ import annotations

import html as _html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from sos.contracts.audit import AuditEvent
from sos.contracts.traces import TraceDetailResponse, TraceIndexResponse, TraceSummary
from sos.kernel.auth import verify_bearer

from ..templates.traces import TRACES_DETAIL_HTML, TRACES_INDEX_HTML

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


def _build_index(days: int, limit: int) -> list[TraceSummary]:
    """Shared index builder used by the JSON and HTML routes."""
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
    return summaries[:limit]


@router.get("/sos/traces", response_model=TraceIndexResponse)
async def list_traces(
    authorization: str | None = Header(None),
    days: int = Query(1, ge=1, le=7, description="How many days back to scan"),
    limit: int = Query(50, ge=1, le=500, description="Max traces in the response"),
) -> TraceIndexResponse:
    """Return a summary row per distinct ``trace_id`` in the audit log."""
    if verify_bearer(authorization) is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return TraceIndexResponse(traces=_build_index(days=days, limit=limit))


def _render_index_rows(traces: list[TraceSummary]) -> str:
    if not traces:
        return '<tr><td colspan="7" class="empty">no traces in window</td></tr>'
    out: list[str] = []
    for t in traces:
        tid_esc = _html.escape(t.trace_id)
        kinds_pills = " ".join(
            f'<span class="pill kind-{_html.escape(k)}">{_html.escape(k)} · {v}</span>'
            for k, v in sorted(t.kinds.items())
        )
        out.append(
            "<tr>"
            f'<td><code><a href="/sos/traces/{tid_esc}/html">{tid_esc[:12]}…</a></code></td>'
            f"<td><code>{_html.escape(t.first_ts)}</code></td>"
            f"<td><code>{_html.escape(t.last_ts)}</code></td>"
            f"<td>{t.event_count}</td>"
            f"<td>{_html.escape(', '.join(t.tenants))}</td>"
            f"<td>{_html.escape(', '.join(t.agents))}</td>"
            f"<td>{kinds_pills}</td>"
            "</tr>"
        )
    return "".join(out)


def _render_event_rows(events: list[AuditEvent]) -> str:
    if not events:
        return '<tr><td colspan="9" class="empty">no events</td></tr>'
    out: list[str] = []
    for i, ev in enumerate(events, start=1):
        kind_val = _html.escape(ev.kind.value)
        decision_val = _html.escape(ev.decision.value)
        # Slash in "n/a" breaks css class parsing if written raw; keep the
        # template's ``.decision-n\\/a`` rule consistent by swapping to a
        # safe class suffix here.
        decision_class = decision_val.replace("/", "\\/")
        cost_display = (
            f"{ev.cost_micros / 1_000_000:.6f} {_html.escape(ev.cost_currency)}"
            if ev.cost_micros
            else "—"
        )

        payload_blocks: list[str] = []
        for label, blob in (
            ("inputs", ev.inputs),
            ("outputs", ev.outputs),
            ("metadata", ev.metadata),
        ):
            if not blob:
                continue
            pretty = json.dumps(blob, indent=2, sort_keys=True, default=str)
            payload_blocks.append(
                f"<details><summary>{label}</summary>"
                f"<pre>{_html.escape(pretty)}</pre></details>"
            )
        payload_cell = "".join(payload_blocks) or '<span class="empty">—</span>'

        reason_suffix = (
            f" <span class=\"empty\">({_html.escape(ev.reason)})</span>"
            if ev.reason
            else ""
        )

        out.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td><code>{_html.escape(ev.timestamp)}</code></td>"
            f'<td><span class="pill kind-{kind_val}">{kind_val}</span></td>'
            f"<td>{_html.escape(ev.agent)} / {_html.escape(ev.tenant)}</td>"
            f"<td><code>{_html.escape(ev.action)}</code></td>"
            f"<td>{_html.escape(ev.target)}</td>"
            f'<td class="decision-{decision_class}">{decision_val}{reason_suffix}</td>'
            f"<td>{cost_display}</td>"
            f"<td>{payload_cell}</td>"
            "</tr>"
        )
    return "".join(out)


@router.get("/sos/traces/html", response_class=HTMLResponse)
async def list_traces_html(
    authorization: str | None = Header(None),
    days: int = Query(1, ge=1, le=7, description="How many days back to scan"),
    limit: int = Query(50, ge=1, le=500, description="Max traces in the response"),
) -> HTMLResponse:
    """Render the trace index as HTML.

    Registered *before* ``/sos/traces/{trace_id}`` so FastAPI doesn't
    capture ``html`` as a trace id. Auth semantics match the JSON index.
    """
    if verify_bearer(authorization) is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    # Pull the full index (no limit) so the summary cards reflect totals,
    # then slice for the rendered table.
    all_summaries = _build_index(days=days, limit=limit * 20)
    shown = all_summaries[:limit]

    event_total = sum(t.event_count for t in shown)
    tenant_total = len({tenant for t in shown for tenant in t.tenants})
    agent_total = len({agent for t in shown for agent in t.agents})

    body = TRACES_INDEX_HTML.format(
        days=days,
        total=len(all_summaries),
        shown=len(shown),
        event_total=event_total,
        tenant_total=tenant_total,
        agent_total=agent_total,
        trace_rows=_render_index_rows(shown),
    )
    return HTMLResponse(content=body)


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


@router.get("/sos/traces/{trace_id}/html", response_class=HTMLResponse)
async def get_trace_html(
    trace_id: str,
    authorization: str | None = Header(None),
    days: int = Query(1, ge=1, le=7, description="How many days back to scan"),
) -> HTMLResponse:
    """Render one trace's events as HTML, oldest first."""
    if verify_bearer(authorization) is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    matching = [ev for ev in _iter_events(days) if ev.trace_id == trace_id]
    if not matching:
        raise HTTPException(status_code=404, detail="trace not found")

    matching.sort(key=lambda e: e.timestamp)

    body = TRACES_DETAIL_HTML.format(
        trace_id=_html.escape(trace_id),
        trace_id_short=_html.escape(trace_id[:12]),
        event_count=len(matching),
        first_ts=_html.escape(matching[0].timestamp),
        last_ts=_html.escape(matching[-1].timestamp),
        event_rows=_render_event_rows(matching),
    )
    return HTMLResponse(content=body)
