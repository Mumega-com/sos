"""Tests for /sos/pairing/nonce + /sos/pairing endpoints."""
from __future__ import annotations

import base64
import json
from datetime import datetime

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sos.contracts.pairing import PairingResponse
from sos.services.saas.pairing import _NONCE_STORE, router


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sign(priv: ed25519.Ed25519PrivateKey, nonce: str) -> str:
    return "ed25519:" + base64.b64encode(priv.sign(nonce.encode("utf-8"))).decode()


def _pubkey_str(priv: ed25519.Ed25519PrivateKey) -> str:
    pub_bytes = priv.public_key().public_bytes_raw()
    return "ed25519:" + base64.b64encode(pub_bytes).decode()


def _make_client(tmp_path, monkeypatch) -> TestClient:
    """Build an isolated FastAPI app mounting only the pairing router.

    Sets SOS_TOKENS_PATH to a temp file so we never touch production
    tokens.json. Clears the in-process nonce store between tests.
    """
    monkeypatch.setenv("SOS_TOKENS_PATH", str(tmp_path / "tokens.json"))
    _NONCE_STORE.clear()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    return _make_client(tmp_path, monkeypatch)


@pytest.fixture
def tokens_file(tmp_path) -> "object":
    return tmp_path / "tokens.json"


# ---------------------------------------------------------------------------
# nonce endpoint
# ---------------------------------------------------------------------------


def test_nonce_endpoint_issues_fresh_nonce(client: TestClient) -> None:
    r = client.get("/sos/pairing/nonce", params={"agent_name": "hermes"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "nonce" in body
    assert len(body["nonce"]) >= 16
    # expires_at is parseable ISO-8601
    datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))


def test_nonce_endpoint_rejects_invalid_name(client: TestClient) -> None:
    r = client.get("/sos/pairing/nonce", params={"agent_name": "Hermes"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# pairing endpoint
# ---------------------------------------------------------------------------


def test_pairing_full_happy_path(client: TestClient, tmp_path) -> None:
    priv = ed25519.Ed25519PrivateKey.generate()

    nonce_resp = client.get("/sos/pairing/nonce", params={"agent_name": "hermes"})
    assert nonce_resp.status_code == 200
    nonce = nonce_resp.json()["nonce"]

    body = {
        "agent_name": "hermes",
        "pubkey": _pubkey_str(priv),
        "skills": ["content-write", "seo-audit"],
        "model_provider": "anthropic:claude-sonnet-4-5",
        "nonce": nonce,
        "signature": _sign(priv, nonce),
        "role": "specialist",
    }
    r = client.post("/sos/pairing", json=body)
    assert r.status_code == 200, r.text

    # Response validates as PairingResponse
    parsed = PairingResponse.model_validate(r.json())
    assert len(parsed.token) == 64
    assert all(c in "0123456789abcdef" for c in parsed.token)

    # tokens.json grew by 1 with expected fields
    tokens_path = tmp_path / "tokens.json"
    tokens = json.loads(tokens_path.read_text())
    assert len(tokens) == 1
    entry = tokens[0]
    assert entry["agent"] == "hermes"
    assert entry["scope"] == "agent"
    assert entry["role"] == "specialist"
    assert entry["skills"] == ["content-write", "seo-audit"]
    assert entry["model_provider"] == "anthropic:claude-sonnet-4-5"
    assert entry["active"] is True


def test_pairing_rejects_wrong_signature(client: TestClient) -> None:
    priv = ed25519.Ed25519PrivateKey.generate()

    nonce_resp = client.get("/sos/pairing/nonce", params={"agent_name": "hermes"})
    nonce = nonce_resp.json()["nonce"]

    # Sign a DIFFERENT string than the nonce we'll submit.
    wrong_signature = _sign(priv, "some-other-nonce-payload-xxxxxx")

    body = {
        "agent_name": "hermes",
        "pubkey": _pubkey_str(priv),
        "skills": ["content-write"],
        "model_provider": "anthropic:claude-sonnet-4-5",
        "nonce": nonce,
        "signature": wrong_signature,
        "role": "specialist",
    }
    r = client.post("/sos/pairing", json=body)
    assert r.status_code == 401


def test_pairing_rejects_unknown_nonce(client: TestClient) -> None:
    priv = ed25519.Ed25519PrivateKey.generate()
    fake_nonce = "abcdef0123456789abcdef0123456789"  # never issued

    body = {
        "agent_name": "hermes",
        "pubkey": _pubkey_str(priv),
        "skills": ["content-write"],
        "model_provider": "anthropic:claude-sonnet-4-5",
        "nonce": fake_nonce,
        "signature": _sign(priv, fake_nonce),
        "role": "specialist",
    }
    r = client.post("/sos/pairing", json=body)
    assert r.status_code == 400


def test_pairing_rejects_nonce_not_matching_agent(client: TestClient) -> None:
    priv = ed25519.Ed25519PrivateKey.generate()

    nonce_resp = client.get("/sos/pairing/nonce", params={"agent_name": "alice"})
    nonce = nonce_resp.json()["nonce"]

    body = {
        "agent_name": "bob",
        "pubkey": _pubkey_str(priv),
        "skills": ["content-write"],
        "model_provider": "anthropic:claude-sonnet-4-5",
        "nonce": nonce,
        "signature": _sign(priv, nonce),
        "role": "specialist",
    }
    r = client.post("/sos/pairing", json=body)
    assert r.status_code == 400


def test_pairing_nonce_consumed_after_use(client: TestClient) -> None:
    priv = ed25519.Ed25519PrivateKey.generate()

    nonce_resp = client.get("/sos/pairing/nonce", params={"agent_name": "hermes"})
    nonce = nonce_resp.json()["nonce"]

    body = {
        "agent_name": "hermes",
        "pubkey": _pubkey_str(priv),
        "skills": ["content-write"],
        "model_provider": "anthropic:claude-sonnet-4-5",
        "nonce": nonce,
        "signature": _sign(priv, nonce),
        "role": "specialist",
    }
    r1 = client.post("/sos/pairing", json=body)
    assert r1.status_code == 200

    # Replay: same nonce + signature, should be rejected (nonce consumed).
    r2 = client.post("/sos/pairing", json=body)
    assert r2.status_code == 400


def test_agent_id_increments_on_multiple_pairings(client: TestClient) -> None:
    def _pair() -> str:
        priv = ed25519.Ed25519PrivateKey.generate()
        nonce_resp = client.get("/sos/pairing/nonce", params={"agent_name": "hermes"})
        nonce = nonce_resp.json()["nonce"]
        body = {
            "agent_name": "hermes",
            "pubkey": _pubkey_str(priv),
            "skills": ["content-write"],
            "model_provider": "anthropic:claude-sonnet-4-5",
            "nonce": nonce,
            "signature": _sign(priv, nonce),
            "role": "specialist",
        }
        r = client.post("/sos/pairing", json=body)
        assert r.status_code == 200, r.text
        return r.json()["agent_id"]

    first = _pair()
    second = _pair()
    assert first == "Hermes_sos_001"
    assert second == "Hermes_sos_002"


def test_plaintext_token_not_in_tokens_json(client: TestClient, tmp_path) -> None:
    priv = ed25519.Ed25519PrivateKey.generate()
    nonce_resp = client.get("/sos/pairing/nonce", params={"agent_name": "hermes"})
    nonce = nonce_resp.json()["nonce"]

    body = {
        "agent_name": "hermes",
        "pubkey": _pubkey_str(priv),
        "skills": ["content-write"],
        "model_provider": "anthropic:claude-sonnet-4-5",
        "nonce": nonce,
        "signature": _sign(priv, nonce),
        "role": "specialist",
    }
    r = client.post("/sos/pairing", json=body)
    assert r.status_code == 200
    minted_token = r.json()["token"]

    tokens_path = tmp_path / "tokens.json"
    entries = json.loads(tokens_path.read_text())
    assert len(entries) == 1
    entry = entries[0]
    assert entry["token"] == ""
    assert len(entry["token_hash"]) == 64
    assert all(c in "0123456789abcdef" for c in entry["token_hash"])
    # sanity: hash matches sha256(minted_token)
    import hashlib
    assert entry["token_hash"] == hashlib.sha256(minted_token.encode()).hexdigest()
