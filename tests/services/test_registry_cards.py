"""Tests for the v0.7.2 AgentCard routes on the registry service.

Mirrors test_registry_service.py's patching pattern: monkeypatches
``read_all_cards`` / ``read_card`` at the app module level so no live
Redis is required. Cross-project scope enforcement is verified end to
end because AgentCard is the surface Inkwell will consume — a scope
leak here leaks tenant boundaries to an operator UI.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sos.contracts.agent_card import AgentCard
from sos.services.registry.app import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
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
    token = "test-sys-token-cards"
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", token)
    from sos.kernel.auth import get_cache

    get_cache().invalidate()
    return token


def _make_card(name: str, *, project: str | None = None) -> AgentCard:
    return AgentCard(
        identity_id=f"agent:{name}",
        name=name,
        role="executor",
        tool="claude-code",
        type="tmux",
        session=name,
        warm_policy="warm",
        cache_ttl_s=300,
        project=project,
        registered_at=AgentCard.now_iso(),
        last_seen=AgentCard.now_iso(),
    )


# ---------------------------------------------------------------------------
# Auth — 401 without bearer
# ---------------------------------------------------------------------------


def test_list_cards_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.get("/agents/cards")
    assert resp.status_code == 401


def test_get_card_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.get("/agents/cards/kasra")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Happy path — empty + populated
# ---------------------------------------------------------------------------


def test_list_cards_empty_returns_zero(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sos.services.registry.app.read_all_cards",
        lambda project=None: [],
        raising=True,
    )
    resp = client.get(
        "/agents/cards",
        headers={"Authorization": f"Bearer {system_token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"cards": [], "count": 0}


def test_list_cards_returns_full_payload(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cards = [_make_card("kasra", project="mumega"), _make_card("river")]

    def fake_read_all_cards(project: str | None = None) -> list[AgentCard]:
        return cards

    monkeypatch.setattr(
        "sos.services.registry.app.read_all_cards",
        fake_read_all_cards,
        raising=True,
    )

    resp = client.get(
        "/agents/cards",
        headers={"Authorization": f"Bearer {system_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    names = sorted(c["name"] for c in body["cards"])
    assert names == ["kasra", "river"]

    # Runtime overlay fields survive the round-trip — this is why the
    # endpoint exists at all, over and above the soul-level /agents.
    kasra = next(c for c in body["cards"] if c["name"] == "kasra")
    assert kasra["warm_policy"] == "warm"
    assert kasra["tool"] == "claude-code"
    assert kasra["identity_id"] == "agent:kasra"


# ---------------------------------------------------------------------------
# Single card — happy path + 404
# ---------------------------------------------------------------------------


def test_get_card_returns_record(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = _make_card("kasra")

    def fake_read_card(name: str, project: str | None = None) -> AgentCard | None:
        return card if name == "kasra" else None

    monkeypatch.setattr(
        "sos.services.registry.app.read_card",
        fake_read_card,
        raising=True,
    )
    resp = client.get(
        "/agents/cards/kasra",
        headers={"Authorization": f"Bearer {system_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "kasra"
    assert body["identity_id"] == "agent:kasra"


def test_get_card_missing_returns_404(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sos.services.registry.app.read_card",
        lambda name, project=None: None,
        raising=True,
    )
    resp = client.get(
        "/agents/cards/nobody",
        headers={"Authorization": f"Bearer {system_token}"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Scope — cross-project rejected, scoped token forced to own project
# ---------------------------------------------------------------------------


def test_list_cards_cross_project_scoped_token_is_403(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sos.kernel.auth import AuthContext

    def fake_verify(authz: str | None) -> AuthContext | None:
        if authz and authz.endswith("scoped-viamar"):
            return AuthContext(
                agent="viamar-agent",
                project="viamar",
                tenant_slug="mumega",
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
    monkeypatch.setattr(
        "sos.kernel.policy.gate.verify_bearer",
        fake_verify,
        raising=True,
    )

    resp = client.get(
        "/agents/cards?project=dentalnearyou",
        headers={"Authorization": "Bearer scoped-viamar"},
    )
    assert resp.status_code == 403
    assert "viamar" in resp.json().get("detail", "")


def test_list_cards_scoped_token_forced_to_own_project(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sos.contracts.policy import PolicyDecision
    from sos.kernel.auth import AuthContext

    observed: dict[str, Any] = {}

    def fake_verify(authz: str | None) -> AuthContext | None:
        if authz and authz.endswith("scoped-viamar"):
            return AuthContext(
                agent="viamar-agent",
                project="viamar",
                tenant_slug="mumega",
                is_system=False,
                is_admin=False,
                label="scoped",
            )
        return None

    def fake_read_all_cards(project: str | None = None) -> list[AgentCard]:
        observed["project"] = project
        return []

    async def fake_can_execute(**kwargs: Any) -> PolicyDecision:
        return PolicyDecision(
            allowed=True,
            reason="test: gate bypassed",
            tier="act_freely",
            action=kwargs.get("action", ""),
            resource=kwargs.get("resource", ""),
            agent="viamar-agent",
            tenant="mumega",
            pillars_passed=["tenant_scope"],
            pillars_failed=[],
            capability_ok=None,
            metadata={},
        )

    monkeypatch.setattr(
        "sos.services.registry.app._auth_verify_bearer",
        fake_verify,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.registry.app.can_execute",
        fake_can_execute,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.registry.app.read_all_cards",
        fake_read_all_cards,
        raising=True,
    )

    resp = client.get(
        "/agents/cards",
        headers={"Authorization": "Bearer scoped-viamar"},
    )
    assert resp.status_code == 200
    assert observed["project"] == "viamar"


# ---------------------------------------------------------------------------
# Route ordering — ``cards`` must not be captured as an ``agent_id``
# ---------------------------------------------------------------------------


def test_cards_route_not_shadowed_by_agent_id(
    client: TestClient,
    system_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GET /agents/cards`` must hit list_agent_cards, not get_agent('cards').

    Regression guard for the FastAPI ordering subtlety: if the
    ``/agents/{agent_id}`` route were declared first, ``cards`` would be
    captured as an agent_id and the card endpoint would be unreachable.
    """
    called: dict[str, bool] = {"cards": False, "one": False}

    def fake_read_all_cards(project: str | None = None) -> list[AgentCard]:
        called["cards"] = True
        return []

    def fake_read_one(agent_id: str, project: str | None = None):
        called["one"] = True
        return None

    monkeypatch.setattr(
        "sos.services.registry.app.read_all_cards",
        fake_read_all_cards,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.registry.app.read_one",
        fake_read_one,
        raising=True,
    )
    resp = client.get(
        "/agents/cards",
        headers={"Authorization": f"Bearer {system_token}"},
    )
    assert resp.status_code == 200
    assert called["cards"] is True
    assert called["one"] is False
