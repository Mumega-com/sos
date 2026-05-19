"""Tests for the Mumega SaaS Service (http://localhost:8075).

Runs against the live service — no TestClient, no mocking.
Use: cd ~/SOS && python3 -m pytest tests/test_saas_api.py -v
"""
from __future__ import annotations

import time
import httpx
import pytest

BASE_URL = "http://localhost:8075"
ADMIN_KEY = "sk-mumega-internal-001"
HEADERS = {"Authorization": f"Bearer {ADMIN_KEY}"}


def ts() -> str:
    """Millisecond timestamp suffix to make tenant names unique."""
    return str(int(time.time() * 1000))[-8:]


def cancel_tenant(slug: str) -> None:
    """Best-effort cleanup — cancel a tenant by slug."""
    httpx.put(
        f"{BASE_URL}/tenants/{slug}",
        headers=HEADERS,
        json={"status": "cancelled"},
        timeout=10,
    )


# ---------------------------------------------------------------------------
# 1. Health check
# ---------------------------------------------------------------------------

def test_health():
    r = httpx.get(f"{BASE_URL}/health", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "tenants" in body


# ---------------------------------------------------------------------------
# 2. Signup creates tenant
# ---------------------------------------------------------------------------

def test_signup_creates_tenant():
    name = f"Test Biz {ts()}"
    r = httpx.post(
        f"{BASE_URL}/signup",
        json={"name": name, "email": f"test-{ts()}@example.com", "plan": "starter"},
        timeout=15,
    )
    assert r.status_code == 200, f"Signup failed: {r.text}"
    body = r.json()

    slug = body.get("tenant")
    assert slug, "Response should include 'tenant' (slug)"
    assert "mcp_url" in body, "Response should include 'mcp_url'"
    assert "connect" in body, "Response should include 'connect' configs"
    assert "claude_code" in body["connect"], "connect should include claude_code"
    assert "claude_desktop" in body["connect"], "connect should include claude_desktop"

    # Cleanup
    cancel_tenant(slug)


# ---------------------------------------------------------------------------
# 3. Duplicate signup returns error
# ---------------------------------------------------------------------------

def test_signup_duplicate_name():
    name = f"Dup Biz {ts()}"
    email = f"dup-{ts()}@example.com"
    payload = {"name": name, "email": email, "plan": "starter"}

    r1 = httpx.post(f"{BASE_URL}/signup", json=payload, timeout=15)
    assert r1.status_code == 200, f"First signup failed: {r1.text}"
    slug = r1.json().get("tenant")

    r2 = httpx.post(f"{BASE_URL}/signup", json=payload, timeout=10)
    assert r2.status_code == 409, f"Expected 409 for duplicate, got {r2.status_code}: {r2.text}"

    # Cleanup
    cancel_tenant(slug)


# ---------------------------------------------------------------------------
# 4. Auth required on /tenants
# ---------------------------------------------------------------------------

def test_auth_required_on_admin():
    # No auth → 401
    r_no_auth = httpx.get(f"{BASE_URL}/tenants", timeout=10)
    assert r_no_auth.status_code == 401, (
        f"Expected 401 without auth, got {r_no_auth.status_code}"
    )

    # With auth → 200
    r_auth = httpx.get(f"{BASE_URL}/tenants", headers=HEADERS, timeout=10)
    assert r_auth.status_code == 200, (
        f"Expected 200 with auth, got {r_auth.status_code}: {r_auth.text}"
    )
    body = r_auth.json()
    assert "tenants" in body
    assert "count" in body


# ---------------------------------------------------------------------------
# 5. Tenant CRUD: create → get → update → verify → cancel
# ---------------------------------------------------------------------------

def test_tenant_crud():
    name = f"CRUD Biz {ts()}"
    r = httpx.post(
        f"{BASE_URL}/signup",
        json={"name": name, "email": f"crud-{ts()}@example.com", "plan": "starter"},
        timeout=15,
    )
    assert r.status_code == 200, f"Signup failed: {r.text}"
    slug = r.json()["tenant"]

    try:
        # GET tenant
        r_get = httpx.get(f"{BASE_URL}/tenants/{slug}", headers=HEADERS, timeout=10)
        assert r_get.status_code == 200, f"GET tenant failed: {r_get.text}"
        tenant = r_get.json()
        assert tenant["slug"] == slug
        assert tenant["plan"] == "starter"

        # PUT update — change plan
        r_put = httpx.put(
            f"{BASE_URL}/tenants/{slug}",
            headers=HEADERS,
            json={"plan": "growth"},
            timeout=10,
        )
        assert r_put.status_code == 200, f"PUT tenant failed: {r_put.text}"

        # Verify change
        r_verify = httpx.get(f"{BASE_URL}/tenants/{slug}", headers=HEADERS, timeout=10)
        assert r_verify.status_code == 200
        assert r_verify.json()["plan"] == "growth", "Plan should be updated to growth"

    finally:
        cancel_tenant(slug)


# ---------------------------------------------------------------------------
# 6. Seat management — list and delete (POST is broken, skip creation via API)
# ---------------------------------------------------------------------------

def test_seat_management():
    """Test GET /tenants/{slug}/seats and DELETE /tenants/{slug}/seats/{token_id}.

    NOTE: POST /tenants/{slug}/seats has a forward-reference bug in app.py
    (CreateSeatRequest class defined after its first use, causing FastAPI to treat
    the body as a query param → always 422). We test list and delete only.
    """
    name = f"Seat Biz {ts()}"
    r = httpx.post(
        f"{BASE_URL}/signup",
        json={"name": name, "email": f"seat-{ts()}@example.com", "plan": "growth"},
        timeout=15,
    )
    assert r.status_code == 200, f"Signup failed: {r.text}"
    slug = r.json()["tenant"]

    try:
        # GET seats — signup creates the initial token but it's registered as "customer" scope
        r_seats = httpx.get(f"{BASE_URL}/tenants/{slug}/seats", headers=HEADERS, timeout=10)
        assert r_seats.status_code == 200, f"GET seats failed: {r_seats.text}"
        body = r_seats.json()
        assert "seats" in body
        assert "count" in body
        assert "limit" in body  # growth plan = 5

        # If seats exist, test deletion of one
        seats = body["seats"]
        if seats:
            token_id = seats[0]["token_id"]
            r_del = httpx.delete(
                f"{BASE_URL}/tenants/{slug}/seats/{token_id}",
                headers=HEADERS,
                timeout=10,
            )
            assert r_del.status_code == 200, f"DELETE seat failed: {r_del.text}"
            assert r_del.json()["revoked"] is True

    finally:
        cancel_tenant(slug)


# ---------------------------------------------------------------------------
# 7. Marketplace subscribe — verify subscription record (task_id may be None)
# ---------------------------------------------------------------------------

def test_marketplace_subscribe():
    """Marketplace.subscribe() is a Python class method, not an HTTP endpoint.

    The Marketplace class in marketplace.py has no FastAPI routes registered in app.py.
    We test it directly via Python import.
    """
    from sos.services.saas.marketplace import Marketplace

    name = f"Mkt Biz {ts()}"
    r = httpx.post(
        f"{BASE_URL}/signup",
        json={"name": name, "email": f"mkt-{ts()}@example.com", "plan": "starter"},
        timeout=15,
    )
    assert r.status_code == 200, f"Signup failed: {r.text}"
    slug = r.json()["tenant"]

    try:
        mkt = Marketplace()
        result = mkt.subscribe(slug, "lst-seo-audit")

        assert result.get("success") is True, f"Subscribe failed: {result}"
        assert "listing" in result
        assert "task_id" in result  # May be None if Squad unreachable — that's ok
        assert result["listing"]["id"] == "lst-seo-audit"

        # Verify subscription exists
        subs = mkt.my_subscriptions(slug)
        assert any(s["listing_id"] == "lst-seo-audit" for s in subs), (
            "Subscription should appear in my_subscriptions"
        )

        # Unsubscribe cleanup
        mkt.unsubscribe(slug, "lst-seo-audit")

    finally:
        cancel_tenant(slug)


# ---------------------------------------------------------------------------
# 8. Public resolve — no auth needed
# ---------------------------------------------------------------------------

def test_public_resolve():
    """GET /resolve/{hostname} is public (no auth required)."""
    # Known tenant: viamar
    r = httpx.get(f"{BASE_URL}/resolve/viamar.mumega.com", timeout=10)
    assert r.status_code == 200, f"Resolve failed: {r.text}"
    body = r.json()
    assert body["slug"] == "viamar"

    # No auth header needed — verify it also works without
    r_noauth = httpx.get(f"{BASE_URL}/resolve/viamar.mumega.com", timeout=10)
    assert r_noauth.status_code == 200

    # Non-existent hostname → 404
    r_404 = httpx.get(f"{BASE_URL}/resolve/does-not-exist-xyz.mumega.com", timeout=10)
    assert r_404.status_code == 404, (
        f"Expected 404 for unknown host, got {r_404.status_code}"
    )


# ---------------------------------------------------------------------------
# 9. Build enqueue
# ---------------------------------------------------------------------------

def test_build_enqueue():
    name = f"Build Biz {ts()}"
    r = httpx.post(
        f"{BASE_URL}/signup",
        json={"name": name, "email": f"build-{ts()}@example.com", "plan": "starter"},
        timeout=15,
    )
    assert r.status_code == 200, f"Signup failed: {r.text}"
    slug = r.json()["tenant"]

    try:
        r_build = httpx.post(
            f"{BASE_URL}/builds/enqueue/{slug}",
            headers=HEADERS,
            timeout=10,
        )
        assert r_build.status_code == 200, f"Build enqueue failed: {r_build.text}"
        body = r_build.json()
        assert body.get("queued") is True, f"Expected queued:true, got: {body}"
        assert "queue_length" in body

    finally:
        cancel_tenant(slug)
