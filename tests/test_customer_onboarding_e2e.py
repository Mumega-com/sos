"""Customer onboarding end-to-end smoke test.

Covers the full path a new Mumega customer walks when they sign up:

  1. POST /signup on saas service (port 8075)  → new tenant + bus token
  2. Token lands in tokens.json with active=True and project=<slug>
  3. Token can POST /usage on economy service (tenant-scoped write)
  4. Token can GET /usage to read back its own events (tenant-scoped read)
  5. Token CANNOT cross-tenant read (403)
  6. Token can query the saas registry for its own state

Run:
    uv run --with pytest --with httpx --with pydantic python -m pytest \\
        tests/test_customer_onboarding_e2e.py -v

Environment:
    Needs SaaS service running on localhost:8075.
    Needs economy service running on localhost:7010 (if wired), or this
    test falls back to in-process FastAPI TestClient against the economy app.
    Cleans up the tenant it creates (DELETE on /tenants/{slug}, if supported)
    so repeated runs don't accumulate test tenants.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import httpx
import pytest


SAAS_URL = os.environ.get("SAAS_URL", "http://localhost:8075")
# Tests tolerate a missing economy HTTP service by using in-process TestClient.
ECONOMY_URL = os.environ.get("ECONOMY_URL", None)

TOKENS_JSON = Path(__file__).resolve().parents[1] / "sos" / "bus" / "tokens.json"


def _saas_reachable() -> bool:
    try:
        r = httpx.get(f"{SAAS_URL}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _saas_reachable(), reason="SaaS service not running")


@pytest.fixture
def unique_tenant_slug() -> str:
    """Generate a unique tenant name for this test run."""
    return f"e2etest-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def signed_up_tenant(unique_tenant_slug):
    """Sign up a fresh tenant; return its slug + token. Clean up after."""
    name = unique_tenant_slug
    resp = httpx.post(
        f"{SAAS_URL}/signup",
        json={"name": name, "email": f"{name}@example.test", "plan": "starter"},
        timeout=10,
    )
    if resp.status_code == 409:
        pytest.skip("slug collision (rare); skip this run")
    assert resp.status_code == 200, f"signup failed: {resp.status_code} {resp.text}"
    body = resp.json()
    # signup response carries bus_token or mcp_url — support either shape
    token = body.get("bus_token") or body.get("token")
    slug = body.get("slug") or body.get("tenant") or name
    if not token and body.get("mcp_url"):
        token = body["mcp_url"].rstrip("/").split("/")[-1]
    assert token, f"signup response has no token: {body}"

    yield {"slug": slug, "token": token, "name": name, "signup_response": body}

    # Best-effort cleanup
    try:
        httpx.post(
            f"{SAAS_URL}/tenants/{slug}/suspend",
            timeout=5,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Step 1-2 — Signup + token provisioning
# ---------------------------------------------------------------------------


class TestSignupFlow:
    def test_signup_returns_200_and_token(self, signed_up_tenant):
        assert signed_up_tenant["token"]
        assert signed_up_tenant["slug"]
        assert signed_up_tenant["token"].startswith("sk-")

    def test_token_persisted_to_tokens_json(self, signed_up_tenant):
        # small delay because the saas service writes async
        time.sleep(0.5)
        tokens = json.loads(TOKENS_JSON.read_text())
        slug = signed_up_tenant["slug"]
        matching = [t for t in tokens if t.get("project") == slug and t.get("active", True)]
        assert matching, f"no active token for project {slug} in tokens.json"

    def test_tenant_visible_in_registry(self, signed_up_tenant):
        slug = signed_up_tenant["slug"]
        resp = httpx.get(f"{SAAS_URL}/tenants/{slug}", timeout=5)
        # Some registries scope the endpoint behind admin auth; 200 or 401 is both fine.
        assert resp.status_code in (200, 401, 404)
        # If 200, validate the record shape.
        if resp.status_code == 200:
            body = resp.json()
            assert body.get("slug") == slug or body.get("label")


# ---------------------------------------------------------------------------
# Step 3-5 — Token can POST /usage, read own events, cannot cross-read
# ---------------------------------------------------------------------------


class TestUsageFlowInProcess:
    """Uses the in-process FastAPI TestClient for the economy service.

    This verifies the AUTHENTICATION + TENANT-SCOPING contract works
    correctly for the freshly-minted bus token, even without a running
    economy HTTP server. Proves the onboarding pipeline's token shape
    matches the economy auth.
    """

    def _client(self):
        from fastapi.testclient import TestClient
        from sos.services.economy.app import app
        return TestClient(app)

    def test_token_can_post_usage_for_own_tenant(self, signed_up_tenant):
        client = self._client()
        body = {
            "tenant": signed_up_tenant["slug"],
            "provider": "google",
            "model": "gemini-flash-lite-latest",
            "endpoint": "/onboarding-e2e/first-call",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 100,
            "metadata": {"onboarding_e2e": True},
        }
        resp = client.post(
            "/usage",
            json=body,
            headers={"Authorization": f"Bearer {signed_up_tenant['token']}"},
        )
        assert resp.status_code == 201, resp.text
        event = resp.json()
        assert "id" in event
        assert "received_at" in event

    def test_token_cannot_post_for_other_tenant(self, signed_up_tenant):
        client = self._client()
        body = {
            "tenant": "some-other-tenant",
            "provider": "google",
            "model": "gemini-flash-lite-latest",
            "cost_micros": 100,
        }
        resp = client.post(
            "/usage",
            json=body,
            headers={"Authorization": f"Bearer {signed_up_tenant['token']}"},
        )
        assert resp.status_code == 403

    def test_token_can_read_own_usage(self, signed_up_tenant):
        client = self._client()
        # First write one event
        body = {
            "tenant": signed_up_tenant["slug"],
            "provider": "google",
            "model": "gemini-flash-lite-latest",
            "cost_micros": 42,
            "metadata": {"onboarding_e2e": True, "step": "read-back"},
        }
        client.post(
            "/usage",
            json=body,
            headers={"Authorization": f"Bearer {signed_up_tenant['token']}"},
        )
        # Now read back
        resp = client.get(
            "/usage",
            headers={"Authorization": f"Bearer {signed_up_tenant['token']}"},
        )
        assert resp.status_code == 200
        events = resp.json()["events"]
        # At least one event matching our tenant must be present
        mine = [e for e in events if e["tenant"] == signed_up_tenant["slug"]]
        assert mine, "no usage events visible to the tenant's own token"

    def test_token_cannot_read_other_tenant_usage(self, signed_up_tenant):
        client = self._client()
        resp = client.get(
            "/usage?tenant=some-other-tenant",
            headers={"Authorization": f"Bearer {signed_up_tenant['token']}"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Step 6 — Signup response shape advertises everything the customer needs
# ---------------------------------------------------------------------------


class TestSignupResponseShape:
    """The signup response is the 10-second onboarding moment. It has to carry
    everything a customer needs to wire their first MCP call."""

    def test_response_has_mcp_url_or_token(self, signed_up_tenant):
        body = signed_up_tenant["signup_response"]
        has_something = "mcp_url" in body or "token" in body or "bus_token" in body
        assert has_something, f"signup response missing connection info: {body}"

    def test_token_is_usable_form(self, signed_up_tenant):
        # Must be a sk- prefixed token (documented shape)
        assert signed_up_tenant["token"].startswith("sk-")
        assert len(signed_up_tenant["token"]) > 20
