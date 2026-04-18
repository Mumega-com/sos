"""Tests for ``GET /sos/brain/html`` — the operator-facing HTML render.

Mirrors the auth + snapshot semantics of the JSON route (see
``test_dashboard_brain_route.py``) and additionally covers:

1. 200 on hit — HTML contains the snapshot scalars and table headers
2. 200 with empty provider matrix renders the fail-soft empty state
3. 200 with a populated provider matrix renders breaker state class
4. 503 on snapshot miss
5. 401 without bearer
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sos.contracts.brain_snapshot import BrainSnapshot, RoutingDecision
from sos.kernel.auth import AuthContext
from sos.services.brain.service import _BRAIN_SNAPSHOT_KEY
from sos.services.dashboard.routes import brain as brain_route


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot() -> BrainSnapshot:
    now_iso = datetime.now(timezone.utc).isoformat()
    return BrainSnapshot(
        queue_size=7,
        in_flight=["task-a", "task-b", "task-c"],
        recent_routes=[
            RoutingDecision(
                task_id="task-42",
                agent_name="kasra",
                score=0.87,
                routed_at=now_iso,
            )
        ],
        events_by_type={"task.created": 5, "task.completed": 3},
        events_seen=8,
        last_update_ts=now_iso,
        service_started_at=now_iso,
    )


class _SyncStubRedis:
    """Minimal sync stub mimicking the subset of redis.Redis used by the route."""

    def __init__(self, value: str | None) -> None:
        self._value = value

    def get(self, key: str) -> str | None:
        assert key == _BRAIN_SNAPSHOT_KEY
        return self._value


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(brain_route.router)
    return app


def _auth_ok(h):
    return (
        AuthContext(is_system=True, is_admin=True, label="test") if h else None
    )


# ---------------------------------------------------------------------------
# 1. 200 + HTML contains expected snapshot scalars (empty matrix path)
# ---------------------------------------------------------------------------


def test_html_route_renders_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    snap_json = _make_snapshot().model_dump_json()
    monkeypatch.setattr(
        brain_route, "_get_redis", lambda: _SyncStubRedis(snap_json)
    )
    monkeypatch.setattr(brain_route, "verify_bearer", _auth_ok)
    # Force empty provider matrix so this test covers the fail-soft path too.
    monkeypatch.setattr(brain_route, "_load_provider_state", lambda: [])

    client = TestClient(_make_app())
    res = client.get(
        "/sos/brain/html", headers={"Authorization": "Bearer testtok"}
    )

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    body = res.text

    # Snapshot scalars appear.
    assert ">7<" in body  # queue_size
    assert ">3<" in body  # in_flight count
    assert ">8<" in body  # events_seen
    assert ">0<" in body  # provider count (empty matrix)

    # Route row rendered.
    assert "task-42" in body
    assert "kasra" in body
    assert "0.87" in body

    # Events-by-type rows rendered (descending by count).
    assert "task.created" in body
    assert "task.completed" in body
    # task.created (5) should appear before task.completed (3).
    assert body.index("task.created") < body.index("task.completed")

    # Empty-matrix fail-soft text.
    assert "no provider matrix loaded" in body


# ---------------------------------------------------------------------------
# 2. 200 with providers — breaker state class + tier pill render
# ---------------------------------------------------------------------------


def test_html_route_renders_provider_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    snap_json = _make_snapshot().model_dump_json()
    monkeypatch.setattr(
        brain_route, "_get_redis", lambda: _SyncStubRedis(snap_json)
    )
    monkeypatch.setattr(brain_route, "verify_bearer", _auth_ok)
    monkeypatch.setattr(
        brain_route,
        "_load_provider_state",
        lambda: [
            {
                "id": "claude-primary",
                "tier": "primary",
                "backend": "claude-adapter",
                "model": "claude-sonnet-4-6",
                "state": "closed",
                "failures": 0,
            },
            {
                "id": "openai-fallback",
                "tier": "fallback",
                "backend": "openai-adapter",
                "model": "gpt-4o",
                "state": "open",
                "failures": 9,
            },
        ],
    )

    client = TestClient(_make_app())
    res = client.get(
        "/sos/brain/html", headers={"Authorization": "Bearer testtok"}
    )
    assert res.status_code == 200
    body = res.text

    # Provider id + model shown.
    assert "claude-primary" in body
    assert "claude-sonnet-4-6" in body
    assert "openai-fallback" in body
    assert "gpt-4o" in body

    # Breaker state CSS class present for both states.
    assert "state-closed" in body
    assert "state-open" in body

    # Healthy count = 1 (one closed out of two).
    assert ">1 healthy<" in body


# ---------------------------------------------------------------------------
# 3. 503 on snapshot miss
# ---------------------------------------------------------------------------


def test_html_route_503_on_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brain_route, "_get_redis", lambda: _SyncStubRedis(None))
    monkeypatch.setattr(brain_route, "verify_bearer", _auth_ok)

    client = TestClient(_make_app())
    res = client.get(
        "/sos/brain/html", headers={"Authorization": "Bearer testtok"}
    )
    assert res.status_code == 503
    assert res.json()["detail"] == "brain snapshot unavailable"


# ---------------------------------------------------------------------------
# 4. 401 without bearer — must not hit redis or provider loader
# ---------------------------------------------------------------------------


def test_html_route_401_without_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brain_route, "verify_bearer", _auth_ok)
    monkeypatch.setattr(
        brain_route,
        "_get_redis",
        lambda: (_ for _ in ()).throw(AssertionError("must not hit redis")),
    )
    monkeypatch.setattr(
        brain_route,
        "_load_provider_state",
        lambda: (_ for _ in ()).throw(AssertionError("must not load matrix")),
    )

    client = TestClient(_make_app())
    res = client.get("/sos/brain/html")
    assert res.status_code == 401
