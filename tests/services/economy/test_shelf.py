"""Tests for Phase 7 Step 7.6 Shelf commerce routes."""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sos.services.economy import shelf as shelf_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tokens_file(tmp_path, monkeypatch):
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
def shelf_db(tmp_path, monkeypatch):
    """Isolate the shelf SQLite db into a tmp path."""
    db_path = tmp_path / "shelf.db"
    monkeypatch.setenv(shelf_mod._DB_ENV, str(db_path))
    shelf_mod.init_db()
    return db_path


@pytest.fixture
def stripe_hooks(monkeypatch):
    """Swap Stripe hooks for deterministic fakes."""
    created: list[dict[str, Any]] = []

    def fake_create_checkout(product, success_url, cancel_url):
        created.append(
            {"product": product, "success": success_url, "cancel": cancel_url}
        )
        return {"id": f"cs_test_{product.id}", "url": f"https://checkout.stripe.test/{product.id}"}

    def fake_construct_event(payload: bytes, signature: str):
        if signature != "good-signature":
            raise ValueError("signature verification failed")
        return json.loads(payload.decode("utf-8"))

    shelf_mod.set_stripe_hooks(
        create_checkout=fake_create_checkout,
        construct_event=fake_construct_event,
    )
    yield created
    # Restore defaults to keep other tests hermetic.
    shelf_mod.set_stripe_hooks(
        create_checkout=shelf_mod._default_create_checkout,
        construct_event=shelf_mod._default_construct_event,
    )


@pytest.fixture
def client(tokens_file, shelf_db, stripe_hooks, monkeypatch):
    from sos.services.economy.app import app, wallet

    # Stub wallet.credit so the webhook path doesn't touch the real ledger.
    async def fake_credit(user_id, amount, reason="deposit"):
        return amount

    monkeypatch.setattr(wallet, "credit", fake_credit)
    # Also patch the wallet.credit used inside the webhook path.
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "sos.services.economy.wallet.SovereignWallet.credit",
        AsyncMock(return_value=29.0),
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# Auth / list
# ---------------------------------------------------------------------------


def test_list_shelf_requires_bearer(client: TestClient) -> None:
    resp = client.get("/economy/shelf/acme")
    assert resp.status_code == 401


def test_list_shelf_empty_by_default(client: TestClient) -> None:
    resp = client.get(
        "/economy/shelf/acme", headers={"Authorization": "Bearer tk_acme"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"tenant": "acme", "count": 0, "products": []}


# ---------------------------------------------------------------------------
# Add product (admin-only)
# ---------------------------------------------------------------------------


def test_add_product_requires_system_scope(client: TestClient) -> None:
    resp = client.post(
        "/economy/shelf/acme",
        headers={"Authorization": "Bearer tk_acme"},
        json={
            "id": "mumega-playbook",
            "title": "Mumega Playbook",
            "price_cents": 2900,
            "grant_id": "mumega-playbook",
        },
    )
    assert resp.status_code == 403


def test_add_product_with_system_token_succeeds(client: TestClient) -> None:
    resp = client.post(
        "/economy/shelf/acme",
        headers={"Authorization": "Bearer tk_system"},
        json={
            "id": "mumega-playbook",
            "title": "Mumega Playbook",
            "description": "Dogfood product for mumega-internal",
            "price_cents": 2900,
            "currency": "usd",
            "grant_id": "mumega-playbook",
            "mind_multiplier": 0.5,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "mumega-playbook"
    assert body["price_cents"] == 2900
    assert body["active"] is True

    # And now it shows up in the list.
    lst = client.get(
        "/economy/shelf/acme", headers={"Authorization": "Bearer tk_acme"}
    )
    assert lst.json()["count"] == 1


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


def test_checkout_unknown_product_is_404(client: TestClient) -> None:
    resp = client.post(
        "/economy/shelf/checkout/acme/does-not-exist",
        headers={"Authorization": "Bearer tk_acme"},
    )
    assert resp.status_code == 404


def test_checkout_returns_stripe_session(
    client: TestClient, stripe_hooks: list[dict[str, Any]]
) -> None:
    # Seed a product via the admin route.
    client.post(
        "/economy/shelf/acme",
        headers={"Authorization": "Bearer tk_system"},
        json={
            "id": "mumega-playbook",
            "title": "Mumega Playbook",
            "price_cents": 2900,
            "grant_id": "mumega-playbook",
        },
    )

    resp = client.post(
        "/economy/shelf/checkout/acme/mumega-playbook",
        headers={"Authorization": "Bearer tk_acme"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "cs_test_mumega-playbook"
    assert body["url"].startswith("https://checkout.stripe.test/")
    assert body["product_id"] == "mumega-playbook"
    assert body["amount_cents"] == 2900

    # The hook captured product + URLs.
    assert len(stripe_hooks) == 1
    assert stripe_hooks[0]["product"].id == "mumega-playbook"


# ---------------------------------------------------------------------------
# Webhook (capture)
# ---------------------------------------------------------------------------


def test_capture_rejects_missing_signature(client: TestClient) -> None:
    resp = client.post("/economy/shelf/capture", content=b"{}")
    assert resp.status_code == 400


def test_capture_rejects_bad_signature(client: TestClient) -> None:
    resp = client.post(
        "/economy/shelf/capture",
        content=b"{}",
        headers={"Stripe-Signature": "bogus"},
    )
    assert resp.status_code == 400


def test_capture_ignores_non_checkout_events(client: TestClient) -> None:
    payload = json.dumps({"type": "customer.created", "data": {"object": {}}}).encode()
    resp = client.post(
        "/economy/shelf/capture",
        content=payload,
        headers={"Stripe-Signature": "good-signature"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "ignored" in (body.get("reason") or "")


def test_capture_records_checkout_completed_and_is_idempotent(
    client: TestClient,
) -> None:
    # Seed the product so the webhook can credit the right multiplier.
    client.post(
        "/economy/shelf/acme",
        headers={"Authorization": "Bearer tk_system"},
        json={
            "id": "mumega-playbook",
            "title": "Mumega Playbook",
            "price_cents": 2900,
            "grant_id": "mumega-playbook",
            "mind_multiplier": 0.5,
        },
    )

    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_live_123",
                "amount_total": 2900,
                "currency": "usd",
                "customer_details": {"email": "buyer@example.com"},
                "metadata": {
                    "sos_tenant": "acme",
                    "sos_product_id": "mumega-playbook",
                    "sos_grant_id": "mumega-playbook",
                },
            }
        },
    }
    payload = json.dumps(event).encode()

    resp = client.post(
        "/economy/shelf/capture",
        content=payload,
        headers={"Stripe-Signature": "good-signature"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["already_recorded"] is False
    capture_id = body["capture_id"]
    assert capture_id

    # Replay: the same session_id must return already_recorded.
    resp2 = client.post(
        "/economy/shelf/capture",
        content=payload,
        headers={"Stripe-Signature": "good-signature"},
    )
    body2 = resp2.json()
    assert body2["already_recorded"] is True
    assert body2["capture_id"] == capture_id
