"""Tests for the Identity avatar HTTP surface (P0-07 / v0.4.5 Wave 4).

Covers:
- POST /avatar/generate with bearer auth — maps to AvatarGenerator.generate
- POST /avatar/social/on_alpha_drift with bearer auth — maps to
  SocialAutomation.on_alpha_drift
- 401 on missing / invalid bearer
- 403 when token has no scope and is not system/admin
"""
from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    """TestClient for the identity FastAPI app."""
    from sos.services.identity.app import app

    return TestClient(app)


@pytest.fixture
def system_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Install a system-scope Bearer token usable without a project scope."""
    token = "test-sys-token-p0-07"
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", token)
    from sos.kernel.auth import get_cache

    get_cache().invalidate()
    return token


@pytest.fixture
def reset_lazy_singletons():
    """Reset the module-level lazy-init singletons between tests so patches
    of AvatarGenerator / SocialAutomation take effect on the next request."""
    from sos.services.identity import app as identity_app

    identity_app._avatar_gen = None
    identity_app._social = None
    yield
    identity_app._avatar_gen = None
    identity_app._social = None


# ---------------------------------------------------------------------------
# /avatar/generate
# ---------------------------------------------------------------------------


def test_avatar_generate_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.post("/avatar/generate", json={"agent_id": "x", "uv": {}})
    assert resp.status_code == 401


def test_avatar_generate_invalid_bearer_is_401(client: TestClient) -> None:
    resp = client.post(
        "/avatar/generate",
        json={"agent_id": "x", "uv": {}},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


def test_avatar_generate_happy_path(
    client: TestClient,
    system_token: str,
    reset_lazy_singletons: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a system bearer, POST /avatar/generate should hit our patched
    AvatarGenerator.generate and return its dict verbatim.

    We stub the ``_get_avatar_generator`` helper rather than
    ``AvatarGenerator.generate`` directly — ``AvatarGenerator.__init__``
    reads ``Config.data_dir`` which isn't always configured in unit tests.
    """
    stub_result: Dict[str, Any] = {
        "success": True,
        "path": "/tmp/fake.png",
        "dna_hash": "abc123",
        "coherence": 0.8,
    }

    captured: Dict[str, Any] = {}

    class _FakeGen:
        def generate(self, **kwargs: Any) -> Dict[str, Any]:
            captured.update(kwargs)
            return stub_result

    monkeypatch.setattr(
        "sos.services.identity.app._get_avatar_generator",
        lambda: _FakeGen(),
    )

    resp = client.post(
        "/avatar/generate",
        json={
            "agent_id": "River",
            "uv": {"p": 0.7, "phi": 0.8},
            "alpha_drift": 0.0005,
            "event_type": "dream_synthesis",
        },
        headers={"Authorization": f"Bearer {system_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == stub_result
    assert captured["agent_id"] == "River"
    assert captured["alpha_drift"] == 0.0005
    assert captured["event_type"] == "dream_synthesis"
    # UV16D reconstructed from the dict
    from sos.contracts.identity import UV16D

    assert isinstance(captured["uv"], UV16D)
    assert captured["uv"].p == pytest.approx(0.7)
    assert captured["uv"].phi == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# /avatar/social/on_alpha_drift
# ---------------------------------------------------------------------------


def test_social_drift_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.post(
        "/avatar/social/on_alpha_drift",
        json={"agent_id": "x", "uv": {}, "alpha_value": 0.0, "insight": "i"},
    )
    assert resp.status_code == 401


def test_social_drift_happy_path(
    client: TestClient,
    system_token: str,
    reset_lazy_singletons: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a system bearer, POST /avatar/social/on_alpha_drift should hit
    our stubbed SocialAutomation.on_alpha_drift and return its dict.

    Stubs the lazy-init helper — the real class constructor builds an
    AvatarGenerator which needs Config.data_dir.
    """
    stub_result: Dict[str, Any] = {
        "triggered": True,
        "alpha": 0.0,
        "avatar": {"success": True, "path": "/tmp/fake.png"},
        "platforms": {"twitter": {"success": True}},
    }

    captured: Dict[str, Any] = {}

    class _FakeSocial:
        async def on_alpha_drift(self, **kwargs: Any) -> Dict[str, Any]:
            captured.update(kwargs)
            return stub_result

    monkeypatch.setattr(
        "sos.services.identity.app._get_social_automation",
        lambda: _FakeSocial(),
    )

    resp = client.post(
        "/avatar/social/on_alpha_drift",
        json={
            "agent_id": "River",
            "uv": {"phi": 0.65},
            "alpha_value": 0.0,
            "insight": "a quiet insight",
            "platforms": ["twitter"],
        },
        headers={"Authorization": f"Bearer {system_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == stub_result
    assert captured["agent_id"] == "River"
    assert captured["alpha_value"] == 0.0
    assert captured["insight"] == "a quiet insight"
    assert captured["platforms"] == ["twitter"]


# ---------------------------------------------------------------------------
# Scope enforcement: non-system, non-scoped token → 403
# ---------------------------------------------------------------------------


def test_avatar_generate_403_on_scopeless_token(
    client: TestClient,
    reset_lazy_singletons: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verified-but-unscoped token (no project/tenant/agent) must be rejected."""
    from sos.kernel import auth as auth_module

    fake_ctx = auth_module.AuthContext(
        agent=None,
        project=None,
        tenant_slug=None,
        is_system=False,
        is_admin=False,
        label="unit-test-scopeless",
        raw_token_hash=None,
        env_source=None,
    )
    # Patch verify_bearer as seen from the identity app module.
    monkeypatch.setattr(
        "sos.services.identity.app._auth_verify_bearer",
        lambda _auth: fake_ctx,
    )
    resp = client.post(
        "/avatar/generate",
        json={"agent_id": "x", "uv": {}},
        headers={"Authorization": "Bearer whatever"},
    )
    assert resp.status_code == 403
    assert "scope" in resp.json().get("detail", "").lower()
