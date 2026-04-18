"""Tests for the dashboard ``GET /sos/traces`` and ``GET /sos/traces/{trace_id}``
routes (v0.6.0 Step 2.4).

The route aggregates the disk audit log into a per-trace summary index
and a per-trace detail view. We seed ``tmp_path/{tenant}/{YYYY-MM-DD}.jsonl``
with handwritten ``AuditEvent`` lines and point ``_audit_root`` at it, so
no Redis or filesystem state from the host machine is touched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sos.contracts.audit import AuditDecision, AuditEvent, AuditEventKind
from sos.kernel.auth import AuthContext
from sos.services.dashboard.routes import traces as traces_route


def _make_event(
    *,
    eid: str,
    ts: str,
    agent: str,
    tenant: str,
    trace_id: str | None,
    kind: AuditEventKind,
    action: str = "tool.x",
    target: str = "user@example.com",
) -> AuditEvent:
    return AuditEvent(
        id=eid,
        timestamp=ts,
        agent=agent,
        tenant=tenant,
        trace_id=trace_id,
        kind=kind,
        action=action,
        target=target,
        decision=AuditDecision.ALLOW if kind == AuditEventKind.INTENT else AuditDecision.NOT_APPLICABLE,
    )


def _seed(root: Path, tenant: str, date: str, events: list[AuditEvent]) -> None:
    path = root / tenant / f"{date}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for ev in events:
            fh.write(ev.model_dump_json() + "\n")


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(traces_route.router)
    return app


@pytest.fixture
def seeded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Audit root with two traces (T1 two-event, T2 one-event) and one untraced event."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trace_a = "a" * 32
    trace_b = "b" * 32

    _seed(
        tmp_path,
        "acme",
        today,
        [
            _make_event(
                eid="1",
                ts=f"{today}T10:00:00+00:00",
                agent="kasra",
                tenant="acme",
                trace_id=trace_a,
                kind=AuditEventKind.INTENT,
            ),
            _make_event(
                eid="2",
                ts=f"{today}T10:00:01+00:00",
                agent="kasra",
                tenant="acme",
                trace_id=trace_a,
                kind=AuditEventKind.ACTION_COMPLETED,
            ),
            _make_event(
                eid="3",
                ts=f"{today}T09:30:00+00:00",
                agent="river",
                tenant="acme",
                trace_id=None,
                kind=AuditEventKind.INTENT,
            ),
        ],
    )
    _seed(
        tmp_path,
        "globex",
        today,
        [
            _make_event(
                eid="4",
                ts=f"{today}T11:00:00+00:00",
                agent="kasra",
                tenant="globex",
                trace_id=trace_b,
                kind=AuditEventKind.INTENT,
            ),
        ],
    )

    monkeypatch.setattr(traces_route, "_audit_root", lambda: tmp_path)
    monkeypatch.setattr(
        traces_route,
        "verify_bearer",
        lambda h: AuthContext(is_system=True, is_admin=True, label="test") if h else None,
    )
    return tmp_path


def test_list_traces_aggregates_by_trace_id(seeded: Path) -> None:
    client = TestClient(_make_app())
    res = client.get("/sos/traces", headers={"Authorization": "Bearer t"})
    assert res.status_code == 200
    body = res.json()
    by_id = {row["trace_id"]: row for row in body["traces"]}

    # Untraced event must not appear in the index.
    assert len(by_id) == 2

    t_a = by_id["a" * 32]
    assert t_a["event_count"] == 2
    assert t_a["tenants"] == ["acme"]
    assert t_a["kinds"] == {"intent": 1, "action_completed": 1}

    t_b = by_id["b" * 32]
    assert t_b["event_count"] == 1
    assert t_b["tenants"] == ["globex"]

    # Sorted by last_ts desc — trace B (11:00) before trace A (10:00:01).
    assert body["traces"][0]["trace_id"] == "b" * 32


def test_trace_detail_returns_events_oldest_first(seeded: Path) -> None:
    client = TestClient(_make_app())
    res = client.get("/sos/traces/" + "a" * 32, headers={"Authorization": "Bearer t"})
    assert res.status_code == 200
    body = res.json()
    assert body["trace_id"] == "a" * 32
    assert [e["id"] for e in body["events"]] == ["1", "2"]


def test_trace_detail_404_when_unknown(seeded: Path) -> None:
    client = TestClient(_make_app())
    res = client.get("/sos/traces/" + "c" * 32, headers={"Authorization": "Bearer t"})
    assert res.status_code == 404


def test_traces_require_bearer(seeded: Path) -> None:
    client = TestClient(_make_app())
    res = client.get("/sos/traces")
    assert res.status_code == 401
    res = client.get("/sos/traces/" + "a" * 32)
    assert res.status_code == 401
