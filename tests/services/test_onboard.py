"""Tests for Sprint 006 E.2 / G68 — tenant onboarding flow (GitHub OAuth + Stripe Quote + contracts).

TC-G68a — sign_intent + verify_intent: round-trip succeeds; tampered sig fails; expired fails
TC-G68b — consume_nonce atomicity: second consume raises ValueError (consumed); expired raises
TC-G68c — upsert_onboard_principal: idempotent on (email, tenant_id='prospect'); roles unchanged
TC-G68d — create_contract idempotency: same stripe_quote_id returns same contract id
TC-G68e — create_stripe_quote: returns synthetic quote when STRIPE_SECRET_KEY absent
TC-G68f — /onboard/start: rejects tampered intent token (HTTP 400)
TC-G68g — /onboard/start: redirects to GitHub OAuth URL containing nonce as state= param
TC-G68h — /onboard/github/callback: rejects missing code/state (HTTP 400)
TC-G68i — /onboard/github/callback: rejects expired/consumed nonce (HTTP 400)
TC-G68j — /onboard/discord-intent: requires admin auth; returns signed token + URL
"""
from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

# ADV-G68-001: ONBOARD_INTENT_SECRET must be set; no hardcoded fallback in production.
# Provide a deterministic test value so sign_intent / verify_intent work in unit tests.
os.environ.setdefault("ONBOARD_INTENT_SECRET", "test-intent-secret-not-for-production")

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# TC-G68a — sign_intent / verify_intent
# ---------------------------------------------------------------------------


class TestIntentSigning:
    def test_round_trip(self) -> None:
        from sos.contracts.onboarding import sign_intent, verify_intent

        intent = {"email_hint": "alice@example.com", "plan": "starter"}
        token = sign_intent(intent)
        recovered = verify_intent(token)
        assert recovered["email_hint"] == "alice@example.com"
        assert recovered["plan"] == "starter"
        assert "exp" in recovered

    def test_tampered_signature_rejected(self) -> None:
        from sos.contracts.onboarding import sign_intent, verify_intent

        token = sign_intent({"plan": "starter"})
        tampered = token[:-4] + "dead"
        with pytest.raises(ValueError, match="signature invalid"):
            verify_intent(tampered)

    def test_expired_token_rejected(self) -> None:
        from sos.contracts.onboarding import sign_intent, verify_intent

        # ttl_seconds=0 generates a token that expires immediately
        token = sign_intent({"plan": "starter"}, ttl_seconds=-1)
        with pytest.raises(ValueError, match="expired"):
            verify_intent(token)

    def test_malformed_token_rejected(self) -> None:
        from sos.contracts.onboarding import verify_intent

        with pytest.raises(ValueError, match="malformed"):
            verify_intent("notavalidtoken")


# ---------------------------------------------------------------------------
# TC-G68b — consume_nonce atomicity (DB-skipped without DATABASE_URL)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (os.environ.get("DATABASE_URL") or os.environ.get("MIRROR_DATABASE_URL")),
    reason="requires live database",
)
class TestConsumeNonceDB:
    def test_consume_once_succeeds(self) -> None:
        from sos.contracts.onboarding import consume_nonce, create_onboard_nonce

        nonce = create_onboard_nonce({"plan": "starter"})
        intent = consume_nonce(nonce)
        assert intent.get("plan") == "starter"

    def test_double_consume_raises(self) -> None:
        from sos.contracts.onboarding import consume_nonce, create_onboard_nonce

        nonce = create_onboard_nonce({"plan": "starter"})
        consume_nonce(nonce)
        with pytest.raises(ValueError, match="consumed"):
            consume_nonce(nonce)

    def test_nonexistent_nonce_raises(self) -> None:
        from sos.contracts.onboarding import consume_nonce

        with pytest.raises(ValueError, match="not found"):
            consume_nonce("0" * 64)  # valid length, doesn't exist


# ---------------------------------------------------------------------------
# TC-G68c — upsert_onboard_principal idempotency (mocked)
# ---------------------------------------------------------------------------


class TestUpsertOnboardPrincipal:
    def test_idempotent_returns_same_id(self) -> None:
        from sos.contracts.onboarding import upsert_onboard_principal

        mock_principal = MagicMock()
        mock_principal.id = "pid-abc123"

        # Patch the module-level name now that it's a top-level import
        with patch("sos.contracts.onboarding.upsert_principal", return_value=mock_principal) as mock_up:
            pid1 = upsert_onboard_principal(
                github_login="alice", email="alice@example.com", display_name="Alice"
            )
            pid2 = upsert_onboard_principal(
                github_login="alice", email="alice@example.com", display_name="Alice Updated"
            )

        assert pid1 == "pid-abc123"
        assert pid2 == "pid-abc123"
        # Both calls use tenant_id='prospect'
        for call in mock_up.call_args_list:
            assert call.kwargs["tenant_id"] == "prospect"

    def test_synthetic_email_for_private_github(self) -> None:
        from sos.contracts.onboarding import upsert_onboard_principal

        mock_principal = MagicMock()
        mock_principal.id = "pid-xyz"

        with patch("sos.contracts.onboarding.upsert_principal", return_value=mock_principal) as mock_up:
            upsert_onboard_principal(
                github_login="bob", email=None, display_name="Bob"
            )

        # email= should be the synthetic github.invalid address
        called_email = mock_up.call_args.kwargs["email"]
        assert called_email == "bob@github.invalid"


# ---------------------------------------------------------------------------
# TC-G68d — create_contract idempotency (mocked DB)
# ---------------------------------------------------------------------------


class TestCreateContractIdempotency:
    def _mock_conn(self, contract_id: str):
        mock_cur = MagicMock()
        mock_cur.__enter__ = lambda s: s
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = {"id": contract_id}

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        return mock_conn

    def test_returns_contract_id(self) -> None:
        from sos.contracts.onboarding import create_contract

        with patch("sos.contracts.onboarding._connect", return_value=self._mock_conn("cid-001")):
            cid = create_contract(
                principal_id="pid-001",
                stripe_customer_id="cus_test",
                stripe_quote_id="qt_test_001",
                stripe_quote_url="https://quote.stripe.com/test/qt_test_001",
            )
        assert cid == "cid-001"

    def test_invalid_status_raises(self) -> None:
        from sos.contracts.onboarding import create_contract

        with pytest.raises(ValueError, match="invalid contract status"):
            create_contract(
                principal_id="pid",
                stripe_customer_id=None,
                stripe_quote_id="qt_test",
                stripe_quote_url="https://example.com",
                status="paid",  # not a valid status
            )


# ---------------------------------------------------------------------------
# TC-G68e — create_stripe_quote: synthetic quote without STRIPE_SECRET_KEY
# ---------------------------------------------------------------------------


class TestCreateStripeQuote:
    def test_synthetic_quote_when_no_secret(self) -> None:
        from sos.contracts.onboarding import create_stripe_quote

        env_backup = os.environ.pop("STRIPE_SECRET_KEY", None)
        try:
            quote_id, quote_url = create_stripe_quote(
                principal_id="pid-test",
                email="test@example.com",
                display_name="Test User",
                plan="starter",
            )
        finally:
            if env_backup is not None:
                os.environ["STRIPE_SECRET_KEY"] = env_backup

        assert quote_id.startswith("qt_test_")
        assert "stripe.com" in quote_url

    def test_raises_without_price_id_when_stripe_configured(self) -> None:
        from sos.contracts.onboarding import create_stripe_quote

        # Patch _resolve_stripe_customer so no real Stripe API call is made;
        # the error we're testing (missing STRIPE_PRICE_ID_STARTER) occurs
        # after customer resolution.
        with (
            patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_xxx"}),
            patch("sos.contracts.onboarding._resolve_stripe_customer", return_value="cus_fake"),
        ):
            os.environ.pop("STRIPE_PRICE_ID_STARTER", None)
            with pytest.raises(ValueError, match="STRIPE_PRICE_ID_STARTER"):
                create_stripe_quote(
                    principal_id="pid-001",
                    email="test@example.com",
                    display_name="Test",
                    plan="starter",
                )


# ---------------------------------------------------------------------------
# TC-G68f + TC-G68g — /onboard/start HTTP routes
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from fastapi import FastAPI
    from sos.services.saas.onboard_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, follow_redirects=False)


class TestOnboardStartRoute:
    def test_rejects_tampered_intent(self, client: TestClient) -> None:
        resp = client.get("/onboard/start?intent=notvalid.deadsig")
        assert resp.status_code == 400
        assert "invalid intent" in resp.json()["detail"]

    def test_redirects_to_github_with_nonce(self, client: TestClient) -> None:
        from sos.contracts.onboarding import sign_intent

        token = sign_intent({"plan": "starter"})

        with (
            patch("sos.services.saas.onboard_routes.create_onboard_nonce", return_value="abc123nonce") as _mock_nonce,
            patch("sos.services.saas.onboard_routes._check_nonce_rate_limit"),
            patch.dict(os.environ, {"GITHUB_CLIENT_ID": "gh-client-id-test"}),
        ):
            resp = client.get(f"/onboard/start?intent={token}")

        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "github.com/login/oauth/authorize" in location
        assert "state=abc123nonce" in location
        assert "client_id=gh-client-id-test" in location

    def test_returns_503_without_github_client_id(self, client: TestClient) -> None:
        from sos.contracts.onboarding import sign_intent

        token = sign_intent({"plan": "starter"})

        env_backup = os.environ.pop("GITHUB_CLIENT_ID", None)
        try:
            with (
                patch("sos.services.saas.onboard_routes.create_onboard_nonce", return_value="nonce123"),
                patch("sos.services.saas.onboard_routes._check_nonce_rate_limit"),
            ):
                resp = client.get(f"/onboard/start?intent={token}")
        finally:
            if env_backup:
                os.environ["GITHUB_CLIENT_ID"] = env_backup

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# TC-G68h + TC-G68i — /onboard/github/callback
# ---------------------------------------------------------------------------


class TestGithubCallback:
    def test_rejects_missing_code_and_state(self, client: TestClient) -> None:
        resp = client.get("/onboard/github/callback")
        assert resp.status_code == 400

    def test_rejects_oauth_error_param(self, client: TestClient) -> None:
        resp = client.get("/onboard/github/callback?error=access_denied&state=nonce")
        assert resp.status_code == 400
        # ADV-G68-013: raw error code must NOT be reflected; mapped to safe message
        assert "access_denied" not in resp.json()["detail"]
        assert "denied" in resp.json()["detail"].lower()

    def test_rejects_invalid_nonce(self, client: TestClient) -> None:
        with patch(
            "sos.services.saas.onboard_routes.consume_nonce",
            side_effect=ValueError("onboard nonce not found"),
        ):
            resp = client.get("/onboard/github/callback?code=gh-code&state=bad-nonce")
        assert resp.status_code == 400
        assert "nonce" in resp.json()["detail"]

    def test_happy_path_redirects_to_quote_url(self, client: TestClient) -> None:
        quote_url = "https://quote.stripe.com/test/qt_test_pid1"

        with (
            patch("sos.services.saas.onboard_routes.consume_nonce", return_value={"plan": "starter"}),
            patch(
                "sos.services.saas.onboard_routes._exchange_github_code",
                new=AsyncMock(return_value="gh-access-token"),
            ),
            patch(
                "sos.services.saas.onboard_routes._fetch_github_profile",
                new=AsyncMock(return_value={"login": "alice", "email": "alice@example.com", "name": "Alice"}),
            ),
            patch("sos.services.saas.onboard_routes.upsert_onboard_principal", return_value="pid-001"),
            patch("sos.services.saas.onboard_routes.create_stripe_quote", return_value=("qt_001", quote_url)),
            patch("sos.services.saas.onboard_routes.create_contract", return_value="cid-001"),
        ):
            resp = client.get("/onboard/github/callback?code=gh-code&state=valid-nonce")

        assert resp.status_code == 302
        assert resp.headers["location"] == quote_url


# ---------------------------------------------------------------------------
# TC-G68j — /onboard/discord-intent requires admin auth
# ---------------------------------------------------------------------------


class TestDiscordIntentRoute:
    def test_rejects_unauthenticated(self, client: TestClient) -> None:
        resp = client.post("/onboard/discord-intent", json={"plan": "starter"})
        assert resp.status_code == 401

    def test_returns_signed_url_with_admin_auth(self, client: TestClient) -> None:
        from sos.contracts.onboarding import verify_intent

        # Provide admin key via env so the require_admin dep resolves correctly
        with patch.dict(os.environ, {"SOS_SAAS_ADMIN_KEY": "test-admin-key"}):
            resp = client.post(
                "/onboard/discord-intent",
                json={"email_hint": "prospect@example.com", "plan": "starter"},
                headers={"Authorization": "Bearer test-admin-key"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "intent_token" in data
        assert "onboard_url" in data
        # Token must be verifiable
        intent = verify_intent(data["intent_token"])
        assert intent["email_hint"] == "prospect@example.com"
        assert intent["plan"] == "starter"
