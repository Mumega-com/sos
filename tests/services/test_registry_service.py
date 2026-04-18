"""Tests for the SOS Registry HTTP service (``sos/services/registry/app.py``).

Covers:
- GET /health returns 200 + canonical shape
- GET /agents returns ``{agents: [...], count: N}`` with patched ``read_all``
- GET /agents/{agent_id} returns a single record / 404
- Auth: missing bearer is 401, cross-project scoped token is 403
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sos.kernel.identity import AgentIdentity
from sos.services.registry.app import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Provide a TestClient; skip the startup service-registration path
    which would otherwise require Redis."""
    async def _noop() -> None:  # pragma: no cover — fixture setup
        return None

    monkeypatch.setattr(
        "sos.services.registry.app._startup",
        _noop,
        raising=True,
    )
    return TestClient(app)


@pytest.fixture
def system_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Install a system-scope Bearer token usable for any project."""
    token = "test-sys-token-p0-09"
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", token)
    from sos.kernel.auth import get_cache

    get_cache().invalidate()
    return token


def _make_agent(name: str, caps: list[str] | None = None) -> AgentIdentity:
    ident = AgentIdentity(name=name, model="gemini")
    ident.capabilities = list(caps or [])
    return ident


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_returns_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "registry"
    assert body["status"] in {"ok", "degraded"}
    assert "version" in body
    assert "uptime_seconds" in body


# ---------------------------------------------------------------------------
# /agents — auth
# ---------------------------------------------------------------------------


def test_list_agents_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.get("/agents")
    assert resp.status_code == 401


def test_list_agents_invalid_bearer_is_401(client: TestClient) -> None:
    resp = client.get(
        "/agents",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /agents — happy path
# ---------------------------------------------------------------------------


def test_list_agents_returns_count_and_items(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents = [
        _make_agent("alpha", ["python", "sql"]),
        _make_agent("beta", ["typescript"]),
    ]

    def fake_read_all(project: str | None = None) -> list[AgentIdentity]:
        return agents

    monkeypatch.setattr(
        "sos.services.registry.app.read_all", fake_read_all, raising=True
    )

    resp = client.get(
        "/agents",
        headers={"Authorization": f"Bearer {system_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    names = sorted(a["name"] for a in body["agents"])
    assert names == ["alpha", "beta"]


def test_list_agents_empty_is_zero_count(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sos.services.registry.app.read_all",
        lambda project=None: [],
        raising=True,
    )
    resp = client.get(
        "/agents",
        headers={"Authorization": f"Bearer {system_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"agents": [], "count": 0}


# ---------------------------------------------------------------------------
# /agents — cross-project scope rejection
# ---------------------------------------------------------------------------


def test_cross_project_scoped_token_is_403(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scoped token that explicitly asks for a different project → 403."""
    from sos.kernel.auth import AuthContext

    def fake_verify(authz: str | None) -> AuthContext | None:
        if authz and authz.endswith("scoped-token-viamar"):
            return AuthContext(
                agent="viamar-agent",
                project="viamar",
                tenant_slug="viamar",
                is_system=False,
                is_admin=False,
                label="scoped",
            )
        return None

    monkeypatch.setattr(
        "sos.services.registry.app._auth_verify_bearer",
        fake_verify,
        raising=True,
    )

    resp = client.get(
        "/agents?project=dentalnearyou",
        headers={"Authorization": "Bearer scoped-token-viamar"},
    )
    assert resp.status_code == 403
    assert "viamar" in resp.json().get("detail", "")


def test_scoped_token_without_explicit_project_is_forced_to_scope(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scoped token with no ?project= arg still filters to its own scope."""
    from sos.kernel.auth import AuthContext

    observed: dict[str, Any] = {}

    def fake_verify(authz: str | None) -> AuthContext | None:
        if authz and authz.endswith("scoped-token-viamar"):
            return AuthContext(
                agent="viamar-agent",
                project="viamar",
                tenant_slug="viamar",
                is_system=False,
                is_admin=False,
                label="scoped",
            )
        return None

    def fake_read_all(project: str | None = None) -> list[AgentIdentity]:
        observed["project"] = project
        return []

    monkeypatch.setattr(
        "sos.services.registry.app._auth_verify_bearer",
        fake_verify,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.registry.app.read_all",
        fake_read_all,
        raising=True,
    )

    resp = client.get(
        "/agents",
        headers={"Authorization": "Bearer scoped-token-viamar"},
    )
    assert resp.status_code == 200
    assert observed["project"] == "viamar"


# ---------------------------------------------------------------------------
# /agents/{agent_id}
# ---------------------------------------------------------------------------


def test_get_agent_returns_record(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _make_agent("alpha", ["python"])

    def fake_read_one(agent_id: str, project: str | None = None) -> AgentIdentity | None:
        if agent_id == "alpha":
            return agent
        return None

    monkeypatch.setattr(
        "sos.services.registry.app.read_one", fake_read_one, raising=True
    )

    resp = client.get(
        "/agents/alpha",
        headers={"Authorization": f"Bearer {system_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "alpha"


def test_get_agent_missing_returns_404(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sos.services.registry.app.read_one",
        lambda agent_id, project=None: None,
        raising=True,
    )
    resp = client.get(
        "/agents/nobody",
        headers={"Authorization": f"Bearer {system_token}"},
    )
    assert resp.status_code == 404
