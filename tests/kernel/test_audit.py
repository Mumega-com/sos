"""Tests for sos.kernel.audit — the v0.5.0 unified audit stream."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sos.contracts.audit import AuditDecision, AuditEvent, AuditEventKind
from sos.kernel.audit import append_event, new_event, read_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(**kwargs) -> AuditEvent:
    defaults = dict(
        agent="a",
        tenant="t",
        kind=AuditEventKind.INTENT,
        action="x",
        target="y",
    )
    defaults.update(kwargs)
    return new_event(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_append_and_read_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """append_event writes a record; read_events returns exactly that record."""
    monkeypatch.setattr("sos.kernel.audit._audit_dir", lambda: tmp_path)

    event = _make_event()
    await append_event(event)

    events = read_events("t")
    assert len(events) == 1
    assert events[0].id == event.id
    assert events[0].agent == "a"
    assert events[0].kind == AuditEventKind.INTENT


async def test_filter_by_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """read_events(kind=...) returns only events of that kind."""
    monkeypatch.setattr("sos.kernel.audit._audit_dir", lambda: tmp_path)

    await append_event(_make_event(kind=AuditEventKind.INTENT))
    await append_event(_make_event(kind=AuditEventKind.POLICY_DECISION))
    await append_event(_make_event(kind=AuditEventKind.ACTION_COMPLETED))

    results = read_events("t", kind=AuditEventKind.INTENT)
    assert len(results) == 1
    assert results[0].kind == AuditEventKind.INTENT


def test_immutability_frozen() -> None:
    """Mutating a frozen AuditEvent field raises ValidationError (Pydantic v2 frozen=True)."""
    event = _make_event()
    with pytest.raises((TypeError, Exception)) as exc_info:
        # Pydantic v2 raises ValidationError for frozen models
        object.__setattr__(event, "agent", "mutated")  # bypass __setattr__ — still raises on validate
        event.agent = "mutated"  # this should raise
    # If object.__setattr__ succeeded, verify by triggering validation
    # The frozen check is on __setattr__; let's use the proper path:


def test_immutability_frozen_via_setattr() -> None:
    """Direct attribute assignment on a frozen AuditEvent raises ValidationError."""
    import pydantic
    event = _make_event()
    with pytest.raises(pydantic.ValidationError):
        event.agent = "mutated"  # type: ignore[misc]


async def test_disk_works_when_redis_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """append_event persists to disk even when Redis is unavailable."""
    monkeypatch.setattr("sos.kernel.audit._audit_dir", lambda: tmp_path)

    # Patch redis to be unavailable
    import sys
    original = sys.modules.get("redis")
    sys.modules["redis"] = None  # type: ignore[assignment]
    sys.modules["redis.asyncio"] = None  # type: ignore[assignment]

    try:
        event = _make_event(agent="durability-check")
        result = await append_event(event)
        assert result == event.id
    finally:
        if original is None:
            sys.modules.pop("redis", None)
            sys.modules.pop("redis.asyncio", None)
        else:
            sys.modules["redis"] = original

    # File must exist and contain the event
    events = read_events("t")
    assert any(e.agent == "durability-check" for e in events)


async def test_read_events_corrupted_line_tolerated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A corrupted JSONL line is silently skipped; valid lines are returned."""
    monkeypatch.setattr("sos.kernel.audit._audit_dir", lambda: tmp_path)

    event = _make_event(agent="clean")
    await append_event(event)

    # Inject garbage before the valid line
    from datetime import datetime, timezone
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    audit_file = tmp_path / "t" / f"{date_str}.jsonl"
    content = audit_file.read_text()
    audit_file.write_text("NOT_JSON_AT_ALL\n" + content)

    events = read_events("t")
    assert len(events) == 1
    assert events[0].agent == "clean"


async def test_read_defaults_to_today(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """read_events without a date kwarg returns today's events."""
    monkeypatch.setattr("sos.kernel.audit._audit_dir", lambda: tmp_path)

    event = _make_event(agent="today-check")
    await append_event(event)

    events = read_events("t")  # no date kwarg
    assert any(e.agent == "today-check" for e in events)
