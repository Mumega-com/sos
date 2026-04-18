"""E2E test: Brain scoring → dispatch → dashboard snapshot.

Proves the full in-process pipeline using fakeredis (no live Redis):
  agent_joined seeded → task.created seeded →
  _tick() × 2 (ingest + score/dispatch) →
  task.scored + task.routed on brain stream →
  BrainSnapshot persisted to sos:state:brain:snapshot →
  GET /sos/brain → 200 with correct snapshot.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from sos.contracts.brain_snapshot import BrainSnapshot
from sos.contracts.messages import AgentJoinedMessage  # noqa: F401 — for clarity
from sos.kernel.identity import AgentIdentity
from sos.kernel.auth import AuthContext
from sos.services.brain import service as brain_service_module
from sos.services.brain.service import BrainService, _BRAIN_SNAPSHOT_KEY
from sos.services.dashboard.routes import brain as brain_route


def _make_dashboard_app() -> FastAPI:
    """Minimal FastAPI app with only the brain router (avoids multipart dep)."""
    _app = FastAPI()
    _app.include_router(brain_route.router)
    return _app

# ---------------------------------------------------------------------------
# Stream names used by BrainService
# ---------------------------------------------------------------------------

_AGENTS_STREAM = "sos:stream:global:squad:agents"
_TASKS_STREAM = "sos:stream:global:squad:tasks"
_BRAIN_EMIT_STREAM = "sos:stream:global:squad:brain"


# ---------------------------------------------------------------------------
# Helpers — envelope builders
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_agent_joined_fields(agent_name: str) -> dict[str, str]:
    payload = {"agent_name": agent_name, "joined_at": _now()}
    return {
        "type": "agent_joined",
        "source": "agent:kernel",
        "target": "sos:channel:system:events",
        "timestamp": _now(),
        "version": "1.0",
        "message_id": str(uuid.uuid4()),
        "payload": json.dumps(payload),
    }


def _make_task_created_fields(
    task_id: str,
    *,
    labels: list[str] | None = None,
    skill_id: str | None = None,
    priority: str = "high",
    title: str = "publish wordpress post",
    trace_id: str | None = None,
) -> dict[str, str]:
    payload: dict[str, object] = {
        "task_id": task_id,
        "title": title,
        "priority": priority,
    }
    if labels is not None:
        payload["labels"] = labels
    if skill_id is not None:
        payload["skill_id"] = skill_id
    fields: dict[str, str] = {
        "type": "task.created",
        "source": "agent:squad",
        "target": "sos:channel:tasks",
        "timestamp": _now(),
        "version": "1.0",
        "message_id": str(uuid.uuid4()),
        "payload": json.dumps(payload),
    }
    if trace_id is not None:
        fields["trace_id"] = trace_id
    return fields


# ---------------------------------------------------------------------------
# Sync stub that wraps the async fakeredis for the dashboard route
# (the dashboard GET /sos/brain calls _get_redis().get() synchronously)
# ---------------------------------------------------------------------------


class _SyncFakeRedisProxy:
    """Thin sync proxy that reads from a shared in-memory store.

    BrainService writes snapshot JSON via the async fakeredis client.
    The dashboard route reads it via _get_redis().get() (synchronous).
    We share a reference to the async FakeRedis's internal server so
    both read from the same in-memory store.
    """

    def __init__(self, snapshot_holder: dict[str, str]) -> None:
        self._store = snapshot_holder

    def get(self, key: str) -> str | None:
        return self._store.get(key)


# ---------------------------------------------------------------------------
# Main E2E test
# ---------------------------------------------------------------------------


async def test_brain_e2e_score_dispatch_dashboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: agent_joined + task.created → scored + routed → dashboard 200."""

    # ------------------------------------------------------------------
    # 1. Set up fakeredis and BrainService
    # ------------------------------------------------------------------
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

    svc = BrainService(
        redis_client=fake,
        stream_patterns=[
            "sos:stream:global:squad:*",
        ],
    )

    # ------------------------------------------------------------------
    # 2. Mock AsyncRegistryClient.list_agents to return hermes-test
    # (P0-09 — Brain reaches the registry over HTTP; no real service needed)
    # ------------------------------------------------------------------
    hermes = AgentIdentity(name="hermes-test")
    hermes.capabilities.extend(["wordpress", "media"])

    async def _list_agents():
        return [hermes]

    monkeypatch.setattr(
        brain_service_module._registry_client,
        "list_agents",
        _list_agents,
    )

    # ------------------------------------------------------------------
    # 3. Seed agent_joined and task.created onto the agents/tasks streams
    # ------------------------------------------------------------------
    await fake.xadd(_AGENTS_STREAM, _make_agent_joined_fields("hermes-test"))

    task_id = "task-wp-e2e-001"
    # Seed a known trace_id on the inbound envelope so we can verify it
    # propagates through task.scored + task.routed emissions.
    inbound_trace_id = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
    await fake.xadd(
        _TASKS_STREAM,
        _make_task_created_fields(
            task_id, labels=["wordpress"], trace_id=inbound_trace_id
        ),
    )

    # ------------------------------------------------------------------
    # 4. Run two ticks:
    #    Tick 1 — reads agent_joined + task.created, scores, dispatches
    #    Tick 2 — no new events, persists final snapshot
    # ------------------------------------------------------------------
    await svc._tick()
    await svc._tick()

    # ------------------------------------------------------------------
    # 5. Assert task.scored appeared on the brain stream
    # ------------------------------------------------------------------
    all_entries = await fake.xrange(_BRAIN_EMIT_STREAM)
    scored_entries = [
        fields for _eid, fields in all_entries if fields.get("type") == "task.scored"
    ]
    assert len(scored_entries) == 1, (
        f"expected exactly one task.scored, got {len(scored_entries)}: {scored_entries}"
    )
    scored_payload = json.loads(scored_entries[0]["payload"])
    assert scored_payload["task_id"] == task_id, (
        f"task.scored payload.task_id mismatch: {scored_payload}"
    )

    # ------------------------------------------------------------------
    # 6. Assert task.routed appeared on the brain stream with hermes-test
    # ------------------------------------------------------------------
    routed_entries = [
        fields for _eid, fields in all_entries if fields.get("type") == "task.routed"
    ]
    assert len(routed_entries) == 1, (
        f"expected exactly one task.routed, got {len(routed_entries)}: {routed_entries}"
    )
    routed_payload = json.loads(routed_entries[0]["payload"])
    assert routed_payload["task_id"] == task_id, (
        f"task.routed payload.task_id mismatch: {routed_payload}"
    )
    assert routed_payload["routed_to"] == "hermes-test", (
        f"expected routed_to=hermes-test, got {routed_payload['routed_to']!r}"
    )

    # ------------------------------------------------------------------
    # 6a. Assert trace_id propagates — inbound envelope → scored → routed
    # ------------------------------------------------------------------
    assert scored_entries[0].get("trace_id") == inbound_trace_id, (
        f"task.scored must carry inbound trace_id {inbound_trace_id!r}, "
        f"got {scored_entries[0].get('trace_id')!r}"
    )
    assert routed_entries[0].get("trace_id") == inbound_trace_id, (
        f"task.routed must carry inbound trace_id {inbound_trace_id!r}, "
        f"got {routed_entries[0].get('trace_id')!r}"
    )

    # ------------------------------------------------------------------
    # 7. Assert BrainSnapshot key exists and is valid
    # ------------------------------------------------------------------
    raw_snapshot = await fake.get(_BRAIN_SNAPSHOT_KEY)
    assert raw_snapshot is not None, "sos:state:brain:snapshot key must exist after tick"

    snapshot = BrainSnapshot.model_validate_json(raw_snapshot)
    assert snapshot.queue_size == 0, (
        f"queue_size should be 0 after dispatch, got {snapshot.queue_size}"
    )
    assert len(snapshot.recent_routes) >= 1, "recent_routes must contain at least one entry"
    last_route = snapshot.recent_routes[-1]
    assert last_route.agent_name == "hermes-test", (
        f"recent_routes[-1].agent_name expected hermes-test, got {last_route.agent_name!r}"
    )

    # ------------------------------------------------------------------
    # 8. Dashboard route via httpx.AsyncClient
    #    - monkeypatch _get_redis to a sync proxy backed by our fakeredis data
    #    - monkeypatch verify_bearer to accept a test token
    # ------------------------------------------------------------------

    # Build a sync proxy that holds the snapshot JSON we already verified above.
    snapshot_store: dict[str, str] = {_BRAIN_SNAPSHOT_KEY: raw_snapshot}
    sync_proxy = _SyncFakeRedisProxy(snapshot_store)

    monkeypatch.setattr(brain_route, "_get_redis", lambda: sync_proxy)
    monkeypatch.setattr(
        brain_route,
        "verify_bearer",
        lambda h: AuthContext(is_system=True, is_admin=True, label="test") if h else None,
    )

    transport = ASGITransport(app=_make_dashboard_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/sos/brain",
            headers={"Authorization": "Bearer test-e2e-token"},
        )

    assert response.status_code == 200, (
        f"GET /sos/brain expected 200, got {response.status_code}: {response.text}"
    )

    body = response.json()
    dashboard_snapshot = BrainSnapshot.model_validate(body)

    assert dashboard_snapshot.queue_size == 0, (
        f"dashboard queue_size expected 0, got {dashboard_snapshot.queue_size}"
    )
    assert len(dashboard_snapshot.recent_routes) >= 1, (
        "dashboard recent_routes must be non-empty"
    )
    assert dashboard_snapshot.recent_routes[-1].agent_name == "hermes-test", (
        f"dashboard recent_routes[-1].agent_name expected hermes-test, "
        f"got {dashboard_snapshot.recent_routes[-1].agent_name!r}"
    )
