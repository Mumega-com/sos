"""Contract tests for v0.9.2.1 signed /mesh/enroll.

Six acceptance cases from docs/plans/2026-04-19-mesh-security-wave.md S6:
1. Missing sig → 401 (challenge gate).
2. Bad sig with fresh nonce → 403.
3. Replayed nonce → 403 on the second attempt.
4. TOFU first enroll with valid sig → 200, first_seen=True, identity persisted.
5. Re-enroll with mismatched public_key → 409 SECURITY audit.
6. Re-enroll with matching public_key → 200, first_seen=False.

These tests monkeypatch the nonce store + registry write helpers so they
run without Redis. End-to-end redis tests live in tests/test_mesh_enroll_e2e.py.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

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
from sos.kernel.identity import AgentIdentity, VerificationStatus
from sos.services.registry.app import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def _noop() -> None:
        return None

    monkeypatch.setattr("sos.services.registry.app._startup", _noop, raising=True)
    return TestClient(app)


@pytest.fixture
def keypair() -> tuple[str, str]:
    """Fresh keypair per test so identities don't leak between cases."""
    return generate_keypair()


def _fake_system_verify(token_suffix: str) -> Any:
    def _verify(authz: str | None) -> AuthContext | None:
        if authz and authz.endswith(token_suffix):
            return AuthContext(
                agent="sys-agent",
                project=None,
                tenant_slug=None,
                is_system=True,
                is_admin=True,
                label="system",
            )
        return None

    return _verify


async def _allow_gate(**kwargs: Any) -> PolicyDecision:
    return PolicyDecision(
        allowed=True,
        reason="system/admin scope",
        tier="act_freely",
        action=kwargs.get("action", ""),
        resource=kwargs.get("resource", ""),
        agent="sys-agent",
        tenant="mumega",
        pillars_passed=["system/admin"],
        pillars_failed=[],
        capability_ok=None,
        metadata={},
    )


def _install_auth(
    monkeypatch: pytest.MonkeyPatch, token_suffix: str = "sys-signed"
) -> str:
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
    monkeypatch.setattr(
        "sos.services.registry.app.can_execute", _allow_gate, raising=True
    )
    return token_suffix


def _install_nonce_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """In-memory nonce store for tests. Returns the underlying state dict."""
    state: dict[str, bool] = {}

    def fake_issue(agent_id: str, ttl_s: int = 60) -> tuple[str, int]:
        import secrets

        nonce = secrets.token_urlsafe(16)
        state[f"{agent_id}:{nonce}"] = True
        return nonce, 0

    def fake_consume(agent_id: str, nonce: str) -> bool:
        key = f"{agent_id}:{nonce}"
        return state.pop(key, False)

    monkeypatch.setattr(
        "sos.services.registry.app.nonce_store.issue", fake_issue, raising=True
    )
    monkeypatch.setattr(
        "sos.services.registry.app.nonce_store.consume", fake_consume, raising=True
    )
    return {"state": state}


def _install_registry_io(
    monkeypatch: pytest.MonkeyPatch,
    existing: Optional[AgentIdentity] = None,
) -> dict[str, Any]:
    """Fake registry read/write. Returns the stash for assertions."""
    stash: dict[str, Any] = {"identity": existing, "card": None}

    def fake_read_one(agent_id: str, project: Optional[str] = None) -> Optional[AgentIdentity]:
        return stash["identity"]

    def fake_write(ident: AgentIdentity, project: Optional[str] = None, ttl_seconds: int = 0) -> None:
        stash["identity"] = ident

    def fake_write_card(card: AgentCard, project: Optional[str] = None, ttl_seconds: int = 300) -> None:
        stash["card"] = card

    async def fake_emit(*args: Any, **kwargs: Any) -> None:
        stash["first_seen_emitted"] = True

    monkeypatch.setattr("sos.services.registry.app.read_one", fake_read_one, raising=True)
    monkeypatch.setattr("sos.services.registry.app.write", fake_write, raising=True)
    monkeypatch.setattr(
        "sos.services.registry.app.write_card", fake_write_card, raising=True
    )
    monkeypatch.setattr(
        "sos.services.registry.app._emit_first_seen", fake_emit, raising=True
    )
    return stash


def _sign_body(
    priv_b64: str,
    pub_b64: str,
    nonce: str,
    *,
    agent_id: str = "agent:test-signed",
    name: str = "test-signed",
    role: str = "executor",
    skills: Optional[list[str]] = None,
    squads: Optional[list[str]] = None,
    heartbeat_url: Optional[str] = None,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    skills = skills or []
    squads = squads or []
    payload_hash = canonical_payload_hash(
        {
            "agent_id": agent_id,
            "name": name,
            "role": role,
            "skills": skills,
            "squads": squads,
            "public_key": pub_b64,
        }
    )
    sig = sign(priv_b64, enroll_message(agent_id, nonce, payload_hash))
    return {
        "agent_id": agent_id,
        "name": name,
        "role": role,
        "skills": skills,
        "squads": squads,
        "heartbeat_url": heartbeat_url,
        "project": project,
        "public_key": pub_b64,
        "nonce": nonce,
        "signature": sig,
    }


# ---------------------------------------------------------------------------
# Case 1 — Missing signature fields → 401
# ---------------------------------------------------------------------------


def test_missing_signature_returns_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _install_auth(monkeypatch)
    body = {
        "agent_id": "agent:nosig",
        "name": "nosig",
        "role": "executor",
        "skills": [],
        "squads": [],
    }
    resp = client.post(
        "/mesh/enroll", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401
    assert "signed enrollment" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Case 2 — Bad signature with fresh nonce → 403
# ---------------------------------------------------------------------------


def test_bad_signature_returns_403(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    keypair: tuple[str, str],
) -> None:
    token = _install_auth(monkeypatch)
    _install_nonce_store(monkeypatch)
    _install_registry_io(monkeypatch)
    priv_b64, pub_b64 = keypair

    # Issue a nonce via the challenge endpoint (goes through the fake store).
    ch = client.post("/mesh/challenge", json={"agent_id": "agent:badsig"})
    assert ch.status_code == 200
    nonce = ch.json()["nonce"]

    body = _sign_body(priv_b64, pub_b64, nonce, agent_id="agent:badsig", name="badsig")
    # Corrupt the signature.
    body["signature"] = "A" * len(body["signature"])

    resp = client.post(
        "/mesh/enroll", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403
    assert "signature" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Case 3 — Replayed nonce → 403 on second attempt
# ---------------------------------------------------------------------------


def test_replayed_nonce_returns_403(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    keypair: tuple[str, str],
) -> None:
    token = _install_auth(monkeypatch)
    _install_nonce_store(monkeypatch)
    _install_registry_io(monkeypatch)
    priv_b64, pub_b64 = keypair

    ch = client.post("/mesh/challenge", json={"agent_id": "agent:replay"})
    nonce = ch.json()["nonce"]

    body = _sign_body(priv_b64, pub_b64, nonce, agent_id="agent:replay", name="replay")
    first = client.post(
        "/mesh/enroll", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert first.status_code == 200

    second = client.post(
        "/mesh/enroll", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert second.status_code == 403
    assert "nonce" in second.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Case 4 — TOFU first enroll with valid sig → 200, first_seen=True
# ---------------------------------------------------------------------------


def test_tofu_first_enroll_returns_200(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    keypair: tuple[str, str],
) -> None:
    token = _install_auth(monkeypatch)
    _install_nonce_store(monkeypatch)
    stash = _install_registry_io(monkeypatch, existing=None)
    priv_b64, pub_b64 = keypair

    ch = client.post("/mesh/challenge", json={"agent_id": "agent:tofu"})
    nonce = ch.json()["nonce"]
    body = _sign_body(priv_b64, pub_b64, nonce, agent_id="agent:tofu", name="tofu")

    resp = client.post(
        "/mesh/enroll", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["enrolled"] is True
    assert data["first_seen"] is True

    ident = stash["identity"]
    assert ident is not None
    assert ident.public_key == pub_b64
    assert ident.verification_status == VerificationStatus.VERIFIED
    assert stash.get("first_seen_emitted") is True


# ---------------------------------------------------------------------------
# Case 5 — Re-enroll with mismatched public_key → 409
# ---------------------------------------------------------------------------


def test_pubkey_mismatch_returns_409(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    keypair: tuple[str, str],
) -> None:
    token = _install_auth(monkeypatch)
    _install_nonce_store(monkeypatch)

    # Seed an existing identity with a different pubkey.
    _orig_priv, orig_pub = generate_keypair()
    existing = AgentIdentity(name="rotate", public_key=orig_pub)
    existing.verification_status = VerificationStatus.VERIFIED
    _install_registry_io(monkeypatch, existing=existing)

    new_priv, new_pub = keypair
    ch = client.post("/mesh/challenge", json={"agent_id": "agent:rotate"})
    nonce = ch.json()["nonce"]
    body = _sign_body(new_priv, new_pub, nonce, agent_id="agent:rotate", name="rotate")

    resp = client.post(
        "/mesh/enroll", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 409
    assert "public_key" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Case 6 — Re-enroll with matching public_key → 200, first_seen=False
# ---------------------------------------------------------------------------


def test_reenroll_matching_pubkey_returns_200(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    keypair: tuple[str, str],
) -> None:
    token = _install_auth(monkeypatch)
    _install_nonce_store(monkeypatch)
    priv_b64, pub_b64 = keypair

    existing = AgentIdentity(name="stable", public_key=pub_b64)
    existing.verification_status = VerificationStatus.VERIFIED
    _install_registry_io(monkeypatch, existing=existing)

    ch = client.post("/mesh/challenge", json={"agent_id": "agent:stable"})
    nonce = ch.json()["nonce"]
    body = _sign_body(priv_b64, pub_b64, nonce, agent_id="agent:stable", name="stable")

    resp = client.post(
        "/mesh/enroll", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["enrolled"] is True
    assert data["first_seen"] is False


# ---------------------------------------------------------------------------
# Case 7 — Tampered payload (role change) → 403 (hash mismatch breaks sig)
# ---------------------------------------------------------------------------


def test_tampered_payload_returns_403(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    keypair: tuple[str, str],
) -> None:
    token = _install_auth(monkeypatch)
    _install_nonce_store(monkeypatch)
    _install_registry_io(monkeypatch)
    priv_b64, pub_b64 = keypair

    ch = client.post("/mesh/challenge", json={"agent_id": "agent:tamper"})
    nonce = ch.json()["nonce"]
    body = _sign_body(
        priv_b64, pub_b64, nonce, agent_id="agent:tamper", name="tamper", role="executor"
    )
    # Change role AFTER signing — hash no longer matches signature.
    body["role"] = "captain"

    resp = client.post(
        "/mesh/enroll", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403
