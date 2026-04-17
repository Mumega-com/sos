"""Tests for the dashboard ``GET /sos/brain`` route and the BrainService.snapshot
hand-off.

Five tests:
  1. ``BrainService.snapshot`` returns a valid BrainSnapshot
  2. ``BrainService._tick`` persists the snapshot to redis
  3. Dashboard route returns 200 + BrainSnapshot when redis has the key
  4. Dashboard route returns 503 when redis has no snapshot
  5. Dashboard route returns 401 when the Authorization header is missing
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

try:
    import fakeredis.aioredis as fake_aioredis  # type: ignore[import-untyped]
    HAS_FAKEREDIS = True
except ImportError:  # pragma: no cover
    HAS_FAKEREDIS = False

from sos.contracts.brain_snapshot import BrainSnapshot
from sos.services.auth import AuthContext
from sos.services.brain.service import (
    _BRAIN_SNAPSHOT_KEY,
    BrainService,
)
from sos.services.brain.state import RoutingDecision
from sos.services.dashboard.routes import brain as brain_route


skipif_no_fakeredis = pytest.mark.skipif(
    not HAS_FAKEREDIS, reason="fakeredis not installed"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot() -> BrainSnapshot:
    now_iso = datetime.now(timezone.utc).isoformat()
    return BrainSnapshot(
        queue_size=3,
        in_flight=["task-a", "task-b"],
        recent_routes=[],
        events_by_type={"task.created": 2},
        events_seen=2,
        last_update_ts=now_iso,
        service_started_at=now_iso,
    )


def _fake_redis():
    return fake_aioredis.FakeRedis(decode_responses=True)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(brain_route.router)
    return app


# ---------------------------------------------------------------------------
# 1. snapshot() returns a valid BrainSnapshot
# ---------------------------------------------------------------------------


@skipif_no_fakeredis
def test_snapshot_method_returns_valid_brainsnapshot() -> None:
    r = _fake_redis()
    svc = BrainService(redis_client=r)

    # Seed state: 2 enqueued, 1 in-flight, 1 routing decision, 1 event count.
    svc.state.enqueue("task-1", score=0.9)
    svc.state.enqueue("task-2", score=0.5)
    svc.state.tasks_in_flight.add("task-3")
    svc.state.add_routing_decision(
        RoutingDecision(
            task_id="task-4",
            agent_name="kasra",
            score=0.8,
            routed_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    svc.state.record_event("task.created", datetime.now(timezone.utc).isoformat())

    snap = svc.snapshot()

    assert isinstance(snap, BrainSnapshot)
    assert snap.queue_size == 2
    assert snap.in_flight == ["task-3"]
    assert len(snap.recent_routes) == 1
    assert snap.recent_routes[0].task_id == "task-4"
    assert snap.recent_routes[0].agent_name == "kasra"
    assert snap.events_by_type == {"task.created": 1}
    assert snap.events_seen == 1
    assert snap.service_started_at  # non-empty ISO string


# ---------------------------------------------------------------------------
# 2. _tick persists the snapshot to redis
# ---------------------------------------------------------------------------


@skipif_no_fakeredis
def test_tick_persists_snapshot_to_redis() -> None:
    r = _fake_redis()
    svc = BrainService(
        redis_client=r, stream_patterns=["sos:stream:global:squad:*"]
    )

    async def _go() -> str | None:
        # Seed one stream so discovery returns it (the "no streams" early
        # return path still writes a snapshot, but we exercise the main path).
        await r.xadd(
            "sos:stream:global:squad:brain-test",
            {
                "type": "task.created",
                "message_id": "m-1",
                "payload": json.dumps({"task_id": "t-x", "title": "hi"}),
            },
        )
        await svc._tick()
        return await r.get(_BRAIN_SNAPSHOT_KEY)

    raw = asyncio.run(_go())
    assert raw is not None, "snapshot must be persisted to redis after a tick"
    # Parses as a valid BrainSnapshot.
    snap = BrainSnapshot.model_validate_json(raw)
    assert isinstance(snap, BrainSnapshot)
    assert snap.events_seen >= 1


# ---------------------------------------------------------------------------
# 3. Dashboard route returns 200 + BrainSnapshot on hit
# ---------------------------------------------------------------------------


class _SyncStubRedis:
    """Minimal sync stub mimicking the subset of redis.Redis used by the route."""

    def __init__(self, value: str | None) -> None:
        self._value = value

    def get(self, key: str) -> str | None:
        assert key == _BRAIN_SNAPSHOT_KEY
        return self._value


def test_dashboard_route_returns_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    snap_json = _make_snapshot().model_dump_json()
    monkeypatch.setattr(
        brain_route, "_get_redis", lambda: _SyncStubRedis(snap_json)
    )
    monkeypatch.setattr(
        brain_route,
        "verify_bearer",
        lambda h: AuthContext(is_system=True, is_admin=True, label="test")
        if h
        else None,
    )

    client = TestClient(_make_app())
    res = client.get("/sos/brain", headers={"Authorization": "Bearer testtok"})
    assert res.status_code == 200
    parsed = BrainSnapshot.model_validate(res.json())
    assert parsed.queue_size == 3
    assert parsed.in_flight == ["task-a", "task-b"]


# ---------------------------------------------------------------------------
# 4. Dashboard route returns 503 on miss
# ---------------------------------------------------------------------------


def test_dashboard_route_503_on_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        brain_route, "_get_redis", lambda: _SyncStubRedis(None)
    )
    monkeypatch.setattr(
        brain_route,
        "verify_bearer",
        lambda h: AuthContext(is_system=True, is_admin=True, label="test")
        if h
        else None,
    )

    client = TestClient(_make_app())
    res = client.get("/sos/brain", headers={"Authorization": "Bearer testtok"})
    assert res.status_code == 503
    assert res.json()["detail"] == "brain snapshot unavailable"


# ---------------------------------------------------------------------------
# 5. Dashboard route returns 401 without bearer
# ---------------------------------------------------------------------------


def test_dashboard_route_401_without_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        brain_route,
        "verify_bearer",
        lambda h: AuthContext(is_system=True, is_admin=True, label="test")
        if h
        else None,
    )
    # _get_redis should never be called when auth fails first.
    monkeypatch.setattr(
        brain_route,
        "_get_redis",
        lambda: (_ for _ in ()).throw(AssertionError("must not hit redis")),
    )

    client = TestClient(_make_app())
    res = client.get("/sos/brain")
    assert res.status_code == 401
