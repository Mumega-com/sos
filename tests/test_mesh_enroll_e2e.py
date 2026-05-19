"""End-to-end test for the v0.9.2.1 signed mesh enrollment protocol.

Drives the real code paths — challenge endpoint, nonce_store GETDEL,
crypto sign+verify, registry TOFU — backed by fakeredis so no live Redis
is needed. This is the protocol guard: if the challenge format, signed
payload fields, or TOFU flow drift, one of these cases fails loud.

Skipped cleanly if fakeredis isn't installed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

try:
    import fakeredis  # type: ignore[import-untyped]

    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False

from sos.contracts.policy import PolicyDecision
from sos.kernel.auth import AuthContext
from sos.kernel.crypto import (
    canonical_payload_hash,
    enroll_message,
    generate_keypair,
    sign,
)
from sos.services.registry.app import app


pytestmark = pytest.mark.skipif(not HAS_FAKEREDIS, reason="fakeredis not installed")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def _noop() -> None:
        return None

    monkeypatch.setattr("sos.services.registry.app._startup", _noop, raising=True)
    return TestClient(app)


@pytest.fixture
def redis_stub(monkeypatch: pytest.MonkeyPatch) -> "fakeredis.FakeRedis":
    """Patch nonce_store + service registry to share one fakeredis instance."""
    fake = fakeredis.FakeRedis(decode_responses=True)

    monkeypatch.setattr(
        "sos.services.registry.nonce_store._get_redis",
        lambda: fake,
        raising=True,
    )
    monkeypatch.setattr(
        "sos.services.registry._get_redis",
        lambda: fake,
        raising=True,
    )
    return fake


@pytest.fixture
def allow_auth(monkeypatch: pytest.MonkeyPatch) -> str:
    """Install a system Bearer + allow-all policy gate so we focus on crypto."""
    token = "e2e-sys"

    def _verify(authz: str | None) -> AuthContext | None:
        if authz and authz.endswith(token):
            return AuthContext(
                agent="sys",
                is_system=True,
                is_admin=True,
                label="e2e",
            )
        return None

    async def _gate(**kwargs) -> PolicyDecision:
        return PolicyDecision(
            allowed=True,
            reason="system/admin scope",
            tier="act_freely",
            action=kwargs.get("action", ""),
            resource=kwargs.get("resource", ""),
            agent="sys",
            tenant="mumega",
            pillars_passed=["system/admin"],
            pillars_failed=[],
            capability_ok=None,
            metadata={},
        )

    monkeypatch.setattr(
        "sos.services.registry.app._auth_verify_bearer", _verify, raising=True
    )
    monkeypatch.setattr(
        "sos.kernel.policy.gate.verify_bearer", _verify, raising=True
    )
    monkeypatch.setattr(
        "sos.services.registry.app.can_execute", _gate, raising=True
    )

    async def _no_emit(*_a, **_kw) -> None:
        return None

    monkeypatch.setattr(
        "sos.services.registry.app._emit_first_seen", _no_emit, raising=True
    )
    return token


def _sign(
    priv_b64: str,
    pub_b64: str,
    nonce: str,
    *,
    agent_id: str,
    name: str,
    role: str = "executor",
    skills: list[str] | None = None,
    squads: list[str] | None = None,
) -> dict:
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
        "public_key": pub_b64,
        "nonce": nonce,
        "signature": sig,
    }


def test_challenge_then_enroll_roundtrip(
    client: TestClient,
    redis_stub,
    allow_auth: str,
) -> None:
    """Full protocol: challenge → sign → enroll → second enroll sees
    existing identity and must match public_key."""
    priv_b64, pub_b64 = generate_keypair()
    agent_id = "agent:e2e-alpha"

    # Step 1 — challenge (no bearer required).
    ch = client.post("/mesh/challenge", json={"agent_id": agent_id})
    assert ch.status_code == 200
    nonce = ch.json()["nonce"]
    assert nonce
    # Nonce must exist in the fake store.
    assert redis_stub.exists(f"sos:mesh:nonce:{agent_id}:{nonce}") == 1

    # Step 2 — signed enroll (first time → TOFU).
    body = _sign(priv_b64, pub_b64, nonce, agent_id=agent_id, name="e2e-alpha")
    resp = client.post(
        "/mesh/enroll",
        json=body,
        headers={"Authorization": f"Bearer {allow_auth}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["enrolled"] is True
    assert data["first_seen"] is True
    # Nonce was consumed — must be gone now.
    assert redis_stub.exists(f"sos:mesh:nonce:{agent_id}:{nonce}") == 0

    # Step 3 — identity is now persisted; re-enrolling with a FRESH nonce
    # but SAME public_key must succeed and first_seen must flip to False.
    ch2 = client.post("/mesh/challenge", json={"agent_id": agent_id})
    nonce2 = ch2.json()["nonce"]
    body2 = _sign(priv_b64, pub_b64, nonce2, agent_id=agent_id, name="e2e-alpha")
    resp2 = client.post(
        "/mesh/enroll",
        json=body2,
        headers={"Authorization": f"Bearer {allow_auth}"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["first_seen"] is False


def test_enroll_with_rotated_key_is_rejected(
    client: TestClient,
    redis_stub,
    allow_auth: str,
) -> None:
    """Re-enroll with a DIFFERENT keypair → 409 (TOFU pin holds)."""
    orig_priv, orig_pub = generate_keypair()
    agent_id = "agent:e2e-rotate"

    ch = client.post("/mesh/challenge", json={"agent_id": agent_id})
    nonce = ch.json()["nonce"]
    body = _sign(orig_priv, orig_pub, nonce, agent_id=agent_id, name="e2e-rotate")
    resp = client.post(
        "/mesh/enroll",
        json=body,
        headers={"Authorization": f"Bearer {allow_auth}"},
    )
    assert resp.status_code == 200

    # Second caller, different keypair, fresh nonce — must be rejected.
    new_priv, new_pub = generate_keypair()
    ch2 = client.post("/mesh/challenge", json={"agent_id": agent_id})
    nonce2 = ch2.json()["nonce"]
    body2 = _sign(new_priv, new_pub, nonce2, agent_id=agent_id, name="e2e-rotate")
    resp2 = client.post(
        "/mesh/enroll",
        json=body2,
        headers={"Authorization": f"Bearer {allow_auth}"},
    )
    assert resp2.status_code == 409


def test_enroll_without_challenge_is_rejected(
    client: TestClient,
    redis_stub,
    allow_auth: str,
) -> None:
    """Fabricated nonce (never issued) → 403 on consume."""
    priv_b64, pub_b64 = generate_keypair()
    agent_id = "agent:e2e-nochallenge"

    body = _sign(
        priv_b64,
        pub_b64,
        nonce="not-a-real-nonce",
        agent_id=agent_id,
        name="e2e-nochallenge",
    )
    resp = client.post(
        "/mesh/enroll",
        json=body,
        headers={"Authorization": f"Bearer {allow_auth}"},
    )
    assert resp.status_code == 403
    assert "nonce" in resp.json()["detail"].lower()
