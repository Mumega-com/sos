"""Tests for the v0.7.1 HTML renders over /sos/traces.

Shares the same tmp-path seeding pattern as test_dashboard_traces_route.py
(which covers the JSON contract). These tests exercise:

1. Index HTML — 200 + trace rows + summary cards
2. Index HTML — empty window renders empty-state row (no seeded traces)
3. Detail HTML — 200 + event rows oldest-first + kind pills
4. Detail HTML — 404 for unknown trace_id
5. Auth — 401 without bearer on both HTML routes
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
    inputs: dict | None = None,
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
        decision=(
            AuditDecision.ALLOW if kind == AuditEventKind.INTENT else AuditDecision.NOT_APPLICABLE
        ),
        inputs=inputs or {},
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


def _auth_ok(h):
    return AuthContext(is_system=True, is_admin=True, label="test") if h else None


@pytest.fixture
def seeded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
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
                inputs={"subject": "hello"},
            ),
            _make_event(
                eid="2",
                ts=f"{today}T10:00:01+00:00",
                agent="kasra",
                tenant="acme",
                trace_id=trace_a,
                kind=AuditEventKind.ACTION_COMPLETED,
            ),
        ],
    )
    _seed(
        tmp_path,
        "globex",
        today,
        [
            _make_event(
                eid="3",
                ts=f"{today}T11:00:00+00:00",
                agent="river",
                tenant="globex",
                trace_id=trace_b,
                kind=AuditEventKind.POLICY_DECISION,
            ),
        ],
    )

    monkeypatch.setattr(traces_route, "_audit_root", lambda: tmp_path)
    monkeypatch.setattr(traces_route, "verify_bearer", _auth_ok)
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Index HTML — content
# ---------------------------------------------------------------------------


def test_html_index_renders_trace_rows(seeded: Path) -> None:
    client = TestClient(_make_app())
    res = client.get("/sos/traces/html", headers={"Authorization": "Bearer t"})

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    body = res.text

    # Both seeded traces show up by prefix (template truncates to 12 chars).
    assert "a" * 12 in body
    assert "b" * 12 in body

    # Summary cards: 2 traces total, 3 events, 2 tenants, 2 agents.
    assert ">2<" in body  # appears multiple times (traces / tenants / agents)
    assert ">3<" in body  # event_total

    # Links to detail pages use full trace_id.
    assert f'href="/sos/traces/{"a" * 32}/html"' in body

    # Kind pills rendered with the right CSS class.
    assert "kind-intent" in body
    assert "kind-action_completed" in body
    assert "kind-policy_decision" in body


# ---------------------------------------------------------------------------
# 2. Index HTML — empty window
# ---------------------------------------------------------------------------


def test_html_index_empty_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Point audit root at an empty dir — no tenants, no events.
    monkeypatch.setattr(traces_route, "_audit_root", lambda: tmp_path)
    monkeypatch.setattr(traces_route, "verify_bearer", _auth_ok)

    client = TestClient(_make_app())
    res = client.get("/sos/traces/html", headers={"Authorization": "Bearer t"})

    assert res.status_code == 200
    assert "no traces in window" in res.text


# ---------------------------------------------------------------------------
# 3. Detail HTML — content
# ---------------------------------------------------------------------------


def test_html_detail_renders_events(seeded: Path) -> None:
    client = TestClient(_make_app())
    trace_a = "a" * 32
    res = client.get(
        f"/sos/traces/{trace_a}/html", headers={"Authorization": "Bearer t"}
    )

    assert res.status_code == 200
    body = res.text

    # Both events appear with their kind pills, oldest first.
    intent_idx = body.find("kind-intent")
    completed_idx = body.find("kind-action_completed")
    assert intent_idx != -1 and completed_idx != -1
    assert intent_idx < completed_idx

    # The back-link and full trace_id render.
    assert 'href="/sos/traces/html"' in body
    assert trace_a in body

    # Sanitised inputs surface as a JSON details block on the INTENT row.
    assert "<details>" in body
    assert "<summary>inputs</summary>" in body
    assert "&quot;subject&quot;" in body  # html-escaped JSON key


# ---------------------------------------------------------------------------
# 4. Detail HTML — 404
# ---------------------------------------------------------------------------


def test_html_detail_404(seeded: Path) -> None:
    client = TestClient(_make_app())
    res = client.get(
        "/sos/traces/" + "c" * 32 + "/html", headers={"Authorization": "Bearer t"}
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 5. Auth — both HTML routes return 401 without bearer
# ---------------------------------------------------------------------------


def test_html_routes_require_bearer(seeded: Path) -> None:
    client = TestClient(_make_app())
    assert client.get("/sos/traces/html").status_code == 401
    assert client.get("/sos/traces/" + "a" * 32 + "/html").status_code == 401
