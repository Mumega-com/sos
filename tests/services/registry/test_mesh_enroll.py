"""Tests for POST /mesh/enroll (phase3/W1).

Covers the 7 acceptance cases from the brief:
1. Missing bearer → 401.
2. System token + valid body → 200, enrolled=True, card round-trips with heartbeat_url.
3. Scoped token enrolls into own project → 200, project forced to token scope.
4. Scoped token tries to enroll into foreign project → 403.
5. Invalid name (e.g. "Bad Name") → 422.
6. Invalid role (e.g. "wizard") → 422.
7. Skills + squads round-trip: card reads back identically.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sos.contracts.agent_card import AgentCard
from sos.contracts.policy import PolicyDecision
from sos.kernel.auth import AuthContext
from sos.kernel.crypto import (
    canonical_payload_hash,
    enroll_message,
    generate_keypair,
    sign,
)
from sos.services.registry.app import app


# ---------------------------------------------------------------------------
# v0.9.2.1 — signed-enroll helpers (shared by all happy-path tests below)
# ---------------------------------------------------------------------------


def _install_signed_harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake nonce store + no-existing-identity so TOFU path returns 200.

    Returns a dict with ``priv_b64``, ``pub_b64`` for the caller to use
    when building bodies with ``_sign_enroll_body``.
    """
    state: dict[str, bool] = {}

    def fake_issue(agent_id: str, ttl_s: int = 60) -> tuple[str, int]:
        import secrets

        nonce = secrets.token_urlsafe(16)
        state[f"{agent_id}:{nonce}"] = True
        return nonce, 0

    def fake_consume(agent_id: str, nonce: str) -> bool:
        return state.pop(f"{agent_id}:{nonce}", False)

    monkeypatch.setattr(
        "sos.services.registry.app.nonce_store.issue", fake_issue, raising=True
    )
    monkeypatch.setattr(
        "sos.services.registry.app.nonce_store.consume", fake_consume, raising=True
    )
    monkeypatch.setattr(
        "sos.services.registry.app.read_one",
        lambda *_a, **_kw: None,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.registry.app.write",
        lambda *_a, **_kw: None,
        raising=True,
    )

    async def _noop_emit(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(
        "sos.services.registry.app._emit_first_seen", _noop_emit, raising=True
    )

    priv_b64, pub_b64 = generate_keypair()
    return {"priv_b64": priv_b64, "pub_b64": pub_b64, "state": state}


def _sign_enroll_body(
    client: TestClient,
    body: dict[str, Any],
    priv_b64: str,
    pub_b64: str,
) -> dict[str, Any]:
    """Fetch a challenge nonce and sign ``body`` in place. Returns the full body."""
    agent_id = body["agent_id"]
    ch = client.post("/mesh/challenge", json={"agent_id": agent_id})
    assert ch.status_code == 200
    nonce = ch.json()["nonce"]

    payload_hash = canonical_payload_hash(
        {
            "agent_id": agent_id,
            "name": body["name"],
            "role": body["role"],
            "skills": list(body.get("skills") or []),
            "squads": list(body.get("squads") or []),
            "public_key": pub_b64,
        }
    )
    sig = sign(priv_b64, enroll_message(agent_id, nonce, payload_hash))
    body["public_key"] = pub_b64
    body["nonce"] = nonce
    body["signature"] = sig
    return body

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Provide a TestClient; skip the startup service-registration path."""

    async def _noop() -> None:  # pragma: no cover
        return None

    monkeypatch.setattr("sos.services.registry.app._startup", _noop, raising=True)
    return TestClient(app)


@pytest.fixture
def system_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Install a system-scope Bearer token recognised by the real auth layer."""
    token = "test-sys-token-mesh-w1"
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", token)
    from sos.kernel.auth import get_cache

    get_cache().invalidate()
    return token


def _fake_system_verify(token_suffix: str) -> Any:
    """Return a fake verify_bearer that recognises a system token by suffix."""

    def _verify(authz: str | None) -> AuthContext | None:
        if authz and authz.endswith(token_suffix):
            return AuthContext(
                agent="sys-agent",
                project=None,
                tenant_slug=None,
                is_system=True,
                is_admin=False,
                label="system",
            )
        return None

    return _verify


def _fake_scoped_verify(project: str, token_suffix: str) -> Any:
    """Return a fake verify_bearer that recognises a scoped token by suffix."""

    def _verify(authz: str | None) -> AuthContext | None:
        if authz and authz.endswith(token_suffix):
            return AuthContext(
                agent="scoped-agent",
                project=project,
                tenant_slug="mumega",
                is_system=False,
                is_admin=False,
                label="scoped",
            )
        return None

    return _verify


async def _allow_gate(**_kwargs: Any) -> PolicyDecision:
    return PolicyDecision(
        allowed=True,
        reason="system/admin scope",
        tier="act_freely",
        action=_kwargs.get("action", ""),
        resource=_kwargs.get("resource", ""),
        agent="sys-agent",
        tenant="mumega",
        pillars_passed=["system/admin"],
        pillars_failed=[],
        capability_ok=None,
        metadata={},
    )


def _valid_body(**overrides: Any) -> dict[str, Any]:
    base = {
        "agent_id": "agent:test-agent",
        "name": "test-agent",
        "role": "executor",
        "skills": [],
        "squads": [],
        "heartbeat_url": None,
        "project": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Case 1 — missing bearer → 401
# ---------------------------------------------------------------------------


def test_missing_bearer_returns_401(client: TestClient) -> None:
    resp = client.post("/mesh/enroll", json=_valid_body())
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Case 2 — system token + valid body → 200, card round-trips with heartbeat_url
# ---------------------------------------------------------------------------


def test_system_token_valid_body_returns_200(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: dict[str, Any] = {}

    def fake_write_card(
        card: AgentCard, project: str | None = None, ttl_seconds: int = 300
    ) -> None:
        stored["card"] = card
        stored["project"] = project

    def fake_read_card(agent_name: str, project: str | None = None) -> AgentCard | None:
        return stored.get("card")

    token_suffix = "sys-mesh-token"
    monkeypatch.setattr(
        "sos.services.registry.app._auth_verify_bearer",
        _fake_system_verify(token_suffix),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.kernel.policy.gate.verify_bearer",
        _fake_system_verify(token_suffix),
        raising=True,
    )
    monkeypatch.setattr("sos.services.registry.app.can_execute", _allow_gate, raising=True)
    monkeypatch.setattr("sos.services.registry.app.write_card", fake_write_card, raising=True)
    monkeypatch.setattr("sos.services.registry.app.read_card", fake_read_card, raising=True)
    harness = _install_signed_harness(monkeypatch)

    body = _sign_enroll_body(
        client,
        _valid_body(heartbeat_url="https://example.com/hb"),
        harness["priv_b64"],
        harness["pub_b64"],
    )
    resp = client.post(
        "/mesh/enroll",
        json=body,
        headers={"Authorization": f"Bearer {token_suffix}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["enrolled"] is True
    assert data["name"] == "test-agent"
    assert data["stale_after"] == 300
    assert data["expires_in"] == 900

    card = stored["card"]
    assert card.heartbeat_url == "https://example.com/hb"
    assert card.tool == "service"
    assert card.type == "service"


# ---------------------------------------------------------------------------
# Case 3 — scoped token enrolls into own project → 200, project forced to scope
# ---------------------------------------------------------------------------


def test_scoped_token_own_project_returns_200(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: dict[str, Any] = {}

    def fake_write_card(
        card: AgentCard, project: str | None = None, ttl_seconds: int = 300
    ) -> None:
        stored["card"] = card
        stored["project"] = project

    token_suffix = "scoped-own-project"
    scoped_project = "acme"

    monkeypatch.setattr(
        "sos.services.registry.app._auth_verify_bearer",
        _fake_scoped_verify(scoped_project, token_suffix),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.kernel.policy.gate.verify_bearer",
        _fake_scoped_verify(scoped_project, token_suffix),
        raising=True,
    )
    monkeypatch.setattr("sos.services.registry.app.can_execute", _allow_gate, raising=True)
    monkeypatch.setattr("sos.services.registry.app.write_card", fake_write_card, raising=True)
    harness = _install_signed_harness(monkeypatch)

    # Scoped token; body.project matches token scope
    body = _sign_enroll_body(
        client,
        _valid_body(project=scoped_project),
        harness["priv_b64"],
        harness["pub_b64"],
    )
    resp = client.post(
        "/mesh/enroll",
        json=body,
        headers={"Authorization": f"Bearer {token_suffix}"},
    )
    assert resp.status_code == 200
    assert resp.json()["project"] == scoped_project
    assert stored["project"] == scoped_project


# ---------------------------------------------------------------------------
# Case 4 — scoped token tries to enroll into foreign project → 403
# ---------------------------------------------------------------------------


def test_scoped_token_foreign_project_returns_403(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_suffix = "scoped-foreign-project"
    scoped_project = "acme"

    monkeypatch.setattr(
        "sos.services.registry.app._auth_verify_bearer",
        _fake_scoped_verify(scoped_project, token_suffix),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.kernel.policy.gate.verify_bearer",
        _fake_scoped_verify(scoped_project, token_suffix),
        raising=True,
    )
    monkeypatch.setattr("sos.services.registry.app.can_execute", _allow_gate, raising=True)

    # body.project differs from token scope
    body = _valid_body(project="other-project")
    resp = client.post(
        "/mesh/enroll",
        json=body,
        headers={"Authorization": f"Bearer {token_suffix}"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Case 5 — invalid name → 422
# ---------------------------------------------------------------------------


def test_invalid_name_returns_422(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_suffix = "sys-mesh-invalid-name"
    monkeypatch.setattr(
        "sos.services.registry.app._auth_verify_bearer",
        _fake_system_verify(token_suffix),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.kernel.policy.gate.verify_bearer",
        _fake_system_verify(token_suffix),
        raising=True,
    )
    monkeypatch.setattr("sos.services.registry.app.can_execute", _allow_gate, raising=True)
    harness = _install_signed_harness(monkeypatch)

    body = _sign_enroll_body(
        client,
        _valid_body(name="Bad Name", agent_id="agent:bad-name"),
        harness["priv_b64"],
        harness["pub_b64"],
    )
    resp = client.post(
        "/mesh/enroll",
        json=body,
        headers={"Authorization": f"Bearer {token_suffix}"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Case 6 — invalid role → 422
# ---------------------------------------------------------------------------


def test_invalid_role_returns_422(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_suffix = "sys-mesh-invalid-role"
    monkeypatch.setattr(
        "sos.services.registry.app._auth_verify_bearer",
        _fake_system_verify(token_suffix),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.kernel.policy.gate.verify_bearer",
        _fake_system_verify(token_suffix),
        raising=True,
    )
    monkeypatch.setattr("sos.services.registry.app.can_execute", _allow_gate, raising=True)
    harness = _install_signed_harness(monkeypatch)

    body = _sign_enroll_body(
        client,
        _valid_body(role="wizard"),
        harness["priv_b64"],
        harness["pub_b64"],
    )
    resp = client.post(
        "/mesh/enroll",
        json=body,
        headers={"Authorization": f"Bearer {token_suffix}"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Case 7 — skills + squads round-trip
# ---------------------------------------------------------------------------


def test_skills_and_squads_roundtrip(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: dict[str, Any] = {}

    def fake_write_card(
        card: AgentCard, project: str | None = None, ttl_seconds: int = 300
    ) -> None:
        stored["card"] = card

    token_suffix = "sys-mesh-roundtrip"
    monkeypatch.setattr(
        "sos.services.registry.app._auth_verify_bearer",
        _fake_system_verify(token_suffix),
        raising=True,
    )
    monkeypatch.setattr(
        "sos.kernel.policy.gate.verify_bearer",
        _fake_system_verify(token_suffix),
        raising=True,
    )
    monkeypatch.setattr("sos.services.registry.app.can_execute", _allow_gate, raising=True)
    monkeypatch.setattr("sos.services.registry.app.write_card", fake_write_card, raising=True)
    harness = _install_signed_harness(monkeypatch)

    body = _sign_enroll_body(
        client,
        _valid_body(skills=["x", "y"], squads=["growth-intel"]),
        harness["priv_b64"],
        harness["pub_b64"],
    )
    resp = client.post(
        "/mesh/enroll",
        json=body,
        headers={"Authorization": f"Bearer {token_suffix}"},
    )
    assert resp.status_code == 200
    card: AgentCard = stored["card"]
    assert card.skills == ["x", "y"]
    assert card.squads == ["growth-intel"]


# ---------------------------------------------------------------------------
# TestMeshSquadResolve — GET /mesh/squad/{slug}  (phase3/W2)
# ---------------------------------------------------------------------------


def _make_card(name: str, squads: list[str], project: str | None = None) -> AgentCard:
    """Build a minimal AgentCard for use in resolve tests."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    return AgentCard(
        identity_id=f"agent:{name}",
        name=name,
        role="executor",
        skills=[],
        squads=squads,
        project=project,
        tool="service",
        type="service",
        registered_at=now,
        last_seen=now,
    )


class TestMeshSquadResolve:
    """Acceptance cases for GET /mesh/squad/{slug}."""

    # ------------------------------------------------------------------
    # Case 1 — missing bearer → 401
    # ------------------------------------------------------------------

    def test_missing_bearer_returns_401(self, client: TestClient) -> None:
        resp = client.get("/mesh/squad/growth-intel")
        assert resp.status_code == 401

    # ------------------------------------------------------------------
    # Case 2 — system token, no cards enrolled → 200, empty list
    # ------------------------------------------------------------------

    def test_no_cards_returns_empty(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        token_suffix = "sys-resolve-empty"
        monkeypatch.setattr(
            "sos.services.registry.app._auth_verify_bearer",
            _fake_system_verify(token_suffix),
            raising=True,
        )
        monkeypatch.setattr(
            "sos.kernel.policy.gate.verify_bearer",
            _fake_system_verify(token_suffix),
            raising=True,
        )
        monkeypatch.setattr("sos.services.registry.app.can_execute", _allow_gate, raising=True)
        monkeypatch.setattr(
            "sos.services.registry.app.read_all_cards",
            lambda project=None: [],
            raising=True,
        )

        resp = client.get(
            "/mesh/squad/growth-intel",
            headers={"Authorization": f"Bearer {token_suffix}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["slug"] == "growth-intel"
        assert data["agents"] == []
        assert data["count"] == 0

    # ------------------------------------------------------------------
    # Case 3 — 3 cards seeded; 2 match → 2 returned
    # ------------------------------------------------------------------

    def test_filter_by_squad_returns_matching_cards(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        token_suffix = "sys-resolve-filter"
        cards = [
            _make_card("alpha", squads=["growth-intel"]),
            _make_card("beta", squads=["growth-intel", "other-squad"]),
            _make_card("gamma", squads=["other-squad"]),
        ]
        monkeypatch.setattr(
            "sos.services.registry.app._auth_verify_bearer",
            _fake_system_verify(token_suffix),
            raising=True,
        )
        monkeypatch.setattr(
            "sos.kernel.policy.gate.verify_bearer",
            _fake_system_verify(token_suffix),
            raising=True,
        )
        monkeypatch.setattr("sos.services.registry.app.can_execute", _allow_gate, raising=True)
        monkeypatch.setattr(
            "sos.services.registry.app.read_all_cards",
            lambda project=None: cards,
            raising=True,
        )

        resp = client.get(
            "/mesh/squad/growth-intel",
            headers={"Authorization": f"Bearer {token_suffix}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        names = {a["name"] for a in data["agents"]}
        assert names == {"alpha", "beta"}

    # ------------------------------------------------------------------
    # Case 4 — scoped token sees only its own project's cards
    # ------------------------------------------------------------------

    def test_scoped_token_project_isolation(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        token_suffix = "scoped-resolve-isolation"
        scoped_project = "proj-a"

        cards_by_project: dict[str | None, list[AgentCard]] = {
            "proj-a": [_make_card("agent-a", squads=["growth-intel"], project="proj-a")],
            "proj-b": [_make_card("agent-b", squads=["growth-intel"], project="proj-b")],
        }

        def fake_read_all_cards(project: str | None = None) -> list[AgentCard]:
            return cards_by_project.get(project, [])

        monkeypatch.setattr(
            "sos.services.registry.app._auth_verify_bearer",
            _fake_scoped_verify(scoped_project, token_suffix),
            raising=True,
        )
        monkeypatch.setattr(
            "sos.kernel.policy.gate.verify_bearer",
            _fake_scoped_verify(scoped_project, token_suffix),
            raising=True,
        )
        monkeypatch.setattr("sos.services.registry.app.can_execute", _allow_gate, raising=True)
        monkeypatch.setattr(
            "sos.services.registry.app.read_all_cards",
            fake_read_all_cards,
            raising=True,
        )

        resp = client.get(
            "/mesh/squad/growth-intel",
            headers={"Authorization": f"Bearer {token_suffix}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["agents"][0]["name"] == "agent-a"
        assert data["project"] == scoped_project

    # ------------------------------------------------------------------
    # Case 5 — slug with no matching cards → 200, empty list
    # ------------------------------------------------------------------

    def test_unknown_squad_returns_empty(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        token_suffix = "sys-resolve-nomatch"
        cards = [_make_card("alpha", squads=["other-squad"])]
        monkeypatch.setattr(
            "sos.services.registry.app._auth_verify_bearer",
            _fake_system_verify(token_suffix),
            raising=True,
        )
        monkeypatch.setattr(
            "sos.kernel.policy.gate.verify_bearer",
            _fake_system_verify(token_suffix),
            raising=True,
        )
        monkeypatch.setattr("sos.services.registry.app.can_execute", _allow_gate, raising=True)
        monkeypatch.setattr(
            "sos.services.registry.app.read_all_cards",
            lambda project=None: cards,
            raising=True,
        )

        resp = client.get(
            "/mesh/squad/no-such-squad",
            headers={"Authorization": f"Bearer {token_suffix}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agents"] == []
        assert data["count"] == 0

    # ------------------------------------------------------------------
    # Case 6 — invalid slug format → 422 (FastAPI path validation)
    # ------------------------------------------------------------------

    def test_invalid_slug_format_returns_422(self, client: TestClient) -> None:
        # URL-encode a space to produce "has%20spaces" as the path segment.
        resp = client.get(
            "/mesh/squad/has%20spaces",
            headers={"Authorization": "Bearer some-token"},
        )
        assert resp.status_code == 422
