"""SOS kernel — unified audit stream (v0.5.0).

Durable, append-only record of every kernel-governed action. Two sinks:

- **Disk** (authoritative): `~/.sos/audit/{tenant}/{YYYY-MM-DD}.jsonl`.
  Synchronous append + fsync. This is the source of truth. Always works.
- **Bus** (observational): `sos:audit:{tenant}` Redis stream, best-effort.
  Real-time consumers tail this. If Redis is down we silently skip — disk
  persistence never blocks.

Public surface is intentionally tiny: `append_event`, `read_events`,
`new_event`. Writers (governance today; policy and arbitration in v0.5.1
and v0.5.2) construct an ``AuditEvent`` and pass it in. They do not see
the sinks. This file is not meant to grow — new event kinds are added by
extending ``sos.contracts.audit.AuditEventKind``, not by adding code here.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sos.contracts.audit import AuditEvent, AuditEventKind

logger = logging.getLogger("sos.kernel.audit")


def _audit_dir() -> Path:
    return Path.home() / ".sos" / "audit"


def _make_id() -> str:
    """Sortable unique id: nanosecond timestamp + 6 bytes random hex."""
    return f"{time.time_ns()}-{secrets.token_hex(6)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_event(
    *,
    agent: str,
    tenant: str,
    kind: AuditEventKind,
    action: str,
    target: str,
    **kwargs: Any,
) -> AuditEvent:
    """Construct an AuditEvent with id + timestamp filled in.

    Convenience only — callers could construct the model directly, but
    this keeps id/timestamp generation consistent across writers.

    If ``trace_id`` is not supplied in kwargs, the current value from
    ``sos.kernel.trace_context.get_current_trace_id()`` is used. This
    lets bus consumers set the trace-id once per inbound message and
    have every audit event produced during handling inherit it.
    """
    if "trace_id" not in kwargs:
        from sos.kernel.trace_context import get_current_trace_id

        current = get_current_trace_id()
        if current is not None:
            kwargs["trace_id"] = current

    return AuditEvent(
        id=_make_id(),
        timestamp=_now_iso(),
        agent=agent,
        tenant=tenant,
        kind=kind,
        action=action,
        target=target,
        **kwargs,
    )


async def append_event(event: AuditEvent) -> str:
    """Persist an audit event. Returns the event id.

    Disk write is synchronous and authoritative — if it fails the call
    raises, because losing an audit record silently is worse than a
    governance hiccup. Bus emit is async best-effort; Redis being down
    never blocks persistence.
    """
    date_str = event.timestamp[:10]  # YYYY-MM-DD from ISO-8601
    audit_file = _audit_dir() / event.tenant / f"{date_str}.jsonl"
    audit_file.parent.mkdir(parents=True, exist_ok=True)

    line = event.model_dump_json()
    with open(audit_file, "a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())

    try:
        import redis.asyncio as aioredis
        redis_pw = os.environ.get("REDIS_PASSWORD", "")
        default_url = f"redis://:{redis_pw}@localhost:6379/0" if redis_pw else "redis://localhost:6379/0"
        redis_url = os.environ.get("REDIS_URL", default_url)
        r = aioredis.from_url(redis_url, decode_responses=True)
        try:
            await r.xadd(
                f"sos:audit:{event.tenant}",
                {"event": line},
                maxlen=10000,
                approximate=True,
            )
        finally:
            await r.aclose()
    except Exception as exc:
        logger.debug("audit bus emit skipped (%s): %s", type(exc).__name__, exc)

    return event.id


def read_events(
    tenant: str,
    *,
    date: Optional[str] = None,
    kind: Optional[AuditEventKind] = None,
    limit: int = 100,
) -> list[AuditEvent]:
    """Read audit events for a tenant from the authoritative disk sink.

    - ``date`` defaults to today (UTC).
    - ``kind`` filters by event kind if given.
    - Returns up to ``limit`` events, newest last (file order).
    - Corrupted lines are silently skipped rather than raising —
      replay should never crash on a single bad record.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    audit_file = _audit_dir() / tenant / f"{date}.jsonl"
    if not audit_file.exists():
        return []

    events: list[AuditEvent] = []
    for line in audit_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            ev = AuditEvent.model_validate_json(line)
        except Exception:
            continue
        if kind is not None and ev.kind != kind:
            continue
        events.append(ev)

    return events[-limit:]


__all__ = ["append_event", "read_events", "new_event"]
