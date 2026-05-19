"""FastAPI TestClient tests for POST /qnft/mint and GET /qnft/{tenant}.

Mocks: wallet.debit (no real SQLite), _qnft_store (in-memory dict), gate
(allow system token). No real Redis or network needed.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tokens_file(tmp_path, monkeypatch):
    """Patch tokens.json so gate resolves system + tenant tokens."""
    tokens = [
        {"label": "system", "token": "tk_system", "active": True, "is_system": True},
        {"label": "acme-tenant", "token": "tk_acme", "project": "acme", "active": True},
    ]
    p = tmp_path / "tokens.json"
    p.write_text(json.dumps(tokens))
    import sos.kernel.auth as auth_mod
    monkeypatch.setattr(auth_mod, "TOKENS_PATH", p)
    auth_mod._cache.invalidate()
    return p


@pytest.fixture
def qnft_store(monkeypatch):
    """Replace _qnft_store functions with in-memory dicts so no Redis needed."""
    _store: dict[str, list[dict[str, Any]]] = {}

    async def fake_append(token: dict[str, Any], *, redis: Any = None) -> None:
        _store.setdefault(token["tenant"], []).append(token)

    async def fake_list(tenant: str, *, redis: Any = None) -> list[dict[str, Any]]:
        return _store.get(tenant, [])

    monkeypatch.setattr("sos.services.economy.app.append_qnft", fake_append)
    monkeypatch.setattr("sos.services.economy.app.list_qnfts", fake_list)
    return _store


@pytest.fixture
def client(tokens_file, qnft_store, monkeypatch):
    """Build a TestClient with real debit mocked to succeed."""
    from sos.services.economy.app import app, wallet

    # Successful debit returns new balance float
    monkeypatch.setattr(wallet, "debit", AsyncMock(return_value=900.0))

    return TestClient(app)


@pytest.fixture
def client_broke(tokens_file, qnft_store, monkeypatch):
    """TestClient where debit raises InsufficientFundsError → 402."""
    from sos.services.economy.app import app, wallet
    from sos.services.economy.wallet import InsufficientFundsError

    monkeypatch.setattr(
        wallet,
        "debit",
        AsyncMock(side_effect=InsufficientFundsError("acme", 100, 0)),
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests — POST /qnft/mint
# ---------------------------------------------------------------------------

_MINT_PAYLOAD = {
    "tenant": "acme",
    "squad_id": "acme-squad-social",
    "role": "social",
    "seat_id": "acme:seat:social",
    "cost_mind": 100,
    "project": "acme",
}


def test_mint_qnft_happy_path(client: TestClient) -> None:
    resp = client.post(
        "/qnft/mint",
        json=_MINT_PAYLOAD,
        headers={"Authorization": "Bearer tk_system"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["tenant"] == "acme"
    assert data["role"] == "social"
    assert data["seat_id"] == "acme:seat:social"
    assert data["mint_cost_mind"] == 100
    assert "token_id" in data
    assert "minted_at" in data
    assert data["claimed_by"] is None


def test_mint_qnft_returns_402_on_insufficient_funds(client_broke: TestClient) -> None:
    resp = client_broke.post(
        "/qnft/mint",
        json=_MINT_PAYLOAD,
        headers={"Authorization": "Bearer tk_system"},
    )
    assert resp.status_code == 402


def test_mint_qnft_requires_auth(client: TestClient) -> None:
    resp = client.post("/qnft/mint", json=_MINT_PAYLOAD)
    assert resp.status_code == 401


def test_mint_qnft_rejects_non_system_token(client: TestClient) -> None:
    """Tenant-scoped token should be denied (require_system=True)."""
    resp = client.post(
        "/qnft/mint",
        json=_MINT_PAYLOAD,
        headers={"Authorization": "Bearer tk_acme"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests — GET /qnft/{tenant}
# ---------------------------------------------------------------------------


def test_list_qnfts_returns_empty_before_mint(tokens_file, qnft_store, monkeypatch) -> None:
    from sos.services.economy.app import app
    client = TestClient(app)
    resp = client.get("/qnft/acme", headers={"Authorization": "Bearer tk_acme"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant"] == "acme"
    assert data["tokens"] == []
    assert data["count"] == 0


def test_list_qnfts_returns_minted_tokens(client: TestClient, qnft_store: dict) -> None:
    # Pre-seed the store.
    qnft_store["acme"] = [{"token_id": "abc", "tenant": "acme", "role": "social"}]
    resp = client.get("/qnft/acme", headers={"Authorization": "Bearer tk_system"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["tokens"][0]["role"] == "social"
