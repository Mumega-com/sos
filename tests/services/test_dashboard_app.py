"""Tests for the v0.8.1 S5 dashboard operator API.

Covers the four JSON routes added by ``sos/services/dashboard/operator_api.py``:

- ``GET  /dashboard/tenants/{project}/summary``
- ``GET  /dashboard/tenants/{project}/agents``
- ``POST /dashboard/agents/{name}/kill``

Plus scope enforcement and audit emission. Patches storage + redis helpers
at the module level so no live Redis is required. Mirrors the fixture
style of ``tests/services/test_objectives_app.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sos.services.dashboard.operator_api import router as operator_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    """Mount just the operator router on a bare FastAPI app.

    The Mumega customer dashboard imports routes that require python-multipart
    (login form), which isn't a test dep. Testing the operator API in isolation
    is also cleaner — customer-UI tests live in test_dashboard_brain_*.py.
    """
    app = FastAPI()
    app.include_router(operator_router)
    return TestClient(app)


@pytest.fixture
def system_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-sys-token-dashboard"
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", token)
    from sos.kernel.auth import get_cache

    get_cache().invalidate()
    return token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_fake_can_execute():
    from sos.contracts.policy import PolicyDecision

    async def _fake(**kwargs: Any) -> PolicyDecision:
        return PolicyDecision(
            allowed=True,
            reason="test: gate bypassed",
            tier="act_freely",
            action=kwargs.get("action", ""),
            resource=kwargs.get("resource", ""),
            agent="test-agent",
            tenant="mumega",
            pillars_passed=["system/admin"],
            pillars_failed=[],
            capability_ok=None,
            metadata={},
        )

    return _fake


def _make_scoped_viamar_verify():
    """Return a fake verify_bearer that yields a viamar-scoped context."""
    from sos.kernel.auth import AuthContext

    def fake_verify(authz: str | None) -> AuthContext | None:
        if authz and "scoped-viamar" in authz:
            return AuthContext(
                agent="viamar-agent",
                project="viamar",
                tenant_slug="mumega",
                is_system=False,
                is_admin=False,
                label="scoped",
            )
        return None

    return fake_verify


class _FakeRedis:
    """In-memory fake mimicking the redis.Redis surface used by the route."""

    def __init__(self, hashes: dict[str, dict[str, str]] | None = None) -> None:
        self._hashes: dict[str, dict[str, str]] = hashes or {}
        self.xadd_calls: list[tuple[str, dict[str, Any]]] = []

    def scan_iter(self, match: str | None = None):
        # Very small glob: only support trailing '*' patterns.
        if match is None:
            for k in self._hashes:
                yield k
            return
        if match.endswith("*"):
            prefix = match[:-1]
            for k in self._hashes:
                if k.startswith(prefix):
                    yield k
            return
        # Exact match fallback.
        if match in self._hashes:
            yield match

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hashes.get(key, {}))

    def xadd(self, stream: str, envelope: dict[str, Any], maxlen: int | None = None) -> str:
        self.xadd_calls.append((stream, dict(envelope)))
        return "0-1"


# ---------------------------------------------------------------------------
# 1. Missing bearer → 401
# ---------------------------------------------------------------------------


def test_summary_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.get("/dashboard/tenants/viamar/summary")
    assert resp.status_code == 401


def test_agents_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.get("/dashboard/tenants/viamar/agents")
    assert resp.status_code == 401


def test_kill_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.post("/dashboard/agents/rogue/kill")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. Cross-project scoped token → 403
# ---------------------------------------------------------------------------


def test_summary_cross_project_scoped_is_403(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """viamar-scoped token asking for /dentalnearyou/summary must get 403."""
    monkeypatch.setattr(
        "sos.services.dashboard.operator_api._auth_verify_bearer",
        _make_scoped_viamar_verify(),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.dashboard.operator_api.can_execute",
        _make_fake_can_execute(),
        raising=True,
    )

    resp = client.get(
        "/dashboard/tenants/dentalnearyou/summary",
        headers={"Authorization": "Bearer scoped-viamar"},
    )
    assert resp.status_code == 403
    detail = resp.json().get("detail", "")
    assert "viamar" in detail
    assert "dentalnearyou" in detail


# ---------------------------------------------------------------------------
# 3. Summary returns counts
# ---------------------------------------------------------------------------


def test_summary_returns_counts(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three objectives across different states → counts tally correctly."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake = _FakeRedis(
        hashes={
            "sos:objectives:viamar:obj1": {
                "id": "obj1", "state": "open", "bounty_mind": "0",
                "updated_at": now_iso,
            },
            "sos:objectives:viamar:obj2": {
                "id": "obj2", "state": "claimed", "bounty_mind": "0",
                "updated_at": now_iso,
            },
            "sos:objectives:viamar:obj3": {
                "id": "obj3", "state": "paid", "bounty_mind": "150",
                "updated_at": now_iso,
            },
            # Index sets should be skipped
            "sos:objectives:viamar:children:obj1": {"not": "a hash"},
            "sos:objectives:viamar:open": {"not": "a hash"},
        }
    )

    monkeypatch.setattr(
        "sos.services.dashboard.operator_api.can_execute",
        _make_fake_can_execute(),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.dashboard.operator_api._get_redis",
        lambda: fake,
        raising=True,
    )

    resp = client.get(
        "/dashboard/tenants/viamar/summary",
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["project"] == "viamar"
    assert body["counts"]["open"] == 1
    assert body["counts"]["claimed"] == 1
    assert body["counts"]["paid"] == 1
    assert body["counts"]["shipped"] == 0
    assert body["mind_burn_24h"] == 150
    assert body["window_hours"] == 24


# ---------------------------------------------------------------------------
# 4. Agents returns card list
# ---------------------------------------------------------------------------


def test_agents_returns_cards(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """read_all_cards is called with the scoped project and serialized to JSON."""
    from sos.contracts.agent_card import AgentCard

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    card = AgentCard(
        identity_id="agent:demo",
        name="demo",
        role="executor",
        tool="claude-code",
        type="tmux",
        project="viamar",
        registered_at=now_iso,
        last_seen=now_iso,
    )

    captured: dict[str, Any] = {}

    def fake_read_all_cards(project: str | None = None):
        captured["project"] = project
        return [card]

    monkeypatch.setattr(
        "sos.services.dashboard.operator_api.can_execute",
        _make_fake_can_execute(),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.dashboard.operator_api.read_all_cards",
        fake_read_all_cards,
        raising=True,
    )

    resp = client.get(
        "/dashboard/tenants/viamar/agents",
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["project"] == "viamar"
    assert body["count"] == 1
    assert len(body["agents"]) == 1
    assert body["agents"][0]["name"] == "demo"
    assert captured["project"] == "viamar"


# ---------------------------------------------------------------------------
# 5. Kill: scoped token → 403
# ---------------------------------------------------------------------------


def test_kill_scoped_token_is_403(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scoped (non-system/non-admin) tokens must not be allowed to kill agents."""
    monkeypatch.setattr(
        "sos.services.dashboard.operator_api._auth_verify_bearer",
        _make_scoped_viamar_verify(),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.dashboard.operator_api.can_execute",
        _make_fake_can_execute(),
        raising=True,
    )

    resp = client.post(
        "/dashboard/agents/rogue/kill",
        headers={"Authorization": "Bearer scoped-viamar"},
    )
    assert resp.status_code == 403
    assert "system or admin" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# 6. Kill: system token sets the Redis key via kill_agent
# ---------------------------------------------------------------------------


def test_kill_system_token_sets_redis_key(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """System token → kill_agent invoked with the agent name, returns 200 + killed_until."""
    called: dict[str, Any] = {}

    def fake_kill_agent(name: str, *, ttl_seconds: int = 86400, redis_url=None) -> str:
        called["name"] = name
        called["ttl"] = ttl_seconds
        return "2026-04-19T20:00:00Z"

    # Swallow the audit emit so we don't need a live Redis for xadd.
    monkeypatch.setattr(
        "sos.services.dashboard.operator_api._emit_audit",
        lambda *a, **k: None,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.dashboard.operator_api.can_execute",
        _make_fake_can_execute(),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.dashboard.operator_api.kill_agent",
        fake_kill_agent,
        raising=True,
    )

    resp = client.post(
        "/dashboard/agents/naughty/kill",
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["agent"] == "naughty"
    assert body["killed_until"] == "2026-04-19T20:00:00Z"
    assert called["name"] == "naughty"


# ---------------------------------------------------------------------------
# 7. Kill emits an audit event
# ---------------------------------------------------------------------------


def test_kill_emits_audit_event(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kill must emit a ``dashboard.agent_killed`` envelope to the audit stream."""
    emitted: list[tuple[str, dict[str, Any]]] = []

    def fake_emit(event_type: str, payload: dict[str, Any]) -> None:
        emitted.append((event_type, payload))

    monkeypatch.setattr(
        "sos.services.dashboard.operator_api.can_execute",
        _make_fake_can_execute(),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.dashboard.operator_api.kill_agent",
        lambda name, **kw: "2026-04-19T20:00:00Z",
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.dashboard.operator_api._emit_audit",
        fake_emit,
        raising=True,
    )

    resp = client.post(
        "/dashboard/agents/rogue/kill",
        headers=_auth_headers(system_token),
    )
    assert resp.status_code == 200
    assert len(emitted) == 1
    event_type, payload = emitted[0]
    assert event_type == "dashboard.agent_killed"
    assert payload["agent"] == "rogue"
    assert payload["killed_until"] == "2026-04-19T20:00:00Z"
    assert "actor" in payload
    assert "tenant_id" in payload
    assert "ts" in payload
