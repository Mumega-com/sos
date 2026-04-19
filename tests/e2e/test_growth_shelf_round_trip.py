"""End-to-end test for the Phase 7 gate (v0.10.1).

Phase 7 gate — per docs/plans/2026-04-19-mumega-mothership.md:

    A new tenant signs up and gets a brand-vector dossier + wallet +
    1 course/book for sale, all within 10 minutes.

This test exercises that slice in-process, wiring the economy and
integrations services together:

1. Seed a dossier in Redis (what the growth-intel squad would write).
2. Hit ``GET /integrations/dossier/{tenant}/latest`` and assert the Glass
   brand-vector tile would render the dossier summary + opportunities.
3. Admin seeds a Shelf product via ``POST /economy/shelf/{tenant}``.
4. Tenant lists the shelf and sees the product.
5. Tenant hits ``POST /economy/shelf/checkout/{tenant}/{product_id}`` and
   gets a Stripe Checkout URL.
6. Stripe fires ``checkout.session.completed`` → the webhook records the
   capture and credits the tenant's $MIND wallet. Replay is a no-op.

No real Redis, no real Stripe, no real wallet backend — fakes throughout.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from sos.services.economy import shelf as shelf_mod
from sos.services.integrations import app as integrations_app_module


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
    db_path = tmp_path / "shelf.db"
    monkeypatch.setenv(shelf_mod._DB_ENV, str(db_path))
    shelf_mod.init_db()
    return db_path


@pytest.fixture
def stripe_hooks():
    created: list[dict[str, Any]] = []

    def fake_create_checkout(product, success_url, cancel_url):
        created.append(
            {"product": product, "success": success_url, "cancel": cancel_url}
        )
        return {
            "id": f"cs_test_{product.id}",
            "url": f"https://checkout.stripe.test/{product.id}",
        }

    def fake_construct_event(payload: bytes, signature: str):
        if signature != "good-signature":
            raise ValueError("signature verification failed")
        return json.loads(payload.decode("utf-8"))

    shelf_mod.set_stripe_hooks(
        create_checkout=fake_create_checkout,
        construct_event=fake_construct_event,
    )
    yield created
    shelf_mod.set_stripe_hooks(
        create_checkout=shelf_mod._default_create_checkout,
        construct_event=shelf_mod._default_construct_event,
    )


class _FakeDossierRedis:
    """Mirrors tests/services/test_integrations_service.py::_FakeDossierRedis."""

    def __init__(self, value: str | None) -> None:
        self._value = value

    async def get(self, key: str) -> str | None:
        return self._value

    async def aclose(self) -> None:
        return None


@pytest.fixture
def wallet_credit(monkeypatch):
    """Record every wallet.credit call so we can assert on the Phase 7 gate."""
    credited: list[dict[str, Any]] = []

    async def fake_credit(self, user_id, amount, reason="deposit"):
        credited.append({"user_id": user_id, "amount": amount, "reason": reason})
        return amount

    monkeypatch.setattr(
        "sos.services.economy.wallet.SovereignWallet.credit",
        fake_credit,
    )
    return credited


@pytest.fixture
def economy_client(tokens_file, shelf_db, stripe_hooks, wallet_credit):
    from sos.services.economy.app import app

    return TestClient(app)


@pytest.fixture
def integrations_client(tokens_file):
    from sos.services.integrations.app import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Phase 7 gate — one test, end-to-end.
# ---------------------------------------------------------------------------


def test_phase7_gate_dossier_plus_shelf_round_trip(
    economy_client: TestClient,
    integrations_client: TestClient,
    stripe_hooks: list[dict[str, Any]],
    wallet_credit: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = "acme"

    # ------------------------------------------------------------------
    # 1) Seed a dossier in Redis (simulating the growth-intel squad run).
    # ------------------------------------------------------------------
    stored_dossier = json.dumps(
        {
            "tenant": tenant,
            "rendered_at": "2026-04-19T10:00:00+00:00",
            "summary": "Acme — futurist tone. Top opportunities: ai automation.",
            "opportunities": ["ai automation", "brand vector"],
            "threats": ["acme.ai"],
            "markdown": "# Brand Vector — acme\n...",
        }
    )
    monkeypatch.setattr(
        integrations_app_module,
        "_dossier_redis_client",
        lambda: _FakeDossierRedis(stored_dossier),
    )

    # ------------------------------------------------------------------
    # 2) Brand-vector tile would read /integrations/dossier/{tenant}/latest.
    # ------------------------------------------------------------------
    dossier_resp = integrations_client.get(
        f"/integrations/dossier/{tenant}/latest",
        headers={"Authorization": "Bearer tk_acme"},
    )
    assert dossier_resp.status_code == 200, dossier_resp.text
    dossier_body = dossier_resp.json()
    assert dossier_body["tenant"] == tenant
    assert "ai automation" in dossier_body["opportunities"]
    assert "ai automation" in dossier_body["summary"]

    # ------------------------------------------------------------------
    # 3) Admin seeds one Shelf product for the tenant.
    # ------------------------------------------------------------------
    add_resp = economy_client.post(
        f"/economy/shelf/{tenant}",
        headers={"Authorization": "Bearer tk_system"},
        json={
            "id": "mumega-playbook",
            "title": "Mumega Playbook",
            "description": "Dogfood product",
            "price_cents": 2900,
            "currency": "usd",
            "grant_id": "mumega-playbook",
            "mind_multiplier": 0.5,
        },
    )
    assert add_resp.status_code == 200, add_resp.text

    # ------------------------------------------------------------------
    # 4) Tenant lists the shelf — sees the one product.
    # ------------------------------------------------------------------
    list_resp = economy_client.get(
        f"/economy/shelf/{tenant}",
        headers={"Authorization": "Bearer tk_acme"},
    )
    assert list_resp.status_code == 200
    listing = list_resp.json()
    assert listing["count"] == 1
    assert listing["products"][0]["id"] == "mumega-playbook"
    assert listing["products"][0]["price_cents"] == 2900

    # ------------------------------------------------------------------
    # 5) Tenant hits checkout — gets a Stripe Session.
    # ------------------------------------------------------------------
    checkout_resp = economy_client.post(
        f"/economy/shelf/checkout/{tenant}/mumega-playbook",
        headers={"Authorization": "Bearer tk_acme"},
    )
    assert checkout_resp.status_code == 200, checkout_resp.text
    session = checkout_resp.json()
    assert session["session_id"] == "cs_test_mumega-playbook"
    assert session["url"].startswith("https://checkout.stripe.test/")
    assert session["amount_cents"] == 2900
    assert len(stripe_hooks) == 1

    # ------------------------------------------------------------------
    # 6) Stripe fires checkout.session.completed — webhook credits $MIND.
    # ------------------------------------------------------------------
    event_payload = json.dumps(
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_live_real_123",
                    "amount_total": 2900,
                    "currency": "usd",
                    "customer_details": {"email": "buyer@example.com"},
                    "metadata": {
                        "sos_tenant": tenant,
                        "sos_product_id": "mumega-playbook",
                        "sos_grant_id": "mumega-playbook",
                    },
                }
            },
        }
    ).encode()

    first = economy_client.post(
        "/economy/shelf/capture",
        content=event_payload,
        headers={"Stripe-Signature": "good-signature"},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["ok"] is True
    assert first_body["already_recorded"] is False
    first_capture_id = first_body["capture_id"]
    assert first_capture_id

    # Wallet was credited once: amount / 100 * mind_multiplier = 29 * 0.5 = 14.5.
    assert len(wallet_credit) == 1
    assert wallet_credit[0]["user_id"] == tenant
    assert abs(wallet_credit[0]["amount"] - 14.5) < 1e-6
    assert "mumega-playbook" in wallet_credit[0]["reason"]

    # Replay the webhook → idempotent: no new credit, same capture id.
    second = economy_client.post(
        "/economy/shelf/capture",
        content=event_payload,
        headers={"Stripe-Signature": "good-signature"},
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["already_recorded"] is True
    assert second_body["capture_id"] == first_capture_id
    assert len(wallet_credit) == 1  # not double-credited
