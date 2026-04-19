"""Tests for GET /sos/mesh (HTML) + GET /sos/mesh/api (JSON) — phase3/W5.

Cases:
1. /sos/mesh/api with valid bearer, 0 cards → 200, empty payload.
2. /sos/mesh/api with 3 cards seeded (2 in squad, 1 unsquadded) → correct grouping.
3. /sos/mesh/api without bearer → 401.
4. /sos/mesh without cookie → 302 redirect to login.
5. /sos/mesh with admin cookie → 200, contains "Mesh" heading + agent name.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sos.contracts.agent_card import AgentCard
from sos.kernel.auth import AuthContext
from sos.services.dashboard.routes.sos_mesh import router

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_card(name: str, squads: list[str], stale: bool = False) -> AgentCard:
    now = AgentCard.now_iso()
    return AgentCard(
        identity_id=f"agent:{name}",
        name=name,
        role="executor",
        tool="sdk",
        type="service",
        squads=squads,
        registered_at=now,
        last_seen=now,
        stale=stale,
    )


def _fake_system_verify(suffix: str) -> Any:
    def _v(authz: str | None) -> AuthContext | None:
        if authz and authz.endswith(suffix):
            return AuthContext(
                agent="sys",
                project=None,
                tenant_slug=None,
                is_system=True,
                is_admin=False,
                label="system",
            )
        return None

    return _v


def _admin_cookie(token: str) -> str:
    """Produce the JSON cookie value the dashboard auth layer expects."""
    return json.dumps({"token": token, "project": None, "is_admin": True})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    _app = FastAPI()
    _app.include_router(router)
    return TestClient(_app, raise_server_exceptions=True)


@pytest.fixture
def system_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-mesh-dashboard-tok"
    monkeypatch.setattr(
        "sos.services.dashboard.routes.sos_mesh.verify_bearer",
        _fake_system_verify(token),
        raising=True,
    )
    return token


@pytest.fixture
def admin_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-mesh-admin-tok"

    def _fake_tenant(cookie_val: str | None) -> dict[str, Any] | None:
        if cookie_val:
            try:
                data = json.loads(cookie_val)
                if data.get("token") == token:
                    return {"token": token, "project": None, "is_admin": True}
            except Exception:
                pass
        return None

    monkeypatch.setattr(
        "sos.services.dashboard.routes.sos_mesh._tenant_from_cookie",
        _fake_tenant,
        raising=True,
    )
    return token


# ---------------------------------------------------------------------------
# Case 1 — valid bearer, 0 cards → 200 with empty payload
# ---------------------------------------------------------------------------


def test_api_empty_cards(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sos.services.dashboard.routes.sos_mesh.read_all_cards",
        lambda **_kw: [],
        raising=True,
    )
    resp = client.get("/sos/mesh/api", headers={"Authorization": f"Bearer {system_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["squads"] == {}
    assert body["unsquadded"] == []
    assert body["total"] == 0
    assert "generated_at" in body


# ---------------------------------------------------------------------------
# Case 2 — 3 cards seeded: 2 in "growth-intel", 1 unsquadded
# ---------------------------------------------------------------------------


def test_api_squad_grouping(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cards = [
        _make_card("alpha", squads=["growth-intel"]),
        _make_card("beta", squads=["growth-intel"]),
        _make_card("gamma", squads=[]),
    ]
    monkeypatch.setattr(
        "sos.services.dashboard.routes.sos_mesh.read_all_cards",
        lambda **_kw: cards,
        raising=True,
    )
    resp = client.get("/sos/mesh/api", headers={"Authorization": f"Bearer {system_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert "growth-intel" in body["squads"]
    assert len(body["squads"]["growth-intel"]) == 2
    assert len(body["unsquadded"]) == 1
    assert body["unsquadded"][0]["name"] == "gamma"


# ---------------------------------------------------------------------------
# Case 3 — no bearer → 401
# ---------------------------------------------------------------------------


def test_api_no_bearer_returns_401(client: TestClient) -> None:
    resp = client.get("/sos/mesh/api")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Case 4 — HTML without cookie → 302 redirect to login
# ---------------------------------------------------------------------------


def test_html_no_cookie_redirects(client: TestClient) -> None:
    resp = client.get("/sos/mesh", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Case 5 — HTML with admin cookie → 200, "Mesh" heading + agent name
# ---------------------------------------------------------------------------


def test_html_admin_sees_mesh_page(
    client: TestClient,
    admin_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cards = [_make_card("my-agent", squads=["ops"])]
    monkeypatch.setattr(
        "sos.services.dashboard.routes.sos_mesh.read_all_cards",
        lambda **_kw: cards,
        raising=True,
    )
    cookie_val = json.dumps({"token": admin_token, "project": None, "is_admin": True})
    resp = client.get("/sos/mesh", cookies={"mum_dash": cookie_val})
    assert resp.status_code == 200
    body = resp.text
    assert "Mesh" in body
    assert "my-agent" in body
